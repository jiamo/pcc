"""Darwin-only real-window acknowledgement for the canonical kit command list."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.kernel_ir.metal_render_surface import write_metal_render_bridge


REPO = Path(__file__).resolve().parents[2]


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="requires AppKit and Metal")
def test_module_command_list_reaches_real_bridge(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    bridge_source = write_metal_render_bridge(tmp_path)
    bridge = tmp_path / "libpcc_gui_metal.dylib"
    built_bridge = subprocess.run(
        [
            "clang",
            "-fobjc-arc",
            "-framework",
            "Foundation",
            "-framework",
            "Metal",
            "-framework",
            "AppKit",
            "-framework",
            "QuartzCore",
            "-dynamiclib",
            str(bridge_source),
            "-o",
            str(bridge),
        ],
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert built_bridge.returncode == 0, built_bridge.stdout + built_bridge.stderr

    for module in ("pcc_gui_high.py", "pcc_gui_kit.py"):
        shutil.copy(REPO / "projects" / "mac_diff_app" / module, tmp_path / module)
    source = tmp_path / "kit_real_bridge.py"
    source.write_text(
        f'''from pcc.unsafe import calloc, cstr, load_i64, stack_alloc, store_i64
import pcc_gui_high as gui
import pcc_gui_kit as kit

def main() -> int:
    if gui.init(cstr("pcc kit bridge"), 64, 64, cstr({str(bridge)!r})) != 0:
        return 11
    if kit.pcc_kit_init(4) != 0:
        return 12
    root = kit.pcc_kit_create(-1)
    child = kit.pcc_kit_create(root)
    kit.pcc_kit_rect(root, 0, 0, 64, 64, 0xFF101820)
    kit.pcc_kit_rect(child, 8, 8, 32, 24, 0xFF40A0E0)
    rects = calloc(128, 1)
    colors = calloc(16, 1)
    texts = calloc(96, 1)
    rn = stack_alloc(8)
    tn = stack_alloc(8)
    attempt = 0
    ack = -1
    while attempt < 60 and ack != 0:
        store_i64(rn, 0, 0)
        store_i64(tn, 0, 0)
        kit.pcc_kit_render(root, rects, colors, rn, texts, tn)
        gui.render_scene(rects, colors, load_i64(rn, 0), texts, load_i64(tn, 0), 64, 64)
        ack = gui.render_ack()
        if ack != 0:
            gui.running()
            gui.sleep(16)
        attempt = attempt + 1
    print("PCC_GUI_REAL_BRIDGE_ACK", ack, load_i64(rn, 0))
    gui.close()
    return ack

main()
''',
        encoding="utf-8",
    )
    exe = tmp_path / "kit_real_bridge"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(
        [str(exe)], env=env, text=True, capture_output=True, timeout=30
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "PCC_GUI_REAL_BRIDGE_ACK 0 2" in ran.stdout, ran.stdout
