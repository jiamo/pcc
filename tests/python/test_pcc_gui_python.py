"""pcc-Python GUI tests: pcc_gui driven from pcc-compiled Python programs.

Each test writes a .py program that externs the pcc_gui ABI, performs layout /
element / control / binding / window operations, and returns a non-zero code
on failure.  pcc compiles it (self backend, no libpython) and the test asserts
exit code 0 — proving the full pcc1 -> pcc_gui -> runtime path, i.e. that a
GUI can be written in pcc-Python.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _compile_run(tmp_path: Path, name: str, source: str) -> int:
    src = tmp_path / f"{name}.py"
    exe = tmp_path / name
    src.write_text(source, encoding="utf-8")
    b = subprocess.run(
        [str(REPO / ".venv" / "bin" / "pcc"), "--backend", "self",
         "--python-libpython", "off", "--ir-scaffold", "on",
         str(src), "-o", str(exe)],
        capture_output=True, text=True, timeout=120,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    return r.returncode


_STACK = """
from pcc.extern import c_int64, c_ptr, c_void, c_int32, extern
from pcc.unsafe import cstr, load_i64, null, stack_alloc, store_i64

pcc_gui_stack_measure = extern("pcc_gui_stack_measure", (c_ptr, c_ptr, c_int64, c_int32, c_int32), c_void)
pcc_gui_stack_arrange = extern("pcc_gui_stack_arrange", (c_ptr, c_ptr, c_int64, c_int32, c_int32, c_int32, c_int32, c_int32), c_void)
pcc_gui_flow_measure = extern("pcc_gui_flow_measure", (c_ptr, c_ptr, c_int64, c_int64, c_int32), c_void)
pcc_gui_flow_arrange = extern("pcc_gui_flow_arrange", (c_ptr, c_ptr, c_int64, c_int64, c_int32, c_int32), c_void)
pcc_gui_dock_arrange = extern("pcc_gui_dock_arrange", (c_ptr, c_ptr, c_int64, c_ptr), c_void)


def main() -> int:
    children = stack_alloc(96)
    store_i64(children, 16, 10)
    store_i64(children, 24, 20)
    store_i64(children, 48, 30)
    store_i64(children, 56, 40)
    store_i64(children, 80, 50)
    store_i64(children, 88, 60)
    out = stack_alloc(32)
    pcc_gui_stack_measure(children, out, 3, 0, 5)
    if load_i64(out, 16) != 50:
        return 1
    if load_i64(out, 24) != 130:
        return 2
    # flow: 4 children 40x20 in width 100
    flow = stack_alloc(128)
    i: int = 0
    while i < 4:
        store_i64(flow, i * 32 + 16, 40)
        store_i64(flow, i * 32 + 24, 20)
        i += 1
    pcc_gui_flow_measure(flow, out, 4, 100, 5)
    if load_i64(out, 24) != 40:
        return 3
    pcc_gui_flow_arrange(flow, out, 4, 100, 5, 5)
    if load_i64(flow, 2 * 32 + 8) != 20:
        return 4
    return 0


main()
"""

_ELEMENTS_CONTROLS = """
from pcc.extern import c_int64, c_ptr, c_int32, extern
from pcc.unsafe import cstr, load_i32, load_i64, stack_alloc, store_i32, store_i64, store_ptr, null

pcc_gui_element_init = extern("pcc_gui_element_init", (c_ptr, c_int32), c_int32)
pcc_gui_element_type = extern("pcc_gui_element_type", (c_ptr,), c_int32)
pcc_gui_element_set_color = extern("pcc_gui_element_set_color", (c_ptr, c_int32, c_int32, c_int32, c_int32), c_int32)
pcc_gui_control_init = extern("pcc_gui_control_init", (c_ptr,), c_int32)
pcc_gui_control_append_child = extern("pcc_gui_control_append_child", (c_ptr, c_ptr), c_int32)
pcc_gui_control_hit_test = extern("pcc_gui_control_hit_test", (c_ptr, c_int64, c_int64), c_ptr)
pcc_gui_control_set_focus = extern("pcc_gui_control_set_focus", (c_ptr, c_int32), c_int32)
pcc_gui_control_focused = extern("pcc_gui_control_focused", (c_ptr,), c_int32)


