"""End-to-end acceptance for the canonical declarative mac diff source."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "projects" / "mac_diff_app"
ENTRY = PROJECT / "declarative_headless.py"
MODEL = PROJECT / "declarative_app.py"
APP = PROJECT / "app.py"
CASE_A = PROJECT / "samples" / "case_a.txt"
CASE_B = PROJECT / "samples" / "case_b.txt"


def _assert_trace(output: str, gc_backend: str) -> None:
    smoke = next(
        line for line in output.splitlines() if "PCC_MAC_DIFF_SMOKE" in line
    )
    assert "left_rows= 13" in smoke
    assert "right_rows= 12" in smoke
    assert "ops= 13" in smoke
    assert "equal= 7" in smoke
    assert "deleted= 1" in smoke
    assert "inserted= 0" in smoke
    assert "changed= 5" in smoke
    assert "PCC_MAC_DIFF_DECLARATIVE_OK components=5" in output
    if gc_backend:
        assert gc_backend in {"0", "1", "2", "3", "4"}


def test_canonical_entry_has_no_manual_frame_node_mutation() -> None:
    entry = APP.read_text(encoding="utf-8")
    model = MODEL.read_text(encoding="utf-8")
    build = (PROJECT / "build.sh").read_text(encoding="utf-8")
    assert "from declarative_app import run_app" in entry
    assert "projects/mac_diff_app/app.py" in build
    assert "kit_window.py" not in build
    assert "while gui.running()" not in entry
    assert "while gui.running()" not in model
    assert "pcc_gui_component_render_commit" not in entry
    for boundary in (
        "pcc_gui_components_init",
        "pcc_gui_scheduler_run_budgeted",
        "pcc_gui_events_dispatch",
        "pcc_gui_style_apply_class",
        "pcc_gui_managed_state_set",
        "pcc_gui_managed_binding_add",
        "pcc_gui_commands_invoke",
        "pcc_gui_app_lifecycle_post",
    ):
        assert boundary in model
    assert "LINES_L" in model and "LINES_R" in model
    assert "_repair_ops" in model
    for canary_edge in (
        'enqueue_reduce(toolbar, 3',
        "restart_count(toolbar)",
        'cstr("bg-accent/50 -x-3/[dense]")',
        "managed_state_get(status, 1, managed)",
        "_invoke_command(1, toolbar",
        "_invoke_command(2, toolbar",
        'app_post(7, 1, null(), 0, 1, 0)',
        'function_addr("mac_diff_root_release")',
    ):
        assert canary_edge in model


@pytest.mark.integration
def test_declarative_canary_host_pcc(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    exe = tmp_path / "mac_diff_declarative_host"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(REPO / ".venv" / "bin" / "pcc"),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(ENTRY),
            "-o",
            str(exe),
        ],
        cwd=PROJECT,
        env=env,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(
        [str(exe), str(CASE_A), str(CASE_B)],
        cwd=PROJECT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    _assert_trace(ran.stdout, "")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe="pcc1")
@pytest.mark.parametrize("gc_backend", ["0", "1", "2", "3", "4"])
def test_declarative_canary_current_pcc1_gc0_to_gc4(
    tmp_path: Path,
    pcc_py_runtime_archive: Path,
    gc_backend: str,
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the declarative GUI canary")
    exe = tmp_path / f"mac_diff_declarative_pcc1_gc{gc_backend}"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    env["PCC_GC_BACKEND"] = gc_backend
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(ENTRY),
            "-o",
            str(exe),
        ],
        cwd=PROJECT,
        env=env,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(
        [str(exe), str(CASE_A), str(CASE_B)],
        cwd=PROJECT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    _assert_trace(ran.stdout, gc_backend)
