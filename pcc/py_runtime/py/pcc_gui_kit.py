"""Canonical pcc-Python composition-tree GUI kernel.

The kernel owns a bounded, reclaimable render tree.  A node id is a stable
generation/index pair: the first generation intentionally has generation zero
so legacy callers still observe ids 0, 1, 2, ...; a recycled slot receives a
different id and stale ids are rejected.

Node record (208 bytes):
  parent@0 first_child@8 next@16 prev@24
  x@32 y@40 w@48 h@56 measured_w@64 measured_h@72
  visible@80(i32) layout@84(i32) type@88(i32) color@92(i32)
  scroll@96 data_or_text_len@104 flags_or_font@112 text_ptr@120
  generation@128 alive@136(i32) dock@140(i32) free_next@144
  padding_l@152 padding_t@160 padding_r@168 padding_b@176 gap@184
  clip_children@192(i32) event_flags@200(i64)

Node types: 0=container, 1=rect leaf, 2=text leaf, 3=scroll container.
Layouts: 0=stack-v, 1=stack-h, 2=dock.
Dock sides: 0=none, 1=left, 2=top, 3=right, 4=bottom, 5=fill.

``pcc_kit_route_event_v2`` only reports the complete leaf-to-root hit path.
It never dispatches component callbacks.  ``pcc_kit_route_event`` is the
explicit legacy compatibility wrapper that retains kernel-handler bubbling.
"""

from pcc.extern import c_abi_typed_export
from pcc.unsafe import (
    calloc,
    call_i64_i64,
    define_global_i64,
    free,
    global_addr,
    int_to_ptr,
    load_i32,
    load_i64,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
)


NODE_SIZE = 208
DOCK_NONE = 0
DOCK_LEFT = 1
DOCK_TOP = 2
DOCK_RIGHT = 3
DOCK_BOTTOM = 4
DOCK_FILL = 5

define_global_i64("pcc_kit_pool", 0)
define_global_i64("pcc_kit_count", 0)
define_global_i64("pcc_kit_live_count", 0)
define_global_i64("pcc_kit_cap", 0)
define_global_i64("pcc_kit_free_head", -1)
define_global_i64("pcc_kit_focus_node", -1)
define_global_i64("pcc_kit_hover_node", -1)
define_global_i64("pcc_kit_removal_hook", 0)


def _slot(node_id: int) -> int:
    return node_id & 0xFFFFFFFF


def _generation(node_id: int) -> int:
    return (node_id >> 32) & 0x7FFFFFFF


def _make_id(idx: int) -> int:
    gen = load_i64(int_to_ptr(_slot_addr(idx)), 128)
    return (gen << 32) | idx


def _slot_addr(idx: int) -> int:
    return load_i64(global_addr("pcc_kit_pool"), 0) + idx * NODE_SIZE


def _node_addr(node_id: int) -> int:
    """Internal compatibility helper; raw first-generation indices work."""
    return _slot_addr(_slot(node_id))


def _n8(node_id: int, off: int) -> int:
    return load_i64(int_to_ptr(_node_addr(node_id)), off)


def _n4(node_id: int, off: int) -> int:
    return load_i32(int_to_ptr(_node_addr(node_id)), off)


def _s8(node_id: int, off: int, value: int) -> None:
    store_i64(int_to_ptr(_node_addr(node_id)), off, value)


def _s4(node_id: int, off: int, value: int) -> None:
    store_i32(int_to_ptr(_node_addr(node_id)), off, value)


def _valid(node_id: int) -> int:
    if node_id < 0:
        return 0
    idx = _slot(node_id)
    count = load_i64(global_addr("pcc_kit_count"), 0)
    if idx < 0 or idx >= count:
        return 0
    if load_i32(int_to_ptr(_slot_addr(idx)), 136) == 0:
        return 0
    if load_i64(int_to_ptr(_slot_addr(idx)), 128) != _generation(node_id):
        return 0
    return 1


def _reset_slot(node_id: int, parent: int) -> None:
    _s8(node_id, 0, parent)
    _s8(node_id, 8, -1)
    _s8(node_id, 16, -1)
    _s8(node_id, 24, -1)
    _s8(node_id, 32, 0)
    _s8(node_id, 40, 0)
    _s8(node_id, 48, 0)
    _s8(node_id, 56, 0)
    _s8(node_id, 64, 0)
    _s8(node_id, 72, 0)
    _s4(node_id, 80, 1)
    _s4(node_id, 84, 0)
    _s4(node_id, 88, 0)
    _s4(node_id, 92, 0xFFFFFFFF)
    _s8(node_id, 96, 0)
    _s8(node_id, 104, 0)
    _s8(node_id, 112, 0)
    _s8(node_id, 120, 0)
    _s4(node_id, 136, 1)
    _s4(node_id, 140, DOCK_NONE)
    _s8(node_id, 144, -1)
    _s8(node_id, 152, 0)
    _s8(node_id, 160, 0)
    _s8(node_id, 168, 0)
    _s8(node_id, 176, 0)
    _s8(node_id, 184, 0)
    _s4(node_id, 192, 0)
    _s8(node_id, 200, 0)


def _last_child(parent: int) -> int:
    child = _n8(parent, 8)
    if child < 0:
        return -1
    while _n8(child, 16) >= 0:
        child = _n8(child, 16)
    return child


