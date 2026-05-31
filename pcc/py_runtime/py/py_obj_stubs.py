"""Phase 4c.2: pcc-Python replacement for py_runtime/src/py_obj_stubs.c.

Contains:
  py_float_*      : Phase 3 stubs (return NULL / 0.0)
  py_obj_repr     : Phase 3 stub (return NULL)
  py_obj_str      : real implementation — dispatches on type tag

Layout offsets (mirroring PyObjectHeader in py_internal.h):
    0  refcount (int64)
    8  type_tag (int32)
    12 flags    (int32)

Tagged int: low bit of pointer value is 1 → PY_TYPE_INT = 2.
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_double, c_int32, c_int64, c_void
from pcc.unsafe import (
    global_load_ptr,
    cstr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_f64,
    load_ptr,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i8,
    store_i64,
    store_f64,
    store_ptr,
    untag_int,
)


py_incref               = extern("py_incref",               (c_ptr,),          c_void)
py_decref               = extern("py_decref",               (c_ptr,),          c_void)
py_exc_get_message      = extern("py_exc_get_message",      (c_ptr,),          c_ptr)
py_int_from_i64         = extern("py_int_from_i64",         (c_int64,),        c_ptr)
py_int_value_i64        = extern("py_int_value_i64",        (c_ptr,),          c_int64)
py_int_to_str_obj       = extern("py_int_to_str_obj",       (c_ptr,),          c_ptr)
py_str_new              = extern("py_str_new",              (c_ptr, c_int64),  c_ptr)
py_user_str_dispatch    = extern("py_user_str_dispatch",    (c_ptr,),          c_ptr)
py_user_repr_dispatch   = extern("py_user_repr_dispatch",   (c_ptr,),          c_ptr)
py_err_occurred         = extern("py_err_occurred",         (),                c_int64)
py_isinstance           = extern("py_isinstance",           (c_ptr, c_ptr),    c_int64)
py_exc_builtin_class    = extern("py_exc_builtin_class",    (c_int64,),        c_ptr)
py_instance_getattr     = extern("py_instance_getattr",     (c_ptr, c_ptr),    c_ptr)
py_clear_exception      = extern("py_clear_exception",      (),                c_void)
py_mem_alloc            = extern("py_mem_alloc",            (c_int64,),        c_ptr)
py_mem_free             = extern("py_mem_free",             (c_ptr,),          c_void)
py_bigint_to_double     = extern("py_bigint_to_double",     (c_ptr,),          c_double)
py_float_repr_shortest  = extern("py_float_repr_shortest",  (c_ptr,),          c_ptr)
py_list_len             = extern("py_list_len",             (c_ptr,),          c_int64)
py_list_get             = extern("py_list_get",             (c_ptr, c_int64),  c_ptr)
py_tuple_len            = extern("py_tuple_len",            (c_ptr,),          c_int64)
py_tuple_get            = extern("py_tuple_get",            (c_ptr, c_int64),  c_ptr)
pcc_gc_alloc            = extern("pcc_gc_alloc",            (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)
pcc_gc_load_ptr         = extern("pcc_gc_load_ptr",         (c_ptr, c_ptr),              c_ptr)
pcc_gc_store_ptr        = extern("pcc_gc_store_ptr",        (c_ptr, c_ptr, c_ptr),       c_void)
py_str_concat           = extern("py_str_concat",           (c_ptr, c_ptr),    c_ptr)


def _type_of(obj) -> int:
    # Offsets and type-tag literals inlined to avoid module-level
    # runtime-initialized globals (which require a main() and conflict
    # with library linkage). See py_internal.h / PY_TYPE_* enum.
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)   # PyObjectHeader.type_tag


@c_abi_export("py_float_from_f64")
def py_float_from_f64(v: float):
    # PyFloatObject layout (24 bytes):
    #   0   refcount (i64)
    #   8   type_tag (i32) = PY_TYPE_FLOAT (3)
    #   12  flags    (i32)
    #   16  value    (f64)
    p = pcc_gc_alloc(24, 3, 0)
    if ptr_is_null(p):
        return null()
    store_i64(p, 0, 1)
    store_i32(p, 8, 3)
    store_f64(p, 16, v)
    return p


@c_abi_export("py_float_to_f64")
def py_float_to_f64(o) -> float:
    if ptr_is_null(o):
        return 0.0
    if is_tagged_int(o):
        i: int = untag_int(o)
        return float(i)
    tag: int = load_i32(o, 8)
    if tag == 3:              # PY_TYPE_FLOAT
        return load_f64(o, 16)
    if tag == 2:              # PY_TYPE_INT (bignum)
        return py_bigint_to_double(o)
    if tag == 1:              # PY_TYPE_BOOL
        return float(load_i32(o, 16))
    return 0.0


@c_abi_export("py_float_is_integer")
def py_float_is_integer(o) -> int:
    # float.is_integer(): finite and no fractional part. Mirrors
    # py_float_is_integer in py_obj_stubs.c (avoids math.h: |v|>=2^53 is always
    # integral; otherwise fits int64 so the truncate round-trip is exact).
    v: float = py_float_to_f64(o)
    if v != v:                          # nan
        return 0
    if v != 0.0 and v == v * 2.0:       # +/-inf
        return 0
    a: float = v
    if a < 0.0:
        a = 0.0 - a
    if a >= 9007199254740992.0:         # >= 2^53
        return 1
    iv: int = int(v)
    if v == float(iv):
        return 1
    return 0


@c_abi_export("py_float_add")
def py_float_add(a, b):
    # float + numeric -> float, matching CPython float.__add__/__radd__. This is
    # the generic-object add path used when either operand is a float (e.g. a
    # boxed result of true-division: ``obj.attr / n + m``). py_float_to_f64
    # coerces int / bool / float to a double; a non-numeric operand returns
    # null so the caller surfaces the error instead of a wrong number. Was an
    # unimplemented stub (TODO phase3) -> float arithmetic via py_obj_add
    # silently produced null in DEFAULT mode.
    at: int = _type_of(a)
    bt: int = _type_of(b)
    a_num: int = 0
    if at == 1 or at == 2 or at == 3:
        a_num = 1
    b_num: int = 0
    if bt == 1 or bt == 2 or bt == 3:
        b_num = 1
    if a_num == 1 and b_num == 1:
        return py_float_from_f64(py_float_to_f64(a) + py_float_to_f64(b))
    return null()


@c_abi_export("py_float_sub")
def py_float_sub(a, b):
    # float - numeric -> float (mirrors py_float_add). Non-numeric -> null.
    at: int = _type_of(a)
    bt: int = _type_of(b)
    a_num: int = 0
    if at == 1 or at == 2 or at == 3:
        a_num = 1
    b_num: int = 0
    if bt == 1 or bt == 2 or bt == 3:
        b_num = 1
    if a_num == 1 and b_num == 1:
        return py_float_from_f64(py_float_to_f64(a) - py_float_to_f64(b))
    return null()


@c_abi_export("py_float_mul")
def py_float_mul(a, b):
    # float * numeric -> float (mirrors py_float_add). Non-numeric -> null.
    at: int = _type_of(a)
    bt: int = _type_of(b)
    a_num: int = 0
    if at == 1 or at == 2 or at == 3:
        a_num = 1
    b_num: int = 0
    if bt == 1 or bt == 2 or bt == 3:
        b_num = 1
    if a_num == 1 and b_num == 1:
        return py_float_from_f64(py_float_to_f64(a) * py_float_to_f64(b))
    return null()


def _store_u64_decimal(buf, pos: int, value: int) -> int:
    if value == 0:
        store_i8(buf, pos, 48)
        return pos + 1
    tmp = py_mem_alloc(32)
    if ptr_is_null(tmp):
        return pos
    n: int = 0
    v: int = value
    while v > 0:
        digit: int = v % 10
        store_i8(tmp, n, 48 + digit)
        n = n + 1
        v = v // 10
    i: int = n - 1
    while i >= 0:
        store_i8(buf, pos, load_i8(tmp, i))
        pos = pos + 1
        i = i - 1
    py_mem_free(tmp)
    return pos


@c_abi_export("py_float_format_fixed")
def py_float_format_fixed(o, precision: int):
    if precision < 0:
        precision = 6
    if precision > 9:
        precision = 9
    v: float = py_float_to_f64(o)
    neg: int = 0
    if v < 0.0:
        neg = 1
        v = 0.0 - v
    scale: int = 1
    i: int = 0
    while i < precision:
        scale = scale * 10
        i = i + 1
    scaled: int = int(v * float(scale) + 0.5)
    whole: int = scaled // scale
    frac: int = scaled % scale
    buf = py_mem_alloc(96)
    if ptr_is_null(buf):
        return null()
    pos: int = 0
    if neg != 0:
        store_i8(buf, pos, 45)
        pos = pos + 1
    pos = _store_u64_decimal(buf, pos, whole)
    if precision > 0:
        store_i8(buf, pos, 46)
        pos = pos + 1
        div: int = scale // 10
        while div > 0:
            digit: int = (frac // div) % 10
            store_i8(buf, pos, 48 + digit)
            pos = pos + 1
            div = div // 10
    out = py_str_new(buf, pos)
    py_mem_free(buf)
    return out


@c_abi_export("py_complex_new")
def py_complex_new(real: float, imag: float):
    # PyComplexObject layout: header + real@16 + imag@24.
    p = pcc_gc_alloc(32, 16, 0)
    if ptr_is_null(p):
        return null()
    store_i64(p, 0, 1)
    store_i32(p, 8, 16)     # PY_TYPE_COMPLEX
    store_f64(p, 16, real)
    store_f64(p, 24, imag)
    return p


def _complex_real_part(o) -> float:
    if ptr_is_null(o):
        return 0.0
    if is_tagged_int(o):
        i: int = untag_int(o)
        return float(i)
    tag: int = load_i32(o, 8)
    if tag == 16:
        return load_f64(o, 16)
    if tag == 3:
        return load_f64(o, 16)
    if tag == 2:
        return py_bigint_to_double(o)
    if tag == 1:
        return float(load_i32(o, 16))
    return 0.0


def _complex_imag_part(o) -> float:
    if ptr_is_null(o):
        return 0.0
    if is_tagged_int(o):
        return 0.0
    if load_i32(o, 8) == 16:
        return load_f64(o, 24)
    return 0.0


@c_abi_export("py_complex_real")
def py_complex_real(o):
    return py_float_from_f64(_complex_real_part(o))


@c_abi_export("py_complex_imag")
def py_complex_imag(o):
    return py_float_from_f64(_complex_imag_part(o))


@c_abi_export("py_complex_add")
def py_complex_add(a, b):
    return py_complex_new(
        _complex_real_part(a) + _complex_real_part(b),
        _complex_imag_part(a) + _complex_imag_part(b),
    )


@c_abi_export("py_bytes_new")
def py_bytes_new(data, byte_len: int):
    if byte_len < 0:
        byte_len = 0
    p = pcc_gc_alloc(24 + byte_len + 1, 17, 0)
    if ptr_is_null(p):
        return null()
    store_i64(p, 0, 1)
    store_i32(p, 8, 17)     # PY_TYPE_BYTES
    store_i64(p, 16, byte_len)
    if not ptr_is_null(data):
        i: int = 0
        while i < byte_len:
            store_i8(p, 24 + i, load_i8(data, i))
            i = i + 1
    store_i8(p, 24 + byte_len, 0)
    return p


def _bytearray_new_raw(data, byte_len: int):
    if byte_len < 0:
        byte_len = 0
    p = pcc_gc_alloc(24 + byte_len + 1, 18, 0)
    if ptr_is_null(p):
        return null()
    store_i64(p, 0, 1)
    store_i32(p, 8, 18)     # PY_TYPE_BYTEARRAY
    store_i64(p, 16, byte_len)
    if not ptr_is_null(data):
        i: int = 0
        while i < byte_len:
            store_i8(p, 24 + i, load_i8(data, i))
            i = i + 1
    store_i8(p, 24 + byte_len, 0)
    return p


def _bytes_data(obj):
    tag: int = _type_of(obj)
    if tag == 17 or tag == 18:
        return ptr_add(obj, 24)
    if tag == 19:
        base = pcc_gc_load_ptr(obj, ptr_add(obj, 16))
        return _bytes_data(base)
    return null()


@c_abi_export("py_bytes_hex")
def py_bytes_hex(o):
    # bytes.hex(): lowercase two-hex-digits-per-byte string. Mirrors
    # py_bytes_hex in py_bytes.c. Frontend routes only bytes/bytearray here.
    if ptr_is_null(o) != 0:
        return null()
    n: int = load_i64(o, 16)
    data = _bytes_data(o)
    buf = py_mem_alloc(n * 2 + 1)
    if ptr_is_null(buf) != 0:
        return null()
    i: int = 0
    pos: int = 0
    while i < n:
        c: int = load_i8(data, i) & 0xFF
        hi: int = (c >> 4) & 0xF
        lo: int = c & 0xF
        if hi < 10:
            store_i8(buf, pos, 48 + hi)
        else:
            store_i8(buf, pos, 87 + hi)      # 'a'-10 == 87
        if lo < 10:
            store_i8(buf, pos + 1, 48 + lo)
        else:
            store_i8(buf, pos + 1, 87 + lo)
        pos = pos + 2
        i = i + 1
    out = py_str_new(buf, n * 2)
    py_mem_free(buf)
    return out


def _byte_from_obj(obj) -> int:
    if ptr_is_null(obj):
        return -1
    if is_tagged_int(obj):
        return untag_int(obj)
    tag: int = _type_of(obj)
    if tag == 1:
        if ptr_eq(obj, global_load_ptr("py_True")) != 0:
            return 1
        if ptr_eq(obj, global_load_ptr("py_False")) != 0:
            return 0
        return -1
    if tag == 2:
        return py_int_value_i64(obj)
    return -1


def _bytes_from_int_sequence(seq, as_bytearray: int):
    tag: int = _type_of(seq)
    if tag == 5:
        n: int = py_list_len(seq)
    elif tag == 7:
        n: int = py_tuple_len(seq)
    else:
        return null()
    if n <= 0:
        return _bytearray_new_raw(null(), 0) if as_bytearray else py_bytes_new(null(), 0)
    tmp = py_mem_alloc(n)
    if ptr_is_null(tmp):
        return null()
    i: int = 0
    while i < n:
        if tag == 5:
            item = py_list_get(seq, i)
        else:
            item = py_tuple_get(seq, i)
        if ptr_is_null(item):
            py_mem_free(tmp)
            return null()
        byte: int = _byte_from_obj(item)
        py_decref(item)
        if byte < 0 or byte > 255:
            py_mem_free(tmp)
            return null()
        store_i8(tmp, i, byte)
        i = i + 1
    out = _bytearray_new_raw(tmp, n) if as_bytearray != 0 else py_bytes_new(tmp, n)
    py_mem_free(tmp)
    return out


def _bytes_is_none_or_null(obj) -> int:
    if ptr_is_null(obj):
        return 1
    if ptr_eq(obj, global_load_ptr("py_None")) != 0:
        return 1
    return 0


def _bytes_slice_count(lo: int, hi: int, step: int) -> int:
    count: int = 0
    if step > 0:
        i: int = lo
        while i < hi:
            count = count + 1
            i = i + step
    else:
        i2: int = lo
        while i2 > hi:
            count = count + 1
            i2 = i2 + step
    return count


def _bytes_slice_lo(obj, length: int, step: int) -> int:
    if _bytes_is_none_or_null(obj) != 0:
        if step > 0:
            return 0
        return length - 1
    return py_int_value_i64(obj)


def _bytes_slice_hi(obj, length: int, step: int) -> int:
    if _bytes_is_none_or_null(obj) != 0:
        if step > 0:
            return length
        return -1
    return py_int_value_i64(obj)


def _bytes_normalize_lo(lo: int, length: int, step: int) -> int:
    result: int = lo
    if step > 0:
        if result < 0:
            result = result + length
            if result < 0:
                result = 0
        if result > length:
            result = length
    else:
        if result < 0:
            result = result + length
            if result < 0:
                result = -1
        if result >= length:
            result = length - 1
    return result


def _bytes_normalize_hi(hi_obj, hi: int, length: int, step: int) -> int:
    result: int = hi
    if step > 0:
        if result < 0:
            result = result + length
            if result < 0:
                result = 0
        if result > length:
            result = length
    else:
        if result < 0:
            if _bytes_is_none_or_null(hi_obj) != 0:
                result = -1
            else:
                result = result + length
                if result < 0:
                    result = -1
        if result >= length:
            result = length - 1
    return result


def _bytes_new_same_family(src, data, byte_len: int):
    if _type_of(src) == 18:
        return _bytearray_new_raw(data, byte_len)
    return py_bytes_new(data, byte_len)


@c_abi_export("py_bytes_len")
def py_bytes_len(o) -> int:
    tag: int = _type_of(o)
    if tag == 17 or tag == 18:
        return load_i64(o, 16)
    if tag == 19:
        return py_bytes_len(load_ptr(o, 16))
    return 0


@c_abi_export("py_bytearray_from_obj")
def py_bytearray_from_obj(o):
    if ptr_is_null(o):
        return _bytearray_new_raw(null(), 0)
    if _type_of(o) == 5 or _type_of(o) == 7:
        out = _bytes_from_int_sequence(o, 1)
        if not ptr_is_null(out):
            return out
    return _bytearray_new_raw(_bytes_data(o), py_bytes_len(o))


@c_abi_export("py_bytes_from_obj")
def py_bytes_from_obj(o):
    if ptr_is_null(o):
        return py_bytes_new(null(), 0)
    if _type_of(o) == 5 or _type_of(o) == 7:
        out = _bytes_from_int_sequence(o, 0)
        if not ptr_is_null(out):
            return out
    return py_bytes_new(_bytes_data(o), py_bytes_len(o))


@c_abi_export("py_memoryview_new")
def py_memoryview_new(o):
    p = pcc_gc_alloc(24, 19, 0)
    if ptr_is_null(p):
        return null()
    store_i64(p, 0, 1)
    store_i32(p, 8, 19)     # PY_TYPE_MEMORYVIEW
    store_ptr(p, 16, null())
    pcc_gc_store_ptr(p, ptr_add(p, 16), o)
    return p


@c_abi_export("py_dealloc_memoryview")
def py_dealloc_memoryview(o) -> None:
    if ptr_is_null(o):
        return
    base = pcc_gc_load_ptr(o, ptr_add(o, 16))
    if not ptr_is_null(base):
        py_decref(base)
    pcc_gc_free_object_memory(o)


@c_abi_export("py_bytes_decode")
def py_bytes_decode(o):
    return py_str_new(_bytes_data(o), py_bytes_len(o))


@c_abi_export("py_bytes_getitem")
def py_bytes_getitem(o, k):
    i: int = py_int_value_i64(k)
    n: int = py_bytes_len(o)
    data = _bytes_data(o)
    if i < 0 or i >= n or ptr_is_null(data):
        return null()
    return py_int_from_i64(load_i8(data, i) & 255)


@c_abi_export("py_bytes_slice")
def py_bytes_slice(o, lo, hi, step):
    data = _bytes_data(o)
    if ptr_is_null(data):
        return null()
    length: int = py_bytes_len(o)
    step_v: int = 1
    if _bytes_is_none_or_null(step) == 0:
        step_v = py_int_value_i64(step)
        if step_v == 0:
            return null()

    lo_v: int = _bytes_slice_lo(lo, length, step_v)
    hi_v: int = _bytes_slice_hi(hi, length, step_v)
    lo_v = _bytes_normalize_lo(lo_v, length, step_v)
    hi_v = _bytes_normalize_hi(hi, hi_v, length, step_v)

    count: int = _bytes_slice_count(lo_v, hi_v, step_v)
    if count <= 0:
        return _bytes_new_same_family(o, null(), 0)
    if step_v == 1:
        return _bytes_new_same_family(o, ptr_add(data, lo_v), count)

    tmp = py_mem_alloc(count)
    if ptr_is_null(tmp):
        return null()
    j: int = 0
    if step_v > 0:
        i: int = lo_v
        while i < hi_v:
            store_i8(tmp, j, load_i8(data, i))
            j = j + 1
            i = i + step_v
    else:
        i2: int = lo_v
        while i2 > hi_v:
            if i2 < 0 or i2 >= length:
                break
            store_i8(tmp, j, load_i8(data, i2))
            j = j + 1
            i2 = i2 + step_v
    out = _bytes_new_same_family(o, tmp, j)
    py_mem_free(tmp)
    return out


@c_abi_export("py_bytes_concat")
def py_bytes_concat(a, b):
    if ptr_is_null(a) or ptr_is_null(b):
        return null()
    at: int = _type_of(a)
    bt: int = _type_of(b)
    if not (at == 17 or at == 18):
        return null()
    if not (bt == 17 or bt == 18):
        return null()
    la: int = py_bytes_len(a)
    lb: int = py_bytes_len(b)
    if la < 0 or lb < 0:
        return null()
    if la > 9223372036854775807 - lb:
        return null()
    total: int = la + lb
    out = _bytes_new_same_family(a, null(), total)
    if ptr_is_null(out):
        return null()
    dst = _bytes_data(out)
    ad = _bytes_data(a)
    bd = _bytes_data(b)
    if ptr_is_null(dst) or ptr_is_null(ad) or ptr_is_null(bd):
        return null()
    i: int = 0
    while i < la:
        store_i8(dst, i, load_i8(ad, i))
        i = i + 1
    j: int = 0
    while j < lb:
        store_i8(dst, la + j, load_i8(bd, j))
        j = j + 1
    store_i8(dst, total, 0)
    return out


@c_abi_export("py_bytes_repeat")
def py_bytes_repeat(src, count: int):
    if ptr_is_null(src):
        return null()
    tag: int = _type_of(src)
    if not (tag == 17 or tag == 18):
        return null()
    n: int = py_bytes_len(src)
    data = _bytes_data(src)
    if ptr_is_null(data):
        return null()
    if count <= 0 or n == 0:
        return _bytes_new_same_family(src, null(), 0)
    if count > 9223372036854775807 // n:
        return null()
    total: int = count * n
    out = _bytes_new_same_family(src, null(), total)
    if ptr_is_null(out):
        return null()
    dst = _bytes_data(out)
    if ptr_is_null(dst):
        return null()
    k: int = 0
    while k < count:
        i: int = 0
        while i < n:
            store_i8(dst, k * n + i, load_i8(data, i))
            i = i + 1
        k = k + 1
    store_i8(dst, total, 0)
    return out


@c_abi_export("py_bytearray_setitem")
def py_bytearray_setitem(o, k, v) -> int:
    if _type_of(o) != 18:
        return -1
    i: int = py_int_value_i64(k)
    byte: int = py_int_value_i64(v)
    n: int = load_i64(o, 16)
    if i < 0 or i >= n:
        return -1
    if byte < 0 or byte > 255:
        return -1
    store_i8(o, 24 + i, byte)
    return 0


def _hex_digit(v: int) -> int:
    if v < 10:
        return 48 + v
    return 97 + (v - 10)


def _load_u8(p, offset: int) -> int:
    return load_i8(p, offset) & 255


def _append_hex_escape(buf, pos: int, prefix: int, value: int, digits: int) -> int:
    store_i8(buf, pos, 92)
    pos = pos + 1
    store_i8(buf, pos, prefix)
    pos = pos + 1
    shift: int = (digits - 1) * 4
    while shift >= 0:
        store_i8(buf, pos, _hex_digit((value >> shift) & 15))
        pos = pos + 1
        shift = shift - 4
    return pos


def _obj_repr_str(o, escape_non_ascii: int):
    byte_len: int = load_i64(o, 16)
    src = ptr_add(o, 40)
    out_len: int = 2
    i: int = 0
    if escape_non_ascii != 0:
        out_len = out_len + byte_len * 10
    else:
        while i < byte_len:
            c: int = _load_u8(src, i)
            if c == 92 or c == 39 or c == 10 or c == 13 or c == 9:
                out_len = out_len + 2
            else:
                out_len = out_len + 1
            i = i + 1
    buf = py_mem_alloc(out_len + 1)
    if ptr_is_null(buf):
        return null()
    pos: int = 0
    store_i8(buf, pos, 39)
    pos = pos + 1
    i = 0
    while i < byte_len:
        c2: int = _load_u8(src, i)
        if c2 == 92:
            store_i8(buf, pos, 92)
            pos = pos + 1
            store_i8(buf, pos, 92)
            pos = pos + 1
            i = i + 1
        elif c2 == 39:
            store_i8(buf, pos, 92)
            pos = pos + 1
            store_i8(buf, pos, 39)
            pos = pos + 1
            i = i + 1
        elif c2 == 10:
            store_i8(buf, pos, 92)
            pos = pos + 1
            store_i8(buf, pos, 110)
            pos = pos + 1
            i = i + 1
        elif c2 == 13:
            store_i8(buf, pos, 92)
            pos = pos + 1
            store_i8(buf, pos, 114)
            pos = pos + 1
            i = i + 1
        elif c2 == 9:
            store_i8(buf, pos, 92)
            pos = pos + 1
            store_i8(buf, pos, 116)
            pos = pos + 1
            i = i + 1
        elif escape_non_ascii != 0 and (c2 < 32 or c2 == 127):
            pos = _append_hex_escape(buf, pos, 120, c2, 2)
            i = i + 1
        elif escape_non_ascii != 0 and c2 >= 128:
            cp: int = c2
            next_i: int = i + 1
            if (
                (c2 & 224) == 192
                and i + 1 < byte_len
                and (_load_u8(src, i + 1) & 192) == 128
            ):
                cp = ((c2 & 31) << 6) | (_load_u8(src, i + 1) & 63)
                next_i = i + 2
            elif (
                (c2 & 240) == 224
                and i + 2 < byte_len
                and (_load_u8(src, i + 1) & 192) == 128
                and (_load_u8(src, i + 2) & 192) == 128
            ):
                cp = (
                    ((c2 & 15) << 12)
                    | ((_load_u8(src, i + 1) & 63) << 6)
                    | (_load_u8(src, i + 2) & 63)
                )
                next_i = i + 3
            elif (
                (c2 & 248) == 240
                and i + 3 < byte_len
                and (_load_u8(src, i + 1) & 192) == 128
                and (_load_u8(src, i + 2) & 192) == 128
                and (_load_u8(src, i + 3) & 192) == 128
            ):
                cp = (
                    ((c2 & 7) << 18)
                    | ((_load_u8(src, i + 1) & 63) << 12)
                    | ((_load_u8(src, i + 2) & 63) << 6)
                    | (_load_u8(src, i + 3) & 63)
                )
                next_i = i + 4
            if cp <= 255:
                pos = _append_hex_escape(buf, pos, 120, cp, 2)
            elif cp <= 65535:
                pos = _append_hex_escape(buf, pos, 117, cp, 4)
            else:
                pos = _append_hex_escape(buf, pos, 85, cp, 8)
            i = next_i
        else:
            store_i8(buf, pos, c2)
            pos = pos + 1
            i = i + 1
    store_i8(buf, pos, 39)
    pos = pos + 1
    store_i8(buf, pos, 0)
    out = py_str_new(buf, pos)
    py_mem_free(buf)
    return out


def _str_lit1(b0: int):
    buf = py_mem_alloc(2)
    store_i8(buf, 0, b0)
    store_i8(buf, 1, 0)
    out = py_str_new(buf, 1)
    py_mem_free(buf)
    return out


def _str_lit2(b0: int, b1: int):
    buf = py_mem_alloc(3)
    store_i8(buf, 0, b0)
    store_i8(buf, 1, b1)
    store_i8(buf, 2, 0)
    out = py_str_new(buf, 2)
    py_mem_free(buf)
    return out


def _str_lit3(b0: int, b1: int, b2: int):
    buf = py_mem_alloc(4)
    store_i8(buf, 0, b0)
    store_i8(buf, 1, b1)
    store_i8(buf, 2, b2)
    store_i8(buf, 3, 0)
    out = py_str_new(buf, 3)
    py_mem_free(buf)
    return out


def _str_lit4(b0: int, b1: int, b2: int, b3: int):
    buf = py_mem_alloc(5)
    store_i8(buf, 0, b0)
    store_i8(buf, 1, b1)
    store_i8(buf, 2, b2)
    store_i8(buf, 3, b3)
    store_i8(buf, 4, 0)
    out = py_str_new(buf, 4)
    py_mem_free(buf)
    return out


def _str_false():
    buf = py_mem_alloc(6)
    store_i8(buf, 0, 70)
    store_i8(buf, 1, 97)
    store_i8(buf, 2, 108)
    store_i8(buf, 3, 115)
    store_i8(buf, 4, 101)
    store_i8(buf, 5, 0)
    out = py_str_new(buf, 5)
    py_mem_free(buf)
    return out


def _cat_take(acc, piece):
    # acc and piece are both owned; concat, release both, return new owned str.
    out = py_str_concat(acc, piece)
    py_decref(acc)
    py_decref(piece)
    return out


def _elem_repr(item):
    # Element repr for container formatting; never returns null (so concat is
    # always safe).  Unsupported element types render as '?'.
    r = py_obj_repr(item)
    if ptr_is_null(r):
        return _str_lit1(63)        # '?'
    return r


def _format_list_str(o):
    acc = _str_lit1(91)             # '['
    n: int = py_list_len(o)
    i: int = 0
    while i < n:
        if i > 0:
            acc = _cat_take(acc, _str_lit2(44, 32))     # ', '
        acc = _cat_take(acc, _elem_repr(py_list_get(o, i)))
        i = i + 1
    acc = _cat_take(acc, _str_lit1(93))                 # ']'
    return acc


def _format_tuple_str(o):
    acc = _str_lit1(40)             # '('
    n: int = py_tuple_len(o)
    i: int = 0
    while i < n:
        if i > 0:
            acc = _cat_take(acc, _str_lit2(44, 32))     # ', '
        acc = _cat_take(acc, _elem_repr(py_tuple_get(o, i)))
        i = i + 1
    if n == 1:
        acc = _cat_take(acc, _str_lit1(44))             # trailing ','
    acc = _cat_take(acc, _str_lit1(41))                 # ')'
    return acc


def _format_dict_str(o):
    # PyDictObject: entries ptr @40, entries_used @48.  DictEntry (24 bytes):
    # key @8 (NULL = dead), value @16.  Borrowed key/value via the GC barrier.
    acc = _str_lit1(123)            # '{'
    entries = load_ptr(o, 40)
    entries_used: int = load_i64(o, 48)
    emitted: int = 0
    i: int = 0
    while i < entries_used:
        entry = ptr_add(entries, i * 24)
        if ptr_is_null(load_ptr(entry, 8)) == 0:
            if emitted > 0:
                acc = _cat_take(acc, _str_lit2(44, 32))     # ', '
            acc = _cat_take(acc, _elem_repr(pcc_gc_load_ptr(o, ptr_add(entry, 8))))
            acc = _cat_take(acc, _str_lit2(58, 32))         # ': '
            acc = _cat_take(acc, _elem_repr(pcc_gc_load_ptr(o, ptr_add(entry, 16))))
            emitted = emitted + 1
        i = i + 1
    acc = _cat_take(acc, _str_lit1(125))                # '}'
    return acc


def _format_set_str(o):
    # PySetObject: size @16, capacity @24, entries ptr @40.
    # SetEntry (16 bytes): key @8 (NULL empty, py_set_dummy tombstone).
    size: int = load_i64(o, 16)
    if size == 0:
        buf = py_mem_alloc(6)
        store_i8(buf, 0, 115)   # 's'
        store_i8(buf, 1, 101)   # 'e'
        store_i8(buf, 2, 116)   # 't'
        store_i8(buf, 3, 40)    # '('
        store_i8(buf, 4, 41)    # ')'
        store_i8(buf, 5, 0)
        out = py_str_new(buf, 5)
        py_mem_free(buf)
        return out
    acc = _str_lit1(123)        # '{'
    entries = load_ptr(o, 40)
    dummy = global_load_ptr("py_set_dummy")
    cap: int = load_i64(o, 24)
    emitted: int = 0
    i: int = 0
    while i < cap:
        key = load_ptr(entries, i * 16 + 8)
        if ptr_is_null(key) == 0:
            if ptr_eq(key, dummy) == 0:
                if emitted > 0:
                    acc = _cat_take(acc, _str_lit2(44, 32))     # ', '
                acc = _cat_take(acc, _elem_repr(pcc_gc_load_ptr(o, ptr_add(entries, i * 16 + 8))))
                emitted = emitted + 1
        i = i + 1
    acc = _cat_take(acc, _str_lit1(125))    # '}'
    return acc


def _format_bytes_str(o):
    # bytes repr: b'...' with \\ \' \n \r \t and \xNN escapes.  Data @24,
    # byte_len @16.  Mirrors py_print_fmt.py::_format_bytes but builds a PyStr.
    n: int = load_i64(o, 16)
    data = ptr_add(o, 24)
    buf = py_mem_alloc(n * 4 + 8)
    if ptr_is_null(buf):
        return null()
    pos: int = 0
    store_i8(buf, pos, 98)          # 'b'
    pos = pos + 1
    store_i8(buf, pos, 39)          # "'"
    pos = pos + 1
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 255
        if c == 92:                 # '\'
            store_i8(buf, pos, 92)
            store_i8(buf, pos + 1, 92)
            pos = pos + 2
        elif c == 39:               # "'"
            store_i8(buf, pos, 92)
            store_i8(buf, pos + 1, 39)
            pos = pos + 2
        elif c == 10:
            store_i8(buf, pos, 92)
            store_i8(buf, pos + 1, 110)      # \n
            pos = pos + 2
        elif c == 13:
            store_i8(buf, pos, 92)
            store_i8(buf, pos + 1, 114)      # \r
            pos = pos + 2
        elif c == 9:
            store_i8(buf, pos, 92)
            store_i8(buf, pos + 1, 116)      # \t
            pos = pos + 2
        elif c < 32 or c >= 127:
            # bytes repr: only printable ASCII (32..126) raw; control, DEL and
            # all high bytes (>=128) escape as \xNN. (c == 127 missed 128..255,
            # so b'\xcf\x80' printed the raw UTF-8 char.)
            pos = _append_hex_escape(buf, pos, 120, c, 2)   # \xNN
        else:
            store_i8(buf, pos, c)
            pos = pos + 1
        i = i + 1
    store_i8(buf, pos, 39)          # "'"
    pos = pos + 1
    store_i8(buf, pos, 0)
    out = py_str_new(buf, pos)
    py_mem_free(buf)
    return out


def _format_bytearray_str(o):
    # bytearray repr: bytearray(b'...'). Reuse the bytes inner repr and wrap it
    # in "bytearray(" + ... + ")". py_str_concat builds a new string and leaves
    # its operands borrowed, so each intermediate is decref'd here.
    inner = _format_bytes_str(o)
    if ptr_is_null(inner):
        return null()
    pre = py_str_new(cstr("bytearray("), 10)
    mid = py_str_concat(pre, inner)
    suf = py_str_new(cstr(")"), 1)
    out = py_str_concat(mid, suf)
    py_decref(inner)
    py_decref(pre)
    py_decref(mid)
    py_decref(suf)
    return out


def _float_str(o):
    # str/repr of a float: CPython shortest-round-trip repr via the shared C
    # helper py_float_repr_shortest (handles inf/nan and the trailing ".0"
    # internally). Replaces the old fixed-6-decimal path (py_float_format_fixed
    # + manual trailing-zero strip), which produced "3.333333" for 10/3 rather
    # than CPython's "3.3333333333333335".
    return py_float_repr_shortest(o)


def _format_builtin_str(o, tag: int):
    # Shared str/repr for the builtin non-scalar tags the inline dispatch in
    # py_obj_str / py_obj_repr does not handle directly.  Returns null when the
    # tag is not one of these (caller falls back to user dispatch).
    if tag == 3:                    # PY_TYPE_FLOAT
        return _float_str(o)
    if tag == 0:                    # PY_TYPE_NONE
        return _str_lit4(78, 111, 110, 101)             # 'None'
    if tag == 1:                    # PY_TYPE_BOOL
        if ptr_eq(o, global_load_ptr("py_True")) != 0:
            return _str_lit4(84, 114, 117, 101)         # 'True'
        return _str_false()
    if tag == 5:                    # PY_TYPE_LIST
        return _format_list_str(o)
    if tag == 7:                    # PY_TYPE_TUPLE
        return _format_tuple_str(o)
    if tag == 6:                    # PY_TYPE_DICT
        return _format_dict_str(o)
    if tag == 8:                    # PY_TYPE_SET
        return _format_set_str(o)
    if tag == 17:                   # PY_TYPE_BYTES
        return _format_bytes_str(o)
    if tag == 18:                   # PY_TYPE_BYTEARRAY
        return _format_bytearray_str(o)
    return null()


@c_abi_export("py_obj_repr")
def py_obj_repr(o):
    if ptr_is_null(o):
        return null()
    tag: int = _type_of(o)
    if tag == 4:            # PY_TYPE_STR
        return _obj_repr_str(o, 0)
    if tag == 0 or tag == 1 or tag == 2:
        return py_obj_str(o)
    built = _format_builtin_str(o, tag)
    if not ptr_is_null(built):
        return built
    dunder = py_user_repr_dispatch(o)
    if not ptr_is_null(dunder):
        return dunder
    return null()


@c_abi_export("py_obj_ascii")
def py_obj_ascii(o):
    if ptr_is_null(o):
        return null()
    tag: int = _type_of(o)
    if tag == 4:
        return _obj_repr_str(o, 1)
    return py_obj_repr(o)


@c_abi_export("py_obj_str")
def py_obj_str(o):
    if ptr_is_null(o):
        return null()
    tag: int = _type_of(o)
    if tag == 4:            # PY_TYPE_STR
        py_incref(o)
        return o
    if tag == 2:            # PY_TYPE_INT
        return py_int_to_str_obj(o)
    if tag == 12:           # PY_TYPE_EXC
        msg = py_exc_get_message(o)
        if not ptr_is_null(msg):
            py_incref(msg)
            return msg
        return null()
    built = _format_builtin_str(o, tag)
    if not ptr_is_null(built):
        return built
    dunder = py_user_str_dispatch(o)
    if not ptr_is_null(dunder):
        return dunder
    if py_err_occurred() != 0:
        return null()
    # A user exception subclass instance with no __str__ uses BaseException
    # __str__: the message from ``args`` (args[0] if one, "" if none, the
    # tuple repr otherwise). super().__init__(*args) stores ``args``.
    exc_base = py_exc_builtin_class(0)            # PY_EXC_BASE
    if not ptr_is_null(exc_base):
        if py_isinstance(o, exc_base) != 0:
            args = py_instance_getattr(o, cstr("args"))
            if ptr_is_null(args):
                if py_err_occurred() != 0:
                    py_clear_exception()
                return py_str_new(cstr(""), 0)
            if _type_of(args) == 7:               # PY_TYPE_TUPLE
                n: int = py_tuple_len(args)
                if n == 0:
                    return py_str_new(cstr(""), 0)
                if n == 1:
                    return py_obj_str(py_tuple_get(args, 0))
                return py_obj_repr(args)
            return py_obj_str(args)
    # No user __str__: object.__str__ falls back to __repr__.
    return py_obj_repr(o)
