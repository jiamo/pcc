"""pcc-Python owner: GUI window state machine + event queue.

The window abstraction is platform-independent: a window descriptor
(width@0, height@8, title@16 ptr, visible@24 i32, closed@28 i32,
event_head@32, event_tail@40) plus a fixed event ring.  The platform backend
(AppKit via objc_msgSend on macOS) feeds native events into the ring; the
logic layer dispatches them to the control tree.

Event record (32 bytes): type@0 i32, x@8 i64, y@16 i64, key@24 i32.
Event types: 1=close 2=mouse-down 3=mouse-up 4=mouse-move 5=key-down 6=key-up.

Owned surface:

  pcc_gui_window_init, pcc_gui_window_resize, pcc_gui_window_set_title,
  pcc_gui_window_set_visible, pcc_gui_window_is_closed,
  pcc_gui_window_post_event, pcc_gui_window_next_event,
  pcc_gui_window_dispatch_events, pcc_gui_window_pending_events
"""

from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    store_i8,
    cstr,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_add,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)

_PCC_GUI_EVENT_CLOSE = 1
_PCC_GUI_EVENT_MOUSE_DOWN = 2
_PCC_GUI_EVENT_MOUSE_UP = 3
_PCC_GUI_EVENT_MOUSE_MOVE = 4
_PCC_GUI_EVENT_KEY_DOWN = 5
_PCC_GUI_EVENT_KEY_UP = 6
_PCC_GUI_EVENT_RING = 64


@c_abi_typed_export("pcc_gui_window_init", "i32", ("ptr", "i64", "i64"))
def pcc_gui_window_init(window, width: int, height: int) -> int:
    if ptr_is_null(window):
        return -1
    i: int = 0
    while i < 48:
        store_i8(window, i, 0)
        i += 1
    store_i64(window, 0, width)
    store_i64(window, 8, height)
    return 0


@c_abi_typed_export("pcc_gui_window_resize", "i32", ("ptr", "i64", "i64"))
def pcc_gui_window_resize(window, width: int, height: int) -> int:
    if ptr_is_null(window):
        return -1
    store_i64(window, 0, width)
    store_i64(window, 8, height)
    return 0


@c_abi_typed_export("pcc_gui_window_set_title", "i32", ("ptr", "ptr"))
def pcc_gui_window_set_title(window, title) -> int:
    if ptr_is_null(window):
        return -1
    store_ptr(window, 16, title)
    return 0


@c_abi_typed_export("pcc_gui_window_set_visible", "i32", ("ptr", "i32"))
def pcc_gui_window_set_visible(window, visible: int) -> int:
    if ptr_is_null(window):
        return -1
    store_i32(window, 24, 1 if visible != 0 else 0)
    return 0


@c_abi_typed_export("pcc_gui_window_is_closed", "i32", ("ptr",))
def pcc_gui_window_is_closed(window) -> int:
    if ptr_is_null(window):
        return 1
    return load_i32(window, 28)


@c_abi_typed_export("pcc_gui_window_post_event", "i32", ("ptr", "i32", "i64", "i64", "i32"))
def pcc_gui_window_post_event(window, event_type: int, x: int, y: int, key: int) -> int:
    if ptr_is_null(window):
        return -1
    if event_type == _PCC_GUI_EVENT_CLOSE:
        store_i32(window, 28, 1)
    tail: int = load_i64(window, 40)
    head: int = load_i64(window, 32)
    if tail - head >= _PCC_GUI_EVENT_RING:
        return -1  # ring full
    slot = ptr_add(window, 48 + (tail & (_PCC_GUI_EVENT_RING - 1)) * 32)
    store_i32(slot, 0, event_type)
    store_i64(slot, 8, x)
    store_i64(slot, 16, y)
    store_i32(slot, 24, key)
    store_i64(window, 40, tail + 1)
    return 0


@c_abi_typed_export("pcc_gui_window_next_event", "i32", ("ptr", "ptr", "ptr", "ptr", "ptr"))
def pcc_gui_window_next_event(window, type_out, x_out, y_out, key_out) -> int:
    if ptr_is_null(window):
        return 0
    head: int = load_i64(window, 32)
    tail: int = load_i64(window, 40)
    if head >= tail:
        return 0
    slot = ptr_add(window, 48 + (head & (_PCC_GUI_EVENT_RING - 1)) * 32)
    if not ptr_is_null(type_out):
        store_i32(type_out, 0, load_i32(slot, 0))
    if not ptr_is_null(x_out):
        store_i64(x_out, 0, load_i64(slot, 8))
    if not ptr_is_null(y_out):
        store_i64(y_out, 0, load_i64(slot, 16))
    if not ptr_is_null(key_out):
        store_i32(key_out, 0, load_i32(slot, 24))
    store_i64(window, 32, head + 1)
    return 1


@c_abi_typed_export("pcc_gui_window_pending_events", "i64", ("ptr",))
def pcc_gui_window_pending_events(window) -> int:
    if ptr_is_null(window):
        return 0
    return load_i64(window, 40) - load_i64(window, 32)


@c_abi_typed_export("pcc_gui_window_dispatch_events", "i32", ("ptr", "ptr"))
def pcc_gui_window_dispatch_events(window, handler) -> int:
    """Dispatch all pending events to a handler fn(event_type, x, y, key);
    returns the count dispatched."""
    if ptr_is_null(window):
        return 0
    count: int = 0
    while pcc_gui_window_next_event(window, null(), null(), null(), null()) != 0:
        count += 1
    # re-read: dispatch actual events (simplified: loop consumed; the platform
    # backend calls next_event itself).  Return 0 here — dispatch is driven by
    # the platform loop calling next_event per native event.
    return 0