def _would_cycle(parent: int, child: int) -> int:
    cur = parent
    while cur >= 0:
        if cur == child:
            return 1
        if _valid(cur) == 0:
            return 1
        cur = _n8(cur, 0)
    return 0


@c_abi_typed_export("pcc_kit_init", "i32", ("i64",))
def pcc_kit_init(cap: int) -> int:
    if cap <= 0 or cap > 8192:
        return -1
    old_pool = load_i64(global_addr("pcc_kit_pool"), 0)
    if old_pool != 0:
        free(int_to_ptr(old_pool))
    pool = calloc(cap * NODE_SIZE, 1)
    if ptr_is_null(pool):
        store_i64(global_addr("pcc_kit_pool"), 0, 0)
        return -2
    store_i64(global_addr("pcc_kit_pool"), 0, ptr_to_int(pool))
    store_i64(global_addr("pcc_kit_cap"), 0, cap)
    store_i64(global_addr("pcc_kit_count"), 0, 0)
    store_i64(global_addr("pcc_kit_live_count"), 0, 0)
    store_i64(global_addr("pcc_kit_free_head"), 0, -1)
    store_i64(global_addr("pcc_kit_focus_node"), 0, -1)
    store_i64(global_addr("pcc_kit_hover_node"), 0, -1)
    store_i64(global_addr("pcc_kit_removal_hook"), 0, 0)
    return 0


@c_abi_typed_export("pcc_kit_is_valid", "i32", ("i64",))
def pcc_kit_is_valid(node_id: int) -> int:
    return _valid(node_id)


@c_abi_typed_export("pcc_kit_live_nodes", "i64", ())
def pcc_kit_live_nodes() -> int:
    return load_i64(global_addr("pcc_kit_live_count"), 0)


@c_abi_typed_export("pcc_kit_available_nodes", "i64", ())
def pcc_kit_available_nodes() -> int:
    """Return the number of slots that can be allocated without mutation."""
    cap = load_i64(global_addr("pcc_kit_cap"), 0)
    live = load_i64(global_addr("pcc_kit_live_count"), 0)
    if cap <= live:
        return 0
    return cap - live


@c_abi_typed_export("pcc_kit_parent", "i64", ("i64",))
def pcc_kit_parent(node_id: int) -> int:
    if _valid(node_id) == 0:
        return -1
    return _n8(node_id, 0)


@c_abi_typed_export("pcc_kit_first_child", "i64", ("i64",))
def pcc_kit_first_child(node_id: int) -> int:
    if _valid(node_id) == 0:
        return -1
    return _n8(node_id, 8)


@c_abi_typed_export("pcc_kit_next_sibling", "i64", ("i64",))
def pcc_kit_next_sibling(node_id: int) -> int:
    if _valid(node_id) == 0:
        return -1
    return _n8(node_id, 16)


@c_abi_typed_export("pcc_kit_geometry_get", "i64", ("i64", "i64"))
def pcc_kit_geometry_get(node_id: int, offset: int) -> int:
    """Legacy bridge for the four arranged geometry slots only."""
    if _valid(node_id) == 0:
        return 0
    if offset != 32 and offset != 40 and offset != 48 and offset != 56:
        return 0
    return _n8(node_id, offset)


@c_abi_typed_export("pcc_kit_geometry_set", "void", ("i64", "i64", "i64"))
def pcc_kit_geometry_set(node_id: int, offset: int, value: int) -> None:
    """Legacy bridge for requested x/y/w/h; structural fields stay private."""
    if _valid(node_id) == 0:
        return
    if offset == 32 or offset == 40 or offset == 48 or offset == 56:
        _s8(node_id, offset, value)


@c_abi_typed_export("pcc_kit_node_kind", "i32", ("i64", "i32"))
def pcc_kit_node_kind(node_id: int, kind: int) -> int:
    if _valid(node_id) == 0 or kind < 0 or kind > 3:
        return -1
    _s4(node_id, 88, kind)
    if kind == 3:
        _s4(node_id, 192, 1)
    return 0


@c_abi_typed_export("pcc_kit_create", "i64", ("i64",))
def pcc_kit_create(parent: int) -> int:
    if parent >= 0 and _valid(parent) == 0:
        return -2
    free_head = load_i64(global_addr("pcc_kit_free_head"), 0)
    if free_head >= 0:
        idx = free_head
        store_i64(
            global_addr("pcc_kit_free_head"),
            0,
            load_i64(int_to_ptr(_slot_addr(idx)), 144),
        )
    else:
        count = load_i64(global_addr("pcc_kit_count"), 0)
        cap = load_i64(global_addr("pcc_kit_cap"), 0)
        if count >= cap:
            return -1
        idx = count
        store_i64(global_addr("pcc_kit_count"), 0, count + 1)
    node_id = _make_id(idx)
    _reset_slot(node_id, -1)
    store_i64(
        global_addr("pcc_kit_live_count"),
        0,
        load_i64(global_addr("pcc_kit_live_count"), 0) + 1,
    )
    if parent >= 0:
        if pcc_kit_append_child(parent, node_id) != 0:
            _s4(node_id, 136, 0)
            return -3
    return node_id


