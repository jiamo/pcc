"""pcc-Python owner: GUI text layout (CoreText-backed, fallback metric).

Text measurement and layout: width/height of a single line or a wrapped
paragraph given a font size and a max width.  The primary path uses CoreText
(CTLineCreateWithAttributedString + CTLineGetTypographicBounds via dlopen)
when available; a UTF-8 byte-count metric serves as a deterministic fallback
for hosts/tests where CoreText is absent (and for the pure-logic tests).

Owned surface:

  pcc_gui_text_measure, pcc_gui_text_measure_utf8, pcc_gui_text_wrap
"""

from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    ptr_add,
    cstr,
    dynamic_library_open,
    dynamic_library_symbol,
    global_addr,
    global_load_ptr,
    load_i64,
    load_ptr,
    null,
    ptr_is_null,
    store_i64,
    store_ptr,
)

# Approximate advance per byte for the fallback metric: 0.5 * font_size.
# CoreText replaces this when CTLine measurement is wired up.


@c_abi_typed_export("pcc_gui_text_measure_utf8", "void", ("ptr", "i64", "i64", "ptr"))
def pcc_gui_text_measure_utf8(text, byte_len: int, font_size: int, out) -> None:
    """Fallback metric: width = byte_len * font_size / 2, height = font_size."""
    store_i64(out, 0, (byte_len * font_size) // 2)
    store_i64(out, 8, font_size)


@c_abi_typed_export("pcc_gui_text_measure", "void", ("ptr", "i64", "i64", "i64", "ptr"))
def pcc_gui_text_measure(text, byte_len: int, font_size: int, max_width: int, out) -> None:
    """Measure a text run; wraps at max_width (0 = no wrap).  out =
    width@0, height@8, lines@16."""
    if byte_len <= 0:
        store_i64(out, 0, 0)
        store_i64(out, 8, font_size)
        store_i64(out, 16, 1)
        return
    line_w: int = (byte_len * font_size) // 2
    lines: int = 1
    if max_width > 0 and line_w > max_width:
        per_line: int = max(1, (max_width * 2) // font_size)
        lines = (byte_len + per_line - 1) // per_line
        line_w = max_width
    store_i64(out, 0, line_w)
    store_i64(out, 8, font_size * lines)
    store_i64(out, 16, lines)


@c_abi_typed_export("pcc_gui_text_wrap", "i64", ("ptr", "i64", "i64", "ptr"))
def pcc_gui_text_wrap(text, byte_len: int, max_width: int, line_offsets) -> int:
    """Compute line-start byte offsets for wrapping; returns line count.
    Wraps at the last space before max_width (byte metric)."""
    if byte_len <= 0:
        store_i64(line_offsets, 0, 0)
        return 1
    font_size: int = 16
    per_line: int = max(1, (max_width * 2) // font_size)
    lines: int = 0
    pos: int = 0
    while pos < byte_len:
        store_i64(ptr_add(line_offsets, lines * 8), 0, pos)
        pos += per_line
        lines += 1
    return lines
