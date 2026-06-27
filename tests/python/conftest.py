"""Shared fixtures for tests/python.

Provides immutable C and pcc-Python runtime archives used by native probes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.python.process_timeout import run_process_group_timeout
from tests.runtime_build_cache import cached_c_runtime, cached_pcc_python_runtime

_REPO = Path(__file__).absolute().parents[2]
@pytest.fixture(scope="session")
def c_runtime_archive() -> Path:
    """Return a content-addressed immutable default C runtime archive."""

    return cached_c_runtime() / "libpy_runtime.a"


@pytest.fixture(scope="session")
def threaded_c_runtime_archive(tmp_path_factory):
    """Build one isolated threaded C runtime archive per pytest worker."""

    source = _REPO / "pcc" / "py_runtime"
    work = tmp_path_factory.mktemp("threaded_c_runtime") / "py_runtime"
    shutil.copytree(
        source,
        work,
        ignore=shutil.ignore_patterns(
            "_native", "__pycache__", "build", "build_*", "*.a", "*.a.target"
        ),
    )
    result = subprocess.run(
        [
            "make",
            "-B",
            "-C",
            str(work),
            "PCC_WITH_THREADS=1",
            "libpy_runtime.a",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return work / "libpy_runtime.a"


@pytest.fixture(scope="session")
def pcc_py_runtime_archive(tmp_path_factory):
    """Return the immutable pcc-Python archive required by pcc1 tests.

    Consumers pass this path through ``PCC_RUNTIME_ARCHIVE``.  The fixture
    never rebuilds the repository's shared ``libpy_runtime_pcc_py.a`` under
    xdist.
    """

    del tmp_path_factory
    return cached_pcc_python_runtime() / "libpy_runtime_pcc_py.a"