@c_abi_typed_export("pcc_kit_detach", "i32", ("i64",))
def pcc_kit_detach(node_id: int) -> int:
    if _valid(node_id) == 0:
        return -1
    parent = _n8(node_id, 0)
    prev = _n8(node_id, 24)
    nxt = _n8(node_id, 16)
    if parent >= 0 and _valid(parent) != 0:
        if _n8(parent, 8) == node_id:
            _s8(parent, 8, nxt)
    if prev >= 0 and _valid(prev) != 0:
        _s8(prev, 16, nxt)
    if nxt >= 0 and _valid(nxt) != 0:
        _s8(nxt, 24, prev)
    _s8(node_id, 0, -1)
    _s8(node_id, 16, -1)
    _s8(node_id, 24, -1)
    return 0


@c_abi_typed_export("pcc_kit_append_child", "i32", ("i64", "i64"))
def pcc_kit_append_child(parent: int, child: int) -> int:
    if _valid(parent) == 0 or _valid(child) == 0 or parent == child:
        return -1
    if _would_cycle(parent, child) != 0:
        return -2
    pcc_kit_detach(child)
    last = _last_child(parent)
    _s8(child, 0, parent)
    if last < 0:
        _s8(parent, 8, child)
    else:
        _s8(last, 16, child)
        _s8(child, 24, last)
    return 0


@c_abi_typed_export("pcc_kit_insert_before", "i32", ("i64", "i64", "i64"))
def pcc_kit_insert_before(parent: int, child: int, before: int) -> int:
    if before < 0:
        return pcc_kit_append_child(parent, child)
    if _valid(parent) == 0 or _valid(child) == 0 or _valid(before) == 0:
        return -1
    if child == before or _n8(before, 0) != parent:
        return -2
    if _would_cycle(parent, child) != 0:
        return -3
    pcc_kit_detach(child)
    prev = _n8(before, 24)
    _s8(child, 0, parent)
    _s8(child, 16, before)
    _s8(child, 24, prev)
    _s8(before, 24, child)
    if prev >= 0:
        _s8(prev, 16, child)
    else:
        _s8(parent, 8, child)
    return 0


@c_abi_typed_export("pcc_kit_reorder", "i32", ("i64", "i64", "i64"))
def pcc_kit_reorder(parent: int, child: int, before: int) -> int:
    """Move child before sibling; before=-1 moves it to paint-order end."""
    return pcc_kit_insert_before(parent, child, before)


@c_abi_typed_export("pcc_kit_replace_children", "i32", ("i64", "ptr", "i64"))
def pcc_kit_replace_children(parent: int, ordered_nodes, count: int) -> int:
    """Atomically replace one parent's direct-child order.

    Validation is complete before the first link is changed.  Every supplied
    node must either already be a direct child of ``parent`` or be detached;
    this prevents one component commit from stealing another owner's node.
    Once validation succeeds the rewrite contains no allocation and no
    fallible operation.
    """
    if _valid(parent) == 0 or count < 0:
        return -1
    if count > 0 and ptr_is_null(ordered_nodes):
        return -2

    i = 0
    while i < count:
        node_id = load_i64(ordered_nodes, i * 8)
        if _valid(node_id) == 0 or node_id == parent:
            return -3
        old_parent = _n8(node_id, 0)
        if old_parent != -1 and old_parent != parent:
            return -4
        if _would_cycle(parent, node_id) != 0:
            return -5
        j = 0
        while j < i:
            if load_i64(ordered_nodes, j * 8) == node_id:
                return -6
            j = j + 1
        i = i + 1

    child = _n8(parent, 8)
    while child >= 0:
        nxt = _n8(child, 16)
        _s8(child, 0, -1)
        _s8(child, 16, -1)
        _s8(child, 24, -1)
        child = nxt

    if count == 0:
        _s8(parent, 8, -1)
        return 0

    first = load_i64(ordered_nodes, 0)
    _s8(parent, 8, first)
    i = 0
    while i < count:
        node_id = load_i64(ordered_nodes, i * 8)
        prev = -1
        nxt = -1
        if i > 0:
            prev = load_i64(ordered_nodes, (i - 1) * 8)
        if i + 1 < count:
            nxt = load_i64(ordered_nodes, (i + 1) * 8)
        _s8(node_id, 0, parent)
        _s8(node_id, 24, prev)
        _s8(node_id, 16, nxt)
        i = i + 1
    return 0


@c_abi_typed_export("pcc_kit_set_removal_hook", "void", ("ptr",))
def pcc_kit_set_removal_hook(hook) -> None:
    store_i64(global_addr("pcc_kit_removal_hook"), 0, ptr_to_int(hook))


def _destroy_subtree(node_id: int) -> int:
    if _valid(node_id) == 0:
        return -1
    child = _n8(node_id, 8)
    removed = 1
    while child >= 0:
        nxt = _n8(child, 16)
        child_removed = _destroy_subtree(child)
        if child_removed > 0:
            removed = removed + child_removed
        child = nxt
    pcc_kit_detach(node_id)
    if load_i64(global_addr("pcc_kit_focus_node"), 0) == node_id:
        store_i64(global_addr("pcc_kit_focus_node"), 0, -1)
    if load_i64(global_addr("pcc_kit_hover_node"), 0) == node_id:
        store_i64(global_addr("pcc_kit_hover_node"), 0, -1)
    hook = load_i64(global_addr("pcc_kit_removal_hook"), 0)
    if hook != 0:
        call_i64_i64(int_to_ptr(hook), node_id)
    idx = _slot(node_id)
    gen = _n8(node_id, 128) + 1
    if gen > 0x7FFFFFFF:
        gen = 1
    _s4(node_id, 136, 0)
    _s8(node_id, 128, gen)
    _s8(node_id, 8, -1)
    _s8(node_id, 16, -1)
    _s8(node_id, 24, -1)
    _s8(node_id, 144, load_i64(global_addr("pcc_kit_free_head"), 0))
    store_i64(global_addr("pcc_kit_free_head"), 0, idx)
    store_i64(
        global_addr("pcc_kit_live_count"),
        0,
        load_i64(global_addr("pcc_kit_live_count"), 0) - 1,
    )
    return removed


