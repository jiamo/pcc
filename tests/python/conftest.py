"""Shared fixtures for tests/python.

Provides immutable C and pcc-Python runtime archives used by native probes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pcc.tools.runtime_archive_provenance import (
    PRODUCTION_POLICY,
    verify_runtime_archive_manifest,
)
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

    explicit = os.environ.get("PCC_RUNTIME_ARCHIVE")
    if explicit:
        archive = Path(explicit).resolve()
        assert archive.name == "libpy_runtime_pcc_py.a"
        manifest = Path(str(archive) + ".provenance.json")
        records = verify_runtime_archive_manifest(
            archive,
            runtime_root=Path(__file__).resolve().parents[2] / "pcc" / "py_runtime",
            manifest_path=manifest,
        )
        assert records["policy"] == PRODUCTION_POLICY
        assert all(
            member["source_kind"] == "pcc-python"
            and member["producer_kind"] == "pcc-python-library-ir-to-obj"
            and member["uses_host_cc"] is False
            for member in records["members"]
        )
        return archive

    del tmp_path_factory
    return cached_pcc_python_runtime() / "libpy_runtime_pcc_py.a"
