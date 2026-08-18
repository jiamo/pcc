"""Phase 4c: pcc-Python port of py_int_convert.c.

Converts tagged or heap ints to int64 for runtime callers that need a
native scalar and an overflow flag. Tagged-int decoding still uses the
existing C helper until the unsafe pointer/integer intrinsic exists.

PyIntObject layout:
    offset  0   PyObjectHeader
    offset 16   sign       (i32)
    offset 20   ndigits    (i32)
    offset 24   digits[]   (u32 little-endian)
"""

__pcc_runtime_port__ = True

from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.py_runtime.py.py_abi_constants import (
    PYBYTESOBJECT_BYTE_LEN_OFFSET,
    PYBYTESOBJECT_DATA_OFFSET,
    PYINTOBJECT_DIGITS_OFFSET,
    PYINTOBJECT_NDIGITS_OFFSET,
    PYINTOBJECT_SIGN_OFFSET,
    PYMEMORYVIEWOBJECT_BASE_OFFSET,
    PYOBJECTHEADER_TYPE_TAG_OFFSET,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_INT,
    PY_TYPE_MEMORYVIEW,
)
from pcc.unsafe import (
    cstr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    null,
    ptr_add,
    ptr_is_null,
    store_i8,
    store_i32,
    untag_int,
)


py_int_value_i64     = extern("py_int_value_i64",     (c_ptr,),                  c_int64)
# Unbox a C-extension number scalar (numpy int/bool scalar from ndarray element
# access) via its nb_int/nb_index slot. C-only helper (py_capi_shim.c); no cc
# baseline mirror because cext objects only exist under the no-libpython C-API
# shim that this port archive links.
py_cext_number_to_i64 = extern("py_cext_number_to_i64", (c_ptr, c_ptr),          c_int64)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)
py_bigint_alloc = extern("py_bigint_alloc", (c_int64,), c_ptr)
py_bigint_to_pyobject = extern("py_bigint_to_pyobject", (c_ptr,), c_ptr)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
# py_raise increfs; a caller that created the exception must release it.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)


def _set_overflow(slot, value: int) -> None:
    if not ptr_is_null(slot):
        store_i32(slot, 0, value)


def _load_u32(obj, offset: int) -> int:
    v: int = load_i32(obj, offset)
    if v < 0:
        v = v + 4294967296
    return v


def _store_u32(obj, offset: int, value: int) -> None:
    store_i32(obj, offset, value)


def _byteorder_is_big(byteorder) -> int:
    raw = py_str_utf8(byteorder)
    if ptr_is_null(raw):
        return -1
    if (
        load_i8(raw, 0) == 98
        and load_i8(raw, 1) == 105
        and load_i8(raw, 2) == 103
        and load_i8(raw, 3) == 0
    ):
        return 1
    if (
        load_i8(raw, 0) == 108
        and load_i8(raw, 1) == 105
        and load_i8(raw, 2) == 116
        and load_i8(raw, 3) == 116
        and load_i8(raw, 4) == 108
        and load_i8(raw, 5) == 101
        and load_i8(raw, 6) == 0
    ):
        return 0
    return -1


def _raise_int_bytes(kind: int, message) -> None:
    py_raise_owned(py_exc_new(kind, message))


def _int_bytes_like_base(value):
    current = value
    while not ptr_is_null(current) and not is_tagged_int(current):
        tag: int = load_i32(current, PYOBJECTHEADER_TYPE_TAG_OFFSET)
        if tag == PY_TYPE_BYTES or tag == PY_TYPE_BYTEARRAY:
            return current
        if tag != PY_TYPE_MEMORYVIEW:
            return null()
        current = pcc_gc_load_ptr(
            current,
            ptr_add(current, PYMEMORYVIEWOBJECT_BASE_OFFSET),
        )
    return null()


@c_abi_export("py_int_to_i64")
def py_int_to_i64(o, overflow) -> int:
    # Tagged small ints are the overwhelming majority of unboxes, and this
    # function was the single hottest symbol in a `pcc1 -> pcc2` build (117
    # instructions).  pcc does not inline, so the fast path must contain no
    # calls: `is_tagged_int` / `untag_int` / `ptr_is_null` / `store_i32` are
    # compiler intrinsics, while `_set_overflow` and the `py_int_value_i64`
    # extern are real calls — the old fast path made both.  A tagged value is
    # `(v << 1) | 1`, always odd and therefore never null, so testing it
    # before the null check is equivalent.  (The module docstring's claim that
    # tagged decoding needs the C helper predates the `untag_int` intrinsic.)
    if is_tagged_int(o):
        if not ptr_is_null(overflow):
            store_i32(overflow, 0, 0)
        return untag_int(o)
    _set_overflow(overflow, 0)
    if ptr_is_null(o):
        _set_overflow(overflow, 1)
        return 0
    if load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_INT:
        # Not a pcc heap int. A C-extension number scalar (numpy int/bool)
        # unboxes through its number protocol; py_cext_number_to_i64 sets
        # overflow=1 and returns 0 for any non-cext-number object, preserving
        # the previous behaviour for genuinely non-integer objects.
        return py_cext_number_to_i64(o, overflow)

    sign: int = load_i32(o, PYINTOBJECT_SIGN_OFFSET)
    if sign == 0:
        return 0
    ndigits: int = load_i32(o, PYINTOBJECT_NDIGITS_OFFSET)
    if ndigits <= 0:
        return 0
    if ndigits > 2:
        _set_overflow(overflow, 1)
        return 0

    low: int = _load_u32(o, PYINTOBJECT_DIGITS_OFFSET)
    high: int = 0
    if ndigits == 2:
        high = _load_u32(o, PYINTOBJECT_DIGITS_OFFSET + 4)

    if sign > 0:
        if high > 2147483647:
            _set_overflow(overflow, 1)
            return 0
        return high * 4294967296 + low

    if high > 2147483648:
        _set_overflow(overflow, 1)
        return 0
    if high == 2147483648:
        if low != 0:
            _set_overflow(overflow, 1)
            return 0
        min_i64: int = -9223372036854775807
        return min_i64 - 1
    return 0 - (high * 4294967296 + low)


