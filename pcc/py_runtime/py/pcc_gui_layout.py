"""pcc-Python owner: declarative GUI layout engine.

Core layout capabilities absorbed from a native declarative UI framework's
layout system: stack layout (axis + cross alignment) and table layout
(row/column size allocation).  The layout engine is pure geometry — no
platform calls — so it is fully testable and reusable by any render backend
(CoreGraphics on macOS, later others).

A layout node is a 32-byte rect: left@0, top@8, width@16, height@24.

Owned surface (stable C ABI names):

  pcc_gui_stack_measure, pcc_gui_stack_arrange,
  pcc_gui_table_measure, pcc_gui_table_arrange
"""

from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr,
    load_i32,
    load_i64,
    null,
    ptr_add,
    ptr_is_null,
    store_i64,
)

# alignment codes

# orientation


def _clamp(value: int, lo: int, hi: int) -> int:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _align_pos(align: int, available: int, size: int) -> int:
    if align == (2):
        return available - size
    if align == (1):
        return (available - size) // 2
    return 0  # MIN or STRETCH (STRETCH fills, caller handles)


@c_abi_typed_export("pcc_gui_stack_measure", "void", ("ptr", "ptr", "i64", "i32", "i32"))
def pcc_gui_stack_measure(children, out, count: int, orientation: int, child_spacing: int) -> None:
    """Measure a stack of children (each a 32-byte preferred-size rect).
    out = total (width@16, height@24); children keep their preferred sizes.
    Vertical: total height = sum(heights) + spacing*(count-1), width = max.
    Horizontal: total width = sum(widths) + spacing*(count-1), height = max."""
    total_main: int = 0
    max_cross: int = 0
    i: int = 0
    while i < count:
        child = ptr_add(children, i * 32)
        w: int = load_i64(child, 16)
        h: int = load_i64(child, 24)
        if orientation == (0):
            total_main += h
            if w > max_cross:
                max_cross = w
        else:
            total_main += w
            if h > max_cross:
                max_cross = h
        i += 1
    if count > 1:
        total_main += child_spacing * (count - 1)
    if orientation == (0):
        store_i64(out, 16, max_cross)
        store_i64(out, 24, total_main)
    else:
        store_i64(out, 16, total_main)
        store_i64(out, 24, max_cross)


@c_abi_typed_export("pcc_gui_stack_arrange", "void", ("ptr", "ptr", "i64", "i32", "i32", "i32", "i32", "i32"))
def pcc_gui_stack_arrange(
    children, out, count: int, orientation: int, child_spacing: int,
    cross_align: int, main_align: int, stretch_cross: int,
) -> None:
    """Arrange children within the output rect (left@0/top@8/width@16/height@24).
    Each child's position is written in-place.  main_align applies when the
    total content is smaller than the box; cross_align aligns each child on
    the cross axis; stretch_cross forces each child to fill the cross size."""
    box_w: int = load_i64(out, 16)
    box_h: int = load_i64(out, 24)
    box_left: int = load_i64(out, 0)
    box_top: int = load_i64(out, 8)
    # total main size
    total_main: int = 0
    i: int = 0
    while i < count:
        child = ptr_add(children, i * 32)
        if orientation == (0):
            total_main += load_i64(child, 24)
        else:
            total_main += load_i64(child, 16)
        i += 1
    if count > 1:
        total_main += child_spacing * (count - 1)
    box_main: int = box_h if orientation == (0) else box_w
    main_start: int = 0
    if main_align == (1) and total_main < box_main:
        main_start = (box_main - total_main) // 2
    elif main_align == (2) and total_main < box_main:
        main_start = box_main - total_main
    pos: int = main_start
    i = 0
    while i < count:
        child = ptr_add(children, i * 32)
        w: int = load_i64(child, 16)
        h: int = load_i64(child, 24)
        cross_avail: int = box_w if orientation == (0) else box_h
        cross_size: int = w if orientation == (0) else h
        if stretch_cross != 0:
            cross_size = cross_avail
        cross_pos = _align_pos(cross_align, cross_avail, cross_size)
        if orientation == (0):
            store_i64(child, 0, box_left + cross_pos)
            store_i64(child, 8, box_top + pos)
            store_i64(child, 24, h)
            if stretch_cross != 0:
                store_i64(child, 16, cross_avail)
            pos += h + child_spacing
        else:
            store_i64(child, 0, box_left + pos)
            store_i64(child, 8, box_top + cross_pos)
            store_i64(child, 16, w)
            if stretch_cross != 0:
                store_i64(child, 24, cross_avail)
            pos += w + child_spacing
        i += 1


