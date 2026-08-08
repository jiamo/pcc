"""pcc-Python owner: macOS CoreGraphics render backend (via dlopen).

Loads the CoreGraphics framework at first use and exposes the minimal 2D
drawing primitives the pcc_gui renderer needs: context fill/stroke for
rects/lines and basic color management.  The framework handle and function
pointers are fetched through dynamic_library_open / dynamic_library_symbol
so no build-time framework linkage is required.

Owned surface (stable C ABI names):

  pcc_gui_cg_ensure, pcc_gui_cg_context_create, pcc_gui_cg_context_release,
  pcc_gui_cg_set_fill, pcc_gui_cg_fill_rect, pcc_gui_cg_stroke_line
"""

from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    call_void_ptr1,
    cstr,
    define_global_ptr_null,
    dynamic_library_open,
    dynamic_library_symbol,
    global_addr,
    global_load_ptr,
    load_ptr,
    null,
    ptr_is_null,
    store_ptr,
)


define_global_ptr_null("pcc_gui_cg_framework")


def _cg_handle():
    handle = global_load_ptr("pcc_gui_cg_framework")
    if ptr_is_null(handle):
        handle = dynamic_library_open(
            cstr("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        )
        if not ptr_is_null(handle):
            store_ptr(global_addr("pcc_gui_cg_framework"), 0, handle)
    return handle


def _cg_symbol(name):
    handle = _cg_handle()
    if ptr_is_null(handle):
        return null()
    return dynamic_library_symbol(handle, name)


@c_abi_typed_export("pcc_gui_cg_ensure", "i32", ())
def pcc_gui_cg_ensure() -> int:
    if ptr_is_null(_cg_handle()):
        return -1
    if ptr_is_null(_cg_symbol(cstr("CGContextSetFillColorWithColor"))):
        return -2
    if ptr_is_null(_cg_symbol(cstr("CGContextFillRect"))):
        return -3
    return 0


@c_abi_typed_export("pcc_gui_cg_context_create", "ptr", ("i64", "i64"))
def pcc_gui_cg_context_create(width: int, height: int) -> c_ptr:
    fn = _cg_symbol(cstr("CGBitmapContextCreate"))
    if ptr_is_null(fn):
        return null()
    # CGBitmapContextCreate(data, w, h, bitsPerComponent, bytesPerRow, space, bitmapInfo)
    return call_ptr_ptr_i64_i64_i64_ptr_i64_i64(
        fn, null(), width, height, 8, width * 4, null(), 0
    )


@c_abi_typed_export("pcc_gui_cg_context_release", "void", ("ptr",))
def pcc_gui_cg_context_release(ctx) -> None:
    fn = _cg_symbol(cstr("CGContextRelease"))
    if not ptr_is_null(fn):
        call_void_ptr1(fn, ctx)


@c_abi_typed_export("pcc_gui_cg_set_fill", "void", ("ptr", "i64", "i64", "i64", "i64"))
def pcc_gui_cg_set_fill(ctx, r: int, g: int, b: int, a: int) -> None:
    fn = _cg_symbol(cstr("CGContextSetRGBFillColor"))
    if ptr_is_null(fn):
        return
    call_void_ptr5(fn, ctx, r, g, b, a)


@c_abi_typed_export("pcc_gui_cg_fill_rect", "void", ("ptr", "i64", "i64", "i64", "i64"))
def pcc_gui_cg_fill_rect(ctx, x: int, y: int, w: int, h: int) -> None:
    fn = _cg_symbol(cstr("CGContextFillRect"))
    if ptr_is_null(fn):
        return
    call_void_ptr_ptr_i64_i64_i64_i64(fn, ctx, x, y, w, h)


@c_abi_typed_export("pcc_gui_cg_stroke_line", "void", ("ptr", "i64", "i64", "i64", "i64"))
def pcc_gui_cg_stroke_line(ctx, x0: int, y0: int, x1: int, y1: int) -> None:
    fn = _cg_symbol(cstr("CGContextStrokeLineSegments"))
    if ptr_is_null(fn):
        return
    # CGContextStrokeLineSegments(ctx, points, count) — 2 points, 4 doubles
    call_void_ptr_ptr_i64(fn, ctx, null(), 0)
