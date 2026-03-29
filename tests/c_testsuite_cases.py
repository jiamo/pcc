from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import TranslationUnit
from tests.worker_process import run_worker_process


DEFAULT_TIMEOUT = 10


@dataclass(frozen=True)
class PccCompileResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class CTestsuiteCaseConfig:
    native_cflags: tuple[str, ...] = ()
    cpp_args: tuple[str, ...] = ()


def subprocess_env():
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def _host_cc():
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        raise RuntimeError("host C compiler not found")
    return cc


def _read_case_source(case_path: Path) -> str:
    try:
        return case_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return case_path.read_text(encoding="latin-1")


def _read_case_tags(case_path: Path) -> list[str]:
    tags_path = case_path.parent / (case_path.name + ".tags")
    if not tags_path.is_file():
        return []
    return [tag for tag in tags_path.read_text(encoding="latin-1").split() if tag]


def case_config(case_path: Path) -> CTestsuiteCaseConfig:
    tags = set(_read_case_tags(case_path))
    native_cflags: list[str] = []
    cpp_args: list[str] = []

    if "c11" in tags:
        native_cflags.append("-std=c11")
        cpp_args.append("-std=c11")
    elif "c99" in tags:
        native_cflags.append("-std=c99")
        cpp_args.append("-std=c99")
    elif "c89" in tags:
        native_cflags.append("-std=c89")
        cpp_args.append("-std=c89")

    return CTestsuiteCaseConfig(
        native_cflags=tuple(native_cflags),
        cpp_args=tuple(cpp_args),
    )


def read_expected_output(case_path: Path) -> str:
    expected_path = case_path.parent / (case_path.name + ".expected")
    if expected_path.is_file():
        return expected_path.read_text(encoding="latin-1")
    return ""


def run_native(case_path: Path, repo_root: Path, timeout: int = DEFAULT_TIMEOUT):
    cc = _host_cc()
    config = case_config(case_path)
    with tempfile.TemporaryDirectory(prefix="c_testsuite_native_") as tmpdir:
        binary = Path(tmpdir) / "a.out"
        compile_result = subprocess.run(
            [cc, *config.native_cflags, str(case_path), "-o", str(binary)],
            cwd=repo_root,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if compile_result.returncode != 0:
            return compile_result
        return subprocess.run(
            [str(binary)],
            cwd=repo_root,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def run_pcc(case_path: Path, repo_root: Path, timeout: int = DEFAULT_TIMEOUT) -> PccCompileResult:
    del repo_root
    return _run_pcc_worker("run", case_path, timeout)


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