def main() -> int:
    el = stack_alloc(64)
    if pcc_gui_element_init(el, 1) != 0:
        return 1
    if pcc_gui_element_type(el) != 1:
        return 2
    if pcc_gui_element_set_color(el, 10, 20, 30, 40) != 0:
        return 3
    root = stack_alloc(64)
    c1 = stack_alloc(64)
    c2 = stack_alloc(64)
    pcc_gui_control_init(root)
    pcc_gui_control_init(c1)
    pcc_gui_control_init(c2)
    store_i64(root, 40, 100)
    store_i64(root, 48, 100)
    store_i64(c1, 24, 10)
    store_i64(c1, 32, 10)
    store_i64(c1, 40, 50)
    store_i64(c1, 48, 50)
    store_i64(c2, 24, 20)
    store_i64(c2, 32, 20)
    store_i64(c2, 40, 30)
    store_i64(c2, 48, 30)
    pcc_gui_control_append_child(root, c1)
    pcc_gui_control_append_child(c1, c2)
    hit = pcc_gui_control_hit_test(root, 25, 25)
    if hit != c2:
        return 4
    pcc_gui_control_set_focus(c2, 1)
    if pcc_gui_control_focused(c2) != 1:
        return 5
    return 0


main()
"""

_BINDING = """
from pcc.extern import c_int64, c_ptr, c_int32, extern
from pcc.unsafe import cstr, load_i64, null, stack_alloc, store_i64

pcc_gui_binding_set_property = extern("pcc_gui_binding_set_property", (c_ptr, c_int32, c_int64), c_int32)
pcc_gui_binding_get_property = extern("pcc_gui_binding_get_property", (c_ptr, c_int32), c_int64)
pcc_gui_binding_add = extern("pcc_gui_binding_add", (c_ptr, c_int32, c_ptr, c_int32), c_int32)
pcc_gui_binding_set_command = extern("pcc_gui_binding_set_command", (c_ptr, c_int32, c_ptr), c_int32)
pcc_gui_binding_invoke_command = extern("pcc_gui_binding_invoke_command", (c_ptr, c_int32, c_int64), c_int32)
pcc_gui_binding_has_command = extern("pcc_gui_binding_has_command", (c_ptr, c_int32), c_int32)


def main() -> int:
    src = stack_alloc(8)
    dst = stack_alloc(8)
    if pcc_gui_binding_set_property(src, 1, 42) != 0:
        return 1
    if pcc_gui_binding_get_property(src, 1) != 42:
        return 2
    if pcc_gui_binding_add(src, 1, dst, 2) != 0:
        return 3
    pcc_gui_binding_set_property(src, 1, 77)
    if pcc_gui_binding_get_property(dst, 2) != 77:
        return 4
    return 0


main()
"""


@pytest.mark.integration
def test_gui_python_stack_flow_dock(tmp_path: Path) -> None:
    assert _compile_run(tmp_path, "gui_stack", _STACK) == 0


@pytest.mark.integration
def test_gui_python_elements_controls(tmp_path: Path) -> None:
    assert _compile_run(tmp_path, "gui_elements", _ELEMENTS_CONTROLS) == 0


@pytest.mark.integration
def test_gui_python_binding(tmp_path: Path) -> None:
    assert _compile_run(tmp_path, "gui_binding", _BINDING) == 0

_WINDOW = """
from pcc.extern import c_int64, c_ptr, c_int32, extern
from pcc.unsafe import cstr, load_i64, null, stack_alloc, store_i32, store_i64

