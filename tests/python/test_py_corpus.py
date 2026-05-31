from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

OVERRIDE_TARGET_TRIPLE_WARNING = re.compile(
    r"warning: overriding the module target triple with "
)


def _locate_corpus_root() -> Path:
    test_file = Path(__file__).resolve()
    candidates = [
        test_file.parent / "py_corpus",
        test_file.parent.parent / "py_corpus",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise RuntimeError(f"py_corpus not found. Checked: {', '.join(str(c) for c in candidates)}")


def _load_expected(test_dir: Path) -> tuple[bytes, int]:
    stdout_path = test_dir / "expected.stdout"
    status_path = test_dir / "expected.status"
    status_text = status_path.read_text().strip()
    return (
        stdout_path.read_bytes(),
        int(status_text),
    )


def _collect_py_corpus_cases() -> list[Path]:
    root = _locate_corpus_root()
    cases: list[Path] = []
    for phase in sorted(root.iterdir()):
        if not phase.is_dir() or not phase.name.startswith("phase"):
            continue
        for case_dir in sorted(phase.iterdir()):
            if not case_dir.is_dir():
                continue
            if not (case_dir / "source.py").is_file():
                continue
            if not (case_dir / "expected.stdout").is_file():
                continue
            if not (case_dir / "expected.status").is_file():
                continue
            cases.append(case_dir)
    return cases


def _case_id(case: Path) -> str:
    rel = case.relative_to(_locate_corpus_root())
    return str(rel)


PYTHON_CORPUS_CASES = _collect_py_corpus_cases()


@pytest.mark.parametrize("case_dir", PYTHON_CORPUS_CASES, ids=_case_id)
def test_py_corpus_cases(tmp_path: Path, case_dir: Path) -> None:
    expected_stdout, expected_status = _load_expected(case_dir)
    exe = tmp_path / "program"

    source = case_dir / "source.py"
    compile_cmd = [sys.executable, "-m", "pcc", str(source), "-o", str(exe)]
    compile_result = subprocess.run(
        compile_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if compile_result.returncode != 0:
        output = "\n".join(
            line
            for line in (compile_result.stdout + compile_result.stderr).splitlines()
            if not OVERRIDE_TARGET_TRIPLE_WARNING.search(line)
        )
        raise AssertionError(f"pcc compile failed for {case_dir.name}:\n{output}")

    compile_output = compile_result.stdout + compile_result.stderr
    assert exe.exists(), f"expected output executable not found: {exe}"

    run_result = subprocess.run(
        [str(exe)],
        capture_output=True,
        timeout=30,
    )
    assert run_result.returncode == expected_status, (
        f"status mismatch in {case_dir.name}: expected={expected_status} "
        f"got={run_result.returncode}\n"
        f"compile stdout/stderr:\n{compile_output}"
    )
    assert run_result.stdout == expected_stdout, (
        f"stdout mismatch in {case_dir.name}:\n"
        f"expected={expected_stdout!r}\n"
        f"got={run_result.stdout!r}\n"
        f"stderr={run_result.stderr!r}"
    )
