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

from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
    cstr,
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
    store_i64,
)

py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_mem_alloc = extern("py_mem_alloc", (c_int64,), c_ptr)
py_mem_free = extern("py_mem_free", (c_ptr,), c_void)
py_dict_get = extern("py_dict_get", (c_ptr, c_ptr), c_ptr)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_set = extern("py_dict_set", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_debug_bad_str_concat = extern(
    "pcc_debug_bad_str_concat",
    (c_ptr, c_ptr, c_int64, c_int64),
    c_void,
)


def _str_alloc(byte_len: int):
    # Local replica of py_str_alloc — alloc PyStrObject sized for
    # byte_len + NUL. PyStrObject = 40 bytes.
    if byte_len < 0:
        return null()
    if byte_len > 9223372036854775807 - 41:
        return null()
    s = pcc_gc_alloc(40 + byte_len + 1, 4, 0)
    if ptr_is_null(s) != 0:
        return null()
    store_i64(s, 16, byte_len)  # byte_len
    store_i64(s, 24, -1)  # cp_len
    store_i64(s, 32, -1)  # hash
    store_i8(s, 40 + byte_len, 0)  # NUL terminator
    return s


def _is_ascii_ws(c: int) -> int:
    if c == 32:  # ' '
        return 1
    if c == 9:  # '\t'
        return 1
    if c == 10:  # '\n'
        return 1
    if c == 13:  # '\r'
        return 1
    if c == 11:  # '\v'
        return 1
    if c == 12:  # '\f'
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


def _byte_rfind(hay, hay_len: int, need, need_len: int) -> int:
    if need_len == 0:
        return hay_len
    if need_len > hay_len:
        return -1
    last: int = hay_len - need_len
    i: int = last
    while i >= 0:
        first_hay: int = load_i8(hay, i) & 0xFF
        first_need: int = load_i8(need, 0) & 0xFF
        if first_hay == first_need:
            if _bytes_eq(ptr_add(hay, i), need, need_len) != 0:
                return i
        i = i - 1
    return -1


def _stringlike_data(o):
    if ptr_is_null(o) != 0:
        return null()
    tag: int = load_i32(o, 8)
    if tag == 4:
        return ptr_add(o, 40)
    if tag == 17 or tag == 18:
        return ptr_add(o, 24)
    return null()


def _stringlike_len(o) -> int:
    if ptr_is_null(o) != 0:
        return 0
    tag: int = load_i32(o, 8)
    if tag == 4 or tag == 17 or tag == 18:
        return load_i64(o, 16)
    return 0


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
            ((b0 & 15) << 12) | ((load_i8(data, 1) & 63) << 6) | (load_i8(data, 2) & 63)
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


@c_abi_export("py_str_utf8_encode")
def py_str_utf8_encode(s):
    if ptr_is_null(s) != 0:
        return py_bytes_new(null(), 0)
    byte_len: int = load_i64(s, 16)
    if byte_len <= 0:
        return py_bytes_new(null(), 0)
    return py_bytes_new(ptr_add(s, 40), byte_len)


@c_abi_export("py_str_latin1_encode")
def py_str_latin1_encode(s):
    if ptr_is_null(s) != 0:
        return py_bytes_new(null(), 0)
    byte_len: int = load_i64(s, 16)
    if byte_len <= 0:
        return py_bytes_new(null(), 0)
    buf = malloc(byte_len)
    if ptr_is_null(buf):
        return null()
    raw = ptr_add(s, 40)
    i: int = 0
    out: int = 0
    while i < byte_len:
        b0: int = load_i8(raw, i)
        if b0 < 0:
            b0 = b0 + 256
        cp: int = 0
        step: int = 1
        if b0 < 128:
            cp = b0
        elif (b0 & 224) == 192 and i + 1 < byte_len:
            b1: int = load_i8(raw, i + 1)
            if b1 < 0:
                b1 = b1 + 256
            cp = ((b0 & 31) << 6) | (b1 & 63)
            step = 2
        elif (b0 & 240) == 224 and i + 2 < byte_len:
            b1 = load_i8(raw, i + 1)
            b2: int = load_i8(raw, i + 2)
            if b1 < 0:
                b1 = b1 + 256
            if b2 < 0:
                b2 = b2 + 256
            cp = ((b0 & 15) << 12) | ((b1 & 63) << 6) | (b2 & 63)
            step = 3
        elif (b0 & 248) == 240 and i + 3 < byte_len:
            b1 = load_i8(raw, i + 1)
            b2 = load_i8(raw, i + 2)
            b3: int = load_i8(raw, i + 3)
            if b1 < 0:
                b1 = b1 + 256
            if b2 < 0:
                b2 = b2 + 256
            if b3 < 0:
                b3 = b3 + 256
            cp = ((b0 & 7) << 18) | ((b1 & 63) << 12) | ((b2 & 63) << 6) | (b3 & 63)
            step = 4
        else:
            free(buf)
            return null()
        if cp > 255:
            free(buf)
            return null()
        store_i8(buf, out, cp)
        out = out + 1
        i = i + step
    result = py_bytes_new(buf, out)
    free(buf)
    return result


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
    bo: int = _byte_find(ptr_add(s, 40), sn, ptr_add(sub, 40), pn)
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
    bo: int = _byte_find(ptr_add(s, 40), sn, ptr_add(sub, 40), pn)
    if bo < 0:
        return -1
    return _byte_offset_to_cp_offset(s, bo)


@c_abi_export("py_str_rfind")
def py_str_rfind(s, sub) -> int:
    if ptr_is_null(s) != 0:
        return -1
    if ptr_is_null(sub) != 0:
        return -1
    sn: int = load_i64(s, 16)
    pn: int = load_i64(sub, 16)
    bo: int = _byte_rfind(ptr_add(s, 40), sn, ptr_add(sub, 40), pn)
    if bo < 0:
        return -1
    return _byte_offset_to_cp_offset(s, bo)


@c_abi_export("py_str_startswith")
def py_str_startswith(s, prefix) -> int:
    if ptr_is_null(s) != 0:
        return 0
    if ptr_is_null(prefix) != 0:
        return 0
    if load_i32(prefix, 8) == 7:
        n: int = py_tuple_len(prefix)
        i: int = 0
        while i < n:
            item = py_tuple_get(prefix, i)
            ok: int = py_str_startswith(s, item)
            py_decref(item)
            if ok != 0:
                return 1
            i = i + 1
        return 0
    ds = _stringlike_data(s)
    dp = _stringlike_data(prefix)
    if ptr_is_null(ds) != 0 or ptr_is_null(dp) != 0:
        return 0
    ls: int = _stringlike_len(s)
    lp: int = _stringlike_len(prefix)
    if lp > ls:
        return 0
    if lp == 0:
        return 1
    return _bytes_eq(ds, dp, lp)


@c_abi_export("py_str_endswith")
def py_str_endswith(s, suffix) -> int:
    if ptr_is_null(s) != 0:
        return 0
    if ptr_is_null(suffix) != 0:
        return 0
    if load_i32(suffix, 8) == 7:
        n: int = py_tuple_len(suffix)
        i: int = 0
        while i < n:
            item = py_tuple_get(suffix, i)
            ok: int = py_str_endswith(s, item)
            py_decref(item)
            if ok != 0:
                return 1
            i = i + 1
        return 0
    ds0 = _stringlike_data(s)
    df = _stringlike_data(suffix)
    if ptr_is_null(ds0) != 0 or ptr_is_null(df) != 0:
        return 0
    ls: int = _stringlike_len(s)
    lf: int = _stringlike_len(suffix)
    if lf > ls:
        return 0
    if lf == 0:
        return 1
    ds = ptr_add(ds0, ls - lf)
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
        if c < 48:  # '0'
            return 0
        if c > 57:  # '9'
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
        if c >= 97:  # 'a'
            if c <= 122:  # 'z'
                ok = 1
        if ok == 0:
            if c >= 65:  # 'A'
                if c <= 90:  # 'Z'
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
        if c >= 48:  # '0'
            if c <= 57:  # '9'
                ok = 1
        if ok == 0:
            if c >= 97:  # 'a'
                if c <= 122:  # 'z'
                    ok = 1
        if ok == 0:
            if c >= 65:  # 'A'
                if c <= 90:  # 'Z'
                    ok = 1
        if ok == 0:
            return 0
        i = i + 1
    return 1


@c_abi_export("py_str_isupper")
def py_str_isupper(s) -> int:
    # True iff there is at least one cased (ASCII letter) char and no lowercase
    # one (CPython ignores non-cased chars). Mirrors py_str_isupper in
    # py_str_accessors.c.
    if ptr_is_null(s) != 0:
        return 0
    n: int = load_i64(s, 16)
    data = ptr_add(s, 40)
    has_upper: int = 0
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 0xFF
        if c >= 97 and c <= 122:    # a-z -> not isupper
            return 0
        if c >= 65 and c <= 90:     # A-Z
            has_upper = 1
        i = i + 1
    return has_upper


@c_abi_export("py_str_islower")
def py_str_islower(s) -> int:
    if ptr_is_null(s) != 0:
        return 0
    n: int = load_i64(s, 16)
    data = ptr_add(s, 40)
    has_lower: int = 0
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 0xFF
        if c >= 65 and c <= 90:     # A-Z -> not islower
            return 0
        if c >= 97 and c <= 122:    # a-z
            has_lower = 1
        i = i + 1
    return has_lower


@c_abi_export("py_str_index_of")
def py_str_index_of(s, sub) -> int:
    # str.index(sub): like find() but raises ValueError when sub is absent.
    # Named *_of to avoid the existing py_str_index (s[i] subscript helper).
    idx: int = py_str_find(s, sub)
    if idx < 0:
        py_raise(py_exc_new(2, cstr("substring not found")))   # PY_EXC_VALUEERROR
        return -1
    return idx


@c_abi_export("py_str_rindex_of")
def py_str_rindex_of(s, sub) -> int:
    # str.rindex(sub): like rfind() but raises ValueError when sub is absent.
    idx: int = py_str_rfind(s, sub)
    if idx < 0:
        py_raise(py_exc_new(2, cstr("substring not found")))   # PY_EXC_VALUEERROR
        return -1
    return idx


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
        if c >= 97:  # 'a'
            if c <= 122:  # 'z'
                c = c - 32  # 'a'-'A' = 32
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
        if c >= 65:  # 'A'
            if c <= 90:  # 'Z'
                c = c + 32  # 'a'-'A' = 32
        store_i8(dst, i, c)
        i = i + 1
    cp: int = load_i64(s, 24)
    store_i64(out, 24, cp)
    return out


@c_abi_export("py_str_capitalize")
def py_str_capitalize(s):
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
        if i == 0:
            if c >= 97:  # 'a'
                if c <= 122:  # 'z'
                    c = c - 32
        else:
            if c >= 65:  # 'A'
                if c <= 90:  # 'Z'
                    c = c + 32
        store_i8(dst, i, c)
        i = i + 1
    cp: int = load_i64(s, 24)
    store_i64(out, 24, cp)
    return out


@c_abi_export("py_str_swapcase")
def py_str_swapcase(s):
    # ASCII swapcase, mirrors py_str_accessors.c::py_str_swapcase.
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
        if c >= 97:
            if c <= 122:
                c = c - 32      # a-z -> upper
        else:
            if c >= 65:
                if c <= 90:
                    c = c + 32  # A-Z -> lower
        store_i8(dst, i, c)
        i = i + 1
    cp: int = load_i64(s, 24)
    store_i64(out, 24, cp)
    return out


@c_abi_export("py_str_title")
def py_str_title(s):
    # ASCII titlecase, mirrors py_str_accessors.c::py_str_title.
    if ptr_is_null(s) != 0:
        return null()
    n: int = load_i64(s, 16)
    out = _str_alloc(n)
    if ptr_is_null(out) != 0:
        return null()
    src = ptr_add(s, 40)
    dst = ptr_add(out, 40)
    prev_alpha: int = 0
    i: int = 0
    while i < n:
        c: int = load_i8(src, i) & 0xFF
        is_alpha: int = 0
        if c >= 97:
            if c <= 122:
                is_alpha = 1
        if c >= 65:
            if c <= 90:
                is_alpha = 1
        if is_alpha != 0:
            if prev_alpha == 0:
                if c >= 97:
                    if c <= 122:
                        c = c - 32  # word start -> upper
            else:
                if c >= 65:
                    if c <= 90:
                        c = c + 32  # inside word -> lower
        store_i8(dst, i, c)
        prev_alpha = is_alpha
        i = i + 1
    cp: int = load_i64(s, 24)
    store_i64(out, 24, cp)
    return out


@c_abi_export("py_str_casefold")
def py_str_casefold(s):
    # ASCII casefold == lower (mirrors py_str_accessors.c::py_str_casefold).
    return py_str_lower(s)


@c_abi_export("py_str_concat")
def py_str_concat(a, b):
    if ptr_is_null(a) != 0:
        return null()
    if ptr_is_null(b) != 0:
        return null()
    tag_a: int = _type_of(a)
    tag_b: int = _type_of(b)
    if tag_a != 4:
        pcc_debug_bad_str_concat(a, b, tag_a, tag_b)
        return null()
    if tag_b != 4:
        pcc_debug_bad_str_concat(a, b, tag_a, tag_b)
        return null()
    la: int = load_i64(a, 16)
    lb: int = load_i64(b, 16)
    if la < 0:
        pcc_debug_bad_str_concat(a, b, tag_a, tag_b)
        return null()
    if lb < 0:
        pcc_debug_bad_str_concat(a, b, tag_a, tag_b)
        return null()
    if la > 9223372036854775807 - lb:
        pcc_debug_bad_str_concat(a, b, tag_a, tag_b)
        return null()
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
        ok: int = 1
        k: int = 0
        while k < pn and ok == 1:
            ba: int = load_i8(sdata, i + k) & 0xFF
            bb: int = load_i8(pdata, k) & 0xFF
            if ba != bb:
                ok = 0
            k = k + 1
        if ok == 1:
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


def _fill_byte_count(pad: int, fillobj) -> int:
    # Total bytes for `pad` fill codepoints (fill default ' ' is 1 byte).
    if ptr_is_null(fillobj) != 0:
        return pad
    return pad * load_i64(fillobj, 16)


def _fill_pad(buf, pos: int, pad: int, fillobj) -> int:
    # Write `pad` fill codepoints into buf starting at pos; return new pos.
    if ptr_is_null(fillobj) != 0:
        p: int = 0
        while p < pad:
            store_i8(buf, pos, 32)              # ' '
            pos = pos + 1
            p = p + 1
        return pos
    fill_bytes: int = load_i64(fillobj, 16)
    fill_data = ptr_add(fillobj, 40)
    q: int = 0
    while q < pad:
        b: int = 0
        while b < fill_bytes:
            store_i8(buf, pos, load_i8(fill_data, b))
            pos = pos + 1
            b = b + 1
        q = q + 1
    return pos


@c_abi_export("py_str_rjust")
def py_str_rjust(s, width: int, fillobj):
    n = py_str_len(s)
    if width <= n:
        py_incref(s)
        return s
    pad: int = width - n
    s_bytes: int = load_i64(s, 16)
    s_data = ptr_add(s, 40)
    pad_bytes: int = _fill_byte_count(pad, fillobj)
    buf = py_mem_alloc(s_bytes + pad_bytes + 1)
    if ptr_is_null(buf) != 0:
        return null()
    pos: int = _fill_pad(buf, 0, pad, fillobj)
    k: int = 0
    while k < s_bytes:
        store_i8(buf, pos + k, load_i8(s_data, k))
        k = k + 1
    store_i8(buf, pos + s_bytes, 0)
    out = py_str_new(buf, pos + s_bytes)
    py_mem_free(buf)
    return out


@c_abi_export("py_str_ljust")
def py_str_ljust(s, width: int, fillobj):
    n = py_str_len(s)
    if width <= n:
        py_incref(s)
        return s
    pad: int = width - n
    s_bytes: int = load_i64(s, 16)
    s_data = ptr_add(s, 40)
    pad_bytes: int = _fill_byte_count(pad, fillobj)
    buf = py_mem_alloc(s_bytes + pad_bytes + 1)
    if ptr_is_null(buf) != 0:
        return null()
    k: int = 0
    while k < s_bytes:
        store_i8(buf, k, load_i8(s_data, k))
        k = k + 1
    pos: int = _fill_pad(buf, s_bytes, pad, fillobj)
    store_i8(buf, pos, 0)
    out = py_str_new(buf, pos)
    py_mem_free(buf)
    return out


def _re_escape_is_special(c: int) -> int:
    # CPython 3.7+ re.escape set: ()[]{}?*+-|^$\.&~# plus whitespace.
    if c == 40 or c == 41 or c == 91 or c == 93 or c == 123 or c == 125:
        return 1
    if c == 63 or c == 42 or c == 43 or c == 45 or c == 124 or c == 94:
        return 1
    if c == 36 or c == 92 or c == 46 or c == 38 or c == 126 or c == 35:
        return 1
    if c == 32 or c == 9 or c == 10 or c == 13 or c == 11 or c == 12:
        return 1
    return 0


@c_abi_export("py_re_escape")
def py_re_escape(s):
    if ptr_is_null(s) != 0:
        return null()
    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    buf = py_mem_alloc(byte_len * 2 + 1)
    if ptr_is_null(buf) != 0:
        return null()
    pos: int = 0
    i: int = 0
    while i < byte_len:
        c: int = load_i8(data, i) & 0xFF
        if _re_escape_is_special(c) != 0:
            store_i8(buf, pos, 92)              # backslash
            pos = pos + 1
        store_i8(buf, pos, c)
        pos = pos + 1
        i = i + 1
    store_i8(buf, pos, 0)
    out = py_str_new(buf, pos)
    py_mem_free(buf)
    return out


@c_abi_export("py_str_rsplit_maxsplit")
def py_str_rsplit_maxsplit(s, sep, maxsplit: int):
    # Right split with a maxsplit limit (sep is a non-empty str; the lowering
    # only dispatches the sep-given form).  rsplit without a limit == split.
    if ptr_is_null(s) != 0:
        return null()
    if maxsplit < 0:
        return py_str_split(s, sep)
    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    sep_data = ptr_add(sep, 40)
    sep_len: int = load_i64(sep, 16)
    if sep_len == 0:
        return py_list_new(0)
    if maxsplit == 0:
        out0 = py_list_new(1)
        if ptr_is_null(out0) != 0:
            return null()
        if _list_append_str_range(out0, data, 0, byte_len) != 0:
            return null()
        return out0
    # Scan from the right; store the kept separator byte positions in
    # ascending slots so the result builds left-to-right (no reversal).
    positions = py_mem_alloc(maxsplit * 8)
    if ptr_is_null(positions) != 0:
        return null()
    count: int = 0
    i: int = byte_len - sep_len
    while i >= 0 and count < maxsplit:
        if _bytes_eq(ptr_add(data, i), sep_data, sep_len) != 0:
            store_i64(positions, (maxsplit - 1 - count) * 8, i)
            count = count + 1
            i = i - sep_len
        else:
            i = i - 1
    out = py_list_new(count + 1)
    if ptr_is_null(out) != 0:
        py_mem_free(positions)
        return null()
    prev: int = 0
    j: int = maxsplit - count
    while j < maxsplit:
        p: int = load_i64(positions, j * 8)
        if _list_append_str_range(out, data, prev, p - prev) != 0:
            py_mem_free(positions)
            return null()
        prev = p + sep_len
        j = j + 1
    py_mem_free(positions)
    if _list_append_str_range(out, data, prev, byte_len - prev) != 0:
        return null()
    return out


@c_abi_export("py_str_center")
def py_str_center(s, width: int, fillobj):
    n = py_str_len(s)
    if width <= n:
        py_incref(s)
        return s
    marg: int = width - n
    left: int = marg // 2 + (marg & width & 1)      # CPython center split
    right: int = marg - left
    s_bytes: int = load_i64(s, 16)
    s_data = ptr_add(s, 40)
    pad_l: int = _fill_byte_count(left, fillobj)
    pad_r: int = _fill_byte_count(right, fillobj)
    buf = py_mem_alloc(s_bytes + pad_l + pad_r + 1)
    if ptr_is_null(buf) != 0:
        return null()
    pos: int = _fill_pad(buf, 0, left, fillobj)
    k: int = 0
    while k < s_bytes:
        store_i8(buf, pos + k, load_i8(s_data, k))
        k = k + 1
    pos = pos + s_bytes
    pos = _fill_pad(buf, pos, right, fillobj)
    store_i8(buf, pos, 0)
    out = py_str_new(buf, pos)
    py_mem_free(buf)
    return out


@c_abi_export("py_str_zfill")
def py_str_zfill(s, width: int):
    n = py_str_len(s)
    if width <= n:
        py_incref(s)
        return s
    pad: int = width - n
    s_bytes: int = load_i64(s, 16)
    s_data = ptr_add(s, 40)
    sign: int = 0
    if s_bytes > 0:
        c0: int = load_i8(s_data, 0) & 0xFF
        if c0 == 43 or c0 == 45:                    # leading '+' or '-'
            sign = 1
    buf = py_mem_alloc(s_bytes + pad + 1)
    if ptr_is_null(buf) != 0:
        return null()
    pos: int = 0
    if sign != 0:
        store_i8(buf, 0, load_i8(s_data, 0))
        pos = 1
    z: int = 0
    while z < pad:
        store_i8(buf, pos, 48)                      # '0'
        pos = pos + 1
        z = z + 1
    k: int = sign
    while k < s_bytes:
        store_i8(buf, pos, load_i8(s_data, k))
        pos = pos + 1
        k = k + 1
    store_i8(buf, pos, 0)
    out = py_str_new(buf, pos)
    py_mem_free(buf)
    return out


@c_abi_export("py_str_expandtabs")
def py_str_expandtabs(s, tabsize: int):
    # str.expandtabs(tabsize): '\t' -> spaces up to the next tabsize column
    # boundary; '\n'/'\r' reset the column. Mirrors py_str_expandtabs in
    # py_str_accessors.c (ASCII/byte-oriented column tracking).
    if ptr_is_null(s) != 0:
        return null()
    s_bytes: int = load_i64(s, 16)
    s_data = ptr_add(s, 40)
    mult: int = 1
    if tabsize > 1:
        mult = tabsize
    buf = py_mem_alloc(s_bytes * mult + 1)
    if ptr_is_null(buf) != 0:
        return null()
    pos: int = 0
    col: int = 0
    i: int = 0
    while i < s_bytes:
        c: int = load_i8(s_data, i) & 0xFF
        if c == 9:                          # '\t'
            if tabsize > 0:
                spaces: int = tabsize - (col % tabsize)
                k: int = 0
                while k < spaces:
                    store_i8(buf, pos, 32)  # ' '
                    pos = pos + 1
                    k = k + 1
                col = col + spaces
        elif c == 10 or c == 13:            # '\n' or '\r'
            store_i8(buf, pos, c)
            pos = pos + 1
            col = 0
        else:
            store_i8(buf, pos, c)
            pos = pos + 1
            col = col + 1
        i = i + 1
    store_i8(buf, pos, 0)
    out = py_str_new(buf, pos)
    py_mem_free(buf)
    return out


@c_abi_export("py_str_translate")
def py_str_translate(s, table):
    # str.translate(table): map each byte through table (dict {ord:ord|str|None}).
    # Absent->keep, None->delete, int->that byte, str->its bytes. Two-pass (size
    # then fill). Mirrors py_str_translate in py_str_accessors.c (byte/ASCII).
    if ptr_is_null(s) != 0:
        return null()
    byte_len: int = load_i64(s, 16)
    data = ptr_add(s, 40)
    none = global_load_ptr("py_None")
    out_len: int = 0
    i: int = 0
    while i < byte_len:
        c: int = load_i8(data, i) & 0xFF
        key = py_int_from_i64(c)
        val = py_dict_get(table, key)
        py_decref(key)
        if ptr_is_null(val) != 0:
            out_len = out_len + 1
        elif ptr_eq(val, none) != 0:
            py_decref(val)
        elif _type_of(val) == 4:                # PY_TYPE_STR
            out_len = out_len + load_i64(val, 16)
            py_decref(val)
        else:
            out_len = out_len + 1
            py_decref(val)
        i = i + 1
    buf = py_mem_alloc(out_len + 1)
    if ptr_is_null(buf) != 0:
        return null()
    pos: int = 0
    i = 0
    while i < byte_len:
        c2: int = load_i8(data, i) & 0xFF
        key2 = py_int_from_i64(c2)
        val2 = py_dict_get(table, key2)
        py_decref(key2)
        if ptr_is_null(val2) != 0:
            store_i8(buf, pos, c2)
            pos = pos + 1
        elif ptr_eq(val2, none) != 0:
            py_decref(val2)
        elif _type_of(val2) == 4:               # PY_TYPE_STR
            vlen: int = load_i64(val2, 16)
            vdata = ptr_add(val2, 40)
            j: int = 0
            while j < vlen:
                store_i8(buf, pos, load_i8(vdata, j))
                pos = pos + 1
                j = j + 1
            py_decref(val2)
        else:
            nc: int = py_int_value_i64(val2)
            store_i8(buf, pos, nc & 0xFF)
            pos = pos + 1
            py_decref(val2)
        i = i + 1
    store_i8(buf, pos, 0)
    out = py_str_new(buf, pos)
    py_mem_free(buf)
    return out


@c_abi_export("py_str_maketrans")
def py_str_maketrans(x, y):
    # str.maketrans(x, y) -> {ord(x[i]): ord(y[i])} for the 2-arg form (equal
    # length). Mirrors py_str_maketrans in py_str_accessors.c. py_dict_set
    # increfs key+value, so the fresh ints are decref'd after.
    xlen: int = load_i64(x, 16)
    ylen: int = load_i64(y, 16)
    if xlen != ylen:
        py_raise(py_exc_new(2, cstr(
            "the first two maketrans arguments must have equal length")))
        return null()
    xdata = ptr_add(x, 40)
    ydata = ptr_add(y, 40)
    d = py_dict_new()
    if ptr_is_null(d) != 0:
        return null()
    i: int = 0
    while i < xlen:
        k = py_int_from_i64(load_i8(xdata, i) & 0xFF)
        v = py_int_from_i64(load_i8(ydata, i) & 0xFF)
        py_dict_set(d, k, v)
        py_decref(k)
        py_decref(v)
        i = i + 1
    return d


@c_abi_export("py_str_removeprefix")
def py_str_removeprefix(s, prefix):
    if ptr_is_null(s) != 0:
        return null()
    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    p_data = ptr_add(prefix, 40)
    p_len: int = load_i64(prefix, 16)
    if p_len > 0 and p_len <= byte_len and _bytes_eq(data, p_data, p_len) != 0:
        return _str_from_range(ptr_add(data, p_len), byte_len - p_len)
    py_incref(s)
    return s


@c_abi_export("py_str_removesuffix")
def py_str_removesuffix(s, suffix):
    if ptr_is_null(s) != 0:
        return null()
    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    suf_data = ptr_add(suffix, 40)
    suf_len: int = load_i64(suffix, 16)
    if suf_len > 0 and suf_len <= byte_len and _bytes_eq(
        ptr_add(data, byte_len - suf_len), suf_data, suf_len
    ) != 0:
        return _str_from_range(data, byte_len - suf_len)
    py_incref(s)
    return s


@c_abi_export("py_str_partition")
def py_str_partition(s, sep):
    # (before, sep, after) on first occurrence; (s, "", "") if not found.
    # Byte-level: sep boundaries fall on codepoint boundaries for valid UTF-8.
    if ptr_is_null(s) != 0:
        return null()
    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    sep_data = ptr_add(sep, 40)
    sep_len: int = load_i64(sep, 16)
    found: int = -1
    if sep_len > 0:
        i: int = 0
        while i + sep_len <= byte_len and found < 0:
            if _bytes_eq(ptr_add(data, i), sep_data, sep_len) != 0:
                found = i
            else:
                i = i + 1
    t = py_tuple_new(3)
    if ptr_is_null(t) != 0:
        return null()
    if found < 0:
        py_tuple_set_item(t, 0, s)              # set_item increfs; s is borrowed
        e1 = _str_from_range(data, 0)
        py_tuple_set_item(t, 1, e1)
        py_decref(e1)
        e2 = _str_from_range(data, 0)
        py_tuple_set_item(t, 2, e2)
        py_decref(e2)
    else:
        before = _str_from_range(data, found)
        py_tuple_set_item(t, 0, before)
        py_decref(before)
        mid = _str_from_range(ptr_add(data, found), sep_len)
        py_tuple_set_item(t, 1, mid)
        py_decref(mid)
        after = _str_from_range(ptr_add(data, found + sep_len), byte_len - found - sep_len)
        py_tuple_set_item(t, 2, after)
        py_decref(after)
    return t


@c_abi_export("py_str_rpartition")
def py_str_rpartition(s, sep):
    # (before, sep, after) on the LAST occurrence; ("", "", s) if not found
    # (rpartition puts the original at the END, unlike partition). Mirrors
    # py_str_rpartition in py_str_accessors.c. No break: loop while found < 0.
    if ptr_is_null(s) != 0:
        return null()
    data = ptr_add(s, 40)
    byte_len: int = load_i64(s, 16)
    sep_data = ptr_add(sep, 40)
    sep_len: int = load_i64(sep, 16)
    found: int = -1
    if sep_len > 0 and sep_len <= byte_len:
        i: int = byte_len - sep_len
        while i >= 0 and found < 0:
            if _bytes_eq(ptr_add(data, i), sep_data, sep_len) != 0:
                found = i
            else:
                i = i - 1
    t = py_tuple_new(3)
    if ptr_is_null(t) != 0:
        return null()
    if found < 0:
        e0 = _str_from_range(data, 0)
        py_tuple_set_item(t, 0, e0)
        py_decref(e0)
        e1 = _str_from_range(data, 0)
        py_tuple_set_item(t, 1, e1)
        py_decref(e1)
        py_tuple_set_item(t, 2, s)              # original at the END
    else:
        before = _str_from_range(data, found)
        py_tuple_set_item(t, 0, before)
        py_decref(before)
        mid = _str_from_range(ptr_add(data, found), sep_len)
        py_tuple_set_item(t, 1, mid)
        py_decref(mid)
        after = _str_from_range(ptr_add(data, found + sep_len), byte_len - found - sep_len)
        py_tuple_set_item(t, 2, after)
        py_decref(after)
    return t


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
        ok: int = 0
        first: int = load_i8(data, i) & 0xFF
        if first == sep_first:
            if _bytes_eq(ptr_add(data, i), sep_data, sep_len) != 0:
                ok = 1
        if ok != 0:
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
        ok: int = 0
        first: int = load_i8(data, i) & 0xFF
        if first == sep_first:
            if _bytes_eq(ptr_add(data, i), sep_data, sep_len) != 0:
                ok = 1
        if ok != 0:
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
    if sep_len < 0:
        return null()
    items = load_ptr(lst, 32)
    total: int = 0
    i: int = 0
    while i < length:
        e = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
        if ptr_is_null(e) != 0:
            return null()
        tag: int = _type_of(e)
        if tag != 4:
            return null()
        if i > 0:
            if total > 9223372036854775807 - sep_len:
                return null()
            total = total + sep_len
        elem_len: int = load_i64(e, 16)
        if elem_len < 0:
            return null()
        if total > 9223372036854775807 - elem_len:
            return null()
        total = total + elem_len
        i = i + 1

    out = _str_alloc(total)
    if ptr_is_null(out) != 0:
        return null()

    out_data = ptr_add(out, 40)
    sep_data = ptr_add(sep, 40)
    off: int = 0
    i = 0
    while i < length:
        e = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))
        if i > 0 and sep_len > 0:
            memmove(ptr_add(out_data, off), sep_data, sep_len)
            off = off + sep_len
        elem_len = load_i64(e, 16)
        if elem_len > 0:
            memmove(ptr_add(out_data, off), ptr_add(e, 40), elem_len)
            off = off + elem_len
        i = i + 1
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
        ok: int = 0
        first: int = load_i8(data, i) & 0xFF
        if first == old_first:
            if _bytes_eq(ptr_add(data, i), old_data, old_len) != 0:
                ok = 1
        if ok != 0:
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