@c_abi_typed_export("pcc_kit_destroy_subtree", "i64", ("i64",))
def pcc_kit_destroy_subtree(node_id: int) -> int:
    """Destroy in deterministic child-before-parent order and return count."""
    return _destroy_subtree(node_id)


@c_abi_typed_export("pcc_kit_rect", "void", ("i64", "i64", "i64", "i64", "i64", "i32"))
def pcc_kit_rect(node_id: int, x: int, y: int, w: int, h: int, color: int) -> None:
    if _valid(node_id) == 0:
        return
    _s8(node_id, 32, x)
    _s8(node_id, 40, y)
    _s8(node_id, 48, w)
    _s8(node_id, 56, h)
    _s4(node_id, 92, color)


@c_abi_typed_export("pcc_kit_text", "void", ("i64", "i64", "i64", "ptr", "i64", "i64", "i32"))
def pcc_kit_text(node_id: int, x: int, y: int, text_ptr, tlen: int, font: int, color: int) -> None:
    if _valid(node_id) == 0:
        return
    _s8(node_id, 32, x)
    _s8(node_id, 40, y)
    _s8(node_id, 48, tlen * 7)
    _s8(node_id, 56, font)
    _s4(node_id, 88, 2)
    _s4(node_id, 92, color)
    _s8(node_id, 104, tlen)
    _s8(node_id, 112, font)
    _s8(node_id, 120, ptr_to_int(text_ptr))


@c_abi_typed_export("pcc_kit_visible", "void", ("i64", "i32"))
def pcc_kit_visible(node_id: int, value: int) -> None:
    if _valid(node_id) != 0:
        _s4(node_id, 80, value)


@c_abi_typed_export("pcc_kit_layout", "void", ("i64", "i32"))
def pcc_kit_layout(node_id: int, layout: int) -> None:
    if _valid(node_id) != 0 and layout >= 0 and layout <= 2:
        _s4(node_id, 84, layout)


@c_abi_typed_export("pcc_kit_dock", "void", ("i64", "i32"))
def pcc_kit_dock(node_id: int, side: int) -> None:
    if _valid(node_id) != 0 and side >= DOCK_NONE and side <= DOCK_FILL:
        _s4(node_id, 140, side)


@c_abi_typed_export("pcc_kit_padding", "void", ("i64", "i64", "i64", "i64", "i64"))
def pcc_kit_padding(node_id: int, left: int, top: int, right: int, bottom: int) -> None:
    if _valid(node_id) == 0:
        return
    _s8(node_id, 152, max(0, left))
    _s8(node_id, 160, max(0, top))
    _s8(node_id, 168, max(0, right))
    _s8(node_id, 176, max(0, bottom))


@c_abi_typed_export("pcc_kit_gap", "void", ("i64", "i64"))
def pcc_kit_gap(node_id: int, gap: int) -> None:
    if _valid(node_id) != 0:
        _s8(node_id, 184, max(0, gap))


@c_abi_typed_export("pcc_kit_style_set", "i32", ("i64", "i32", "i64"))
def pcc_kit_style_set(node_id: int, field: int, value: int) -> int:
    """Apply one bounded scalar style field without exposing raw node storage."""
    if _valid(node_id) == 0:
        return -1
    if field == 1 or field == 2:
        _s4(node_id, 92, value)
    elif field == 3:
        _s8(node_id, 112, value)
    elif field == 4:
        _s8(node_id, 48, max(0, value))
    elif field == 5:
        _s8(node_id, 56, max(0, value))
    elif field == 6:
        pcc_kit_padding(node_id, value, value, value, value)
    elif field == 7:
        pcc_kit_gap(node_id, value)
    elif field == 8:
        _s8(node_id, 32, value)
    elif field == 9:
        _s8(node_id, 40, value)
    else:
        return -1
    return 0


@c_abi_typed_export("pcc_kit_style_get", "i64", ("i64", "i32"))
def pcc_kit_style_get(node_id: int, field: int) -> int:
    if _valid(node_id) == 0:
        return 0
    if field == 1 or field == 2:
        return _n4(node_id, 92)
    if field == 3:
        return _n8(node_id, 112)
    if field == 4:
        return _n8(node_id, 48)
    if field == 5:
        return _n8(node_id, 56)
    if field == 6:
        return _n8(node_id, 152)
    if field == 7:
        return _n8(node_id, 184)
    if field == 8:
        return _n8(node_id, 32)
    if field == 9:
        return _n8(node_id, 40)
    return 0


