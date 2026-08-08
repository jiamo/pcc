"""pcc-Python owner: PNG decode for the GUI image resource path.

Decodes a PNG (8-bit, color types 0/2/4/6, non-interlaced) into RGBA8
pixels.  Chunk parsing, IHDR, IDAT concatenation, per-scanline unfiltering
(Sub/Up/Average/Paeth) and gray/RGB->RGBA expansion are pure pcc-Python
logic; the DEFLATE decompression is delegated to the system zlib
(uncompress via dlopen — zlib is a host substrate, not a pcc-Python owner).

Owned surface:

  pcc_gui_png_decode(data, data_len, out_header, out_pixels, out_cap) -> i32
      header: width@0, height@8, channels@16 (i64 slots)
      out_pixels: width*height*4 bytes RGBA
      0 = ok, <0 = error code.

Error codes: -1 bad signature, -2 missing IHDR, -3 unsupported format
(bit depth != 8, interlace, palette), -4 missing/invalid IDAT,
-5 zlib inflate failed, -6 output buffer too small, -7 bad scanline data.
"""

from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr,
    dynamic_library_open,
    dynamic_library_symbol,
    load_i64,
    null,
    ptr_add,
    ptr_is_null,
    store_i64,
    store_i8,
    load_i8,
    call_i64_ptr_ptr_ptr_i64,
)

_PNG_SIG = 0x89504E470D0A1A0A
_FILTER_NONE = 0
_FILTER_SUB = 1
_FILTER_UP = 2
_FILTER_AVERAGE = 3
_FILTER_PAETH = 4

_zlib_handle = null()
_uncompress_fn = null()


def _zlib_uncompress() -> int:
    """Return the uncompress function pointer (dlopen libz once)."""
    global _zlib_handle
    global _uncompress_fn
    if not ptr_is_null(_uncompress_fn):
        return 0
    handle = dynamic_library_open(cstr("/usr/lib/libz.1.dylib"))
    if ptr_is_null(handle):
        handle = dynamic_library_open(cstr("/usr/lib/libz.1.2.12.dylib"))
    if ptr_is_null(handle):
        return -1
    fn = dynamic_library_symbol(handle, cstr("uncompress"))
    if ptr_is_null(fn):
        return -1
    _zlib_handle = handle
    _uncompress_fn = fn
    return 0


