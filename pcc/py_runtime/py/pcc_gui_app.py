"""STATUS: WORK-IN-PROGRESS — this facade is NOT complete and is NOT in the
production archive.  Do not use it; the verified high-level API is
``pcc_gui_high.App`` (see projects/mac_diff_app/).  The text/image facade
below is unfinished (text is a placeholder).  Either complete this module
and add it to the Makefile, or delete it; keeping it as-is misrepresents a
stable API.

pcc-Python owner: low-level pcc_gui app facade (C ABI for the user layer).

This module is the internal engine behind the friendly ``pcc_gui_high``
class API — ordinary users never touch it.  State lives in named i64
globals (native pointers stored as integers via ptr_to_int); the
rect/color/theme/anim buffers are calloc'd at init.

C ABI surface (exported into the production archive):

  pcc_gui_app_init(title, dylib_path, w, h) -> i32
  pcc_gui_app_theme(key, color) / pcc_gui_app_theme_get(key) -> i64
  pcc_gui_app_clear(color)
  pcc_gui_app_rect(x, y, w, h, color)
  pcc_gui_app_progress(x, y, w, h, value, accent)
  pcc_gui_app_text(x, y, byte_len, font, color)
  pcc_gui_app_image(png, png_len, x, y, w, h) -> i32
  pcc_gui_app_anim_start(from, to, dur) / _step(ms) / _value() / _done()
  pcc_gui_app_present() -> i32
  pcc_gui_app_sleep_ms(ms)
  pcc_gui_app_running() -> i32
  pcc_gui_app_close()

Colors are ARGB in the low 32 bits of an i64.
"""

from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    call_i64_ptr1,
    call_i64_ptr3_i64_i64_i64,
    call_ptr_i64_i64,
    calloc,
    cstr,
    define_global_i64,
    dynamic_library_open,
    dynamic_library_symbol,
    global_addr,
    int_to_ptr,
    load_i64,
    null,
    ptr_is_null,
    ptr_to_int,
    store_i32,
    store_i64,
)

# ---- pcc_gui core externs ----
pcc_gui_theme_set_color = extern("pcc_gui_theme_set_color", (c_ptr, c_int32, c_int64), c_int32)
pcc_gui_theme_get_color = extern("pcc_gui_theme_get_color", (c_ptr, c_int32), c_int64)
pcc_gui_anim_start = extern("pcc_gui_anim_start", (c_ptr, c_int64, c_int64, c_int64), c_int32)
pcc_gui_anim_step = extern("pcc_gui_anim_step", (c_ptr, c_int64), c_int32)
pcc_gui_anim_value = extern("pcc_gui_anim_value", (c_ptr,), c_int64)
pcc_gui_anim_done = extern("pcc_gui_anim_done", (c_ptr,), c_int32)
pcc_gui_text_measure = extern("pcc_gui_text_measure", (c_ptr, c_int64, c_int64, c_int64, c_ptr), c_void)
pcc_gui_png_decode = extern("pcc_gui_png_decode", (c_ptr, c_int64, c_ptr, c_ptr, c_int64), c_int64)
pcc_platform_sleep_ns = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)

# ---- named state globals (compile-time symbols; values written at runtime) ----
define_global_i64("pcc_gui_app_win", 0)
define_global_i64("pcc_gui_app_create", 0)
define_global_i64("pcc_gui_app_render", 0)
define_global_i64("pcc_gui_app_show", 0)
define_global_i64("pcc_gui_app_close", 0)
define_global_i64("pcc_gui_app_pump", 0)
define_global_i64("pcc_gui_app_closed", 0)
define_global_i64("pcc_gui_app_rects", 0)
define_global_i64("pcc_gui_app_colors", 0)
define_global_i64("pcc_gui_app_theme_storage", 0)
define_global_i64("pcc_gui_app_anim", 0)
define_global_i64("pcc_gui_app_imgbuf", 0)
define_global_i64("pcc_gui_app_count", 0)
define_global_i64("pcc_gui_app_w", 0)
define_global_i64("pcc_gui_app_h", 0)
define_global_i64("pcc_gui_app_running", 0)

MAX_RECTS = 64
IMG_W = 16
IMG_H = 16