@c_abi_typed_export("pcc_kit_clip_children", "void", ("i64", "i32"))
def pcc_kit_clip_children(node_id: int, on: int) -> None:
    if _valid(node_id) != 0:
        _s4(node_id, 192, 1 if on != 0 else 0)


@c_abi_typed_export("pcc_kit_scroll_container", "void", ("i64", "i32"))
def pcc_kit_scroll_container(node_id: int, on: int) -> None:
    if _valid(node_id) != 0:
        _s4(node_id, 88, 3 if on != 0 else 0)
        _s4(node_id, 192, 1 if on != 0 else _n4(node_id, 192))


def _scroll_max(node_id: int) -> int:
    content = _n8(node_id, 72) - _n8(node_id, 160) - _n8(node_id, 176)
    viewport = _n8(node_id, 56) - _n8(node_id, 160) - _n8(node_id, 176)
    result = content - viewport
    if result < 0:
        return 0
    return result


@c_abi_typed_export("pcc_kit_scroll_max", "i64", ("i64",))
def pcc_kit_scroll_max(node_id: int) -> int:
    if _valid(node_id) == 0:
        return 0
    return _scroll_max(node_id)


@c_abi_typed_export("pcc_kit_scroll", "i64", ("i64", "i64"))
def pcc_kit_scroll(node_id: int, offset: int) -> int:
    if _valid(node_id) == 0:
        return -1
    limit = _scroll_max(node_id)
    if offset < 0:
        offset = 0
    if offset > limit:
        offset = limit
    _s8(node_id, 96, offset)
    return offset


@c_abi_typed_export("pcc_kit_scroll_by", "i64", ("i64", "i64"))
def pcc_kit_scroll_by(node_id: int, delta: int) -> int:
    if _valid(node_id) == 0:
        return -1
    return pcc_kit_scroll(node_id, _n8(node_id, 96) + delta)


def _visible_children(node_id: int) -> int:
    count = 0
    child = _n8(node_id, 8)
    while child >= 0:
        if _valid(child) != 0 and _n4(child, 80) != 0:
            count = count + 1
        child = _n8(child, 16)
    return count


def _measure(node_id: int, avail_w: int, avail_h: int) -> None:
    if _valid(node_id) == 0:
        return
    node_type = _n4(node_id, 88)
    if node_type == 1 or node_type == 2:
        _s8(node_id, 64, _n8(node_id, 48))
        _s8(node_id, 72, _n8(node_id, 56))
        return
    layout = _n4(node_id, 84)
    child = _n8(node_id, 8)
    mw = 0
    mh = 0
    gap = _n8(node_id, 184)
    seen = 0
    while child >= 0:
        if _valid(child) != 0 and _n4(child, 80) != 0:
            _measure(child, avail_w, avail_h)
            cw = _n8(child, 64)
            ch = _n8(child, 72)
            if cw == 0:
                cw = _n8(child, 48)
            if ch == 0:
                ch = _n8(child, 56)
            if layout == 0 or node_type == 3:
                if seen > 0:
                    mh = mh + gap
                mh = mh + ch
                if cw > mw:
                    mw = cw
            elif layout == 1:
                if seen > 0:
                    mw = mw + gap
                mw = mw + cw
                if ch > mh:
                    mh = ch
            else:
                side = _n4(child, 140)
                if side == DOCK_LEFT or side == DOCK_RIGHT:
                    mw = mw + cw
                    if ch > mh:
                        mh = ch
                elif side == DOCK_TOP or side == DOCK_BOTTOM:
                    mh = mh + ch
                    if cw > mw:
                        mw = cw
                else:
                    if cw > mw:
                        mw = cw
                    if ch > mh:
                        mh = ch
                if seen > 0:
                    if side == DOCK_LEFT or side == DOCK_RIGHT:
                        mw = mw + gap
                    elif side == DOCK_TOP or side == DOCK_BOTTOM:
                        mh = mh + gap
            seen = seen + 1
        child = _n8(child, 16)
    mw = mw + _n8(node_id, 152) + _n8(node_id, 168)
    mh = mh + _n8(node_id, 160) + _n8(node_id, 176)
    if _n8(node_id, 48) > mw:
        mw = _n8(node_id, 48)
    if _n8(node_id, 56) > mh:
        mh = _n8(node_id, 56)
    _s8(node_id, 64, mw)
    _s8(node_id, 72, mh)


def _arrange_stack(node_id: int, x: int, y: int, w: int, h: int, horizontal: int, scroll: int) -> None:
    left = _n8(node_id, 152)
    top = _n8(node_id, 160)
    right = _n8(node_id, 168)
    bottom = _n8(node_id, 176)
    gap = _n8(node_id, 184)
    ix = x + left
    iy = y + top
    iw = max(0, w - left - right)
    ih = max(0, h - top - bottom)
    cx = ix
    cy = iy - scroll
    seen = 0
    child = _n8(node_id, 8)
    while child >= 0:
        if _valid(child) != 0 and _n4(child, 80) != 0:
            if seen > 0:
                if horizontal != 0:
                    cx = cx + gap
                else:
                    cy = cy + gap
            cw = _n8(child, 48)
            ch = _n8(child, 56)
            if cw == 0:
                cw = _n8(child, 64)
            if ch == 0:
                ch = _n8(child, 72)
            if horizontal != 0 and ch == 0:
                ch = ih
            if horizontal == 0 and cw == 0:
                cw = iw
            _arrange(child, cx, cy, cw, ch)
            if horizontal != 0:
                cx = cx + cw
            else:
                cy = cy + ch
            seen = seen + 1
        child = _n8(child, 16)