@c_abi_typed_export("pcc_gui_table_measure", "void", ("ptr", "ptr", "ptr", "i64", "i64"))
def pcc_gui_table_measure(
    cell_sizes, out, totals, rows: int, cols: int,
) -> None:
    """Measure a table: cell_sizes is rows*cols preferred-size rects.
    out gets total width/height; totals gets per-row height (rows i64s)
    and per-column width (cols i64s), which table_arrange consumes.
    Row height = max cell height in row; column width = max cell width."""
    i: int = 0
    while i < rows:
        row_max: int = 0
        j: int = 0
        while j < cols:
            cell = ptr_add(cell_sizes, (i * cols + j) * 32)
            h: int = load_i64(cell, 24)
            if h > row_max:
                row_max = h
            j += 1
        store_i64(totals, i * 8, row_max)
        i += 1
    j = 0
    while j < cols:
        col_max: int = 0
        i = 0
        while i < rows:
            cell = ptr_add(cell_sizes, (i * cols + j) * 32)
            w: int = load_i64(cell, 16)
            if w > col_max:
                col_max = w
            i += 1
        store_i64(totals, (rows + j) * 8, col_max)
        j += 1
    total_w: int = 0
    total_h: int = 0
    j = 0
    while j < cols:
        total_w += load_i64(totals, (rows + j) * 8)
        j += 1
    i = 0
    while i < rows:
        total_h += load_i64(totals, i * 8)
        i += 1
    store_i64(out, 16, total_w)
    store_i64(out, 24, total_h)


@c_abi_typed_export("pcc_gui_table_arrange", "void", ("ptr", "ptr", "ptr", "i64", "i64", "i32", "i32", "i32", "i32"))
def pcc_gui_table_arrange(
    cell_rects, out, totals, rows: int, cols: int,
    v_align: int, h_align: int, v_stretch: int, h_stretch: int,
) -> None:
    """Arrange table cells within out.  cell_rects are rows*cols 32-byte
    rects whose positions are written in place; sizes are kept unless the
    matching stretch flag is set.  v_align/h_align apply per cell."""
    left0: int = load_i64(out, 0)
    top0: int = load_i64(out, 8)
    i: int = 0
    y: int = top0
    while i < rows:
        row_h: int = load_i64(totals, i * 8)
        j: int = 0
        x: int = left0
        while j < cols:
            col_w: int = load_i64(totals, (rows + j) * 8)
            cell = ptr_add(cell_rects, (i * cols + j) * 32)
            cw: int = load_i64(cell, 16)
            ch: int = load_i64(cell, 24)
            px = _align_pos(h_align, col_w, cw)
            py = _align_pos(v_align, row_h, ch)
            store_i64(cell, 0, x + px)
            store_i64(cell, 8, y + py)
            if h_stretch != 0:
                store_i64(cell, 16, col_w)
            if v_stretch != 0:
                store_i64(cell, 24, row_h)
            x += col_w
            j += 1
        y += row_h
        i += 1


# --- Flow / Dock layout ----------------------------------------------

