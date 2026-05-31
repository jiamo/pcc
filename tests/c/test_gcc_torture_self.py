from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import TranslationUnit
from tests.gcc_torture_cases import (
    DEFAULT_TIMEOUT,
    PccCompileResult,
    _read_case_source,
    run_native,
    run_pcc,
)
from tests.self_backend_c_testsuite_common import (
    REPO_ROOT,
    assert_result_triplet_matches,
)

GCC_TORTURE_DIR = REPO_ROOT / "projects" / "gcc-torture-execute"
GCC_TORTURE_MANIFEST_PATH = REPO_ROOT / "tests" / "gcc_torture_manifest.json"
GCC_TORTURE_MANIFEST = json.loads(GCC_TORTURE_MANIFEST_PATH.read_text())

# Keep this as the full runtime-exact-match bucket so self-backend promotion
# evidence stays broad. Use short range scouts before running this formal gate;
# the full parametrized run is intentionally expensive on the supported host.
GCC_TORTURE_SELF_BACKEND_EXACT_MATCH_CASES = tuple(
    GCC_TORTURE_MANIFEST.get("runtime_exact_match", [])
)
GCC_TORTURE_SELF_BACKEND_DIFFERENTIAL_CASES = GCC_TORTURE_SELF_BACKEND_EXACT_MATCH_CASES
GCC_TORTURE_SELF_BACKEND_RETURNCODE_CASES = tuple(
    GCC_TORTURE_MANIFEST.get("runtime_returncode_match_only", [])
)


def _case_params(cases):
    return [
        pytest.param(
            relative_path,
            marks=pytest.mark.xdist_group(name=f"gcc_torture:{relative_path}"),
        )
        for relative_path in cases
    ]


def _case_path(relative_path: str) -> Path:
    return GCC_TORTURE_DIR / relative_path


@lru_cache(maxsize=None)
def _run_backend(
    case_path: Path,
    *,
    backend: str,
    timeout: int = DEFAULT_TIMEOUT,
    allow_unimplemented_backend: bool = False,
):
    unit = TranslationUnit(case_path.name, str(case_path), _read_case_source(case_path))
    try:
        result = CEvaluator(
            backend=backend,
            allow_unimplemented_backend=allow_unimplemented_backend,
        ).run_translation_units_with_system_cc(
            [unit],
            base_dir=str(case_path.parent),
            include_dirs=[str(case_path.parent)],
            timeout=timeout,
        )
        return PccCompileResult(result.returncode, result.stdout, result.stderr)
    except Exception as exc:
        return PccCompileResult(1, "", str(exc))


def _run_self_backend(case_path: Path, timeout: int = DEFAULT_TIMEOUT):
    return _run_backend(
        case_path,
        backend="self",
        timeout=timeout,
        allow_unimplemented_backend=True,
    )


def _run_llvm_backend(case_path: Path, timeout: int = DEFAULT_TIMEOUT):
    return run_pcc(case_path, REPO_ROOT, timeout)


@pytest.mark.parametrize(
    "relative_path", _case_params(GCC_TORTURE_SELF_BACKEND_EXACT_MATCH_CASES)
)
def test_gcc_torture_self_backend_runtime_matches_native_exactly(relative_path):
    case_path = _case_path(relative_path)
    assert case_path.is_file(), f"missing gcc torture case: {case_path}"

    native_result = run_native(case_path, REPO_ROOT)
    self_result = _run_self_backend(case_path)

    assert_result_triplet_matches(
        relative_path, "self", self_result, "native", native_result
    )


@pytest.mark.parametrize(
    "relative_path", _case_params(GCC_TORTURE_SELF_BACKEND_DIFFERENTIAL_CASES)
)
def test_gcc_torture_self_backend_matches_llvm_on_exact_match_bucket(relative_path):
    case_path = _case_path(relative_path)
    assert case_path.is_file(), f"missing gcc torture case: {case_path}"

    llvm_result = _run_llvm_backend(case_path)
    self_result = _run_self_backend(case_path)

    assert_result_triplet_matches(
        relative_path, "self", self_result, "llvm", llvm_result
    )


@pytest.mark.parametrize(
    "relative_path", _case_params(GCC_TORTURE_SELF_BACKEND_RETURNCODE_CASES)
)
def test_gcc_torture_self_backend_returncode_matches_native(relative_path):
    case_path = _case_path(relative_path)
    assert case_path.is_file(), f"missing gcc torture case: {case_path}"

    native_result = run_native(case_path, REPO_ROOT)
    self_result = _run_self_backend(case_path)

    assert self_result.returncode == native_result.returncode, (
        f"{relative_path} return code mismatch:\n"
        f"native={native_result.returncode}\n"
        f"self={self_result.returncode}\n"
        f"self stderr:\n{self_result.stderr}"
    )


@pytest.mark.parametrize(
    "relative_path", _case_params(GCC_TORTURE_SELF_BACKEND_RETURNCODE_CASES)
)
def test_gcc_torture_self_backend_returncode_matches_llvm(relative_path):
    case_path = _case_path(relative_path)
    assert case_path.is_file(), f"missing gcc torture case: {case_path}"

    llvm_result = _run_llvm_backend(case_path)
    self_result = _run_self_backend(case_path)

    assert self_result.returncode == llvm_result.returncode, (
        f"{relative_path} return code mismatch:\n"
        f"llvm={llvm_result.returncode}\n"
        f"self={self_result.returncode}\n"
        f"self stderr:\n{self_result.stderr}"
    )
