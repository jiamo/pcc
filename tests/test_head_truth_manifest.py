from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import scripts.head_truth_manifest as head_truth
import scripts.head_truth_gate as head_truth_gate

from scripts.head_truth_manifest import (
    FAIL,
    PASS,
    TIMEOUT,
    GateSpec,
    GateResult,
    _pytest_summary,
    build_manifest,
    selected_gate_failures,
    serialize_manifest,
    validate_manifest,
)


def _result(
    gate_id: str,
    *,
    kind: str = "pytest",
    status: str = PASS,
) -> GateResult:
    bootstrap = gate_id in {"llvm-bootstrap", "self-five-gc-bootstrap"}
    native_artifact = bootstrap or gate_id == "numpy-core-head"
    return GateResult(
        gate_id=gate_id,
        command=["uv", "run", "pytest"],
        timeout_seconds=10,
        kind=kind,
        status=status,
        returncode=0 if status == PASS else 1,
        duration_seconds=1.5,
        log_path=f"build/{gate_id}.log",
        output_sha256="a" * 64,
        pytest_summary="1 passed in 0.10s" if kind.startswith("pytest") else None,
        backend="self" if native_artifact else None,
        gc_backend="0..4" if gate_id == "self-five-gc-bootstrap" else None,
        links_libpython=False if native_artifact else None,
        pcc2_pcc3_equal=True if bootstrap else None,
        artifact_paths=(
            ["build/pcc1", "build/pcc2", "build/pcc3"]
            if bootstrap
            else (
                ["build/head-truth/numpy-core/result.json"]
                if gate_id == "numpy-core-head"
                else []
            )
        ),
        observations=(
            [
                {
                    "first_blocker": {
                        "kind": "first_missing_module",
                        "phase": "Py_mod_exec",
                        "value": "math",
                    },
                    "first_blocker_ratchet": {
                        "accepted": True,
                        "status": "STABLE",
                    },
                }
            ]
            if gate_id == "numpy-core-head"
            else []
        ),
    )


def _manifest() -> dict[str, object]:
    return build_manifest(
        source={
            "commit": "1" * 40,
            "worktree_dirty": False,
            "worktree_fingerprint": "2" * 64,
        },
        platform_info={
            "system": "Darwin",
            "release": "test",
            "machine": "arm64",
            "python": "3.13.0",
        },
        results=[
            _result("runtime-archive-preflight", kind="command"),
            _result("fallback-ratchet"),
            _result("gc-production-contract"),
            _result("llvm-bootstrap", kind="bootstrap"),
            _result("self-five-gc-bootstrap", kind="pytest-bootstrap"),
            _result("numpy-core-head", kind="command"),
        ],
        generated_at="2026-07-10T00:00:00Z",
    )


def _gate(manifest: dict[str, object], gate_id: str) -> dict[str, object]:
    return next(gate for gate in manifest["gates"] if gate["gate_id"] == gate_id)


def test_complete_manifest_is_commit_bound_and_deterministic() -> None:
    manifest = _manifest()

    assert validate_manifest(manifest, require_complete=True) == []
    assert manifest["complete"] is True
    assert manifest["claimable_commit"] is True
    assert serialize_manifest(manifest) == serialize_manifest(deepcopy(manifest))
    assert json.loads(serialize_manifest(manifest)) == manifest


def test_incomplete_or_dirty_manifest_is_not_claimable() -> None:
    manifest = _manifest()
    manifest["source"]["worktree_dirty"] = True
    manifest["claimable_commit"] = False
    fallback = _gate(manifest, "fallback-ratchet")
    fallback["status"] = FAIL
    fallback["returncode"] = 1
    manifest["complete"] = False

    errors = validate_manifest(manifest, require_complete=True)

    assert "required gate fallback-ratchet is not PASS" in errors
    assert "complete manifest required" in errors


def test_pass_rejects_missing_summary_timeout_and_bad_bootstrap_claims() -> None:
    manifest = _manifest()
    _gate(manifest, "fallback-ratchet")["pytest_summary"] = None
    _gate(manifest, "gc-production-contract")["returncode"] = 124
    _gate(manifest, "llvm-bootstrap")["links_libpython"] = True
    _gate(manifest, "self-five-gc-bootstrap")["pcc2_pcc3_equal"] = False

    errors = validate_manifest(manifest)

    assert "fallback-ratchet: pytest PASS requires a final summary" in errors
    assert "gc-production-contract: PASS requires returncode 0" in errors
    assert "llvm-bootstrap: PASS requires links_libpython=false" in errors
    assert "self-five-gc-bootstrap: PASS requires pcc2_pcc3_equal=true" in errors


