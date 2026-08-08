"""Narrow native window bridge over the verified PCC AppKit/Metal surface."""

from pcc.extern import c_int64, c_ptr, c_int32, extern
from pcc.unsafe import (
    call_i64_ptr1,
    call_i64_ptr2,
    call_i64_ptr3,
    call_i64_ptr3_i64_i64_i64,
    call_ptr_i64_i64,
    cstr,
    define_global_i64_array,
    dynamic_library_open,
    dynamic_library_symbol,
    global_addr,
    int_to_ptr,
    load_i64,
    load_ptr,
    null,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i64,
)


pcc_platform_sleep_ns = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)

# win, render, show, close, pump, closed, click, text, capture, width, height,
# last-render-ack
define_global_i64_array(
    "harness_gui_bridge_state", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -1
)


def _g(offset: int) -> int:
    return load_i64(global_addr("harness_gui_bridge_state"), offset)


def _setg(offset: int, value: int) -> None:
    store_i64(global_addr("harness_gui_bridge_state"), offset, value)


def _gp(offset: int):
    return int_to_ptr(_g(offset))


def init(title, width: int, height: int, dylib_path) -> int:
    handle = dynamic_library_open(dylib_path)
    if ptr_is_null(handle):
        return -1
    create = dynamic_library_symbol(handle, cstr("pcc_gui_metal_window_create"))
    render = dynamic_library_symbol(handle, cstr("pcc_gui_metal_window_render"))
    show = dynamic_library_symbol(handle, cstr("pcc_gui_metal_window_show"))
    close = dynamic_library_symbol(handle, cstr("pcc_gui_metal_window_close"))
    pump = dynamic_library_symbol(handle, cstr("pcc_gui_metal_run_loop_pump"))
    closed = dynamic_library_symbol(handle, cstr("pcc_gui_metal_window_is_closed"))
    click = dynamic_library_symbol(handle, cstr("pcc_gui_metal_window_poll_click"))
    text = dynamic_library_symbol(handle, cstr("pcc_gui_metal_window_text"))
    capture = dynamic_library_symbol(handle, cstr("pcc_gui_metal_window_capture"))
    if (
        ptr_is_null(create)
        or ptr_is_null(render)
        or ptr_is_null(show)
        or ptr_is_null(close)
        or ptr_is_null(pump)
        or ptr_is_null(closed)
        or ptr_is_null(click)
        or ptr_is_null(text)
    ):
        return -2
    window = call_ptr_i64_i64(create, title, width, height)
    if ptr_is_null(window):
        return -3
    _setg(0, ptr_to_int(window))
    _setg(8, ptr_to_int(render))
    _setg(16, ptr_to_int(show))
    _setg(24, ptr_to_int(close))
    _setg(32, ptr_to_int(pump))
    _setg(40, ptr_to_int(closed))
    _setg(48, ptr_to_int(click))
    _setg(56, ptr_to_int(text))
    _setg(64, ptr_to_int(capture))
    _setg(72, width)
    _setg(80, height)
    call_i64_ptr1(show, window)
    call_i64_ptr1(pump, null())
    return 0


def render_scene(rects, colors, rect_count: int, texts, text_count: int) -> None:
    _setg(
        88,
        call_i64_ptr3_i64_i64_i64(
            _gp(8), _gp(0), rects, colors, rect_count, _g(72), _g(80)
        ),
    )
    i = 0
    while i < text_count:
        record = texts
        params = stack_alloc(48)
        store_i64(params, 0, i)
        store_i64(params, 8, load_i64(record, i * 48 + 0))
        store_i64(params, 16, load_i64(record, i * 48 + 8))
        store_i64(params, 24, 0)
        store_i64(params, 32, 0)
        call_i64_ptr3_i64_i64_i64(
            _gp(56),
            _gp(0),
            load_ptr(record, i * 48 + 40),
            params,
            load_i64(record, i * 48 + 16),
            load_i64(record, i * 48 + 24),
            load_i64(record, i * 48 + 32),
        )
        i += 1
    while i < 64:
        params = stack_alloc(48)
        store_i64(params, 0, i)
        call_i64_ptr3_i64_i64_i64(
            _gp(56), _gp(0), null(), params, 0, 12, 0
        )
        i += 1


def render_ack() -> int:
    return _g(88)


def poll_click(x_out, y_out) -> int:
    return call_i64_ptr3(_gp(48), _gp(0), x_out, y_out)


def running() -> int:
    call_i64_ptr1(_gp(32), null())
    return 0 if call_i64_ptr1(_gp(40), _gp(0)) != 0 else 1


def sleep(milliseconds: int) -> None:
    pcc_platform_sleep_ns(milliseconds * 1000000)


def capture(path) -> int:
    if ptr_is_null(_gp(64)):
        return -1
    return call_i64_ptr2(_gp(64), _gp(0), path)


def close() -> None:
    call_i64_ptr1(_gp(24), _gp(0))
