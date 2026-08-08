"""pcc_gui_high: module-level function API for pcc-GUI programs.

The high-level layer for pcc-Python GUI apps.  All functions are
module-level (class-method multi-arg passing has an open pcc compiler bug,
see docs/investigations/…-2026-08-07c), and every detail — window handle,
render buffers, text slots, bridge function pointers — is hidden inside.

    import pcc_gui_high as gui

    rc = gui.init(cstr("my app"), 900, 600, cstr("/abs/path/libpcc_gui_metal.dylib"))
    gui.theme(0, 0xFF2B2B2B)
    gui.anim_start(0, 100, 2000)
    while gui.running():
        v = gui.anim_value()
        gui.anim_step(16)
        gui.clear(0xFF1E1E1E)
        gui.rect(0, 0, 900, 48, gui.theme_get(0))
        gui.button(16, 52, 90, 30)
        gui.text(1, 40, 58, cstr("Open"), 4, 13, 0xFFEEEEEE)   # real glyphs
        gui.present()
        gui.sleep(16)
    gui.close()

Colors are ARGB (0xAARRGGBB).  Text is drawn via the Metal bridge
CATextLayer path (slot 0..511, top-left coords).
"""

from pcc.extern import c_int64, c_ptr, c_int32, c_void, extern
from pcc.unsafe import (
    call_i64_ptr1,
    call_i64_ptr2,
    call_i64_ptr_i64,
    call_i64_ptr3,
    call_i64_ptr3_i64_i64_i64,
    call_ptr_i64_i64,
    calloc,
    cstr,
    define_global_i64_array,
    dynamic_library_open,
    dynamic_library_symbol,
    global_addr,
    int_to_ptr,
    load_i64,
    null,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
)

# ---- pcc_gui core ----
pcc_gui_theme_set_color = extern("pcc_gui_theme_set_color", (c_ptr, c_int32, c_int64), c_int32)
pcc_gui_theme_get_color = extern("pcc_gui_theme_get_color", (c_ptr, c_int32), c_int64)
pcc_gui_anim_start = extern("pcc_gui_anim_start", (c_ptr, c_int64, c_int64, c_int64), c_int32)
pcc_gui_anim_step = extern("pcc_gui_anim_step", (c_ptr, c_int64), c_int32)
pcc_gui_anim_value = extern("pcc_gui_anim_value", (c_ptr,), c_int64)
pcc_gui_anim_done = extern("pcc_gui_anim_done", (c_ptr,), c_int32)
pcc_platform_sleep_ns = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)

