"""pcc-Python owner: GUI control tree + event routing.

A control is a 64-byte node: parent@0, first_child@8, next_sibling@16,
bounds@24..56 (left,top,width,height i64s), state@56 (focused/hovered bits).
Controls form a tree; hit-testing walks children in reverse order (topmost
first); events (mouse down/up/move, key) route from the hit target up to the
first handler.

Event codes: 1=mouse-down 2=mouse-up 3=mouse-move 4=key-down 5=key-up

Owned surface:

  pcc_gui_control_init, pcc_gui_control_append_child, pcc_gui_control_hit_test,
  pcc_gui_control_set_focus, pcc_gui_control_focused, pcc_gui_control_route_event
"""

from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    store_i8,
    cstr,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)

_PCC_GUI_EVENT_MOUSE_DOWN = 1
_PCC_GUI_EVENT_MOUSE_UP = 2
_PCC_GUI_EVENT_MOUSE_MOVE = 3
_PCC_GUI_EVENT_KEY_DOWN = 4
_PCC_GUI_EVENT_KEY_UP = 5


@c_abi_typed_export("pcc_gui_control_init", "i32", ("ptr",))
def pcc_gui_control_init(control) -> int:
    if ptr_is_null(control):
        return -1
    i: int = 0
    while i < 64:
        store_i8(control, i, 0)
        i += 1
    return 0


@c_abi_typed_export("pcc_gui_control_append_child", "i32", ("ptr", "ptr"))
def pcc_gui_control_append_child(parent, child) -> int:
    if ptr_is_null(parent) or ptr_is_null(child):
        return -1
    store_ptr(child, 0, parent)
    last = load_ptr(parent, 8)
    if ptr_is_null(last):
        store_ptr(parent, 8, child)
    else:
        while not ptr_is_null(load_ptr(last, 16)):
            last = load_ptr(last, 16)
        store_ptr(last, 16, child)
    return 0


@c_abi_typed_export("pcc_gui_control_hit_test", "ptr", ("ptr", "i64", "i64"))
def pcc_gui_control_hit_test(root, x: int, y: int) -> c_ptr:
    """Topmost control containing (x,y); walks children in reverse (paint)
    order so the last child (on top) wins."""
    if ptr_is_null(root):
        return null()
    left: int = load_i64(root, 24)
    top: int = load_i64(root, 32)
    w: int = load_i64(root, 40)
    h: int = load_i64(root, 48)
    if x < left or x >= left + w or y < top or y >= top + h:
        return null()
    # check children (reverse order)
    child = load_ptr(root, 8)
    hit = null()
    while not ptr_is_null(child):
        child_hit = pcc_gui_control_hit_test(child, x, y)
        if not ptr_is_null(child_hit):
            hit = child_hit
        child = load_ptr(child, 16)
    if not ptr_is_null(hit):
        return hit
    return root


@c_abi_typed_export("pcc_gui_control_set_focus", "i32", ("ptr", "i32"))
def pcc_gui_control_set_focus(control, focused: int) -> int:
    if ptr_is_null(control):
        return -1
    state: int = load_i32(control, 56)
    if focused != 0:
        store_i32(control, 56, state | 1)
    else:
        store_i32(control, 56, state & ~1)
    return 0


@c_abi_typed_export("pcc_gui_control_focused", "i32", ("ptr",))
def pcc_gui_control_focused(control) -> int:
    if ptr_is_null(control):
        return 0
    return load_i32(control, 56) & 1


@c_abi_typed_export("pcc_gui_control_route_event", "i32", ("ptr", "i32", "i64", "i64"))
def pcc_gui_control_route_event(control, event: int, x: int, y: int) -> int:
    """Route an event to the hit control (for pointer events) and its
    ancestor chain.  Returns the control id (pointer value) that handled it,
    or 0 if unhandled.  A control "handles" when its state bit 2 is set."""
    if ptr_is_null(control):
        return 0
    current = control
    while not ptr_is_null(current):
        state: int = load_i32(current, 56)
        if (state & 2) != 0:
            return current
        current = load_ptr(current, 0)
    return 0
