from __future__ import annotations

from functools import lru_cache

import pytest

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import TranslationUnit
from tests.c_testsuite_cases import (
    PccCompileResult,
    _default_timeout,
    _read_case_source,
    case_config,
    read_expected_output,
    run_native,
    run_pcc,
)
from tests.self_backend_c_testsuite_common import (
    REPO_ROOT,
    assert_result_triplet_matches,
    c_testsuite_case_path,
    cases_with_expected_output,
    exact_match_cases,
)

# The self backend now clears the full broad 220-case runtime exact-match
# manifest, so keep the repository-scale gate at full width instead of a
# curated prefix bucket.
C_TESTSUITE_SELF_BACKEND_EXACT_MATCH_CASES = exact_match_cases()
C_TESTSUITE_SELF_BACKEND_DIFFERENTIAL_CASES = C_TESTSUITE_SELF_BACKEND_EXACT_MATCH_CASES


def _case_params(cases):
    return [
        pytest.param(
            filename,
            marks=pytest.mark.xdist_group(name=f"c_testsuite:{filename}"),
        )
        for filename in cases
    ]


def _run_self_backend(case_path: Path, timeout: int | None = None) -> PccCompileResult:
    return _run_backend(
        case_path, backend="self", timeout=timeout, allow_unimplemented_backend=True
    )


def _run_llvm_backend(case_path: Path, timeout: int | None = None) -> PccCompileResult:
    return run_pcc(case_path, REPO_ROOT, timeout)


@lru_cache(maxsize=None)
def _run_backend(
    case_path: Path,
    *,
    backend: str,
    timeout: int | None = None,
    allow_unimplemented_backend: bool = False,
) -> PccCompileResult:
    if timeout is None:
        timeout = _default_timeout()
    config = case_config(case_path)
    unit = TranslationUnit(case_path.name, str(case_path), _read_case_source(case_path))
    try:
        result = CEvaluator(
            backend=backend,
            allow_unimplemented_backend=allow_unimplemented_backend,
        ).run_translation_units_with_system_cc(
            [unit],
            base_dir=str(case_path.parent),
            include_dirs=[str(case_path.parent)],
            cpp_args=config.cpp_args,
            timeout=timeout,
        )
        return PccCompileResult(result.returncode, result.stdout, result.stderr)
    except Exception as exc:
        return PccCompileResult(1, "", str(exc))


@pytest.mark.parametrize(
    "filename", _case_params(C_TESTSUITE_SELF_BACKEND_EXACT_MATCH_CASES)
)
def test_c_testsuite_self_backend_runtime_matches_native_exactly(filename):
    case_path = c_testsuite_case_path(filename)
    assert case_path.is_file(), f"missing c-testsuite case: {case_path}"

    native_result = run_native(case_path, REPO_ROOT)
    self_result = _run_self_backend(case_path)

    assert_result_triplet_matches(
        filename, "self", self_result, "native", native_result
    )


@pytest.mark.parametrize(
    "filename", _case_params(C_TESTSUITE_SELF_BACKEND_DIFFERENTIAL_CASES)
)
def test_c_testsuite_self_backend_matches_llvm_on_exact_match_bucket(filename):
    case_path = c_testsuite_case_path(filename)
    assert case_path.is_file(), f"missing c-testsuite case: {case_path}"

    llvm_result = _run_llvm_backend(case_path)
    self_result = _run_self_backend(case_path)

    assert_result_triplet_matches(filename, "self", self_result, "llvm", llvm_result)


C_TESTSUITE_SELF_BACKEND_CASES_WITH_EXPECTED_OUTPUT = cases_with_expected_output(
    C_TESTSUITE_SELF_BACKEND_EXACT_MATCH_CASES
)


@pytest.mark.parametrize(
    "filename", _case_params(C_TESTSUITE_SELF_BACKEND_CASES_WITH_EXPECTED_OUTPUT)
)
def test_c_testsuite_self_backend_output_matches_expected_file(filename):
    case_path = c_testsuite_case_path(filename)
    expected_output = read_expected_output(case_path)
    self_result = _run_self_backend(case_path)

    assert self_result.returncode == 0, (
        f"{filename} self backend returned {self_result.returncode}:\n"
        f"{self_result.stderr}"
    )
    assert self_result.stdout == expected_output, (
        f"{filename} output vs .expected mismatch:\n"
        f"expected={expected_output!r}\nself={self_result.stdout!r}"
    )
