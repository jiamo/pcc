"""Metal render-surface hardware gate: pcc_gui draws solid-color rects
offscreen and the pixel buffer is verified.  Hardware-gated (skips when no
Metal device)."""

from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path

import pytest

from pcc.kernel_ir.metal_render_surface import (
    RENDER_BRIDGE_LAST_ERROR,
    RENDER_BRIDGE_SYMBOL,
    write_metal_render_bridge,
)

pytestmark = pytest.mark.gpu_hardware


@pytest.fixture(scope="module")
def render_dylib(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("metal_render")
    write_metal_render_bridge(out)
    dylib = out / "pcc_gui_metal_render.dylib"
    b = subprocess.run(
        ["clang", "-fobjc-arc", "-framework", "Foundation", "-framework", "Metal",
         "-framework", "AppKit", "-framework", "QuartzCore",
         "-dynamiclib", str(out / "pcc_gui_metal_render_bridge.m"), "-o", str(dylib)],
        capture_output=True, text=True, timeout=120,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    return dylib


@pytest.mark.skipif(
    not Path("/System/Library/Frameworks/Metal.framework").exists(),
    reason="Metal framework unavailable",
)
def test_metal_render_surface_rects_pixels(render_dylib: Path) -> None:
    lib = ctypes.CDLL(str(render_dylib))
    render = getattr(lib, RENDER_BRIDGE_SYMBOL)
    render.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int64,
        ctypes.c_int64, ctypes.c_int64,
        ctypes.c_void_p, ctypes.c_uint64,
    ]
    render.restype = ctypes.c_int64
    err_copy = getattr(lib, RENDER_BRIDGE_LAST_ERROR)
    err_copy.argtypes = [ctypes.c_char_p, ctypes.c_uint64]
    err_copy.restype = ctypes.c_int64

    W, H, N = 300, 300, 2
    Rect = ctypes.c_int64 * 4
    rects = (Rect * N)(
        Rect(0, 0, 100, 100),      # red 100x100 at origin
        Rect(200, 200, 50, 50),    # blue 50x50 at (200,200)
    )
    Color = ctypes.c_uint8 * 4
    colors = (Color * N)(Color(255, 0, 0, 255), Color(0, 0, 255, 255))
    pixels = (ctypes.c_uint8 * (W * H * 4))()
    rc = render(rects, colors, N, W, H, pixels, len(pixels))
    if rc != 0:
        buf = ctypes.create_string_buffer(512)
        err_copy(buf, 512)
        pytest.skip(f"no Metal device or render failed: {rc} {buf.value.decode()!r}")
    def px(x: int, y: int) -> tuple[int, int, int]:
        o = (y * W + x) * 4
        return pixels[o], pixels[o + 1], pixels[o + 2]
    assert px(50, 50) == (255, 0, 0), "red rect center"
    assert px(225, 225) == (0, 0, 255), "blue rect center"
    assert px(150, 150) == (0, 0, 0), "clear background"
    assert px(0, 0) == (255, 0, 0), "red rect corner"


def test_metal_render_bridge_source_shape() -> None:
    from pcc.kernel_ir.metal_render_surface import metal_render_bridge_source

    src = metal_render_bridge_source()
    assert "pcc_rect_vs" in src
    assert "pcc_rect_fs" in src
    assert "MTLRenderCommandEncoder" in src
    assert "CAMetalLayer" in src  # windowed present path
    assert "pcc_gui_metal_window_poll_click" in src
    assert "PccGuiLifecycleDelegate" in src
    assert "pcc_gui_metal_lifecycle_install" in src
    assert "pcc_gui_metal_lifecycle_probe" in src
    assert "applicationShouldTerminate" in src
    assert "applicationShouldHandleReopen" in src
    assert "windowDidResize" in src
    assert "getBytes" in src