def _arrange_dock(node_id: int, x: int, y: int, w: int, h: int) -> None:
    rx = x + _n8(node_id, 152)
    ry = y + _n8(node_id, 160)
    rw = max(0, w - _n8(node_id, 152) - _n8(node_id, 168))
    rh = max(0, h - _n8(node_id, 160) - _n8(node_id, 176))
    gap = _n8(node_id, 184)
    child = _n8(node_id, 8)
    while child >= 0:
        if _valid(child) != 0 and _n4(child, 80) != 0:
            side = _n4(child, 140)
            cw = _n8(child, 48)
            ch = _n8(child, 56)
            if cw == 0:
                cw = _n8(child, 64)
            if ch == 0:
                ch = _n8(child, 72)
            if side == DOCK_LEFT:
                cw = min(cw, rw)
                _arrange(child, rx, ry, cw, rh)
                rx = rx + cw + gap
                rw = max(0, rw - cw - gap)
            elif side == DOCK_TOP:
                ch = min(ch, rh)
                _arrange(child, rx, ry, rw, ch)
                ry = ry + ch + gap
                rh = max(0, rh - ch - gap)
            elif side == DOCK_RIGHT:
                cw = min(cw, rw)
                _arrange(child, rx + rw - cw, ry, cw, rh)
                rw = max(0, rw - cw - gap)
            elif side == DOCK_BOTTOM:
                ch = min(ch, rh)
                _arrange(child, rx, ry + rh - ch, rw, ch)
                rh = max(0, rh - ch - gap)
            elif side == DOCK_FILL:
                _arrange(child, rx, ry, rw, rh)
            else:
                _arrange(child, rx, ry, cw, ch)
        child = _n8(child, 16)


def _arrange(node_id: int, x: int, y: int, w: int, h: int) -> None:
    if _valid(node_id) == 0:
        return
    _s8(node_id, 32, x)
    _s8(node_id, 40, y)
    _s8(node_id, 48, w)
    _s8(node_id, 56, h)
    node_type = _n4(node_id, 88)
    if node_type == 1 or node_type == 2:
        return
    if node_type == 3:
        limit = _scroll_max(node_id)
        scroll = _n8(node_id, 96)
        if scroll > limit:
            scroll = limit
            _s8(node_id, 96, scroll)
        _arrange_stack(node_id, x, y, w, h, 0, scroll)
        return
    layout = _n4(node_id, 84)
    if layout == 0:
        _arrange_stack(node_id, x, y, w, h, 0, 0)
    elif layout == 1:
        _arrange_stack(node_id, x, y, w, h, 1, 0)
    else:
        _arrange_dock(node_id, x, y, w, h)


@c_abi_typed_export("pcc_kit_layout_tree", "void", ("i64", "i64", "i64"))
def pcc_kit_layout_tree(node_id: int, w: int, h: int) -> None:
    if _valid(node_id) == 0:
        return
    _measure(node_id, w, h)
    _arrange(node_id, _n8(node_id, 32), _n8(node_id, 40), w, h)


@c_abi_typed_export("pcc_kit_handler", "void", ("i64", "i32"))
def pcc_kit_handler(node_id: int, on: int) -> None:
    if _valid(node_id) == 0:
        return
    flags = _n8(node_id, 200)
    _s8(node_id, 200, flags | 1 if on != 0 else flags & -2)


@c_abi_typed_export("pcc_kit_focus", "void", ("i64",))
def pcc_kit_focus(node_id: int) -> None:
    if node_id < 0 or _valid(node_id) != 0:
        store_i64(global_addr("pcc_kit_focus_node"), 0, node_id)


@c_abi_typed_export("pcc_kit_key_handler", "void", ("i64", "i32"))
def pcc_kit_key_handler(node_id: int, on: int) -> None:
    if _valid(node_id) == 0:
        return
    flags = _n8(node_id, 200)
    _s8(node_id, 200, flags | 2 if on != 0 else flags & -3)


@c_abi_typed_export("pcc_kit_focused", "i32", ("i64",))
def pcc_kit_focused(node_id: int) -> int:
    if _valid(node_id) != 0 and load_i64(global_addr("pcc_kit_focus_node"), 0) == node_id:
        return 1
    return 0


@c_abi_typed_export("pcc_kit_key_event", "i64", ("i64", "i64"))
def pcc_kit_key_event(key: int, etype: int) -> int:
    cur = load_i64(global_addr("pcc_kit_focus_node"), 0)
    while cur >= 0 and _valid(cur) != 0:
        if (_n8(cur, 200) & 2) != 0:
            return cur
        cur = _n8(cur, 0)
    return -1


@c_abi_typed_export("pcc_kit_wrap", "void", ("i64", "i32"))
def pcc_kit_wrap(node_id: int, on: int) -> None:
    if _valid(node_id) == 0:
        return
    flags = _n8(node_id, 112)
    _s8(node_id, 112, flags | 0x10000 if on != 0 else flags & -0x10001)


@c_abi_typed_export("pcc_kit_lerp", "i64", ("i64", "i64", "i64"))
def pcc_kit_lerp(a: int, b: int, t: int) -> int:
    if t <= 0:
        return a
    if t >= 1000:
        return b
    return a + (b - a) * t // 1000


