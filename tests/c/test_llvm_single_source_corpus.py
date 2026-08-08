from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.c_testsuite_cases import run_native, run_pcc
from tests.llvm_single_source_cases import load_llvm_single_source_corpus


pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).absolute().parents[2]
MANIFEST = REPO_ROOT / "tests" / "llvm_single_source_manifest.json"


def test_pinned_llvm_single_source_subset_matches_native_fail_fast():
    corpus = load_llvm_single_source_corpus(REPO_ROOT, MANIFEST)
    started = time.monotonic()

    for case in corpus.cases:
        with ThreadPoolExecutor(max_workers=2) as executor:
            native_future = executor.submit(
                run_native,
                case.source_path,
                REPO_ROOT,
                corpus.case_timeout_seconds,
            )
            pcc_future = executor.submit(
                run_pcc,
                case.source_path,
                REPO_ROOT,
                corpus.case_timeout_seconds,
            )
            native = native_future.result()
            pcc = pcc_future.result()

        assert native.returncode == 0, (
            f"native compiler rejected pinned case {case.relative_path}:\n"
            f"{native.stderr}"
        )
        assert pcc.returncode == 0, (
            f"pcc rejected pinned case {case.relative_path}:\n{pcc.stderr}"
        )
        assert pcc.stdout == native.stdout, (
            f"stdout mismatch for {case.relative_path}:\n"
            f"native={native.stdout!r}\npcc={pcc.stdout!r}"
        )
        assert pcc.stderr == native.stderr, (
            f"stderr mismatch for {case.relative_path}:\n"
            f"native={native.stderr!r}\npcc={pcc.stderr!r}"
        )
        elapsed = time.monotonic() - started
        assert elapsed <= corpus.wall_time_budget_seconds, (
            f"bounded llvm SingleSource corpus exceeded "
            f"{corpus.wall_time_budget_seconds}s after {case.relative_path}: "
            f"{elapsed:.3f}s"
        )
