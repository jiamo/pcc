"""Shared fixtures for tests/python.

Provides immutable C and pcc-Python runtime archives used by native probes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.runtime_build_cache import (
    cached_c_runtime,
    cached_pcc_python_runtime,
    cached_threaded_c_runtime,
)


@pytest.fixture(scope="session")
def c_runtime_archive() -> Path:
    """Return a content-addressed immutable default C runtime archive."""

    return cached_c_runtime() / "libpy_runtime.a"


@pytest.fixture(scope="session")
def threaded_c_runtime_archive(tmp_path_factory):
    """Return one content-addressed threaded archive across all workers."""

    del tmp_path_factory
    return cached_threaded_c_runtime() / "libpy_runtime.a"


@pytest.fixture(scope="session")
def pcc_py_runtime_archive(tmp_path_factory):
    """Return the immutable pcc-Python archive required by pcc1 tests.

    Consumers pass this path through ``PCC_RUNTIME_ARCHIVE``.  The fixture
    never rebuilds the repository's shared ``libpy_runtime_pcc_py.a`` under
    xdist.
    """

    del tmp_path_factory
    return cached_pcc_python_runtime() / "libpy_runtime_pcc_py.a"