def _contains(node_id: int, x: int, y: int) -> int:
    bx = _n8(node_id, 32)
    by = _n8(node_id, 40)
    return 1 if x >= bx and x < bx + _n8(node_id, 48) and y >= by and y < by + _n8(node_id, 56) else 0


@c_abi_typed_export("pcc_kit_hit", "i64", ("i64", "i64", "i64"))
def pcc_kit_hit(node_id: int, x: int, y: int) -> int:
    """Return the deepest topmost node using render/paint sibling order."""
    if _valid(node_id) == 0:
        return -1
    return _hit_with_clip(
        node_id,
        x,
        y,
        _n8(node_id, 32),
        _n8(node_id, 40),
        _n8(node_id, 48),
        _n8(node_id, 56),
    )


def _hit_with_clip(node_id: int, x: int, y: int, clip_x: int, clip_y: int, clip_w: int, clip_h: int) -> int:
    if _valid(node_id) == 0 or _n4(node_id, 80) == 0:
        return -1
    if x < clip_x or x >= clip_x + clip_w or y < clip_y or y >= clip_y + clip_h:
        return -1
    inside = _contains(node_id, x, y)
    node_type = _n4(node_id, 88)
    clips = _n4(node_id, 192) != 0 or node_type == 3
    if clips and inside == 0:
        return -1
    next_clip_x = clip_x
    next_clip_y = clip_y
    next_clip_w = clip_w
    next_clip_h = clip_h
    if clips:
        next_clip_x = _n8(node_id, 32)
        next_clip_y = _n8(node_id, 40)
        next_clip_w = _n8(node_id, 48)
        next_clip_h = _n8(node_id, 56)
    child = _last_child(node_id)
    while child >= 0:
        result = _hit_with_clip(
            child,
            x,
            y,
            next_clip_x,
            next_clip_y,
            next_clip_w,
            next_clip_h,
        )
        if result >= 0:
            return result
        child = _n8(child, 24)
    return node_id if inside != 0 else -1


@c_abi_typed_export("pcc_kit_hit_path_v1", "i64", ("i64", "i64", "i64", "ptr", "i64"))
def pcc_kit_hit_path_v1(root: int, x: int, y: int, path_out, capacity: int) -> int:
    """Write the complete leaf-to-root path; never return a partial path."""
    hit = pcc_kit_hit(root, x, y)
    if hit < 0:
        return 0
    needed = 0
    cur = hit
    while cur >= 0 and _valid(cur) != 0:
        needed = needed + 1
        cur = _n8(cur, 0)
    if capacity < needed or ptr_is_null(path_out):
        return -needed
    cur = hit
    i = 0
    while cur >= 0 and i < needed:
        store_i64(path_out, i * 8, cur)
        cur = _n8(cur, 0)
        i = i + 1
    return needed


@c_abi_typed_export("pcc_kit_route_event_v2", "i64", ("i64", "i64", "i64", "i64", "ptr", "i64"))
def pcc_kit_route_event_v2(root: int, x: int, y: int, etype: int, path_out, capacity: int) -> int:
    """Versioned routing surface: report path only; component layer dispatches."""
    return pcc_kit_hit_path_v1(root, x, y, path_out, capacity)


@c_abi_typed_export("pcc_kit_route_event", "i64", ("i64", "i64", "i64", "i64"))
def pcc_kit_route_event(root: int, x: int, y: int, etype: int) -> int:
    """Legacy compatibility wrapper retaining kernel flag-bit bubbling."""
    cur = pcc_kit_hit(root, x, y)
    while cur >= 0 and _valid(cur) != 0:
        if (_n8(cur, 200) & 1) != 0:
            return cur
        cur = _n8(cur, 0)
    return -1


@c_abi_typed_export("pcc_kit_hover", "i64", ("i64", "i64", "i64", "i32"))
def pcc_kit_hover(root: int, x: int, y: int, on: int) -> int:
    old = load_i64(global_addr("pcc_kit_hover_node"), 0)
    if old >= 0 and _valid(old) != 0:
        _s8(old, 200, _n8(old, 200) & -0x11)
    store_i64(global_addr("pcc_kit_hover_node"), 0, -1)
    if on == 0:
        return -1
    hit = pcc_kit_hit(root, x, y)
    if hit >= 0:
        _s8(hit, 200, _n8(hit, 200) | 0x10)
        store_i64(global_addr("pcc_kit_hover_node"), 0, hit)
    return hit


@c_abi_typed_export("pcc_kit_hovered", "i32", ("i64",))
def pcc_kit_hovered(node_id: int) -> int:
    if _valid(node_id) != 0 and load_i64(global_addr("pcc_kit_hover_node"), 0) == node_id:
        return 1
    return 0


def _intersect(ax: int, ay: int, aw: int, ah: int, bx: int, by: int, bw: int, bh: int, out) -> int:
    left = max(ax, bx)
    top = max(ay, by)
    right = min(ax + aw, bx + bw)
    bottom = min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0
    store_i64(out, 0, left)
    store_i64(out, 8, top)
    store_i64(out, 16, right - left)
    store_i64(out, 24, bottom - top)
    return 1