@c_abi_export("py_int_to_bytes")
def py_int_to_bytes(v, length: int, byteorder):
    big: int = _byteorder_is_big(byteorder)
    if big < 0:
        _raise_int_bytes(
            2,
            cstr("byteorder must be either 'little' or 'big'"),
        )
        return null()
    if length < 0:
        _raise_int_bytes(2, cstr("length argument must be non-negative"))
        return null()

    tagged: bool = is_tagged_int(v)
    ndigits: int = 0
    small_low: int = 0
    small_high: int = 0
    if tagged:
        raw: int = py_int_value_i64(v)
        if raw < 0:
            _raise_int_bytes(15, cstr("can't convert negative int to unsigned"))
            return null()
        small_low = raw & 4294967295
        small_high = raw >> 32
        if small_high != 0:
            ndigits = 2
        elif small_low != 0:
            ndigits = 1
    else:
        if ptr_is_null(v) or load_i32(v, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_INT:
            _raise_int_bytes(3, cstr("to_bytes expects an int"))
            return null()
        if load_i32(v, PYINTOBJECT_SIGN_OFFSET) < 0:
            _raise_int_bytes(15, cstr("can't convert negative int to unsigned"))
            return null()
        ndigits = load_i32(v, PYINTOBJECT_NDIGITS_OFFSET)

    needed: int = 0
    if ndigits > 0:
        if tagged:
            top: int = small_low
            if ndigits == 2:
                top = small_high
        else:
            top = _load_u32(v, PYINTOBJECT_DIGITS_OFFSET + (ndigits - 1) * 4)
        top_bytes: int = 4
        while top_bytes > 1 and (top >> ((top_bytes - 1) * 8)) == 0:
            top_bytes = top_bytes - 1
        needed = (ndigits - 1) * 4 + top_bytes
    if needed > length:
        _raise_int_bytes(15, cstr("int too big to convert"))
        return null()

    out = py_bytes_new(null(), length)
    if ptr_is_null(out):
        return null()
    i: int = 0
    while i < needed:
        limb_index: int = i // 4
        if tagged:
            limb: int = small_low
            if limb_index == 1:
                limb = small_high
        else:
            limb = _load_u32(v, PYINTOBJECT_DIGITS_OFFSET + limb_index * 4)
        store_i8(out, PYBYTESOBJECT_DATA_OFFSET + i, (limb >> ((i % 4) * 8)) & 255)
        i = i + 1
    if big != 0:
        i = 0
        j: int = length - 1
        while i < j:
            tmp: int = load_i8(out, PYBYTESOBJECT_DATA_OFFSET + i)
            store_i8(out, PYBYTESOBJECT_DATA_OFFSET + i, load_i8(out, PYBYTESOBJECT_DATA_OFFSET + j))
            store_i8(out, PYBYTESOBJECT_DATA_OFFSET + j, tmp)
            i = i + 1
            j = j - 1
    return out


@c_abi_export("py_int_from_bytes")
def py_int_from_bytes(bytes_obj, byteorder):
    big: int = _byteorder_is_big(byteorder)
    if big < 0:
        _raise_int_bytes(
            2,
            cstr("byteorder must be either 'little' or 'big'"),
        )
        return null()
    base = _int_bytes_like_base(bytes_obj)
    if ptr_is_null(base):
        _raise_int_bytes(3, cstr("from_bytes expects a bytes object"))
        return null()

    n: int = load_i64(base, PYBYTESOBJECT_BYTE_LEN_OFFSET)
    ndigits: int = (n + 3) // 4
    if ndigits < 1:
        ndigits = 1
    out = py_bigint_alloc(ndigits)
    if ptr_is_null(out):
        return null()
    i: int = 0
    while i < n:
        le: int = i
        if big != 0:
            le = n - 1 - i
        byte: int = load_i8(base, PYBYTESOBJECT_DATA_OFFSET + i) & 255
        offset: int = PYINTOBJECT_DIGITS_OFFSET + (le // 4) * 4
        limb: int = _load_u32(out, offset)
        _store_u32(out, offset, limb | (byte << ((le % 4) * 8)))
        i = i + 1

    used: int = ndigits
    while used > 0 and _load_u32(out, PYINTOBJECT_DIGITS_OFFSET + (used - 1) * 4) == 0:
        used = used - 1
    store_i32(out, PYINTOBJECT_NDIGITS_OFFSET, used)
    if used > 0:
        store_i32(out, PYINTOBJECT_SIGN_OFFSET, 1)
    else:
        store_i32(out, PYINTOBJECT_SIGN_OFFSET, 0)
    return py_bigint_to_pyobject(out)
