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
    return case_path.read_text(encoding="latin-1")


def compile_native(case_path: Path, repo_root: Path, timeout: int = DEFAULT_TIMEOUT):
    cc = _host_cc()
    with tempfile.TemporaryDirectory(prefix="gcc_torture_native_compile_") as tmpdir:
        binary = Path(tmpdir) / "a.out"
        return subprocess.run(
            [cc, str(case_path), "-o", str(binary)],
            cwd=repo_root,
            env=subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def run_native(case_path: Path, repo_root: Path, timeout: int = DEFAULT_TIMEOUT):
    cc = _host_cc()
    with tempfile.TemporaryDirectory(prefix="gcc_torture_native_") as tmpdir:
        binary = Path(tmpdir) / "a.out"
        compile_result = subprocess.run(
            [cc, str(case_path), "-o", str(binary)],
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


def compile_pcc(case_path: Path, timeout: int = DEFAULT_TIMEOUT) -> PccCompileResult:
    return _run_pcc_worker("compile", case_path, timeout)


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
    unit = TranslationUnit(case_path.name, str(case_path), _read_case_source(case_path))
    try:
        evaluator = CEvaluator()
        if mode == "compile":
            evaluator.compile_translation_units(
                [unit],
                base_dir=str(case_path.parent),
                include_dirs=[str(case_path.parent)],
            )
            conn.send({"returncode": 0, "stdout": "", "stderr": ""})
            return

        result = evaluator.run_translation_units_with_system_cc(
            [unit],
            base_dir=str(case_path.parent),
            include_dirs=[str(case_path.parent)],
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
