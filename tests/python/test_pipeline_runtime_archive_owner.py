"""Focused facade contracts for runtime archive policy extraction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def test_codegen_freshness_checker_uses_the_host_python_boundary(
    tmp_path: Path,
    monkeypatch,
):
    archive = tmp_path / "libpy_runtime_pcc_py.a"
    Path(str(archive) + ".provenance.json").write_text("{}\n", encoding="utf-8")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime_archive.subprocess, "run", fake_run)

    assert not runtime_archive.provenance_codegen_stale(
        str(archive),
        pcc_source_root=lambda: "/source/root",
        host_python_command=lambda: "/host/python",
    )
    assert calls[0][0][0] == "/host/python"
    assert calls[0][0][-2] == "/source/root"
    assert calls[0][0][-1] == str(archive) + ".provenance.json"
    assert calls[0][1]["timeout"] == 90

    monkeypatch.setattr(
        runtime_archive.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    assert runtime_archive.provenance_codegen_stale(
        str(archive),
        pcc_source_root=lambda: "/source/root",
        host_python_command=lambda: "/host/python",
    )
