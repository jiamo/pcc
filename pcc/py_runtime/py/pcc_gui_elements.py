"""pcc-Python owner: GUI render element system.

Render elements are the declarative drawing primitives a control's renderer
emits; the platform backend (pcc_gui_cg on macOS) consumes them.  Element
type codes:

  1 solid-fill rect   (color r,g,b,a + rounded radius)
  2 border rect       (color + width + radius)
  3 line              (color + width + x0,y0,x1,y1)
  4 text              (utf8, font-id, size, color, align)
  5 image             (buffer ptr, w, h)
  6 polygon           (point-count + point array ptr)
  7 vertical gradient (top/bottom color)

Each element is a 64-byte record: type@0 i32, color@8..24 (r,g,b,a i32s),
geometry@32..56 (i64s), payload ptr@56.  A renderer walks a list of elements
and issues the corresponding platform draw calls.

Owned surface:

  pcc_gui_element_init, pcc_gui_element_type, pcc_gui_element_set_color,
  pcc_gui_element_set_geometry, pcc_gui_element_set_payload
"""

from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    store_i8,
    cstr,
    load_i32,
    load_i64,
    null,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)

_PCC_GUI_EL_SOLID = 1
_PCC_GUI_EL_BORDER = 2
_PCC_GUI_EL_LINE = 3
_PCC_GUI_EL_TEXT = 4
_PCC_GUI_EL_IMAGE = 5
_PCC_GUI_EL_POLYGON = 6
_PCC_GUI_EL_GRADIENT = 7


@c_abi_typed_export("pcc_gui_element_init", "i32", ("ptr", "i32"))
def pcc_gui_element_init(element, elem_type: int) -> int:
    if ptr_is_null(element):
        return -1
    i: int = 0
    while i < 64:
        store_i8(element, i, 0)
        i += 1
    store_i32(element, 0, elem_type)
    return 0


@c_abi_typed_export("pcc_gui_element_type", "i32", ("ptr",))
def pcc_gui_element_type(element) -> int:
    if ptr_is_null(element):
        return 0
    return load_i32(element, 0)


@c_abi_typed_export("pcc_gui_element_set_color", "i32", ("ptr", "i32", "i32", "i32", "i32"))
def pcc_gui_element_set_color(element, r: int, g: int, b: int, a: int) -> int:
    if ptr_is_null(element):
        return -1
    store_i32(element, 8, r)
    store_i32(element, 12, g)
    store_i32(element, 16, b)
    store_i32(element, 20, a)
    return 0


@c_abi_typed_export("pcc_gui_element_set_geometry", "i32", ("ptr", "i64", "i64", "i64", "i64"))
def pcc_gui_element_set_geometry(element, v0: int, v1: int, v2: int, v3: int) -> int:
    if ptr_is_null(element):
        return -1
    store_i64(element, 32, v0)
    store_i64(element, 40, v1)
    store_i64(element, 48, v2)
    store_i64(element, 56, v3)
    return 0


@c_abi_typed_export("pcc_gui_element_set_payload", "i32", ("ptr", "ptr"))
def pcc_gui_element_set_payload(element, payload) -> int:
    if ptr_is_null(element):
        return -1
    store_ptr(element, 24, payload)
    return 0


@c_abi_typed_export("pcc_gui_element_get_color", "i64", ("ptr",))
def pcc_gui_element_get_color(element) -> int:
    if ptr_is_null(element):
        return 0
    return load_i64(element, 8)