def _g(slot: str) -> int:
    if slot == "pcc_gui_app_win":
        return load_i64(global_addr("pcc_gui_app_win"), 0)
    if slot == "pcc_gui_app_create":
        return load_i64(global_addr("pcc_gui_app_create"), 0)
    if slot == "pcc_gui_app_render":
        return load_i64(global_addr("pcc_gui_app_render"), 0)
    if slot == "pcc_gui_app_show":
        return load_i64(global_addr("pcc_gui_app_show"), 0)
    if slot == "pcc_gui_app_close":
        return load_i64(global_addr("pcc_gui_app_close"), 0)
    if slot == "pcc_gui_app_pump":
        return load_i64(global_addr("pcc_gui_app_pump"), 0)
    if slot == "pcc_gui_app_closed":
        return load_i64(global_addr("pcc_gui_app_closed"), 0)
    if slot == "pcc_gui_app_rects":
        return load_i64(global_addr("pcc_gui_app_rects"), 0)
    if slot == "pcc_gui_app_colors":
        return load_i64(global_addr("pcc_gui_app_colors"), 0)
    if slot == "pcc_gui_app_theme_storage":
        return load_i64(global_addr("pcc_gui_app_theme_storage"), 0)
    if slot == "pcc_gui_app_anim":
        return load_i64(global_addr("pcc_gui_app_anim"), 0)
    if slot == "pcc_gui_app_imgbuf":
        return load_i64(global_addr("pcc_gui_app_imgbuf"), 0)
    if slot == "pcc_gui_app_count":
        return load_i64(global_addr("pcc_gui_app_count"), 0)
    if slot == "pcc_gui_app_w":
        return load_i64(global_addr("pcc_gui_app_w"), 0)
    if slot == "pcc_gui_app_h":
        return load_i64(global_addr("pcc_gui_app_h"), 0)
    if slot == "pcc_gui_app_running":
        return load_i64(global_addr("pcc_gui_app_running"), 0)
    return 0


def _setg(slot: str, value: int) -> None:
    if slot == "pcc_gui_app_win":
        store_i64(global_addr("pcc_gui_app_win"), 0, value)
    elif slot == "pcc_gui_app_create":
        store_i64(global_addr("pcc_gui_app_create"), 0, value)
    elif slot == "pcc_gui_app_render":
        store_i64(global_addr("pcc_gui_app_render"), 0, value)
    elif slot == "pcc_gui_app_show":
        store_i64(global_addr("pcc_gui_app_show"), 0, value)
    elif slot == "pcc_gui_app_close":
        store_i64(global_addr("pcc_gui_app_close"), 0, value)
    elif slot == "pcc_gui_app_pump":
        store_i64(global_addr("pcc_gui_app_pump"), 0, value)
    elif slot == "pcc_gui_app_closed":
        store_i64(global_addr("pcc_gui_app_closed"), 0, value)
    elif slot == "pcc_gui_app_rects":
        store_i64(global_addr("pcc_gui_app_rects"), 0, value)
    elif slot == "pcc_gui_app_colors":
        store_i64(global_addr("pcc_gui_app_colors"), 0, value)
    elif slot == "pcc_gui_app_theme_storage":
        store_i64(global_addr("pcc_gui_app_theme_storage"), 0, value)
    elif slot == "pcc_gui_app_anim":
        store_i64(global_addr("pcc_gui_app_anim"), 0, value)
    elif slot == "pcc_gui_app_imgbuf":
        store_i64(global_addr("pcc_gui_app_imgbuf"), 0, value)
    elif slot == "pcc_gui_app_count":
        store_i64(global_addr("pcc_gui_app_count"), 0, value)
    elif slot == "pcc_gui_app_w":
        store_i64(global_addr("pcc_gui_app_w"), 0, value)
    elif slot == "pcc_gui_app_h":
        store_i64(global_addr("pcc_gui_app_h"), 0, value)
    elif slot == "pcc_gui_app_running":
        store_i64(global_addr("pcc_gui_app_running"), 0, value)


def _gptr(slot: str):
    return int_to_ptr(_g(slot))


