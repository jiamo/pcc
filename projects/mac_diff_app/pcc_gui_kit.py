"""Compatibility declarations for the canonical runtime GUI kernel.

This project-local module deliberately contains no tree implementation.  The
single production owner is ``pcc/py_runtime/py/pcc_gui_kit.py`` and its C ABI
symbols are supplied by the production runtime archive.  Keeping this thin
module lets older single-file examples continue to use ``import pcc_gui_kit``
without creating a divergent second kernel.
"""

from pcc.extern import c_int32, c_int64, c_ptr, c_void, extern


pcc_kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
pcc_kit_is_valid = extern("pcc_kit_is_valid", (c_int64,), c_int32)
pcc_kit_live_nodes = extern("pcc_kit_live_nodes", (), c_int64)
pcc_kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
pcc_kit_detach = extern("pcc_kit_detach", (c_int64,), c_int32)
pcc_kit_append_child = extern("pcc_kit_append_child", (c_int64, c_int64), c_int32)
pcc_kit_insert_before = extern(
    "pcc_kit_insert_before", (c_int64, c_int64, c_int64), c_int32
)
pcc_kit_reorder = extern("pcc_kit_reorder", (c_int64, c_int64, c_int64), c_int32)
pcc_kit_destroy_subtree = extern("pcc_kit_destroy_subtree", (c_int64,), c_int64)
pcc_kit_set_removal_hook = extern("pcc_kit_set_removal_hook", (c_ptr,), c_void)
pcc_kit_rect = extern(
    "pcc_kit_rect",
    (c_int64, c_int64, c_int64, c_int64, c_int64, c_int32),
    c_void,
)
pcc_kit_text = extern(
    "pcc_kit_text",
    (c_int64, c_int64, c_int64, c_ptr, c_int64, c_int64, c_int32),
    c_void,
)
pcc_kit_visible = extern("pcc_kit_visible", (c_int64, c_int32), c_void)
pcc_kit_layout = extern("pcc_kit_layout", (c_int64, c_int32), c_void)
pcc_kit_dock = extern("pcc_kit_dock", (c_int64, c_int32), c_void)
pcc_kit_padding = extern(
    "pcc_kit_padding", (c_int64, c_int64, c_int64, c_int64, c_int64), c_void
)
pcc_kit_gap = extern("pcc_kit_gap", (c_int64, c_int64), c_void)
pcc_kit_clip_children = extern("pcc_kit_clip_children", (c_int64, c_int32), c_void)
pcc_kit_scroll_container = extern(
    "pcc_kit_scroll_container", (c_int64, c_int32), c_void
)
pcc_kit_scroll_max = extern("pcc_kit_scroll_max", (c_int64,), c_int64)
pcc_kit_scroll = extern("pcc_kit_scroll", (c_int64, c_int64), c_int64)
pcc_kit_scroll_by = extern("pcc_kit_scroll_by", (c_int64, c_int64), c_int64)
pcc_kit_layout_tree = extern(
    "pcc_kit_layout_tree", (c_int64, c_int64, c_int64), c_void
)
pcc_kit_handler = extern("pcc_kit_handler", (c_int64, c_int32), c_void)
pcc_kit_key_handler = extern("pcc_kit_key_handler", (c_int64, c_int32), c_void)
pcc_kit_focus = extern("pcc_kit_focus", (c_int64,), c_void)
pcc_kit_focused = extern("pcc_kit_focused", (c_int64,), c_int32)
pcc_kit_key_event = extern("pcc_kit_key_event", (c_int64, c_int64), c_int64)
pcc_kit_wrap = extern("pcc_kit_wrap", (c_int64, c_int32), c_void)
pcc_kit_lerp = extern("pcc_kit_lerp", (c_int64, c_int64, c_int64), c_int64)
pcc_kit_hit = extern("pcc_kit_hit", (c_int64, c_int64, c_int64), c_int64)
pcc_kit_hit_path_v1 = extern(
    "pcc_kit_hit_path_v1", (c_int64, c_int64, c_int64, c_ptr, c_int64), c_int64
)
pcc_kit_route_event_v2 = extern(
    "pcc_kit_route_event_v2",
    (c_int64, c_int64, c_int64, c_int64, c_ptr, c_int64),
    c_int64,
)
pcc_kit_route_event = extern(
    "pcc_kit_route_event", (c_int64, c_int64, c_int64, c_int64), c_int64
)
pcc_kit_hover = extern(
    "pcc_kit_hover", (c_int64, c_int64, c_int64, c_int32), c_int64
)
pcc_kit_hovered = extern("pcc_kit_hovered", (c_int64,), c_int32)
pcc_kit_render = extern(
    "pcc_kit_render", (c_int64, c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_void
)

_geometry_get = extern("pcc_kit_geometry_get", (c_int64, c_int64), c_int64)
_geometry_set = extern(
    "pcc_kit_geometry_set", (c_int64, c_int64, c_int64), c_void
)


def _n8(node_id: int, offset: int) -> int:
    return _geometry_get(node_id, offset)


def _s8(node_id: int, offset: int, value: int) -> None:
    _geometry_set(node_id, offset, value)
