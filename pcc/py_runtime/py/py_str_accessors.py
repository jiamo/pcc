"""Phase 4c.16-prep: pcc-Python port of py_str_accessors.c.

PyStrObject accessors and small string helpers. Most helpers are byte-level;
py_str_len and py_str_find preserve Python's codepoint-visible semantics.
The remaining py_str.c body keeps only py_str_new.

PyStrObject layout (from py_internal.h):
    offset  0   PyObjectHeader   (16 bytes)
    offset 16   byte_len         (i64)
    offset 24   cp_len           (i64, -1 if not yet computed)
    offset 32   hash             (i64, -1 if not yet computed)
    offset 40   data[]           (UTF-8 bytes + NUL, flexible array)
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.unsafe import (
    free,
    global_load_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memmove,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i8,
    store_i32,
    store_i64,
)

py_str_new         = extern("py_str_new",         (c_ptr, c_int64),  c_ptr)
py_int_value_i64   = extern("py_int_value_i64",   (c_ptr,),          c_int64)
py_list_new        = extern("py_list_new",        (c_int64,),        c_ptr)
py_list_append     = extern("py_list_append",     (c_ptr, c_ptr),    c_void)
py_decref          = extern("py_decref",          (c_ptr,),          c_void)


def _str_alloc(byte_len: int):
    # Local replica of py_str_alloc — alloc PyStrObject sized for
    # byte_len + NUL. PyStrObject = 40 bytes.
    if byte_len < 0:
        return null()
    s = malloc(40 + byte_len + 1)
    if ptr_is_null(s) != 0:
        return null()
    store_i64(s, 0, 1)               # refcount
    store_i32(s, 8, 4)               # PY_TYPE_STR
    store_i32(s, 12, 0)              # flags
    store_i64(s, 16, byte_len)       # byte_len
    store_i64(s, 24, -1)             # cp_len
    store_i64(s, 32, -1)             # hash
    store_i8(s, 40 + byte_len, 0)    # NUL terminator
    return s


def _is_ascii_ws(c: int) -> int:
    if c == 32:        # ' '
        return 1
    if c == 9:         # '\t'
        return 1
    if c == 10:        # '\n'
        return 1
    if c == 13:        # '\r'
        return 1
    if c == 11:        # '\v'
        return 1
    if c == 12:        # '\f'
        return 1
    return 0


def _bytes_eq(a, b, n: int) -> int:
    # Byte-by-byte memcmp equivalent; returns 1 if equal, 0 otherwise.
    i: int = 0
    while i < n:
        if (load_i8(a, i) & 0xFF) != (load_i8(b, i) & 0xFF):
            return 0
        i = i + 1
    return 1


def _utf8_codepoint_count(data, byte_len: int) -> int:
    count: int = 0
    i: int = 0
    while i < byte_len:
        b: int = load_i8(data, i) & 0xFF
        if (b & 0xC0) != 0x80:
            count = count + 1
        i = i + 1
    return count


def _byte_find(hay, hay_len: int, need, need_len: int) -> int:
    if need_len == 0:
        return 0
    if need_len > hay_len:
        return -1
    last: int = hay_len - need_len
    i: int = 0
    while i <= last:
        first_hay: int = load_i8(hay, i) & 0xFF
        first_need: int = load_i8(need, 0) & 0xFF
        if first_hay == first_need:
            if _bytes_eq(ptr_add(hay, i), need, need_len) != 0:
                return i
        i = i + 1
    return -1


def _byte_offset_to_cp_offset(s, byte_off: int) -> int:
    if byte_off <= 0:
        return 0
    n: int = load_i64(s, 16)
    if byte_off >= n:
        byte_off = n
    return _utf8_codepoint_count(ptr_add(s, 40), byte_off)


def _str_cp_len(s) -> int:
    cp: int = load_i64(s, 24)
    if cp < 0:
        cp = _utf8_codepoint_count(ptr_add(s, 40), load_i64(s, 16))
        store_i64(s, 24, cp)
    return cp


def _utf8_byte_offset_for_codepoint(s, cp_idx: int) -> int:
    if cp_idx <= 0:
        return 0
    byte_len: int = load_i64(s, 16)
    # Once _str_cp_len has proved an ASCII-only string, codepoint
    # offsets are byte offsets. The parser/lexer index source text by
    # position; without this fast path each s[i] rescans from byte 0.
    cached_cp_len: int = load_i64(s, 24)
    if cached_cp_len == byte_len:
        if cp_idx >= byte_len:
            return byte_len
        return cp_idx
    data = ptr_add(s, 40)
    seen: int = 0
    i: int = 0
    while i < byte_len:
        b: int = load_i8(data, i) & 0xFF
        if (b & 0xC0) != 0x80:
            if seen == cp_idx:
                return i
            seen = seen + 1
        i = i + 1
    return byte_len


def _utf8_codepoint_byte_len(s, byte_off: int) -> int:
    byte_len: int = load_i64(s, 16)
    if byte_off < 0:
        return 0
    if byte_off >= byte_len:
        return 0
    b: int = load_i8(ptr_add(s, 40), byte_off) & 0xFF
    if (b & 128) == 0:
        return 1
    if (b & 224) == 192:
        return 2
    if (b & 240) == 224:
        return 3
    if (b & 248) == 240:
        return 4
    return 1


def _clamp_slice_index(i: int, cp_len: int) -> int:
    if i < 0:
        i = i + cp_len
        if i < 0:
            i = 0
    elif i > cp_len:
        i = cp_len
    return i


def _normalise_index(i: int, cp_len: int) -> int:
    if i < 0:
        i = i + cp_len
    if i < 0 or i >= cp_len:
        return -1
    return i


def _is_none_or_null(obj) -> int:
    if ptr_is_null(obj) != 0:
        return 1
    if ptr_eq(obj, global_load_ptr("py_None")) != 0:
        return 1
    return 0


def _str_from_range(data, n: int):
    out = _str_alloc(n)
    if ptr_is_null(out) != 0:
        return null()
    if n > 0:
        memmove(ptr_add(out, 40), data, n)
    return out


def _type_of(obj) -> int:
    if is_tagged_int(obj) != 0:
        return 2
    return load_i32(obj, 8)


def _int_or_default(obj, default_value: int) -> int:
    if ptr_is_null(obj) != 0:
        return default_value
    if ptr_eq(obj, global_load_ptr("py_None")) != 0:
        return default_value
    if _type_of(obj) == 2:
        return py_int_value_i64(obj)
    return default_value


@c_abi_export("py_str_byte_len")
def py_str_byte_len(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    return load_i64(s, 16)


@c_abi_export("py_str_utf8")
def py_str_utf8(s):
    if ptr_is_null(s) != 0:
        return null()
    return ptr_add(s, 40)


@c_abi_export("py_str_len")
def py_str_len(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    cp: int = load_i64(s, 24)
    if cp < 0:
        cp = _utf8_codepoint_count(ptr_add(s, 40), load_i64(s, 16))
        store_i64(s, 24, cp)
    return cp


@c_abi_export("py_str_ord")
def py_str_ord(s) -> int:
    if ptr_is_null(s) != 0:
        return -1
    return _utf8_ord_at_byte(s, 0)


def _utf8_ord_at_byte(s, byte_off: int) -> int:
    byte_len: int = load_i64(s, 16)
    if byte_off < 0:
        return -1
    if byte_off >= byte_len:
        return -1
    remaining: int = byte_len - byte_off
    data = ptr_add(ptr_add(s, 40), byte_off)
    b0: int = load_i8(data, 0) & 255
    if b0 < 128:
        return b0
    if (b0 & 224) == 192:
        if remaining < 2:
            return -1
        return ((b0 & 31) << 6) | (load_i8(data, 1) & 63)
    if (b0 & 240) == 224:
        if remaining < 3:
            return -1
        return (
            ((b0 & 15) << 12)
            | ((load_i8(data, 1) & 63) << 6)
            | (load_i8(data, 2) & 63)
        )
    if (b0 & 248) == 240:
        if remaining < 4:
            return -1
        return (
            ((b0 & 7) << 18)
            | ((load_i8(data, 1) & 63) << 12)
            | ((load_i8(data, 2) & 63) << 6)
            | (load_i8(data, 3) & 63)
        )
    return -1


@c_abi_export("py_str_ord_at_i64")
def py_str_ord_at_i64(s, idx: int) -> int:
    if ptr_is_null(s) != 0:
        return -1
    cp_len: int = _str_cp_len(s)
    real: int = _normalise_index(idx, cp_len)
    if real < 0:
        return -1
    if load_i64(s, 24) == load_i64(s, 16):
        return load_i8(ptr_add(s, 40), real) & 255
    bo: int = _utf8_byte_offset_for_codepoint(s, real)
    return _utf8_ord_at_byte(s, bo)


@c_abi_export("py_str_byte_at_i64")
def py_str_byte_at_i64(s, idx: int) -> int:
    if ptr_is_null(s) != 0:
        return -1
    byte_len: int = load_i64(s, 16)
    if idx < 0:
        return -1
    if idx >= byte_len:
        return -1
    return load_i8(ptr_add(s, 40), idx) & 255


@c_abi_export("py_str_byte_slice_i64")
def py_str_byte_slice_i64(s, lo: int, hi: int):
    if ptr_is_null(s) != 0:
        return null()
    byte_len: int = load_i64(s, 16)
    if lo < 0:
        lo = 0
    if hi < lo:
        hi = lo
    if lo > byte_len:
        lo = byte_len
    if hi > byte_len:
        hi = byte_len
    out = _str_from_range(ptr_add(ptr_add(s, 40), lo), hi - lo)
    if ptr_is_null(out) == 0:
        if load_i64(s, 24) == byte_len:
            store_i64(out, 24, load_i64(out, 16))
    return out


@c_abi_export("py_chr_from_i64")
def py_chr_from_i64(codepoint: int):
    if codepoint < 0:
        return null()
    if codepoint > 1114111:
        return null()
    if codepoint >= 55296:
        if codepoint <= 57343:
            return null()

    length: int = 0
    out = _str_alloc(4)
    if ptr_is_null(out) != 0:
        return null()
    data = ptr_add(out, 40)
    if codepoint <= 127:
        store_i8(data, 0, codepoint)
        length = 1
    elif codepoint <= 2047:
        store_i8(data, 0, 192 | (codepoint >> 6))
        store_i8(data, 1, 128 | (codepoint & 63))
        length = 2
    elif codepoint <= 65535:
        store_i8(data, 0, 224 | (codepoint >> 12))
        store_i8(data, 1, 128 | ((codepoint >> 6) & 63))
        store_i8(data, 2, 128 | (codepoint & 63))
        length = 3
    else:
        store_i8(data, 0, 240 | (codepoint >> 18))
        store_i8(data, 1, 128 | ((codepoint >> 12) & 63))
        store_i8(data, 2, 128 | ((codepoint >> 6) & 63))
        store_i8(data, 3, 128 | (codepoint & 63))
        length = 4
    store_i64(out, 16, length)
    store_i8(data, length, 0)
    return out


@c_abi_export("py_str_eq")
def py_str_eq(a, b) -> int:
    if ptr_eq(a, b) != 0:
        return 1
    if ptr_is_null(a) != 0:
        return 0
    if ptr_is_null(b) != 0:
        return 0
    la: int = load_i64(a, 16)
    lb: int = load_i64(b, 16)
    if la != lb:
        return 0
    if la == 0:
        return 1
    da = ptr_add(a, 40)
    db = ptr_add(b, 40)
    return _bytes_eq(da, db, la)


@c_abi_export("py_str_contains")
def py_str_contains(s, sub) -> int:
    if ptr_is_null(s) != 0:
        return 0
    if ptr_is_null(sub) != 0:
        return 0
    sn: int = load_i64(s, 16)
    pn: int = load_i64(sub, 16)
    bo: int = _byte_find(
        ptr_add(s, 40), sn, ptr_add(sub, 40), pn
    )
    if bo < 0:
        return 0
    return 1


@c_abi_export("py_str_find")
def py_str_find(s, sub) -> int:
    if ptr_is_null(s) != 0:
        return -1
    if ptr_is_null(sub) != 0:
        return -1
    sn: int = load_i64(s, 16)
    pn: int = load_i64(sub, 16)
    bo: int = _byte_find(
        ptr_add(s, 40), sn, ptr_add(sub, 40), pn
    )
    if bo < 0:
        return -1
    return _byte_offset_to_cp_offset(s, bo)


@c_abi_export("py_str_startswith")
def py_str_startswith(s, prefix) -> int:
    if ptr_is_null(s) != 0:
        return 0
    if ptr_is_null(prefix) != 0:
        return 0
    ls: int = load_i64(s, 16)
    lp: int = load_i64(prefix, 16)
    if lp > ls:
        return 0
    if lp == 0:
        return 1
    ds = ptr_add(s, 40)
    dp = ptr_add(prefix, 40)
    return _bytes_eq(ds, dp, lp)


@c_abi_export("py_str_endswith")
def py_str_endswith(s, suffix) -> int:
    if ptr_is_null(s) != 0:
        return 0
    if ptr_is_null(suffix) != 0:
        return 0
    ls: int = load_i64(s, 16)
    lf: int = load_i64(suffix, 16)
    if lf > ls:
        return 0
    if lf == 0:
        return 1
    ds = ptr_add(s, 40 + (ls - lf))
    df = ptr_add(suffix, 40)
    return _bytes_eq(ds, df, lf)


@c_abi_export("py_str_isdigit")
def py_str_isdigit(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    n: int = load_i64(s, 16)
    if n == 0:
        return 0
    data = ptr_add(s, 40)
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 0xFF
        if c < 48:               # '0'
            return 0
        if c > 57:               # '9'
            return 0
        i = i + 1
    return 1


@c_abi_export("py_str_isalpha")
def py_str_isalpha(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    n: int = load_i64(s, 16)
    if n == 0:
        return 0
    data = ptr_add(s, 40)
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 0xFF
        ok: int = 0
        if c >= 97:              # 'a'
            if c <= 122:         # 'z'
                ok = 1
        if ok == 0:
            if c >= 65:          # 'A'
                if c <= 90:      # 'Z'
                    ok = 1
        if ok == 0:
            return 0
        i = i + 1
    return 1


@c_abi_export("py_str_isspace")
def py_str_isspace(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    n: int = load_i64(s, 16)
    if n == 0:
        return 0
    data = ptr_add(s, 40)
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 0xFF
        # ASCII whitespace: ' ' \t \n \r \v \f
        ws: int = 0
        if c == 32:
            ws = 1
        if c == 9:
            ws = 1
        if c == 10:
            ws = 1
        if c == 13:
            ws = 1
        if c == 11:
            ws = 1
        if c == 12:
            ws = 1
        if ws == 0:
            return 0
        i = i + 1
    return 1


@c_abi_export("py_str_isalnum")
def py_str_isalnum(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    n: int = load_i64(s, 16)
    if n == 0:
        return 0
    data = ptr_add(s, 40)
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 0xFF
        ok: int = 0
        if c >= 48:              # '0'
            if c <= 57:          # '9'
                ok = 1
        if ok == 0:
            if c >= 97:          # 'a'
                if c <= 122:     # 'z'
                    ok = 1
        if ok == 0:
            if c >= 65:          # 'A'
                if c <= 90:      # 'Z'
                    ok = 1
        if ok == 0:
            return 0
        i = i + 1
    return 1


@c_abi_export("py_str_strip")
def py_str_strip(s):
    if ptr_is_null(s) != 0:
        return null()
    n: int = load_i64(s, 16)
    data = ptr_add(s, 40)
    lo: int = 0
    hi: int = n
    done: int = 0
    while lo < hi and done == 0:
        c: int = load_i8(data, lo) & 0xFF
        if _is_ascii_ws(c) == 0:
            done = 1
        else:
            lo = lo + 1
    done = 0
    while hi > lo and done == 0:
        c: int = load_i8(data, hi - 1) & 0xFF
        if _is_ascii_ws(c) == 0:
            done = 1
        else:
            hi = hi - 1
    return py_str_new(ptr_add(data, lo), hi - lo)


@c_abi_export("py_str_lstrip")
def py_str_lstrip(s):
    if ptr_is_null(s) != 0:
        return null()
    n: int = load_i64(s, 16)
    data = ptr_add(s, 40)
    lo: int = 0
    hi: int = n
    done: int = 0
    while lo < hi and done == 0:
        c: int = load_i8(data, lo) & 0xFF
        if _is_ascii_ws(c) == 0:
            done = 1
        else:
            lo = lo + 1
    return py_str_new(ptr_add(data, lo), hi - lo)


@c_abi_export("py_str_rstrip")
def py_str_rstrip(s):
    if ptr_is_null(s) != 0:
        return null()
    n: int = load_i64(s, 16)
    data = ptr_add(s, 40)
    lo: int = 0
    hi: int = n
    done: int = 0
    while hi > lo and done == 0:
        c: int = load_i8(data, hi - 1) & 0xFF
        if _is_ascii_ws(c) == 0:
            done = 1
        else:
            hi = hi - 1
    return py_str_new(ptr_add(data, lo), hi - lo)


def _byte_in_chars(c: int, chars_data, n: int) -> int:
    k: int = 0
    while k < n:
        cc: int = load_i8(chars_data, k) & 0xFF
        if cc == c:
            return 1
        k = k + 1
    return 0


@c_abi_export("py_str_strip_chars")
def py_str_strip_chars(s, chars):
    if ptr_is_null(s) != 0:
        return null()
    n: int = load_i64(s, 16)
    data = ptr_add(s, 40)
    cn: int = load_i64(chars, 16)
    cdata = ptr_add(chars, 40)
    lo: int = 0
    hi: int = n
    done: int = 0
    while lo < hi and done == 0:
        c: int = load_i8(data, lo) & 0xFF
        if _byte_in_chars(c, cdata, cn) == 0:
            done = 1
        else:
            lo = lo + 1
    done = 0
    while hi > lo and done == 0:
        c: int = load_i8(data, hi - 1) & 0xFF
        if _byte_in_chars(c, cdata, cn) == 0:
            done = 1
        else:
            hi = hi - 1
    return py_str_new(ptr_add(data, lo), hi - lo)


@c_abi_export("py_str_lstrip_chars")
def py_str_lstrip_chars(s, chars):
    if ptr_is_null(s) != 0:
        return null()
    n: int = load_i64(s, 16)
    data = ptr_add(s, 40)
    cn: int = load_i64(chars, 16)
    cdata = ptr_add(chars, 40)
    lo: int = 0
    hi: int = n
    done: int = 0
    while lo < hi and done == 0:
        c: int = load_i8(data, lo) & 0xFF
        if _byte_in_chars(c, cdata, cn) == 0:
            done = 1
        else:
            lo = lo + 1
    return py_str_new(ptr_add(data, lo), hi - lo)


@c_abi_export("py_str_rstrip_chars")
def py_str_rstrip_chars(s, chars):
    if ptr_is_null(s) != 0:
        return null()
    n: int = load_i64(s, 16)
    data = ptr_add(s, 40)
    cn: int = load_i64(chars, 16)
    cdata = ptr_add(chars, 40)
    lo: int = 0
    hi: int = n
    done: int = 0
    while hi > lo and done == 0:
        c: int = load_i8(data, hi - 1) & 0xFF
        if _byte_in_chars(c, cdata, cn) == 0:
            done = 1
        else:
            hi = hi - 1
    return py_str_new(ptr_add(data, lo), hi - lo)


@c_abi_export("py_str_upper")
def py_str_upper(s):
    if ptr_is_null(s) != 0:
        return null()
    n: int = load_i64(s, 16)
    out = _str_alloc(n)
    if ptr_is_null(out) != 0:
        return null()
    src = ptr_add(s, 40)
    dst = ptr_add(out, 40)
    i: int = 0
    while i < n:
        c: int = load_i8(src, i) & 0xFF
        if c >= 97:                  # 'a'
            if c <= 122:             # 'z'
                c = c - 32           # 'a'-'A' = 32
        store_i8(dst, i, c)
        i = i + 1
    cp: int = load_i64(s, 24)
    store_i64(out, 24, cp)
    return out


@c_abi_export("py_str_lower")
def py_str_lower(s):
    if ptr_is_null(s) != 0:
        return null()
    n: int = load_i64(s, 16)
    out = _str_alloc(n)
    if ptr_is_null(out) != 0:
        return null()
    src = ptr_add(s, 40)
    dst = ptr_add(out, 40)
    i: int = 0
    while i < n:
        c: int = load_i8(src, i) & 0xFF
        if c >= 65:                  # 'A'
            if c <= 90:              # 'Z'
                c = c + 32           # 'a'-'A' = 32
        store_i8(dst, i, c)
        i = i + 1
    cp: int = load_i64(s, 24)
    store_i64(out, 24, cp)
    return out


@c_abi_export("py_str_concat")
def py_str_concat(a, b):
    if ptr_is_null(a) != 0:
        return null()
    if ptr_is_null(b) != 0:
        return null()
    la: int = load_i64(a, 16)
    lb: int = load_i64(b, 16)
    total: int = la + lb
    out = _str_alloc(total)
    if ptr_is_null(out) != 0:
        return null()
    out_data = ptr_add(out, 40)
    if la > 0:
        memmove(out_data, ptr_add(a, 40), la)
    if lb > 0:
        memmove(ptr_add(out_data, la), ptr_add(b, 40), lb)
    # cp_len cache: if both inputs have cached cp_len, sum them.
    cp_a: int = load_i64(a, 24)
    cp_b: int = load_i64(b, 24)
    if cp_a >= 0 and cp_b >= 0:
        store_i64(out, 24, cp_a + cp_b)
    return out


@c_abi_export("py_str_repeat")
def py_str_repeat(s, n):
    if ptr_is_null(s) != 0:
        return null()
    count: int = _int_or_default(n, 0)
    byte_len: int = load_i64(s, 16)
    if count <= 0:
        return py_str_new(null(), 0)
    if byte_len == 0:
        return py_str_new(null(), 0)
    if count > 9223372036854775807 // byte_len:
        return null()
    total: int = count * byte_len
    out = _str_alloc(total)
    if ptr_is_null(out) != 0:
        return null()
    src = ptr_add(s, 40)
    dst = ptr_add(out, 40)
    i: int = 0
    while i < count:
        memmove(ptr_add(dst, i * byte_len), src, byte_len)
        i = i + 1
    cp: int = load_i64(s, 24)
    if cp >= 0:
        store_i64(out, 24, cp * count)
    return out


@c_abi_export("py_str_slice")
def py_str_slice(s, lo, hi, step):
    if ptr_is_null(s) != 0:
        return null()
    cp_len: int = _str_cp_len(s)
    step_v: int = 1
    if _is_none_or_null(step) == 0:
        step_v = _int_or_default(step, 1)
    if step_v == 0:
        return null()

    if step_v > 0:
        lo_v: int = 0
        hi_v: int = cp_len
        if _is_none_or_null(lo) == 0:
            lo_v = _int_or_default(lo, 0)
        if _is_none_or_null(hi) == 0:
            hi_v = _int_or_default(hi, cp_len)
        lo_v = _clamp_slice_index(lo_v, cp_len)
        hi_v = _clamp_slice_index(hi_v, cp_len)
        if lo_v >= hi_v:
            return py_str_new(null(), 0)

        if step_v == 1:
            bo_lo: int = _utf8_byte_offset_for_codepoint(s, lo_v)
            bo_hi: int = _utf8_byte_offset_for_codepoint(s, hi_v)
            return _str_from_range(ptr_add(ptr_add(s, 40), bo_lo), bo_hi - bo_lo)

        bo_lo2: int = _utf8_byte_offset_for_codepoint(s, lo_v)
        bo_hi2: int = _utf8_byte_offset_for_codepoint(s, hi_v)
        cap: int = bo_hi2 - bo_lo2
        out = _str_alloc(cap)
        if ptr_is_null(out) != 0:
            return null()
        src = ptr_add(s, 40)
        dst = ptr_add(out, 40)
        out_bytes: int = 0
        out_cps: int = 0
        cp_index: int = lo_v
        bpos: int = bo_lo2
        next_target: int = lo_v
        while bpos < bo_hi2:
            w: int = _utf8_codepoint_byte_len(s, bpos)
            if cp_index == next_target:
                memmove(ptr_add(dst, out_bytes), ptr_add(src, bpos), w)
                out_bytes = out_bytes + w
                out_cps = out_cps + 1
                next_target = next_target + step_v
            bpos = bpos + w
            cp_index = cp_index + 1
        store_i64(out, 16, out_bytes)
        store_i8(dst, out_bytes, 0)
        store_i64(out, 24, out_cps)
        return out

    default_lo: int = cp_len - 1
    default_hi: int = -1
    lo_v2: int = default_lo
    hi_v2: int = default_hi
    if _is_none_or_null(lo) == 0:
        lo_v2 = _int_or_default(lo, default_lo)
    if _is_none_or_null(hi) == 0:
        hi_v2 = _int_or_default(hi, default_hi)

    if lo_v2 < 0:
        lo_v2 = lo_v2 + cp_len
    if lo_v2 >= cp_len:
        lo_v2 = cp_len - 1
    if lo_v2 < 0:
        return py_str_new(null(), 0)

    if _is_none_or_null(hi) == 0:
        if hi_v2 < 0:
            hi_v2 = hi_v2 + cp_len
        if hi_v2 < -1:
            hi_v2 = -1
        if hi_v2 > cp_len:
            hi_v2 = cp_len

    if lo_v2 <= hi_v2:
        return py_str_new(null(), 0)

    span: int = lo_v2 - hi_v2
    pos_step: int = -step_v
    out_n: int = (span + pos_step - 1) // pos_step
    cp_off = malloc((cp_len + 1) * 8)
    if ptr_is_null(cp_off) != 0:
        return null()

    src2 = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    cp: int = 0
    i: int = 0
    while i < byte_len:
        b: int = load_i8(src2, i) & 0xFF
        if (b & 0xC0) != 0x80:
            store_i64(cp_off, cp * 8, i)
            cp = cp + 1
        i = i + 1
    store_i64(cp_off, cp_len * 8, byte_len)

    out2 = _str_alloc(byte_len)
    if ptr_is_null(out2) != 0:
        free(cp_off)
        return null()
    dst2 = ptr_add(out2, 40)
    out_bytes2: int = 0
    k: int = 0
    while k < out_n:
        cp_idx: int = lo_v2 + step_v * k
        start: int = load_i64(cp_off, cp_idx * 8)
        end: int = load_i64(cp_off, (cp_idx + 1) * 8)
        width: int = end - start
        memmove(ptr_add(dst2, out_bytes2), ptr_add(src2, start), width)
        out_bytes2 = out_bytes2 + width
        k = k + 1
    store_i64(out2, 16, out_bytes2)
    store_i8(dst2, out_bytes2, 0)
    store_i64(out2, 24, out_n)
    free(cp_off)
    return out2


@c_abi_export("py_str_index")
def py_str_index(s, idx_obj):
    if ptr_is_null(s) != 0:
        return null()
    idx: int = _int_or_default(idx_obj, 0)
    cp_len: int = _str_cp_len(s)
    real: int = _normalise_index(idx, cp_len)
    if real < 0:
        return null()
    bo: int = _utf8_byte_offset_for_codepoint(s, real)
    w: int = _utf8_codepoint_byte_len(s, bo)
    out = _str_from_range(ptr_add(ptr_add(s, 40), bo), w)
    if ptr_is_null(out) == 0:
        store_i64(out, 24, 1)
    return out


@c_abi_export("py_str_count")
def py_str_count(s, sub) -> int:
    if ptr_is_null(s) != 0:
        return 0
    if ptr_is_null(sub) != 0:
        return 0
    sn: int = load_i64(s, 16)
    pn: int = load_i64(sub, 16)
    if pn == 0:
        return sn + 1
    sdata = ptr_add(s, 40)
    pdata = ptr_add(sub, 40)
    count: int = 0
    i: int = 0
    while i + pn <= sn:
        # Compare pn bytes at sdata+i vs pdata.
        match: int = 1
        k: int = 0
        while k < pn and match == 1:
            ba: int = load_i8(sdata, i + k) & 0xFF
            bb: int = load_i8(pdata, k) & 0xFF
            if ba != bb:
                match = 0
            k = k + 1
        if match == 1:
            count = count + 1
            i = i + pn
        else:
            i = i + 1
    return count


@c_abi_export("py_str_hash")
def py_str_hash(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    cached: int = load_i64(s, 32)
    if cached != -1:
        return cached
    h: int = -3750763034362895579
    data = ptr_add(s, 40)
    n: int = load_i64(s, 16)
    i: int = 0
    while i < n:
        b: int = load_i8(data, i) & 0xFF
        h = h ^ b
        h = h * 1099511628211
        i = i + 1
    if h == -1:
        h = -2
    store_i64(s, 32, h)
    return h


def _list_append_str_range(lst, data, start: int, n: int) -> int:
    part = _str_from_range(ptr_add(data, start), n)
    if ptr_is_null(part) != 0:
        py_decref(lst)
        return -1
    py_list_append(lst, part)
    py_decref(part)
    return 0


def _str_splitlines_impl(s, keepends: int):
    if ptr_is_null(s) != 0:
        return null()
    out = py_list_new(4)
    if ptr_is_null(out) != 0:
        return null()
    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    start: int = 0
    i: int = 0
    while i < byte_len:
        c: int = load_i8(data, i) & 0xFF
        if c == 13 or c == 10:
            end: int = i
            after: int = i + 1
            if c == 13:
                if i + 1 < byte_len:
                    nxt: int = load_i8(data, i + 1) & 0xFF
                    if nxt == 10:
                        after = i + 2
            frag_end: int = end
            if keepends != 0:
                frag_end = after
            if _list_append_str_range(out, data, start, frag_end - start) != 0:
                return null()
            i = after
            start = after
        else:
            i = i + 1
    if start < byte_len:
        if _list_append_str_range(out, data, start, byte_len - start) != 0:
            return null()
    return out


@c_abi_export("py_str_splitlines_keepends")
def py_str_splitlines_keepends(s, keepends: int):
    real_keepends: int = 0
    if keepends != 0:
        real_keepends = 1
    return _str_splitlines_impl(s, real_keepends)


@c_abi_export("py_str_splitlines")
def py_str_splitlines(s):
    return _str_splitlines_impl(s, 0)


def _str_split_whitespace(s):
    out = py_list_new(4)
    if ptr_is_null(out) != 0:
        return null()
    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    i: int = 0
    while i < byte_len:
        while i < byte_len and _is_ascii_ws(load_i8(data, i) & 0xFF) != 0:
            i = i + 1
        if i >= byte_len:
            return out
        start: int = i
        while i < byte_len and _is_ascii_ws(load_i8(data, i) & 0xFF) == 0:
            i = i + 1
        if _list_append_str_range(out, data, start, i - start) != 0:
            return null()
    return out


@c_abi_export("py_str_split")
def py_str_split(s, sep):
    if ptr_is_null(s) != 0:
        return null()
    if _is_none_or_null(sep) != 0:
        return _str_split_whitespace(s)

    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    sep_data = ptr_add(sep, 40)
    sep_len: int = load_i64(sep, 16)
    if sep_len == 0:
        return py_list_new(0)

    out = py_list_new(4)
    if ptr_is_null(out) != 0:
        return null()

    start: int = 0
    i: int = 0
    sep_first: int = load_i8(sep_data, 0) & 0xFF
    while i + sep_len <= byte_len:
        match: int = 0
        first: int = load_i8(data, i) & 0xFF
        if first == sep_first:
            if _bytes_eq(ptr_add(data, i), sep_data, sep_len) != 0:
                match = 1
        if match != 0:
            if _list_append_str_range(out, data, start, i - start) != 0:
                return null()
            i = i + sep_len
            start = i
        else:
            i = i + 1

    if _list_append_str_range(out, data, start, byte_len - start) != 0:
        return null()
    return out


def _str_split_whitespace_maxsplit(s, maxsplit: int):
    if maxsplit < 0:
        return _str_split_whitespace(s)
    out = py_list_new(4)
    if ptr_is_null(out) != 0:
        return null()
    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    i: int = 0
    splits: int = 0
    while i < byte_len:
        while i < byte_len and _is_ascii_ws(load_i8(data, i) & 0xFF) != 0:
            i = i + 1
        if i >= byte_len:
            return out
        if splits >= maxsplit:
            if _list_append_str_range(out, data, i, byte_len - i) != 0:
                return null()
            return out
        start: int = i
        while i < byte_len and _is_ascii_ws(load_i8(data, i) & 0xFF) == 0:
            i = i + 1
        if _list_append_str_range(out, data, start, i - start) != 0:
            return null()
        splits = splits + 1
    return out


@c_abi_export("py_str_split_maxsplit")
def py_str_split_maxsplit(s, sep, maxsplit: int):
    if ptr_is_null(s) != 0:
        return null()
    if maxsplit < 0:
        return py_str_split(s, sep)
    if _is_none_or_null(sep) != 0:
        return _str_split_whitespace_maxsplit(s, maxsplit)

    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    sep_data = ptr_add(sep, 40)
    sep_len: int = load_i64(sep, 16)
    if sep_len == 0:
        return py_list_new(0)

    out = py_list_new(4)
    if ptr_is_null(out) != 0:
        return null()

    start: int = 0
    i: int = 0
    splits: int = 0
    sep_first: int = load_i8(sep_data, 0) & 0xFF
    while i + sep_len <= byte_len and splits < maxsplit:
        match: int = 0
        first: int = load_i8(data, i) & 0xFF
        if first == sep_first:
            if _bytes_eq(ptr_add(data, i), sep_data, sep_len) != 0:
                match = 1
        if match != 0:
            if _list_append_str_range(out, data, start, i - start) != 0:
                return null()
            i = i + sep_len
            start = i
            splits = splits + 1
        else:
            i = i + 1

    if _list_append_str_range(out, data, start, byte_len - start) != 0:
        return null()
    return out


@c_abi_export("py_str_join")
def py_str_join(sep, lst):
    if ptr_is_null(sep) != 0:
        return null()
    if ptr_is_null(lst) != 0:
        return null()
    length: int = load_i64(lst, 16)
    if length == 0:
        return py_str_new(null(), 0)

    sep_len: int = load_i64(sep, 16)
    items = load_ptr(lst, 32)
    total: int = 0
    i: int = 0
    while i < length:
        e = load_ptr(items, i * 8)
        if ptr_is_null(e) != 0:
            return null()
        if _type_of(e) != 4:
            return null()
        if i > 0:
            total = total + sep_len
        total = total + load_i64(e, 16)
        i = i + 1

    out = _str_alloc(total)
    if ptr_is_null(out) != 0:
        return null()
    dst = ptr_add(out, 40)
    sep_data = ptr_add(sep, 40)
    off: int = 0
    j: int = 0
    while j < length:
        e = load_ptr(items, j * 8)
        if j > 0:
            if sep_len > 0:
                memmove(ptr_add(dst, off), sep_data, sep_len)
                off = off + sep_len
        elem_len: int = load_i64(e, 16)
        if elem_len > 0:
            memmove(ptr_add(dst, off), ptr_add(e, 40), elem_len)
            off = off + elem_len
        j = j + 1
    return out


def _py_str_replace_impl(s, old, new_value, maxreplace: int):
    if ptr_is_null(s) != 0:
        return null()
    if ptr_is_null(old) != 0:
        return null()
    if ptr_is_null(new_value) != 0:
        return null()

    data = ptr_add(s, 40)
    old_data = ptr_add(old, 40)
    new_data = ptr_add(new_value, 40)
    s_len: int = load_i64(s, 16)
    old_len: int = load_i64(old, 16)
    new_len: int = load_i64(new_value, 16)

    if old_len == 0:
        return py_str_new(data, s_len)
    if maxreplace == 0:
        return py_str_new(data, s_len)

    matches: int = 0
    i: int = 0
    old_first: int = load_i8(old_data, 0) & 0xFF
    while i + old_len <= s_len:
        match: int = 0
        first: int = load_i8(data, i) & 0xFF
        if first == old_first:
            if _bytes_eq(ptr_add(data, i), old_data, old_len) != 0:
                match = 1
        if match != 0:
            matches = matches + 1
            if maxreplace > 0:
                if matches >= maxreplace:
                    break
            i = i + old_len
        else:
            i = i + 1

    if matches == 0:
        return py_str_new(data, s_len)

    total: int = s_len + ((new_len - old_len) * matches)
    if total < 0:
        return null()
    out = _str_alloc(total)
    if ptr_is_null(out) != 0:
        return null()
    dst = ptr_add(out, 40)

    read: int = 0
    write: int = 0
    replaced: int = 0
    while read + old_len <= s_len:
        match2: int = 0
        first2: int = load_i8(data, read) & 0xFF
        if first2 == old_first:
            if _bytes_eq(ptr_add(data, read), old_data, old_len) != 0:
                match2 = 1
        if match2 != 0:
            do_replace: int = 0
            if maxreplace < 0:
                do_replace = 1
            elif replaced < maxreplace:
                do_replace = 1
            if do_replace != 0:
                if new_len > 0:
                    memmove(ptr_add(dst, write), new_data, new_len)
                    write = write + new_len
                replaced = replaced + 1
                read = read + old_len
            else:
                if old_len > 0:
                    memmove(ptr_add(dst, write), old_data, old_len)
                    write = write + old_len
                read = read + old_len
        else:
            b: int = load_i8(data, read) & 0xFF
            store_i8(dst, write, b)
            write = write + 1
            read = read + 1
    while read < s_len:
        b2: int = load_i8(data, read) & 0xFF
        store_i8(dst, write, b2)
        write = write + 1
        read = read + 1
    return out


@c_abi_export("py_str_replace")
def py_str_replace(s, old, new_value):
    return _py_str_replace_impl(s, old, new_value, -1)


@c_abi_export("py_str_replace_count")
def py_str_replace_count(s, old, new_value, maxreplace: int):
    return _py_str_replace_impl(s, old, new_value, maxreplace)
