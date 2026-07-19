from __future__ import annotations

import re
import subprocess
from pathlib import Path

from scripts.head_truth_manifest import REQUIRED_GATE_IDS, gate_specs

ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def _is_git_ignored(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "--", path.as_posix()],
        cwd=ROOT,
        check=False,
        timeout=5,
    )
    assert result.returncode in {0, 1}
    return result.returncode == 0


def _is_git_tracked(path: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", path.as_posix()],
        cwd=ROOT,
        check=False,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode in {0, 1}
    return result.returncode == 0


def _job_timeout_minutes(workflow: str) -> int:
    match = re.search(r"(?m)^    timeout-minutes: (\d+)$", workflow)
    assert match is not None
    return int(match.group(1))


def _step_timeout_minutes(workflow: str, step_name: str) -> int:
    step = _step(workflow, step_name)
    match = re.search(r"(?m)^        timeout-minutes: (\d+)$", step)
    assert match is not None
    return int(match.group(1))


def _step(workflow: str, step_name: str) -> str:
    marker = f"      - name: {step_name}\n"
    assert marker in workflow
    return workflow.split(marker, 1)[1].split("\n      - ", 1)[0]


def _all_step_timeout_minutes(workflow: str) -> list[int]:
    return [
        int(value)
        for value in re.findall(r"(?m)^        timeout-minutes: (\d+)$", workflow)
    ]


def test_light_workflow_runs_every_change_through_registry() -> None:
    workflow = _workflow("head-truth-light.yml")

    assert "  push:" in workflow
    assert "  pull_request:" in workflow
    assert "runs-on: macos-15" in workflow
    assert "--suite light" in workflow
    assert "scripts/head_truth_gate.py" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "timeout-minutes:" in workflow


def test_heavy_workflow_is_reusable_manual_nightly_and_clean_commit_bound() -> None:
    workflow = _workflow("head-truth-heavy.yml")

    assert "  workflow_call:" in workflow
    assert "  workflow_dispatch:" in workflow
    assert "  schedule:" in workflow
    assert "runs-on: macos-15" in workflow
    assert "--suite all" in workflow
    assert "--require-complete" in workflow
    assert "--require-clean-commit" in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_locked_truth_workflows_ship_the_root_lockfile() -> None:
    light = _workflow("head-truth-light.yml")
    heavy = _workflow("head-truth-heavy.yml")

    assert "uv sync --dev --locked" in light
    assert "uv sync --dev --locked" in heavy
    lockfile = ROOT / "uv.lock"
    assert lockfile.is_file()
    assert not _is_git_ignored(lockfile.relative_to(ROOT))
    assert _is_git_tracked(lockfile.relative_to(ROOT))
    assert _is_git_ignored(Path("projects") / "example" / "uv.lock")


def test_dependency_sync_skips_the_release_build_hook() -> None:
    for name in ("head-truth-light.yml", "head-truth-heavy.yml"):
        workflow = _workflow(name)
        install_step = _step(workflow, "Install dependencies")
        assert 'PCC_BUILD_SKIP: "1"' in install_step

        truth_step_name = (
            "Run light truth gates"
            if name == "head-truth-light.yml"
            else "Run complete truth matrix"
        )
        assert "PCC_BUILD_SKIP" not in _step(workflow, truth_step_name)


def test_keep_going_workflow_timeouts_cover_every_selected_gate() -> None:
    specs = gate_specs(ROOT)
    cases = (
        (
            _workflow("head-truth-light.yml"),
            "Run light truth gates",
            {"light"},
        ),
        (
            _workflow("head-truth-heavy.yml"),
            "Run complete truth matrix",
            {"light", "heavy"},
        ),
    )

    for workflow, step_name, suites in cases:
        registered_seconds = sum(
            spec.timeout_seconds for spec in specs if spec.suite in suites
        )
        truth_step_seconds = _step_timeout_minutes(workflow, step_name) * 60
        assert truth_step_seconds >= registered_seconds + 60

        job_minutes = _job_timeout_minutes(workflow)
        explicit_step_minutes = sum(_all_step_timeout_minutes(workflow))
        assert job_minutes >= explicit_step_minutes + 5


def test_release_publish_requires_reusable_heavy_truth_workflow() -> None:
    workflow = _workflow("workflow.yml")

    assert "uses: ./.github/workflows/head-truth-heavy.yml" in workflow
    assert "needs: head-truth" in workflow


def test_light_and_heavy_commands_are_owned_by_manifest_registry() -> None:
    specs = gate_specs(ROOT)
    suites = {spec.suite for spec in specs}
    gate_ids = {spec.gate_id for spec in specs}

    assert suites == {"light", "heavy"}
    assert "runtime-archive-preflight" in gate_ids
    assert "fallback-ratchet" in gate_ids
    assert "control-plane-ratchets" in gate_ids
    assert "gc-production-contract" in gate_ids
    assert "llvm-bootstrap" in gate_ids
    assert "self-five-gc-bootstrap" in gate_ids
    assert "numpy-core-head" in gate_ids
    assert "numpy-core-head" in REQUIRED_GATE_IDS

    control = next(spec for spec in specs if spec.gate_id == "control-plane-ratchets")
    assert "tests/python/test_bootstrap_gate_baseline.py" not in control.command
    assert any("TestObligation1ModeLabeling" in arg for arg in control.command)
    assert any("TestObligation5FixedPointContract" in arg for arg in control.command)


def test_heavy_registry_prebuilds_runtime_archive_before_consumers() -> None:
    specs = gate_specs(ROOT)
    gate_ids = [spec.gate_id for spec in specs]

    assert "runtime-archive-preflight" in gate_ids
    assert "runtime-archive-preflight" in REQUIRED_GATE_IDS
    assert gate_ids.index("runtime-archive-preflight") < gate_ids.index(
        "fallback-ratchet"
    )

    preflight = next(
        spec for spec in specs if spec.gate_id == "runtime-archive-preflight"
    )
    assert preflight.suite == "heavy"
    assert preflight.kind == "command"
    assert preflight.command[:4] == ("make", "-B", "-C", "pcc/py_runtime")
    assert "libpy_runtime_pcc_py.a" in preflight.command
    assert "PCC=../../.venv/bin/pcc" in preflight.command
    assert "PYTHON=../../.venv/bin/python3" in preflight.command
    assert str(ROOT) not in " ".join(preflight.command)

    self_five_gc = next(
        spec for spec in specs if spec.gate_id == "self-five-gc-bootstrap"
    )
    assert self_five_gc.timeout_seconds == 1800

    numpy_core = next(spec for spec in specs if spec.gate_id == "numpy-core-head")
    assert numpy_core.suite == "heavy"
    assert numpy_core.timeout_seconds == 1200
    assert "scripts/numpy_head_gate.py" in numpy_core.command


def test_fallback_timeout_covers_observed_hosted_boundary() -> None:
    fallback = next(
        spec for spec in gate_specs(ROOT) if spec.gate_id == "fallback-ratchet"
    )

    assert fallback.timeout_seconds == 420
