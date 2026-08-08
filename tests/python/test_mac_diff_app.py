"""mac_diff_app smoke truth test: compile the pcc-GUI diff app, run it, and
assert on the STRUCTURED output (not just the exit code — self backend used
to ignore main() return values, see module_lifecycle_lowering fix).

The app opens two fixed sample files, prints

    PCC_MAC_DIFF_SMOKE left_rows=N right_rows=M ops=K equal=.. deleted=.. inserted=..

then renders one declarative frame and exits through the app lifecycle.  This validates the
whole truth chain: file read -> line hash -> LCS diff -> statistics.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "projects" / "mac_diff_app" / "app.py"
LEFT = REPO / "projects" / "mac_diff_app" / "samples" / "left.txt"
RIGHT = REPO / "projects" / "mac_diff_app" / "samples" / "right.txt"


@pytest.mark.skipif(sys.platform != "darwin", reason="requires AppKit/Metal window")
@pytest.mark.integration
def test_mac_diff_app_smoke_truth(tmp_path: Path) -> None:
    assert APP.is_file() and LEFT.is_file() and RIGHT.is_file()
    exe = tmp_path / "mac_diff_app"
    env = dict(os.environ)
    env["PCC_RUNTIME_ARCHIVE"] = str(
        REPO / "pcc" / "py_runtime" / "libpy_runtime_pcc_py.a")
    b = subprocess.run(
        [str(REPO / ".venv" / "bin" / "pcc"), "--backend", "self",
         "--python-libpython", "off", "--ir-scaffold", "on",
         str(APP), "-o", str(exe)],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr

    # dylib next to the exe so the app can dlopen it
    import shutil
    dylib = REPO / "projects" / "mac_diff_app" / "libpcc_gui_metal.dylib"
    if dylib.is_file():
        shutil.copy(dylib, tmp_path / "libpcc_gui_metal.dylib")

    r = subprocess.run(
        [str(exe), str(LEFT), str(RIGHT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "PCC_MAC_DIFF_SMOKE" in out, out
    line = next(
        (ln for ln in out.splitlines() if "PCC_MAC_DIFF_SMOKE" in ln), ""
    )
    assert "left_rows= 5" in line, line
    assert "right_rows= 6" in line, line
    assert "ops= 6" in line, line
    assert "deleted= 0" in line, line
    assert "inserted= 1" in line, line
    assert "equal= 4" in line, line
    assert "changed= 1" in line, line
    assert "PCC_GUI_BRIDGE_ACK render_present= 1" in out
    assert "PCC_MAC_DIFF_DECLARATIVE_OK components=5" in out


@pytest.mark.skipif(sys.platform != "darwin", reason="requires AppKit/Metal window")
@pytest.mark.integration
def test_mac_diff_app_all_diff_cases(tmp_path: Path) -> None:
    """argv open with the all-cases fixture: equal/modify/delete/insert,
    consecutive deletes, tail diffs, empty lines, UTF-8, digits."""
    exe = tmp_path / "mac_diff_app"
    env = dict(os.environ)
    env["PCC_RUNTIME_ARCHIVE"] = str(
        REPO / "pcc" / "py_runtime" / "libpy_runtime_pcc_py.a")
    b = subprocess.run(
        [str(REPO / ".venv" / "bin" / "pcc"), "--backend", "self",
         "--python-libpython", "off", "--ir-scaffold", "on",
         str(APP), "-o", str(exe)],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    import shutil
    dylib = REPO / "projects" / "mac_diff_app" / "libpcc_gui_metal.dylib"
    if dylib.is_file():
        shutil.copy(dylib, tmp_path / "libpcc_gui_metal.dylib")
    a = REPO / "projects" / "mac_diff_app" / "samples" / "case_a.txt"
    bb = REPO / "projects" / "mac_diff_app" / "samples" / "case_b.txt"
    r = subprocess.run(
        [str(exe), str(a), str(bb)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    line = next((ln for ln in out.splitlines() if "PCC_MAC_DIFF_SMOKE" in ln), "")
    assert "left_rows= 13" in line, line
    assert "right_rows= 12" in line, line
    assert "ops= 13" in line, line
    assert "equal= 7" in line, line
    assert "deleted= 1" in line, line
    assert "inserted= 0" in line, line
    assert "changed= 5" in line, line
    assert "PCC_GUI_BRIDGE_ACK render_present= 1" in out
    assert "PCC_MAC_DIFF_DECLARATIVE_OK components=5" in out
