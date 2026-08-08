from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.py_corpus_support import PYTHON_CORPUS_CASES, PythonCorpusCase

OVERRIDE_TARGET_TRIPLE_WARNING = re.compile(
    r"warning: overriding the module target triple with "
)


@pytest.mark.parametrize(
    "case",
    PYTHON_CORPUS_CASES,
    ids=[case.name.replace("__", "/") for case in PYTHON_CORPUS_CASES],
)
def test_py_corpus_cases(tmp_path: Path, case: PythonCorpusCase) -> None:
    exe = tmp_path / "program"

    source = case.source_path
    compile_cmd = [sys.executable, "-m", "pcc", str(source), "-o", str(exe)]
    compile_result = subprocess.run(
        compile_cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    if compile_result.returncode != 0:
        output = "\n".join(
            line
            for line in (compile_result.stdout + compile_result.stderr).splitlines()
            if not OVERRIDE_TARGET_TRIPLE_WARNING.search(line)
        )
        raise AssertionError(f"pcc compile failed for {case.name}:\n{output}")

    compile_output = compile_result.stdout + compile_result.stderr
    assert exe.exists(), f"expected output executable not found: {exe}"

    run_result = subprocess.run(
        [str(exe)],
        capture_output=True,
        timeout=30,
    )
    assert run_result.returncode == case.expected_status, (
        f"status mismatch in {case.name}: expected={case.expected_status} "
        f"got={run_result.returncode}\n"
        f"compile stdout/stderr:\n{compile_output}"
    )
    assert run_result.stdout == case.expected_stdout, (
        f"stdout mismatch in {case.name}:\n"
        f"expected={case.expected_stdout!r}\n"
        f"got={run_result.stdout!r}\n"
        f"stderr={run_result.stderr!r}"
    )