def _emit_rect_cmd(x: int, y: int, w: int, h: int, rects, colors, rn_out, color: int) -> None:
    rn = load_i64(rn_out, 0)
    store_i64(rects, rn * 32 + 0, x)
    store_i64(rects, rn * 32 + 8, y)
    store_i64(rects, rn * 32 + 16, w)
    store_i64(rects, rn * 32 + 24, h)
    store_i32(colors, rn * 4 + 0, (color >> 16) & 255)
    store_i32(colors, rn * 4 + 1, (color >> 8) & 255)
    store_i32(colors, rn * 4 + 2, color & 255)
    store_i32(colors, rn * 4 + 3, 255)
    store_i64(rn_out, 0, rn + 1)


def _emit_text_line(node_id: int, x: int, y: int, length: int, text_ptr: int, font: int, color: int, clip_x: int, clip_y: int, clip_w: int, clip_h: int, texts, tn_out) -> None:
    char_w = max(1, font // 2)
    start = 0
    end = length
    if x < clip_x:
        start = (clip_x - x + char_w - 1) // char_w
    if x + end * char_w > clip_x + clip_w:
        end = (clip_x + clip_w - x) // char_w
    if start < 0:
        start = 0
    if end > length:
        end = length
    # The 48-byte legacy command has no vertical scissor fields.  Retain a
    # partially overlapping line, clamp its origin to the active clip, and
    # trim horizontal glyphs; fully disjoint lines are suppressed.
    if end <= start or y + font <= clip_y or y >= clip_y + clip_h:
        return
    out_y = max(y, clip_y)
    tn = load_i64(tn_out, 0)
    store_i64(texts, tn * 48 + 0, x + start * char_w)
    store_i64(texts, tn * 48 + 8, out_y)
    store_i64(texts, tn * 48 + 16, end - start)
    store_i64(texts, tn * 48 + 24, font)
    store_i64(texts, tn * 48 + 32, color)
    store_i64(texts, tn * 48 + 40, text_ptr + start)
    store_i64(tn_out, 0, tn + 1)


def _kit_render(node_id: int, rects, colors, rn_out, texts, tn_out, clip_x: int, clip_y: int, clip_w: int, clip_h: int) -> None:
    if _valid(node_id) == 0 or _n4(node_id, 80) == 0:
        return
    node_type = _n4(node_id, 88)
    clips = _n4(node_id, 192) != 0 or node_type == 3
    scratch = stack_alloc(32)
    overlaps = _intersect(_n8(node_id, 32), _n8(node_id, 40), _n8(node_id, 48), _n8(node_id, 56), clip_x, clip_y, clip_w, clip_h, scratch)
    if overlaps == 0 and clips:
        return
    vx = 0
    vy = 0
    vw = 0
    vh = 0
    if overlaps != 0:
        vx = load_i64(scratch, 0)
        vy = load_i64(scratch, 8)
        vw = load_i64(scratch, 16)
        vh = load_i64(scratch, 24)
        color = _n4(node_id, 92)
        if color == -1:
            parent = _n8(node_id, 0)
            if parent >= 0 and _valid(parent) != 0:
                color = _n4(parent, 92)
        if (node_type == 0 or node_type == 1 or node_type == 3) and color != -1:
            _emit_rect_cmd(vx, vy, vw, vh, rects, colors, rn_out, color)
        if node_type == 2:
            flags = _n8(node_id, 112)
            tlen = _n8(node_id, 104)
            font = flags & 0xFFFF
            if color == -1:
                color = 0xFF333333
            text_ptr = _n8(node_id, 120)
            if (flags & 0x10000) != 0 and _n8(node_id, 48) > 0:
                per = _n8(node_id, 48) // max(1, font // 2)
                if per < 1:
                    per = 1
                line = 0
                lines = (tlen + per - 1) // per
                while line < lines:
                    line_len = min(per, tlen - line * per)
                    _emit_text_line(node_id, _n8(node_id, 32), _n8(node_id, 40) + line * font, line_len, text_ptr + line * per, font, color, clip_x, clip_y, clip_w, clip_h, texts, tn_out)
                    line = line + 1
            else:
                _emit_text_line(node_id, _n8(node_id, 32), _n8(node_id, 40), tlen, text_ptr, font, color, clip_x, clip_y, clip_w, clip_h, texts, tn_out)
    child_clip_x = clip_x
    child_clip_y = clip_y
    child_clip_w = clip_w
    child_clip_h = clip_h
    if clips:
        child_clip_x = vx
        child_clip_y = vy
        child_clip_w = vw
        child_clip_h = vh
    child = _n8(node_id, 8)
    while child >= 0:
        _kit_render(child, rects, colors, rn_out, texts, tn_out, child_clip_x, child_clip_y, child_clip_w, child_clip_h)
        child = _n8(child, 16)


@c_abi_typed_export("pcc_kit_render", "void", ("i64", "ptr", "ptr", "ptr", "ptr", "ptr"))
def pcc_kit_render(node_id: int, rects, colors, rn_out, texts, tn_out) -> None:
    """Collect clipped 32-byte rect and legacy-compatible 48-byte text commands."""
    if _valid(node_id) == 0:
        return
    _kit_render(
        node_id,
        rects,
        colors,
        rn_out,
        texts,
        tn_out,
        _n8(node_id, 32),
        _n8(node_id, 40),
        _n8(node_id, 48),
        _n8(node_id, 56),
    )