def test_numpy_pass_requires_classified_or_empty_accepted_blocker_record() -> None:
    manifest = _manifest()
    numpy_gate = _gate(manifest, "numpy-core-head")
    numpy_gate["observations"][0]["first_blocker"] = {
        "kind": "provider_slot_count",
        "phase": "Py_mod_exec",
        "value": "200",
    }
    numpy_gate["observations"][0]["first_blocker_ratchet"] = {
        "accepted": False,
        "status": "UNREVIEWED_CHANGE",
    }

    errors = validate_manifest(manifest)

    assert any("explicit empty completion record" in error for error in errors)
    assert "numpy-core-head: PASS requires an accepted first-blocker ratchet" in errors


def test_numpy_pass_accepts_explicit_empty_completion_record() -> None:
    manifest = _manifest()
    observation = _gate(manifest, "numpy-core-head")["observations"][0]
    observation["first_blocker"] = None
    observation["first_blocker_ratchet"] = {
        "accepted": True,
        "status": "STABLE",
    }

    assert validate_manifest(manifest) == []


def test_truth_manifest_tooling_stays_out_of_pcc_compiler_closure() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]

    assert not (root / "pcc" / "head_truth_manifest.py").exists()
    assert (root / "scripts" / "head_truth_manifest.py").is_file()


def test_pytest_summary_accepts_elapsed_clock_suffix() -> None:
    summary, counts = _pytest_summary("20 passed in 166.16s (0:02:46)\n")

    assert summary == "20 passed in 166.16s"
    assert counts == {"passed": 20}


def test_selected_gate_failures_reject_keep_going_failures() -> None:
    results = [
        _result("fallback-ratchet"),
        _result("control-plane-ratchets", status=FAIL),
        _result("gc-production-contract"),
    ]

    assert selected_gate_failures(
        results,
        {"fallback-ratchet", "control-plane-ratchets"},
    ) == ["selected gate control-plane-ratchets is FAIL"]


def test_truth_runner_rejects_unknown_single_gate_without_running(capsys) -> None:
    assert head_truth_gate.main(["run", "--gate", "does-not-exist"]) == 2
    assert "unknown gate(s): does-not-exist" in capsys.readouterr().err


def test_numpy_gate_keeps_fixed_point_field_not_applicable(
    tmp_path: Path, monkeypatch
) -> None:
    spec = GateSpec(
        gate_id="numpy-core-head",
        suite="heavy",
        command=("uv", "run", "python", "scripts/numpy_head_gate.py"),
        timeout_seconds=1200,
        kind="command",
        backend="self",
    )
    process = subprocess.CompletedProcess(
        args=list(spec.command), returncode=0, stdout="PASS\n", stderr=""
    )
    monkeypatch.setattr(
        head_truth,
        "inspect_numpy_head_artifact",
        lambda *_args, **_kwargs: [
            {
                "artifact_paths": ["build/head-truth/numpy-core/result.json"],
                "backend": "self",
                "gc_backend": None,
                "links_libpython": False,
                "pcc2_pcc3_equal": None,
                "failure": None,
            }
        ],
    )

    result = head_truth.run_gate(
        tmp_path,
        tmp_path / "logs",
        spec,
        lambda *_args, **_kwargs: process,
    )

    assert result.status == PASS
    assert result.links_libpython is False
    assert result.pcc2_pcc3_equal is None


def test_timeout_status_survives_bootstrap_artifact_inspection(
    tmp_path: Path, monkeypatch
) -> None:
    spec = GateSpec(
        gate_id="self-five-gc-bootstrap",
        suite="heavy",
        command=("uv", "run", "pytest"),
        timeout_seconds=900,
        kind="pytest-bootstrap",
        backend="self",
        gc_backend="0..4",
    )
    process = subprocess.CompletedProcess(
        args=list(spec.command),
        returncode=124,
        stdout="[TIMEOUT] killed process group after 900.0s\n",
        stderr="",
    )
    monkeypatch.setattr(
        head_truth,
        "inspect_bootstrap_artifacts",
        lambda *_args, **_kwargs: [
            {
                "artifact_paths": ["build/pcc1", "build/pcc2", "build/pcc3"],
                "backend": "self",
                "gc_backend": "4",
                "links_libpython": None,
                "pcc2_pcc3_equal": None,
                "failure": "missing artifacts: build/pcc3",
            }
        ],
    )

    result = head_truth.run_gate(
        tmp_path,
        tmp_path / "logs",
        spec,
        lambda *_args, **_kwargs: process,
    )

    assert result.status == TIMEOUT
    assert result.failure == "command timed out after 900s"
    assert result.observations[0]["failure"] == "missing artifacts: build/pcc3"
