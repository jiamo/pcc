"""Capability-tagged optional GPU payloads inside the distribution manifest.

Metal payloads are artifacts *inside* a package's manifest, never a new
package-environment dimension, and selecting an unavailable capability
fails closed with a stable diagnostic instead of falling back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcc.package.runtime_profile import (
    CapabilityArtifactError,
    read_capability_artifacts,
    select_capability_artifact,
)
from pcc.package.uv_lock_sync import UvLockSyncError, sync_uv_lock

from tests.python.package_environment_profile_contract import (
    assert_profile_environment_invariance,
    base_environment,
    write_fake_pcc1,
    write_locked_project,
)

FAKE_METALLIB = b"MTLB-fake-payload"


def _ship_metal_payload(project: Path) -> None:
    dep = project / "deps" / "dep-a"
    (dep / "dep_a" / "kernel.metallib").write_bytes(FAKE_METALLIB)
    (dep / "pcc-capabilities.json").write_text(
        json.dumps(
            {"artifacts": [{"capability": "metal", "path": "dep_a/kernel.metallib"}]}
        ),
        encoding="utf-8",
    )


def test_metal_payload_is_a_manifest_artifact_not_an_environment_dimension(
    tmp_path,
):
    project = write_locked_project(tmp_path)
    _ship_metal_payload(project)
    env = base_environment(tmp_path)
    pcc1, _counter = write_fake_pcc1(tmp_path)

    report = sync_uv_lock(
        project / "uv.lock", project_root=project, pcc1=str(pcc1), environ=env
    )

    rows = report["packages"][0]["capability_artifacts"]
    assert [row["capability"] for row in rows] == ["metal"]
    assert rows[0]["path"] == "dep_a/kernel.metallib"
    assert len(rows[0]["sha256"]) == 64
    installed = json.loads(
        (Path(str(report["environment_root"])) / "installed.json").read_text(
            encoding="utf-8"
        )
    )
    assert installed["packages"][0]["capability_artifacts"] == rows
    site_payload = (
        Path(str(report["environment_root"]))
        / "site-packages"
        / "dep_a"
        / "kernel.metallib"
    )
    assert site_payload.read_bytes() == FAKE_METALLIB


def test_gpu_policy_switch_reuses_the_synced_environment(tmp_path):
    assert_profile_environment_invariance(
        tmp_path, {"PCC_GPU_BACKEND": "metal", "PCC_METAL": "1"}
    )


def test_invalid_capability_manifest_fails_sync_with_stable_diagnostic(
    tmp_path,
):
    project = write_locked_project(tmp_path)
    (project / "deps" / "dep-a" / "pcc-capabilities.json").write_text(
        json.dumps(
            {"artifacts": [{"capability": "metal", "path": "dep_a/missing.metallib"}]}
        ),
        encoding="utf-8",
    )
    env = base_environment(tmp_path)
    pcc1, _counter = write_fake_pcc1(tmp_path)

    with pytest.raises(UvLockSyncError) as failure:
        sync_uv_lock(
            project / "uv.lock",
            project_root=project,
            pcc1=str(pcc1),
            environ=env,
        )
    assert failure.value.code == "PCC-PKG-UVLOCK-CAPABILITY-INVALID"


def test_read_capability_artifacts_rejects_payloads_escaping_the_root(tmp_path):
    root = tmp_path / "artifact"
    root.mkdir()
    (tmp_path / "outside.metallib").write_bytes(FAKE_METALLIB)
    (root / "pcc-capabilities.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {"capability": "metal", "path": "../outside.metallib"}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CapabilityArtifactError) as failure:
        read_capability_artifacts(root)
    assert failure.value.code == "PCC-PKG-CAPABILITY-MANIFEST-INVALID"


def test_selecting_available_shipped_capability_returns_that_row():
    rows = [{"capability": "metal", "path": "p.metallib", "sha256": "a" * 64}]
    row = select_capability_artifact("metal", rows, ("cpu", "metal"))
    assert row["capability"] == "metal"


def test_selecting_unavailable_capability_fails_closed_without_fallback():
    rows = [{"capability": "metal", "path": "p.metallib", "sha256": "a" * 64}]
    with pytest.raises(CapabilityArtifactError) as failure:
        select_capability_artifact("metal", rows, ("cpu",))
    assert failure.value.code == "PCC-PKG-CAPABILITY-UNAVAILABLE"
    message = str(failure.value)
    assert "'metal'" in message
    assert "cpu" in message
    assert "refusing to fall back" in message


def test_selecting_unshipped_capability_reports_missing_artifact():
    with pytest.raises(CapabilityArtifactError) as failure:
        select_capability_artifact("metal", [], ("cpu", "metal"))
    assert failure.value.code == "PCC-PKG-CAPABILITY-ARTIFACT-MISSING"


def test_unknown_capability_tag_is_rejected():
    with pytest.raises(CapabilityArtifactError) as failure:
        select_capability_artifact("cuda", [], ("cpu", "metal"))
    assert failure.value.code == "PCC-PKG-CAPABILITY-UNKNOWN"