@c_abi_typed_export("pcc_gui_flow_measure", "void", ("ptr", "ptr", "i64", "i64", "i32"))
def pcc_gui_flow_measure(children, out, count: int, line_width: int, h_spacing: int) -> None:
    """Flow layout measure: children wrap to new lines when the running
    width exceeds line_width.  out = total (width = line_width clamped to
    max child width, height = sum of line heights + v_spacing)."""
    max_line_w: int = 0
    line_w: int = 0
    line_h: int = 0
    total_h: int = 0
    i: int = 0
    while i < count:
        child = ptr_add(children, i * 32)
        w: int = load_i64(child, 16)
        h: int = load_i64(child, 24)
        if line_w > 0 and line_w + w > line_width:
            # wrap
            if line_w > max_line_w:
                max_line_w = line_w
            total_h += line_h
            line_w = w
            line_h = h
        else:
            if line_w > 0:
                line_w += h_spacing
            line_w += w
            if h > line_h:
                line_h = h
        i += 1
    if line_w > max_line_w:
        max_line_w = line_w
    total_h += line_h
    store_i64(out, 16, max_line_w)
    store_i64(out, 24, total_h)


@c_abi_typed_export("pcc_gui_flow_arrange", "void", ("ptr", "ptr", "i64", "i64", "i32", "i32"))
def pcc_gui_flow_arrange(children, out, count: int, line_width: int, h_spacing: int, v_spacing: int) -> None:
    """Flow arrange: place children line by line, wrapping at line_width."""
    left0: int = load_i64(out, 0)
    top0: int = load_i64(out, 8)
    x: int = left0
    y: int = top0
    line_h: int = 0
    i: int = 0
    while i < count:
        child = ptr_add(children, i * 32)
        w: int = load_i64(child, 16)
        h: int = load_i64(child, 24)
        if x > left0 and x + w > left0 + line_width:
            x = left0
            y += line_h
            line_h = 0
        store_i64(child, 0, x)
        store_i64(child, 8, y)
        x += w + h_spacing
        if h > line_h:
            line_h = h
        i += 1


@c_abi_typed_export("pcc_gui_dock_measure", "void", ("ptr", "ptr", "i64"))
def pcc_gui_dock_measure(children, out, count: int) -> None:
    """Dock measure: total = outer size; each child has a dock edge
    (0=left,1=top,2=right,3=bottom,4=fill).  child rects carry their
    preferred size; the total is the bounding box of all children."""
    max_w: int = 0
    max_h: int = 0
    i: int = 0
    while i < count:
        child = ptr_add(children, i * 32)
        w: int = load_i64(child, 16)
        h: int = load_i64(child, 24)
        if w > max_w:
            max_w = w
        if h > max_h:
            max_h = h
        i += 1
    store_i64(out, 16, max_w)
    store_i64(out, 24, max_h)


@c_abi_typed_export("pcc_gui_dock_arrange", "void", ("ptr", "ptr", "i64", "ptr"))
def pcc_gui_dock_arrange(children, out, count: int, docks) -> None:
    """Dock arrange: children dock against the remaining box edges in order
    (docks: 0=left,1=top,2=right,3=bottom,4=fill)."""
    left0: int = load_i64(out, 0)
    top0: int = load_i64(out, 8)
    right0: int = left0 + load_i64(out, 16)
    bottom0: int = top0 + load_i64(out, 24)
    i: int = 0
    while i < count:
        child = ptr_add(children, i * 32)
        dock: int = load_i32(docks, i * 4)
        w: int = load_i64(child, 16)
        h: int = load_i64(child, 24)
        if dock == 0:  # left
            store_i64(child, 0, left0)
            store_i64(child, 8, top0)
            store_i64(child, 24, bottom0 - top0)
            left0 += w
        elif dock == 1:  # top
            store_i64(child, 0, left0)
            store_i64(child, 8, top0)
            store_i64(child, 16, right0 - left0)
            top0 += h
        elif dock == 2:  # right
            store_i64(child, 0, right0 - w)
            store_i64(child, 8, top0)
            store_i64(child, 24, bottom0 - top0)
            right0 -= w
        elif dock == 3:  # bottom
            store_i64(child, 0, left0)
            store_i64(child, 8, bottom0 - h)
            store_i64(child, 16, right0 - left0)
            bottom0 -= h
        else:  # fill
            store_i64(child, 0, left0)
            store_i64(child, 8, top0)
            store_i64(child, 16, right0 - left0)
            store_i64(child, 24, bottom0 - top0)
        i += 1
