#!/usr/bin/env python3
"""Phase 1 Python corpus harness.

Walks tests/py_corpus/phase1/*/ and verifies that each test's
source.py, when run by CPython, produces exactly the bytes recorded in
expected.stdout and exits with the status in expected.status.

This harness is a meta-check that the expected.* files are accurate
references. It does NOT invoke pcc — that is a later integration step.

Usage:
    python tests/py_corpus/run_phase1.py              # run all tests
    python tests/py_corpus/run_phase1.py --phase phase1
    python tests/py_corpus/run_phase1.py --filter fib  # substring match on name
    python tests/py_corpus/run_phase1.py -v            # verbose (show each test)

Exit code: 0 if every test matches; 1 otherwise.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


# Corpus root is this script's sibling "phase1" directory by default.
CORPUS_ROOT = Path(__file__).resolve().parent


def load_expected(test_dir: Path) -> tuple[bytes, int] | None:
    """Load expected stdout bytes and exit status for a test directory.

    Returns (stdout_bytes, status) or None if the directory is missing
    one of the required files (treated as "not a real test dir").
    """
    stdout_path = test_dir / "expected.stdout"
    status_path = test_dir / "expected.status"
    source_path = test_dir / "source.py"
    if not (stdout_path.is_file() and status_path.is_file() and source_path.is_file()):
        return None
    stdout_bytes = stdout_path.read_bytes()
    status_text = status_path.read_text().strip()
    try:
        status = int(status_text)
    except ValueError:
        raise SystemExit(
            f"{status_path}: expected.status must contain an integer, got {status_text!r}"
        )
    return stdout_bytes, status


def run_case(test_dir: Path, verbose: bool) -> bool:
    """Run source.py via CPython; compare stdout bytes + exit code.

    Returns True on match, False on mismatch. Prints a diagnostic on
    mismatch (or on success when verbose).
    """
    loaded = load_expected(test_dir)
    if loaded is None:
        print(f"SKIP {test_dir.name}: missing required file(s)")
        return False
    expected_stdout, expected_status = loaded

    source = test_dir / "source.py"
    proc = subprocess.run(
        [sys.executable, str(source)],
        capture_output=True,
        # Do NOT pass text=True; we want byte-exact comparison (incl. newlines).
    )
    ok_stdout = proc.stdout == expected_stdout
    ok_status = proc.returncode == expected_status
    ok = ok_stdout and ok_status

    if ok:
        if verbose:
            print(f"PASS {test_dir.name}")
        return True

    # Mismatch: print detailed diagnostic.
    print(f"FAIL {test_dir.name}")
    if not ok_status:
        print(f"  exit: expected {expected_status}, got {proc.returncode}")
    if not ok_stdout:
        print(f"  stdout mismatch:")
        print(f"    expected ({len(expected_stdout)} bytes): {expected_stdout!r}")
        print(f"    actual   ({len(proc.stdout)} bytes): {proc.stdout!r}")
    if proc.stderr:
        print(f"  stderr: {proc.stderr!r}")
    return False


def collect_tests(phase: str, name_filter: str | None) -> list[Path]:
    """Return sorted list of test directories under <root>/<phase>/ that
    contain a source.py file, optionally filtered by substring match on
    directory name.
    """
    phase_dir = CORPUS_ROOT / phase
    if not phase_dir.is_dir():
        raise SystemExit(f"phase directory not found: {phase_dir}")
    dirs = [
        d
        for d in sorted(phase_dir.iterdir())
        if d.is_dir() and (d / "source.py").is_file()
    ]
    if name_filter:
        dirs = [d for d in dirs if name_filter in d.name]
    return dirs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1 Python corpus CPython-parity harness."
    )
    parser.add_argument(
        "--phase",
        default="phase1",
        help="Corpus phase subdirectory to run (default: phase1).",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Substring filter on test directory names.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print PASS lines for successful tests (default: silent on success).",
    )
    args = parser.parse_args()

    tests = collect_tests(args.phase, args.filter)
    if not tests:
        print(f"No tests found under {CORPUS_ROOT / args.phase}")
        return 1

    failures: list[str] = []
    for test_dir in tests:
        if not run_case(test_dir, args.verbose):
            failures.append(test_dir.name)

    total = len(tests)
    passed = total - len(failures)
    print(f"\n{passed}/{total} tests passed")
    if failures:
        print("Failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
