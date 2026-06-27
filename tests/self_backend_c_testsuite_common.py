from __future__ import annotations

import json
from pathlib import Path

from tests.c_testsuite_cases import PccCompileResult, read_expected_output


REPO_ROOT = Path(__file__).resolve().parents[1]
C_TESTSUITE_DIR = REPO_ROOT / "projects" / "c-testsuite"
C_TESTSUITE_MANIFEST_PATH = REPO_ROOT / "tests" / "c_testsuite_manifest.json"
C_TESTSUITE_MANIFEST = json.loads(C_TESTSUITE_MANIFEST_PATH.read_text(encoding="utf-8"))
C_TESTSUITE_RUNTIME_EXACT_MATCH_CASES = tuple(
    C_TESTSUITE_MANIFEST.get("runtime_exact_match", [])
)


def c_testsuite_case_path(filename: str) -> Path:
    return C_TESTSUITE_DIR / filename


def exact_match_cases(limit: int | None = None) -> tuple[str, ...]:
    cases = C_TESTSUITE_RUNTIME_EXACT_MATCH_CASES
    if limit is None:
        return cases
    return cases[:limit]


def cases_with_expected_output(filenames: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        filename
        for filename in filenames
        if read_expected_output(c_testsuite_case_path(filename)).strip()
    )


def result_triplet_mismatch_message(
    filename: str,
    lhs_name: str,
    lhs_result: PccCompileResult,
    rhs_name: str,
    rhs_result: PccCompileResult,
) -> str | None:
    if lhs_result.returncode != rhs_result.returncode:
        return (
            f"{filename} return code mismatch:\n"
            f"{lhs_name}={lhs_result.returncode}\n"
            f"{rhs_name}={rhs_result.returncode}\n"
            f"{lhs_name} stderr:\n{lhs_result.stderr}\n"
            f"{rhs_name} stderr:\n{rhs_result.stderr}"
        )
    if lhs_result.stdout != rhs_result.stdout:
        return (
            f"{filename} stdout mismatch:\n"
            f"{lhs_name}={lhs_result.stdout!r}\n"
            f"{rhs_name}={rhs_result.stdout!r}"
        )
    if lhs_result.stderr != rhs_result.stderr:
        return (
            f"{filename} stderr mismatch:\n"
            f"{lhs_name}={lhs_result.stderr!r}\n"
            f"{rhs_name}={rhs_result.stderr!r}"
        )
    return None


def assert_result_triplet_matches(
    filename: str,
    lhs_name: str,
    lhs_result: PccCompileResult,
    rhs_name: str,
    rhs_result: PccCompileResult,
) -> None:
    mismatch = result_triplet_mismatch_message(
        filename,
        lhs_name,
        lhs_result,
        rhs_name,
        rhs_result,
    )
    assert mismatch is None, mismatch