@c_abi_typed_export("pcc_gui_app_init", "i32", ("ptr", "ptr", "i64", "i64"))
def pcc_gui_app_init(title, dylib_path, w: int, h: int) -> int:
    hdl = dynamic_library_open(dylib_path)
    if ptr_is_null(hdl):
        return -1
    create = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_create"))
    render = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_render"))
    show = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_show"))
    close = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_close"))
    pump = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_run_loop_pump"))
    closed = dynamic_library_symbol(hdl, cstr("pcc_gui_metal_window_is_closed"))
    if ptr_is_null(create) or ptr_is_null(render) or ptr_is_null(pump):
        return -2
    win = call_ptr_i64_i64(create, title, w, h)
    if ptr_is_null(win):
        return -3
    rects = calloc(MAX_RECTS * 32, 1)
    colors = calloc(MAX_RECTS * 4, 1)
    theme = calloc(512, 1)
    anim = calloc(40, 1)
    imgbuf = calloc(IMG_W * IMG_H * 4, 1)
    if ptr_is_null(rects) or ptr_is_null(colors):
        return -4
    _setg("pcc_gui_app_win", ptr_to_int(win))
    _setg("pcc_gui_app_create", ptr_to_int(create))
    _setg("pcc_gui_app_render", ptr_to_int(render))
    _setg("pcc_gui_app_show", ptr_to_int(show))
    _setg("pcc_gui_app_close", ptr_to_int(close))
    _setg("pcc_gui_app_pump", ptr_to_int(pump))
    _setg("pcc_gui_app_closed", ptr_to_int(closed))
    _setg("pcc_gui_app_rects", ptr_to_int(rects))
    _setg("pcc_gui_app_colors", ptr_to_int(colors))
    _setg("pcc_gui_app_theme_storage", ptr_to_int(theme))
    _setg("pcc_gui_app_anim", ptr_to_int(anim))
    _setg("pcc_gui_app_imgbuf", ptr_to_int(imgbuf))
    _setg("pcc_gui_app_w", w)
    _setg("pcc_gui_app_h", h)
    _setg("pcc_gui_app_count", 0)
    _setg("pcc_gui_app_running", 1)
    call_i64_ptr1(show, win)
    call_i64_ptr1(pump, null())
    return 0


@c_abi_typed_export("pcc_gui_app_theme", "i64", ("i64", "i64"))
def pcc_gui_app_theme(key: int, color: int) -> int:
    return pcc_gui_theme_set_color(
        _gptr("pcc_gui_app_theme_storage"), key, color
    )


@c_abi_typed_export("pcc_gui_app_theme_get", "i64", ("i64",))
def pcc_gui_app_theme_get(key: int) -> int:
    return pcc_gui_theme_get_color(_gptr("pcc_gui_app_theme_storage"), key)


def _emit_rect(x: int, y: int, w: int, h: int, color: int) -> int:
    n: int = _g("pcc_gui_app_count")
    if n >= MAX_RECTS:
        return -1
    r = _gptr("pcc_gui_app_rects")
    c = _gptr("pcc_gui_app_colors")
    store_i64(r, n * 32 + 0, x)
    store_i64(r, n * 32 + 8, y)
    store_i64(r, n * 32 + 16, w)
    store_i64(r, n * 32 + 24, h)
    store_i32(c, n * 4 + 0, (color >> 16) & 255)
    store_i32(c, n * 4 + 1, (color >> 8) & 255)
    store_i32(c, n * 4 + 2, color & 255)
    store_i32(c, n * 4 + 3, (color >> 24) & 255)
    _setg("pcc_gui_app_count", n + 1)
    return 0


@c_abi_typed_export("pcc_gui_app_clear", "i64", ("i64",))
def pcc_gui_app_clear(color: int) -> int:
    return _emit_rect(0, 0, _g("pcc_gui_app_w"), _g("pcc_gui_app_h"), color)


@c_abi_typed_export("pcc_gui_app_rect", "i64", ("i64", "i64", "i64", "i64", "i64"))
def pcc_gui_app_rect(x: int, y: int, w: int, h: int, color: int) -> int:
    return _emit_rect(x, y, w, h, color)


@c_abi_typed_export("pcc_gui_app_progress", "i64", ("i64", "i64", "i64", "i64", "i64", "i64"))
def pcc_gui_app_progress(x: int, y: int, w: int, h: int, value: int, accent: int) -> int:
    _emit_rect(x, y, w, h, 0xFFCCCCCC)
    fill: int = (w * value) // 100
    if fill > 2:
        _emit_rect(x + 2, y + 2, fill - 2, h - 4, accent)
    return 0


@c_abi_typed_export("pcc_gui_app_text", "i64", ("i64", "i64", "i64", "i64", "i64"))
def pcc_gui_app_text(x: int, y: int, byte_len: int, font: int, color: int) -> int:
    out = int_to_ptr(0)  # replaced below
    return 0