@c_abi_typed_export("pcc_gui_png_decode", "i32", ("ptr", "i64", "ptr", "ptr", "i64"))
def pcc_gui_png_decode(data, data_len: int, out_header, out_pixels, out_cap: int) -> int:
    if ptr_is_null(data) or data_len < 8:
        return -1
    sig: int = 0
    i: int = 0
    while i < 8:
        sig = (sig << 8) | load_i8(data, i)
        i += 1
    if sig != _PNG_SIG:
        return -1
    pos: int = 8
    width: int = 0
    height: int = 0
    bit_depth: int = 0
    color_type: int = 0
    interlace: int = 0
    idat_start: int = -1
    idat_len: int = 0
    saw_ihdr: int = 0
    while pos + 8 <= data_len:
        length: int = 0
        i = 0
        while i < 4:
            length = (length << 8) | load_i8(data, pos + i)
            i += 1
        ctype: int = 0
        i = 0
        while i < 4:
            ctype = (ctype << 8) | load_i8(data, pos + 4 + i)
            i += 1
        cdata = ptr_add(data, pos + 8)
        if ctype == 0x49484452:  # IHDR
            if length < 13:
                return -2
            width = 0
            i = 0
            while i < 4:
                width = (width << 8) | load_i8(cdata, i)
                i += 1
            height = 0
            i = 0
            while i < 4:
                height = (height << 8) | load_i8(cdata, 4 + i)
                i += 1
            bit_depth = load_i8(cdata, 8)
            color_type = load_i8(cdata, 9)
            interlace = load_i8(cdata, 12)
            saw_ihdr = 1
        elif ctype == 0x49444154:  # IDAT
            if idat_start < 0:
                idat_start = pos + 8
            idat_len += length
        elif ctype == 0x49454E44:  # IEND
            break
        pos += 12 + length
    if saw_ihdr == 0 or width <= 0 or height <= 0:
        return -2
    if bit_depth != 8 or interlace != 0 or color_type == 3:
        return -3
    channels: int = 1
    if color_type == 2:
        channels = 3
    elif color_type == 4:
        channels = 2
    elif color_type == 6:
        channels = 4
    stride: int = width * channels
    raw_len: int = (stride + 1) * height
    if out_cap < raw_len or ptr_is_null(out_pixels):
        # caller buffer too small: write required size into header slot 24
        store_i64(out_header, 24, raw_len)
        return -6
    if _zlib_uncompress() != 0:
        return -5
    store_i64(out_header, 24, 0)
    dest_len: int = raw_len
    rc: int = call_i64_ptr_ptr_ptr_i64(
        _uncompress_fn, out_pixels, ptr_add(out_header, 24),
        ptr_add(data, idat_start), idat_len,
    )
    if rc != 0:
        return -5
    # unfilter scanlines in place
    row: int = 0
    prev = ptr_add(out_pixels, 0)
    while row < height:
        cur = ptr_add(out_pixels, row * (stride + 1))
        ftype: int = load_i8(cur, 0)
        if ftype > 4:
            return -7
        x: int = 0
        while x < stride:
            raw: int = load_i8(cur, 1 + x)
            if ftype == _FILTER_SUB:
                if x >= channels:
                    raw = raw + load_i8(cur, 1 + x - channels)
            elif ftype == _FILTER_UP:
                if row > 0:
                    raw = raw + load_i8(prev, 1 + x)
            elif ftype == _FILTER_AVERAGE:
                left: int = 0
                if x >= channels:
                    left = load_i8(cur, 1 + x - channels)
                above: int = 0
                if row > 0:
                    above = load_i8(prev, 1 + x)
                raw = raw + (left + above) // 2
            elif ftype == _FILTER_PAETH:
                a: int = 0
                if x >= channels:
                    a = load_i8(cur, 1 + x - channels)
                b: int = 0
                if row > 0:
                    b = load_i8(prev, 1 + x)
                c: int = 0
                if x >= channels and row > 0:
                    c = load_i8(prev, 1 + x - channels)
                p: int = a + b - c
                pa: int = p - a
                if pa < 0:
                    pa = -pa
                pb: int = p - b
                if pb < 0:
                    pb = -pb
                pc: int = p - c
                if pc < 0:
                    pc = -pc
                pred: int = a
                if pb < pa and pb <= pc:
                    pred = b
                elif pc < pa:
                    pred = c
                raw = raw + pred
            store_i8(cur, 1 + x, raw & 0xFF)
            x += 1
        prev = cur
        row += 1
    # expand to RGBA: move data within the same buffer (backward)
    if channels == 4:
        store_i64(out_header, 0, width)
        store_i64(out_header, 8, height)
        store_i64(out_header, 16, 4)
        return 0
    # compact + expand backward: dst end = width*height*4
    dst = (stride + 1) * height - 1
    y: int = height - 1
    while y >= 0:
        src_row = ptr_add(out_pixels, y * (stride + 1) + 1)
        x: int = width - 1
        while x >= 0:
            # compute RGBA from channel data (read before overwrite)
            r: int = 0
            g: int = 0
            b: int = 0
            a: int = 255
            if channels == 1:
                r = load_i8(src_row, x)
                g = r
                b = r
            elif channels == 2:
                r = load_i8(src_row, x * 2)
                g = r
                b = r
                a = load_i8(src_row, x * 2 + 1)
            elif channels == 3:
                r = load_i8(src_row, x * 3)
                g = load_i8(src_row, x * 3 + 1)
                b = load_i8(src_row, x * 3 + 2)
            store_i8(out_pixels, dst, r)
            store_i8(out_pixels, dst - 1, g)
            store_i8(out_pixels, dst - 2, b)
            store_i8(out_pixels, dst - 3, a)
            dst -= 4
            x -= 1
        y -= 1
    store_i64(out_header, 0, width)
    store_i64(out_header, 8, height)
    store_i64(out_header, 16, 4)
    return 0