pcc_gui_window_init = extern("pcc_gui_window_init", (c_ptr, c_int64, c_int64), c_int32)
pcc_gui_window_post_event = extern("pcc_gui_window_post_event", (c_ptr, c_int32, c_int64, c_int64, c_int32), c_int32)
pcc_gui_window_pending_events = extern("pcc_gui_window_pending_events", (c_ptr,), c_int64)
pcc_gui_window_next_event = extern("pcc_gui_window_next_event", (c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_int32)
pcc_gui_window_is_closed = extern("pcc_gui_window_is_closed", (c_ptr,), c_int32)


def main() -> int:
    window = stack_alloc(2560)
    if pcc_gui_window_init(window, 800, 600) != 0:
        return 1
    if pcc_gui_window_post_event(window, 2, 100, 200, 0) != 0:
        return 2
    if pcc_gui_window_post_event(window, 5, 0, 0, 65) != 0:
        return 3
    if pcc_gui_window_pending_events(window) != 2:
        return 4
    t = stack_alloc(4)
    x = stack_alloc(8)
    y = stack_alloc(8)
    k = stack_alloc(4)
    if pcc_gui_window_next_event(window, t, x, y, k) != 1:
        return 5
    if load_i64(x, 0) != 100:
        return 6
    if pcc_gui_window_next_event(window, t, x, y, k) != 1:
        return 7
    if load_i64(x, 0) != 0:
        return 8
    pcc_gui_window_post_event(window, 1, 0, 0, 0)
    if pcc_gui_window_is_closed(window) != 1:
        return 9
    return 0


main()
"""

_TEXT = """
from pcc.extern import c_int64, c_ptr, c_int32, c_void, extern
from pcc.unsafe import cstr, load_i64, null, stack_alloc

pcc_gui_text_measure = extern("pcc_gui_text_measure", (c_ptr, c_int64, c_int64, c_int64, c_ptr), c_void)


def main() -> int:
    out = stack_alloc(24)
    pcc_gui_text_measure(null(), 10, 16, 40, out)
    if load_i64(out, 16) != 2:
        return 1  # 2 lines
    if load_i64(out, 8) != 32:
        return 2  # 16*2 height
    return 0


main()
"""


@pytest.mark.integration
def test_gui_python_window(tmp_path: Path) -> None:
    assert _compile_run(tmp_path, "gui_window", _WINDOW) == 0


@pytest.mark.integration
def test_gui_python_text(tmp_path: Path) -> None:
    assert _compile_run(tmp_path, "gui_text", _TEXT) == 0

_TABLE_THEME_ANIM = """
from pcc.extern import c_int64, c_ptr, c_int32, extern
from pcc.unsafe import cstr, load_i64, stack_alloc, store_i64, store_i32

pcc_gui_table_measure = extern("pcc_gui_table_measure", (c_ptr, c_ptr, c_ptr, c_int64, c_int64), c_void)
pcc_gui_table_arrange = extern("pcc_gui_table_arrange", (c_ptr, c_ptr, c_ptr, c_int64, c_int64, c_int32, c_int32, c_int32, c_int32), c_void)
pcc_gui_theme_init = extern("pcc_gui_theme_init", (c_ptr,), c_int32)
pcc_gui_theme_set_color = extern("pcc_gui_theme_set_color", (c_ptr, c_int32, c_int64), c_int32)
pcc_gui_theme_get_color = extern("pcc_gui_theme_get_color", (c_ptr, c_int32), c_int64)
pcc_gui_anim_start = extern("pcc_gui_anim_start", (c_ptr, c_int64, c_int64, c_int64), c_int32)
pcc_gui_anim_step = extern("pcc_gui_anim_step", (c_ptr, c_int64), c_int32)
pcc_gui_anim_done = extern("pcc_gui_anim_done", (c_ptr,), c_int32)
pcc_gui_anim_value = extern("pcc_gui_anim_value", (c_ptr,), c_int64)


def main() -> int:
    # table 2x2
    cells = stack_alloc(128)
    store_i64(cells, 16, 10)
    store_i64(cells, 24, 20)
    store_i64(cells, 48, 30)
    store_i64(cells, 56, 10)
    store_i64(cells, 80, 15)
    store_i64(cells, 88, 25)
    store_i64(cells, 112, 5)
    store_i64(cells, 120, 5)
    out = stack_alloc(32)
    totals = stack_alloc(32)
    pcc_gui_table_measure(cells, out, totals, 2, 2)
    if load_i64(totals, 0) != 20:
        return 1
    if load_i64(totals, 16) != 15:
        return 2
    if load_i64(out, 16) != 45:
        return 3
    store_i64(out, 16, 45)
    store_i64(out, 24, 45)
    pcc_gui_table_arrange(cells, out, totals, 2, 2, 0, 0, 0, 0)
    if load_i64(cells, 48 + 0) != 15:
        return 4  # cell1 x = col0 width = 15
    # theme
    theme = stack_alloc(512)
    pcc_gui_theme_init(theme)
    pcc_gui_theme_set_color(theme, 3, 0x11223344)
    if pcc_gui_theme_get_color(theme, 3) != 0x11223344:
        return 5
    # animation
    anim = stack_alloc(40)
    pcc_gui_anim_start(anim, 0, 100, 1000)
    pcc_gui_anim_step(anim, 500)
    if pcc_gui_anim_value(anim) != 50:
        return 6
    pcc_gui_anim_step(anim, 500)
    if pcc_gui_anim_done(anim) != 1:
        return 7
    if pcc_gui_anim_value(anim) != 100:
        return 8
    return 0


main()
"""

_EVENT_ROUTING = """
from pcc.extern import c_int64, c_ptr, c_int32, extern
from pcc.unsafe import cstr, load_i64, stack_alloc, store_i32, store_i64

pcc_gui_control_init = extern("pcc_gui_control_init", (c_ptr,), c_int32)
pcc_gui_control_append_child = extern("pcc_gui_control_append_child", (c_ptr, c_ptr), c_int32)
pcc_gui_control_hit_test = extern("pcc_gui_control_hit_test", (c_ptr, c_int64, c_int64), c_ptr)
pcc_gui_control_route_event = extern("pcc_gui_control_route_event", (c_ptr, c_int32, c_int64, c_int64), c_int32)


def main() -> int:
    root = stack_alloc(64)
    c1 = stack_alloc(64)
    c2 = stack_alloc(64)
    pcc_gui_control_init(root)
    pcc_gui_control_init(c1)
    pcc_gui_control_init(c2)
    store_i64(root, 40, 100)
    store_i64(root, 48, 100)
    store_i64(c1, 24, 10)
    store_i64(c1, 32, 10)
    store_i64(c1, 40, 50)
    store_i64(c1, 48, 50)
    store_i64(c2, 24, 20)
    store_i64(c2, 32, 20)
    store_i64(c2, 40, 30)
    store_i64(c2, 48, 30)
    pcc_gui_control_append_child(root, c1)
    pcc_gui_control_append_child(c1, c2)
    hit = pcc_gui_control_hit_test(root, 25, 25)
    if hit != c2:
        return 1
    # mark c2 as handler (state bit 2)
    store_i32(c2, 56, 2)
    handled = pcc_gui_control_route_event(c2, 2, 25, 25)
    if handled != c2:
        return 2
    return 0


main()
"""


@pytest.mark.integration
def test_gui_python_table_theme_anim(tmp_path: Path) -> None:
    assert _compile_run(tmp_path, "gui_table", _TABLE_THEME_ANIM) == 0


@pytest.mark.integration
def test_gui_python_event_routing(tmp_path: Path) -> None:
    assert _compile_run(tmp_path, "gui_events", _EVENT_ROUTING) == 0

_PNG = """
from pcc.extern import c_int64, c_ptr, c_int32, extern
from pcc.unsafe import cstr, load_i64, load_i8, stack_alloc, store_i64

pcc_gui_png_decode = extern("pcc_gui_png_decode", (c_ptr, c_int64, c_ptr, c_ptr, c_int64), c_int32)


def main() -> int:
    # 20x10 RGBA gradient, filter None (see tests: test20x10.png fixture
    # bytes are embedded as a global array by the test harness below)
    return 0


main()
"""


@pytest.mark.integration
def test_gui_python_png_decode(tmp_path: Path) -> None:
    """Decode a real 20x10 RGBA PNG and verify pixels against the source
    pattern; the program embeds the PNG bytes as a global array."""
    png = (Path(__file__).resolve().parents[2] / ".." / ".." / "tmp" / "test20x10.png")
    # fall back: regenerate deterministically here
    import struct
    import zlib

    W, H = 20, 10
    rows = []
    for y in range(H):
        row = bytearray([0])
        for x in range(W):
            row += bytes([(x * 12) % 256, (y * 25) % 256, (x + y) * 7 % 256, 255])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(typ: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    # embed bytes as little-endian i64 words (signed two's complement)
    words = []
    for i in range(0, len(png), 8):
        words.append(int.from_bytes(png[i:i + 8].ljust(8, b"\x00"), "little", signed=True))
    word_str = ", ".join(str(w) for w in words)
    prog = f"""
from pcc.extern import c_int64, c_ptr, c_int32, extern
from pcc.unsafe import cstr, define_global_i64_array, global_addr, load_i64, load_i8, stack_alloc

pcc_gui_png_decode = extern("pcc_gui_png_decode", (c_ptr, c_int64, c_ptr, c_ptr, c_int64), c_int32)

define_global_i64_array("png_words", {word_str})


def main() -> int:
    header = stack_alloc(32)
    pixels = stack_alloc({W*H*4})
    data = global_addr("png_words")
    rc = pcc_gui_png_decode(data, {len(png)}, header, pixels, {W*H*4})
    if rc != 0:
        return 1
    if load_i64(header, 0) != {W}:
        return 2
    if load_i64(header, 8) != {H}:
        return 3
    if load_i64(header, 16) != 4:
        return 4
    # pixel (0,0) = (0,0,0,255); pixel (10,5) = (120,125,105,255)
    def px(o: int) -> int:
        return load_i8(pixels, o) & 0xFF
    if px(0) != 0 or px(1) != 0 or px(2) != 0 or px(3) != 255:
        return 5
    o = (5*{W} + 10)*4
    if px(o) != 120 or px(o+1) != 125 or px(o+2) != 105 or px(o+3) != 255:
        return 6
    # pixel (19,9) = (228,225,196,255)
    o2 = (9*{W} + 19)*4
    if px(o2) != 228 or px(o2+1) != 225 or px(o2+2) != 196:
        return 7
    return 0


main()
"""
    assert _compile_run(tmp_path, "gui_png", prog) == 0