# ---- module state ----
# win@0 render@8 show@16 close@24 pump@32 closed@40 rects@48 colors@56
# theme@64 anim@72 count@88 w@96 h@104 click@112 textfn@120 sizefn@128
# panel@136 capture@144 pane@152 pane-focus@160 panel2@168
# last-scene-render-ack@176 last-present-ack@184
define_global_i64_array("pcc_gui_high_state",
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def _g(off: int) -> int:
    return load_i64(global_addr("pcc_gui_high_state"), off)


def _setg(off: int, value: int) -> None:
    store_i64(global_addr("pcc_gui_high_state"), off, value)


def _gp(off: int):
    return int_to_ptr(_g(off))


def init(title, w: int, h: int, dylib) -> int:
    """Create the window + menu bar and dlopen the Metal bridge."""
    hdl = dynamic_library_open(dylib)
    if ptr_is_null(hdl):
        return -1
    create = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_create"))
    render = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_render"))
    show = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_show"))
    close = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_close"))
    pump = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_run_loop_pump"))
    closed = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_is_closed"))
    click = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_poll_click"))
    txt = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_text"))
    sz = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_size"))
    panel = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_open_panel"))
    capfn = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_capture"))
    panefn = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_pane_set"))
    panefocusfn = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_pane_focus"))
    panel2fn = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_open_panel2"))
    if ptr_is_null(create) or ptr_is_null(render) or ptr_is_null(pump):
        return -2
    win = call_ptr_i64_i64(create, title, w, h)
    if ptr_is_null(win):
        return -3
    _setg(0, ptr_to_int(win))
    _setg(8, ptr_to_int(render))
    _setg(16, ptr_to_int(show))
    _setg(24, ptr_to_int(close))
    _setg(32, ptr_to_int(pump))
    _setg(40, ptr_to_int(closed))
    _setg(48, ptr_to_int(calloc(64 * 32, 1)))
    _setg(56, ptr_to_int(calloc(64 * 4, 1)))
    _setg(64, ptr_to_int(calloc(512, 1)))
    _setg(72, ptr_to_int(calloc(40, 1)))
    _setg(88, 0)
    _setg(96, w)
    _setg(104, h)
    _setg(112, ptr_to_int(click))
    _setg(120, ptr_to_int(txt))
    _setg(128, ptr_to_int(sz))
    _setg(136, ptr_to_int(panel))
    _setg(144, ptr_to_int(capfn))
    _setg(152, ptr_to_int(panefn))
    _setg(160, ptr_to_int(panefocusfn))
    _setg(168, ptr_to_int(panel2fn))
    _setg(176, -1)
    _setg(184, -1)
    call_i64_ptr1(show, win)
    call_i64_ptr1(pump, null())
    return 0


# ---- theme / animation ----
def theme(key: int, color: int) -> None:
    pcc_gui_theme_set_color(_gp(64), key, color)


def theme_get(key: int) -> int:
    return pcc_gui_theme_get_color(_gp(64), key)


def anim_start(f: int, to: int, dur: int) -> None:
    pcc_gui_anim_start(_gp(72), f, to, dur)


def anim_step(ms: int) -> None:
    pcc_gui_anim_step(_gp(72), ms)


def anim_value() -> int:
    return pcc_gui_anim_value(_gp(72))


def anim_done() -> int:
    return pcc_gui_anim_done(_gp(72))


# ---- drawing ----
def _emit(x: int, y: int, w: int, h: int, color: int) -> None:
    n: int = _g(88)
    if n >= 64:
        return
    r = _gp(48)
    c = _gp(56)
    store_i64(r, n * 32 + 0, x)
    store_i64(r, n * 32 + 8, y)
    store_i64(r, n * 32 + 16, w)
    store_i64(r, n * 32 + 24, h)
    store_i32(c, n * 4 + 0, (color >> 16) & 255)
    store_i32(c, n * 4 + 1, (color >> 8) & 255)
    store_i32(c, n * 4 + 2, color & 255)
    store_i32(c, n * 4 + 3, (color >> 24) & 255)
    _setg(88, n + 1)


def clear(color: int) -> None:
    _emit(0, 0, _g(96), _g(104), color)


def rect(x: int, y: int, w: int, h: int, color: int) -> None:
    _emit(x, y, w, h, color)


def progress(x: int, y: int, w: int, h: int, value: int, accent: int) -> None:
    _emit(x, y, w, h, 0xFFCCCCCC)
    fill: int = (w * value) // 100
    if fill > 2:
        _emit(x + 2, y + 2, fill - 2, h - 4, accent)


def button(x: int, y: int, w: int, h: int, color: int) -> None:
    _emit(x, y, w, h, color)
    _emit(x, y, w, 2, 0xFFD0D0D0)
    _emit(x, y + h - 2, w, 2, 0xFF909090)


def text(slot: int, x: int, y: int, text_ptr: c_ptr, text_len: int,
         font: int, color: int) -> None:
    """Real glyph text (slot 0..511, top-left coords)."""
    text_hl(slot, x, y, text_ptr, text_len, font, color, 0, 0)


def text_hl(slot: int, x: int, y: int, text_ptr: c_ptr, text_len: int,
            font: int, color: int, other_ptr: int, other_len: int) -> None:
    """Like text(); when other_len > 0 the bridge diffs this line against the
    other side's bytes (other_ptr) and paints the differing span red."""
    prm = stack_alloc(48)
    store_i64(prm, 0, slot)
    store_i64(prm, 8, x)
    store_i64(prm, 16, y)
    store_i64(prm, 24, other_ptr)
    store_i64(prm, 32, other_len)
    call_i64_ptr3_i64_i64_i64(_gp(120), _gp(0), text_ptr, prm,
                              text_len, font, color)


# ---- input / window ----
def poll_click(x_out, y_out) -> int:
    """1 + write the latest left-click (top-left) if any since last poll."""
    return call_i64_ptr3(_gp(112), _gp(0), x_out, y_out)


def window_size(w_out, h_out) -> int:
    return call_i64_ptr3(_gp(128), _gp(0), w_out, h_out)


def resize(w: int, h: int) -> None:
    """Adopt the current content size so clear/present use the real size."""
    _setg(96, w)
    _setg(104, h)


def pane_focus(pane: int, line: int) -> None:
    """Select + scroll the pane's NSTextView to a display line (0-based)."""
    call_ptr_i64_i64(_gp(160), _gp(0), pane, line)


def pane_set(pane: int, x: int, y: int, w: int, h: int,
             text_ptr: c_ptr, text_len: int, spec_ptr, nlines: int) -> int:
    """Set a whole diff pane as one selectable NSTextView.  spec_ptr: nlines
    records of 5 i64 {line_byte_start, line_byte_len, kind, red_start, red_len}."""
    prm = stack_alloc(48)
    store_i64(prm, 0, x)
    store_i64(prm, 8, y)
    store_i64(prm, 16, w)
    store_i64(prm, 24, h)
    store_i64(prm, 32, ptr_to_int(spec_ptr))
    return call_i64_ptr3_i64_i64_i64(_gp(152), _gp(0), text_ptr, prm,
                                     text_len, nlines, pane)


def capture(path) -> int:
    """Write the window content to a PNG (diagnostic)."""
    return call_i64_ptr2(_gp(144), _gp(0), path)


def open_panel(path_buf, cap: int) -> int:
    """NSOpenPanel: 0 = picked (path written), 1 = cancelled."""
    return call_i64_ptr_i64(_gp(136), path_buf, cap)


def open_panel2(p1, p2) -> int:
    """One multi-select NSOpenPanel: 0 = two files picked (p1,p2 written,
    512-byte bufs), 1 = cancelled / fewer than 2 chosen."""
    return call_i64_ptr2(_gp(168), p1, p2)


def running() -> int:
    call_i64_ptr1(_gp(32), null())
    if call_i64_ptr1(_gp(40), _gp(0)) != 0:
        return 0
    return 1


def sleep(ms: int) -> None:
    pcc_platform_sleep_ns(ms * 1000000)


def render_scene(rects, colors, count: int, texts, tcount: int,
                 w: int, h: int) -> None:
    """Render a scene produced by the composition-tree kernel: rect
    commands via the Metal bridge, text commands via CATextLayer."""
    _setg(176, call_i64_ptr3_i64_i64_i64(
        _gp(8), _gp(0), rects, colors, count, w, h))
    i = 0
    while i < tcount:
        tb = texts
        x = load_i64(tb, i * 48 + 0)
        y = load_i64(tb, i * 48 + 8)
        ln = load_i64(tb, i * 48 + 16)
        font = load_i64(tb, i * 48 + 24)
        color = load_i64(tb, i * 48 + 32)
        tp = int_to_ptr(load_i64(tb, i * 48 + 40))
        prm = stack_alloc(24)
        store_i64(prm, 0, 500 + i)
        store_i64(prm, 8, x)
        store_i64(prm, 16, y)
        call_i64_ptr3_i64_i64_i64(_gp(120), _gp(0), tp, prm, ln, font, color)
        i = i + 1


def present() -> None:
    _setg(184, call_i64_ptr3_i64_i64_i64(
        _gp(8), _gp(0), _gp(48), _gp(56), _g(88), _g(96), _g(104)))
    _setg(88, 0)


def render_ack() -> int:
    """Return the real bridge render/present completion status."""
    return _g(176)


def present_ack() -> int:
    """Return the real bridge completion status from the retained-mode path."""
    return _g(184)


def close() -> None:
    call_i64_ptr1(_gp(24), _gp(0))
