"""Focused facade contracts for runtime archive policy extraction."""

from __future__ import annotations

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_runtime_archive as runtime_archive


def test_runtime_archive_path_and_mode_helpers_have_one_owner(monkeypatch):
    archive = "/tmp/libpy_runtime_pcc_py.a"
    assert pipeline._runtime_archive_target_stamp(archive) == runtime_archive.target_stamp(
        archive
    )
    assert (
        pipeline._runtime_archive_provenance_manifest(archive)
        == runtime_archive.provenance_manifest(archive)
    )
    assert pipeline._runtime_archive_capi_inventory(archive) == runtime_archive.capi_inventory(
        archive
    )
    monkeypatch.setenv("PCC_RUNTIME_CC", "host")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    assert pipeline._runtime_cc_mode() == "cc"
    assert pipeline._runtime_high_mode() == "c"


def test_non_production_archive_bundle_policy_is_basename_scoped():
    assert runtime_archive.requires_provenance("/tmp/libpy_runtime_pcc_py.a")
    assert not runtime_archive.requires_provenance("/tmp/foreign.a")
    assert runtime_archive.requires_c_bundle_validation("/tmp/libpy_runtime.a")
    assert not runtime_archive.requires_c_bundle_validation("/tmp/foreign.a")
