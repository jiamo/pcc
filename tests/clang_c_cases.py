from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import TranslationUnit
from tests.worker_process import run_worker_process


RUN_LINE_RE = re.compile(r"^\s*//\s*RUN:\s*(.*)$")


@dataclass(frozen=True)
class ClangCCaseConfig:
    mode: str
    native_cflags: tuple[str, ...] = ()
    cpp_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class PccCompileResult:
    returncode: int
    stdout: str
    stderr: str


def subprocess_env():
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def case_config(case_path: Path) -> ClangCCaseConfig:
    run_lines = _run_lines(_read_case_source(case_path))
    mode = "runtime"
    native_cflags: list[str] = []
    cpp_args: list[str] = []

    if any(
        token in line
        for line in run_lines
        for token in ("-emit-llvm", "-emit-llvm-only", "-fsyntax-only", "-verify")
    ):
        mode = "compile_only"

    for line in run_lines:
        for token in line.split():
            if token.startswith("-std="):
                native_cflags.append(token)
                cpp_args.append(token)
            elif token in {"-fblocks", "-fwritable-strings"}:
                native_cflags.append(token)

    return ClangCCaseConfig(
        mode=mode,
        native_cflags=tuple(_dedupe(native_cflags)),
        cpp_args=tuple(_dedupe(cpp_args)),
    )


def compile_native(case_path: Path, repo_root: Path) -> subprocess.CompletedProcess[str]:
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        raise RuntimeError("host C compiler not found")

    config = case_config(case_path)
    with tempfile.TemporaryDirectory(prefix="clang_c_native_compile_") as tmpdir:
        binary = Path(tmpdir) / "a.out"
        return subprocess.run(
            [cc, *config.native_cflags, str(case_path), "-o", str(binary)],
            cwd=repo_root,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=20,
        )


def run_native(case_path: Path, repo_root: Path) -> subprocess.CompletedProcess[str]:
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        raise RuntimeError("host C compiler not found")

    config = case_config(case_path)
    with tempfile.TemporaryDirectory(prefix="clang_c_native_") as tmpdir:
        binary = Path(tmpdir) / "a.out"
        compile_result = subprocess.run(
            [cc, *config.native_cflags, str(case_path), "-o", str(binary)],
            cwd=repo_root,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=20,
        )
        if compile_result.returncode != 0:
            return compile_result
        return subprocess.run(
            [str(binary)],
            cwd=repo_root,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=20,
        )


def compile_pcc(case_path: Path, timeout: int = 20) -> PccCompileResult:
    return _run_pcc_worker("compile", case_path, timeout)


def run_pcc(case_path: Path, repo_root: Path, timeout: int = 20) -> PccCompileResult:
    del repo_root
    return _run_pcc_worker("run", case_path, timeout)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _run_lines(source: str) -> list[str]:
    lines = source.splitlines()
    collected: list[str] = []
    current = ""
    for line in lines:
        match = RUN_LINE_RE.match(line)
        if match is None:
            if current:
                collected.append(current.strip())
                current = ""
            continue
        fragment = match.group(1).rstrip()
        if fragment.endswith("\\"):
            current += fragment[:-1].strip() + " "
            continue
        current += fragment
        collected.append(current.strip())
        current = ""
    if current:
        collected.append(current.strip())
    return collected


def _read_case_source(case_path: Path) -> str:
    return case_path.read_text(encoding="latin-1")


def _run_pcc_worker(mode: str, case_path: Path, timeout: int) -> PccCompileResult:
    result = run_worker_process(
        _pcc_worker_entry,
        (mode, str(case_path), timeout),
        timeout,
    )
    if result.timed_out:
        return PccCompileResult(124, "", "timeout")
    payload = result.payload
    if payload is None:
        return PccCompileResult(
            1,
            "",
            f"pcc worker exited without result (exitcode={result.exitcode})",
        )
    return PccCompileResult(
        payload["returncode"],
        payload["stdout"],
        payload["stderr"],
    )


def _pcc_worker_entry(mode: str, case_path_str: str, timeout: int, conn) -> None:
    case_path = Path(case_path_str)
    config = case_config(case_path)
    unit = TranslationUnit(case_path.name, str(case_path), _read_case_source(case_path))
    try:
        evaluator = CEvaluator()
        if mode == "compile":
            evaluator.compile_translation_units(
                [unit],
                base_dir=str(case_path.parent),
                include_dirs=[str(case_path.parent)],
                cpp_args=config.cpp_args,
            )
            conn.send({"returncode": 0, "stdout": "", "stderr": ""})
            return

        result = evaluator.run_translation_units_with_system_cc(
            [unit],
            base_dir=str(case_path.parent),
            include_dirs=[str(case_path.parent)],
            cpp_args=config.cpp_args,
            timeout=timeout,
        )
        conn.send(
            {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
    except Exception as exc:
        conn.send({"returncode": 1, "stdout": "", "stderr": str(exc)})
    finally:
        conn.close()
