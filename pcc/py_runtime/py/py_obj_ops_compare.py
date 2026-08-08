"""Phase 4c.15b: pcc-Python port of py_obj_ops_compare.c.

Equality / hashing / three-way compare / sorted / contains.

The earlier port had an insertion-sort bug: I used `k = 0` to "break"
the inner loop, then unconditionally wrote cur to out[k] — which
overwrote slot 0 with later elements instead of leaving them at slot
j. The fix uses a `done` flag so the post-loop k is the correct slot.

Public object type tags come from the generated ``py_abi_constants`` module.

Object layouts:
    PyStrObject:  byte_len@16 (i64), cp_len@24, hash@32 (i64, -1=unset), data@40
    PyBytesObject/PyByteArrayObject: byte_len@16 (i64), data@24
    PyMemoryViewObject: base@16 (ptr)
    PyListObject: length@16   (i64),  capacity@24, items@32 (ptr)
    PyTupleObject: len@16     (i64),  items[]@24  (flex)

FNV-1a constants (verified to work in pcc-Python signed-i64):
    offset basis: 0xcbf29ce484222325 = -3750763034362895579 (signed)
    prime:        0x100000001b3      =  1099511628211
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void, c_double
from pcc.py_runtime.py.py_abi_constants import (
    C_POINTER_SIZE,
    DICTENTRY_KEY_OFFSET,
    DICTENTRY_SIZE,
    DICTENTRY_VALUE_OFFSET,
    PYBYTEARRAYOBJECT_BYTE_LEN_OFFSET,
    PYBYTEARRAYOBJECT_DATA_OFFSET,
    PYBYTESOBJECT_BYTE_LEN_OFFSET,
    PYBYTESOBJECT_DATA_OFFSET,
    PYDICTOBJECT_ENTRIES_OFFSET,
    PYDICTOBJECT_ENTRIES_USED_OFFSET,
    PYDICTOBJECT_ITEM_COUNT_OFFSET,
    PYFLOATOBJECT_VALUE_OFFSET,
    PYINTOBJECT_SIGN_OFFSET,
    PYCLASSOBJECT_FIELD_NAMES_OFFSET,
    PYCLASSOBJECT_NAME_OFFSET,
    PYCLASSOBJECT_N_FIELDS_OFFSET,
    PYINSTANCEOBJECT_CLS_OFFSET,
    PYINSTANCEOBJECT_FIELDS_OFFSET,
    PYLISTOBJECT_ITEMS_OFFSET,
    PYMEMORYVIEWOBJECT_BASE_OFFSET,
    PYOBJECTHEADER_TYPE_TAG_OFFSET,
    PYSTROBJECT_BYTE_LEN_OFFSET,
    PYSTROBJECT_DATA_OFFSET,
    PYSTROBJECT_HASH_OFFSET,
    PYTUPLEOBJECT_ITEMS_OFFSET,
    PYTUPLEOBJECT_LEN_OFFSET,
    PY_TYPE_BOOL,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_DICT,
    PY_TYPE_FLOAT,
    PY_TYPE_INT,
    PY_TYPE_LIST,
    PY_TYPE_MEMORYVIEW,
    PY_TYPE_NONE,
    PY_TYPE_SET,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
    PY_TYPE_VALUEBOX,
)
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_INSTANCE,
    PY_TYPE_USER_CLASS_START,
)
from pcc.unsafe import (
    cstr,
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i64,
    untag_int,
)

py_int_value_i64     = extern("py_int_value_i64",     (c_ptr,),                    c_int64)
py_int_from_i64      = extern("py_int_from_i64",      (c_int64,),                  c_ptr)
py_int_cmp           = extern("py_int_cmp",           (c_ptr, c_ptr),              c_int32)
py_int_neg           = extern("py_int_neg",           (c_ptr,),                    c_ptr)
py_float_from_f64    = extern("py_float_from_f64",    (c_double,),                 c_ptr)
py_float_to_f64      = extern("py_float_to_f64",      (c_ptr,),                    c_double)

py_set_issubset      = extern("py_set_issubset",      (c_ptr, c_ptr),              c_int64)
py_set_issuperset    = extern("py_set_issuperset",    (c_ptr, c_ptr),              c_int64)
py_set_len           = extern("py_set_len",           (c_ptr,),                    c_int64)
py_str_eq            = extern("py_str_eq",            (c_ptr, c_ptr),              c_int32)
py_str_contains      = extern("py_str_contains",      (c_ptr, c_ptr),              c_int32)
py_str_len           = extern("py_str_len",           (c_ptr,),                    c_int64)

py_list_new          = extern("py_list_new",          (c_int64,),                  c_ptr)
py_list_append       = extern("py_list_append",       (c_ptr, c_ptr),              c_void)
py_list_get          = extern("py_list_get",          (c_ptr, c_int64),            c_ptr)
py_list_set          = extern("py_list_set",          (c_ptr, c_int64, c_ptr),     c_void)
py_list_len          = extern("py_list_len",          (c_ptr,),                    c_int64)
py_list_contains     = extern("py_list_contains",     (c_ptr, c_ptr),              c_int64)
py_dict_keys         = extern("py_dict_keys",         (c_ptr,),                    c_ptr)

py_tuple_get         = extern("py_tuple_get",         (c_ptr, c_int64),            c_ptr)
py_tuple_len         = extern("py_tuple_len",         (c_ptr,),                    c_int64)

py_dict_contains     = extern("py_dict_contains",     (c_ptr, c_ptr),              c_int64)
py_dict_get          = extern("py_dict_get",          (c_ptr, c_ptr),              c_ptr)
py_set_contains      = extern("py_set_contains",      (c_ptr, c_ptr),              c_int64)

py_obj_len           = extern("py_obj_len",           (c_ptr,),                    c_int64)
py_err_occurred      = extern("py_err_occurred",      (),                          c_int64)
py_clear_exception   = extern("py_clear_exception",   (),                          c_void)
py_obj_iter          = extern("py_obj_iter",          (c_ptr,),                    c_ptr)
py_obj_next          = extern("py_obj_next",          (c_ptr,),                    c_ptr)
py_current_exception = extern("py_current_exception", (),                          c_ptr)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,),                  c_ptr)
py_exc_matches       = extern("py_exc_matches",       (c_ptr, c_ptr),              c_int64)
py_obj_getitem       = extern("py_obj_getitem",       (c_ptr, c_ptr),              c_ptr)

py_user_hash_dispatch = extern("py_user_hash_dispatch", (c_ptr, c_ptr),            c_int64)
py_user_contains_dispatch = extern("py_user_contains_dispatch", (c_ptr, c_ptr, c_ptr), c_int64)
py_user_eq_dispatch = extern("py_user_eq_dispatch", (c_ptr, c_ptr),               c_int64)

pcc_gc_load_ptr      = extern("pcc_gc_load_ptr",      (c_ptr, c_ptr),              c_ptr)
py_exc_new           = extern("py_exc_new",           (c_int64, c_ptr),            c_ptr)
py_raise             = extern("py_raise",             (c_ptr,),                    c_void)
py_user_abs_dispatch = extern("py_user_abs_dispatch", (c_ptr,),                    c_ptr)
pcc_capi_is_cext_type_tag = extern("pcc_capi_is_cext_type_tag", (c_int64,),       c_int64)
pcc_capi_cext_absolute = extern("pcc_capi_cext_absolute", (c_ptr,),               c_ptr)
pcc_capi_cext_richcompare_bool = extern(
    "pcc_capi_cext_richcompare_bool", (c_ptr, c_ptr, c_int64), c_int64
)
py_err_occurred      = extern("py_err_occurred",      (),                          c_int64)
py_incref            = extern("py_incref",            (c_ptr,),                    c_void)
py_decref            = extern("py_decref",            (c_ptr,),                    c_void)


def _type_of(o) -> int:
    if is_tagged_int(o) != 0:
        return PY_TYPE_INT
    return load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET)


def _is_bool(o) -> int:
    if ptr_eq(o, global_load_ptr("py_True")) != 0:
        return 1
    if ptr_eq(o, global_load_ptr("py_False")) != 0:
        return 1
    return 0


def _cstr_eq(a, b) -> int:
    # Byte-by-byte NUL-terminated compare, inlined to avoid pulling
    # the substrate strcmp helper into an active runtime module
    # (the substrate-helper boundary is enforced by
    # test_runtime_substrate_spike.py).
    if ptr_eq(a, b) != 0:
        return 1
    if ptr_is_null(a) != 0:
        return 0
    if ptr_is_null(b) != 0:
        return 0
    i: int = 0
    while True:
        ca: int = load_i8(a, i) & 255
        cb: int = load_i8(b, i) & 255
        if ca != cb:
            return 0
        if ca == 0:
            return 1
        i = i + 1


def _valuebox_classes_eq(a, b) -> int:
    if ptr_eq(a, b) != 0:
        if ptr_is_null(a) == 0:
            return 1
        return 0
    if ptr_is_null(a) != 0:
        return 0
    if ptr_is_null(b) != 0:
        return 0
    if _cstr_eq(
        load_ptr(a, PYCLASSOBJECT_NAME_OFFSET),
        load_ptr(b, PYCLASSOBJECT_NAME_OFFSET),
    ) == 0:
        return 0
    n_fields: int = load_i32(a, PYCLASSOBJECT_N_FIELDS_OFFSET)
    if n_fields != load_i32(b, PYCLASSOBJECT_N_FIELDS_OFFSET):
        return 0
    if n_fields < 0:
        return 0
    field_names_a = load_ptr(a, PYCLASSOBJECT_FIELD_NAMES_OFFSET)
    field_names_b = load_ptr(b, PYCLASSOBJECT_FIELD_NAMES_OFFSET)
    i: int = 0
    while i < n_fields:
        fa = null()
        fb = null()
        if ptr_is_null(field_names_a) == 0:
            fa = load_ptr(field_names_a, i * C_POINTER_SIZE)
        if ptr_is_null(field_names_b) == 0:
            fb = load_ptr(field_names_b, i * C_POINTER_SIZE)
        if _cstr_eq(fa, fb) == 0:
            return 0
        i = i + 1
    return 1


def _is_int_like_tag(tag: int) -> int:
    if tag == PY_TYPE_INT:
        return 1
    if tag == PY_TYPE_BOOL:
        return 1
    return 0


def _bool_as_i64(o) -> int:
    if ptr_eq(o, global_load_ptr("py_True")) != 0:
        return 1
    return 0


def _int_or_bool_as_i64(o) -> int:
    if _is_bool(o) != 0:
        return _bool_as_i64(o)
    return py_int_value_i64(o)


def _is_bytes_like_tag(tag: int) -> int:
    if tag == PY_TYPE_BYTES:
        return 1
    if tag == PY_TYPE_BYTEARRAY:
        return 1
    if tag == PY_TYPE_MEMORYVIEW:
        return 1
    return 0


def _bytes_len(o) -> int:
    if _type_of(o) == PY_TYPE_MEMORYVIEW:
        base = pcc_gc_load_ptr(o, ptr_add(o, PYMEMORYVIEWOBJECT_BASE_OFFSET))
        return _bytes_len(base)
    return load_i64(o, PYBYTESOBJECT_BYTE_LEN_OFFSET)


def _bytes_data_ptr(o):
    if _type_of(o) == PY_TYPE_MEMORYVIEW:
        base = pcc_gc_load_ptr(o, ptr_add(o, PYMEMORYVIEWOBJECT_BASE_OFFSET))
        return _bytes_data_ptr(base)
    return ptr_add(o, PYBYTESOBJECT_DATA_OFFSET)


def _dict_key(d, entries, off: int):
    k = load_ptr(entries, off + DICTENTRY_KEY_OFFSET)
    if ptr_is_null(k) != 0:
        return k
    return pcc_gc_load_ptr(d, ptr_add(entries, off + DICTENTRY_KEY_OFFSET))


def _dict_value(d, entries, off: int):
    v = load_ptr(entries, off + DICTENTRY_VALUE_OFFSET)
    if ptr_is_null(v) != 0:
        return v
    return pcc_gc_load_ptr(d, ptr_add(entries, off + DICTENTRY_VALUE_OFFSET))


def _set_key(s, entries, off: int):
    k = load_ptr(entries, off + DICTENTRY_KEY_OFFSET)
    if ptr_is_null(k) != 0:
        return k
    if ptr_eq(k, global_load_ptr("py_set_dummy")) != 0:
        return k
    return pcc_gc_load_ptr(s, ptr_add(entries, off + DICTENTRY_KEY_OFFSET))


def _bytes_cmp(a, b) -> int:
    la: int = _bytes_len(a)
    lb: int = _bytes_len(b)
    n: int = la
    if lb < n:
        n = lb
    da = _bytes_data_ptr(a)
    db = _bytes_data_ptr(b)
    i: int = 0
    while i < n:
        ba: int = load_i8(da, i) & 0xFF
        bb: int = load_i8(db, i) & 0xFF
        if ba < bb:
            return -1
        if ba > bb:
            return 1
        i = i + 1
    if la < lb:
        return -1
    if la > lb:
        return 1
    return 0


# ---- FNV-1a ---------------------------------------------------------

def _fnv1a(p, n: int) -> int:
    # 0xcbf29ce484222325 as signed i64.
    h: int = -3750763034362895579
    i: int = 0
    while i < n:
        b: int = load_i8(p, i) & 0xFF
        h = h ^ b
        # Multiply with i64 wrap. Signed mul wraps the same as unsigned.
        h = h * 1099511628211
        i = i + 1
    if h == -1:
        return -2
    return h


# ---- Three-way compare (recursive over containers) ------------------

def _cmp_threeway(a, b) -> int:
    if ptr_eq(a, b) != 0:
        return 0
    if ptr_is_null(a) != 0:
        if ptr_is_null(b) != 0:
            return 0
        return -1
    if ptr_is_null(b) != 0:
        return 1

    ta: int = _type_of(a)
    tb: int = _type_of(b)
    a_is_int: int = _is_int_like_tag(ta)
    b_is_int: int = _is_int_like_tag(tb)

    if a_is_int != 0:
        if b_is_int != 0:
            if ta == PY_TYPE_INT:
                if tb == PY_TYPE_INT:
                    return py_int_cmp(a, b)
            av: int = _int_or_bool_as_i64(a)
            bv: int = _int_or_bool_as_i64(b)
            if av < bv:
                return -1
            if av > bv:
                return 1
            return 0

    # Numeric with at least one float (pure int/int handled above): compare as
    # doubles. Without this, float vs int / float vs float fell through to the
    # final ``return 0`` (treated as equal), so boxed-float comparisons via
    # py_obj_lt/gt were wrong.
    a_num: int = a_is_int
    if ta == PY_TYPE_FLOAT:
        a_num = 1
    b_num: int = b_is_int
    if tb == PY_TYPE_FLOAT:
        b_num = 1
    if a_num != 0 and b_num != 0:
        fa: float = py_float_to_f64(a)
        fb: float = py_float_to_f64(b)
        if fa < fb:
            return -1
        if fa > fb:
            return 1
        return 0

    if ta == PY_TYPE_STR:                       # STR
        if tb == PY_TYPE_STR:
            # memcmp byte semantics over min(len_a, len_b), scanned a
            # 64-bit word at a time (both data blocks start at offset
            # 40, so 8-step i64 loads stay aligned). i64 words cannot
            # be ordered directly on a little-endian host, so the first
            # differing word falls back to byte order inside it. The
            # old byte-by-byte loop dominated codegen-worker profiles
            # (sorted symbol names share long prefixes).
            la: int = load_i64(a, PYSTROBJECT_BYTE_LEN_OFFSET)
            lb: int = load_i64(b, PYSTROBJECT_BYTE_LEN_OFFSET)
            n: int = la
            if lb < n:
                n = lb
            da = ptr_add(a, PYSTROBJECT_DATA_OFFSET)
            db = ptr_add(b, PYSTROBJECT_DATA_OFFSET)
            i: int = 0
            w_end: int = n - 7
            while i < w_end:
                wa: int = load_i64(da, i)
                wb: int = load_i64(db, i)
                if wa != wb:
                    j: int = i
                    stop: int = i + 8
                    while j < stop:
                        wba: int = load_i8(da, j) & 0xFF
                        wbb: int = load_i8(db, j) & 0xFF
                        if wba < wbb:
                            return -1
                        if wba > wbb:
                            return 1
                        j = j + 1
                i = i + 8
            while i < n:
                ba: int = load_i8(da, i) & 0xFF
                bb: int = load_i8(db, i) & 0xFF
                if ba < bb:
                    return -1
                if ba > bb:
                    return 1
                i = i + 1
            if la < lb:
                return -1
            if la > lb:
                return 1
            return 0

    if _is_bytes_like_tag(ta) != 0:
        if _is_bytes_like_tag(tb) != 0:
            return _bytes_cmp(a, b)

    if ta == PY_TYPE_TUPLE:                       # TUPLE
        if tb == PY_TYPE_TUPLE:
            la: int = py_tuple_len(a)
            lb: int = py_tuple_len(b)
            n: int = la
            if lb < n:
                n = lb
            i: int = 0
            while i < n:
                ea = py_tuple_get(a, i)
                eb = py_tuple_get(b, i)
                r: int = _cmp_threeway(ea, eb)
                if r != 0:
                    return r
                i = i + 1
            if la < lb:
                return -1
            if la > lb:
                return 1
            return 0

    if ta == PY_TYPE_LIST:                       # LIST
        if tb == PY_TYPE_LIST:
            la: int = py_list_len(a)
            lb: int = py_list_len(b)
            n: int = la
            if lb < n:
                n = lb
            i: int = 0
            while i < n:
                ea = py_list_get(a, i)
                eb = py_list_get(b, i)
                r: int = _cmp_threeway(ea, eb)
                py_decref(ea)
                py_decref(eb)
                if r != 0:
                    return r
                i = i + 1
            if la < lb:
                return -1
            if la > lb:
                return 1
            return 0

    if ta == PY_TYPE_NONE:
        if tb == PY_TYPE_NONE:
            return 0

    return 0


@c_abi_export("py_obj_abs")
def py_obj_abs(o):
    if ptr_is_null(o) != 0:
        py_raise(py_exc_new(3, cstr("bad operand type for abs()")))
        return null()
    if is_tagged_int(o) != 0:
        ivalue: int = py_int_value_i64(o)
        if ivalue < 0:
            return py_int_neg(o)
        return py_int_from_i64(ivalue)
    tag: int = _type_of(o)
    if tag == PY_TYPE_BOOL:                      # BOOL
        return py_int_from_i64(_bool_as_i64(o))
    if tag == PY_TYPE_INT:                      # INT
        if load_i32(o, PYINTOBJECT_SIGN_OFFSET) < 0:
            return py_int_neg(o)
        py_incref(o)
        return o
    if tag == PY_TYPE_FLOAT:                      # FLOAT
        fvalue: float = py_float_to_f64(o)
        if fvalue < 0.0:
            return py_float_from_f64(0.0 - fvalue)
        return py_float_from_f64(fvalue)
    if pcc_capi_is_cext_type_tag(tag) != 0:
        return pcc_capi_cext_absolute(o)
    if tag == PY_TYPE_INSTANCE or tag >= PY_TYPE_USER_CLASS_START:
        r = py_user_abs_dispatch(o)
        if ptr_is_null(r) == 0:
            return r
        if py_err_occurred() != 0:
            return null()
    py_raise(py_exc_new(3, cstr("bad operand type for abs()")))
    return null()


# ---- Equality -------------------------------------------------------

@c_abi_export("py_obj_eq")
def py_obj_eq(a, b) -> int:
    if ptr_eq(a, b) != 0:
        return 1
    if ptr_is_null(a) != 0:
        return 0
    if ptr_is_null(b) != 0:
        return 0

    ta: int = _type_of(a)
    tb: int = _type_of(b)

    # numpy / C-extension scalar ==: drive its tp_richcompare (Py_EQ=2), same as
    # py_obj_lt/le/gt/ge already do. Without this a[i] == 3 fell through to the
    # default not-equal and returned False even when the values matched.
    if pcc_capi_is_cext_type_tag(ta) != 0:
        if pcc_capi_cext_richcompare_bool(a, b, 2) > 0:
            return 1
        return 0
    if pcc_capi_is_cext_type_tag(tb) != 0:
        if pcc_capi_cext_richcompare_bool(a, b, 2) > 0:
            return 1
        return 0

    if ta == PY_TYPE_BOOL:                       # BOOL ↔ BOOL: distinct singletons
        if tb == PY_TYPE_BOOL:
            return 0

    if ta == PY_TYPE_STR:                       # STR
        if tb == PY_TYPE_STR:
            if py_str_eq(a, b) != 0:
                return 1
            return 0

    a_int: int = 0
    if ta == PY_TYPE_BOOL or ta == PY_TYPE_INT:
        a_int = 1
    b_int: int = 0
    if tb == PY_TYPE_BOOL or tb == PY_TYPE_INT:
        b_int = 1

    if a_int != 0:
        if b_int != 0:
            if ta == PY_TYPE_INT:
                if tb == PY_TYPE_INT:
                    if py_int_cmp(a, b) == 0:
                        return 1
                    return 0
            if _int_or_bool_as_i64(a) == _int_or_bool_as_i64(b):
                return 1
            return 0

    # Numeric with at least one float (pure int/int handled above): compare as
    # doubles. py_obj_eq had no FLOAT (tag 3) branch, so float==float and
    # float==int fell through to the default ``return 0`` (not-equal) — e.g.
    # ``(c.v / c.w) == 2.5`` was False. Mirrors the float arm of _py_obj_cmp.
    a_num: int = a_int
    if ta == PY_TYPE_FLOAT:
        a_num = 1
    b_num: int = b_int
    if tb == PY_TYPE_FLOAT:
        b_num = 1
    if a_num != 0 and b_num != 0:
        fa: float = py_float_to_f64(a)
        fb: float = py_float_to_f64(b)
        if fa < fb:
            return 0
        if fa > fb:
            return 0
        return 1

    if _is_bytes_like_tag(ta) != 0:
        if _is_bytes_like_tag(tb) != 0:
            if _bytes_cmp(a, b) == 0:
                return 1
            return 0

    if ta == PY_TYPE_TUPLE:                       # TUPLE
        if tb == PY_TYPE_TUPLE:
            la: int = load_i64(a, PYTUPLEOBJECT_LEN_OFFSET)
            lb: int = load_i64(b, PYTUPLEOBJECT_LEN_OFFSET)
            if la != lb:
                return 0
            i: int = 0
            while i < la:
                ea = pcc_gc_load_ptr(a, ptr_add(a, PYTUPLEOBJECT_ITEMS_OFFSET + i * 8))
                eb = pcc_gc_load_ptr(b, ptr_add(b, PYTUPLEOBJECT_ITEMS_OFFSET + i * 8))
                if ptr_eq(ea, eb) == 0:
                    if is_tagged_int(ea) != 0 and is_tagged_int(eb) != 0:
                        return 0
                    if is_tagged_int(ea) == 0 and is_tagged_int(eb) == 0:
                        if ptr_is_null(ea) != 0:
                            return 0
                        if ptr_is_null(eb) != 0:
                            return 0
                        if load_i32(ea, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_STR and load_i32(eb, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_STR:
                            if py_str_eq(ea, eb) == 0:
                                return 0
                        else:
                            if py_obj_eq(ea, eb) == 0:
                                return 0
                    else:
                        if py_obj_eq(ea, eb) == 0:
                            return 0
                i = i + 1
            return 1

    if ta == PY_TYPE_LIST:                       # LIST
        if tb == PY_TYPE_LIST:
            la: int = py_list_len(a)
            lb: int = py_list_len(b)
            if la != lb:
                return 0
            i: int = 0
            while i < la:
                ea = py_list_get(a, i)
                eb = py_list_get(b, i)
                eq: int = py_obj_eq(ea, eb)
                py_decref(ea)
                py_decref(eb)
                if eq == 0:
                    return 0
                i = i + 1
            return 1

    if ta == PY_TYPE_DICT:                       # DICT
        if tb == PY_TYPE_DICT:
            da_size: int = load_i64(a, PYDICTOBJECT_ITEM_COUNT_OFFSET)
            db_size: int = load_i64(b, PYDICTOBJECT_ITEM_COUNT_OFFSET)
            if da_size != db_size:
                return 0
            entries = load_ptr(a, PYDICTOBJECT_ENTRIES_OFFSET)
            used: int = load_i64(a, PYDICTOBJECT_ENTRIES_USED_OFFSET)
            i: int = 0
            while i < used:
                off: int = i * DICTENTRY_SIZE
                key = _dict_key(a, entries, off)
                if ptr_is_null(key) == 0:
                    val = _dict_value(a, entries, off)
                    other = py_dict_get(b, key)
                    if ptr_is_null(other) != 0:
                        return 0
                    eq: int = py_obj_eq(val, other)
                    py_decref(other)
                    if eq == 0:
                        return 0
                i = i + 1
            return 1

    if ta == PY_TYPE_SET:                       # SET
        if tb == PY_TYPE_SET:
            if load_i64(a, 16) != load_i64(b, 16):
                return 0
            entries = load_ptr(a, 40)
            capacity: int = load_i64(a, 24)
            dummy = global_load_ptr("py_set_dummy")
            i: int = 0
            while i < capacity:
                key = _set_key(a, entries, i * 16)
                if ptr_is_null(key) == 0:
                    if ptr_eq(key, dummy) == 0:
                        if py_set_contains(b, key) == 0:
                            return 0
                i = i + 1
            return 1

    if ta == PY_TYPE_VALUEBOX:                     # VALUEBOX
        if tb == PY_TYPE_VALUEBOX:
            cls_a = pcc_gc_load_ptr(a, ptr_add(a, PYINSTANCEOBJECT_CLS_OFFSET))
            cls_b = pcc_gc_load_ptr(b, ptr_add(b, PYINSTANCEOBJECT_CLS_OFFSET))
            if _valuebox_classes_eq(cls_a, cls_b) == 0:
                return 0
            n_fields: int = load_i32(cls_a, PYCLASSOBJECT_N_FIELDS_OFFSET)
            if n_fields < 0:
                return 0
            fields_a = ptr_add(a, PYINSTANCEOBJECT_FIELDS_OFFSET)
            fields_b = ptr_add(b, PYINSTANCEOBJECT_FIELDS_OFFSET)
            i: int = 0
            while i < n_fields:
                va = pcc_gc_load_ptr(
                    a, ptr_add(fields_a, i * C_POINTER_SIZE)
                )
                vb = pcc_gc_load_ptr(
                    b, ptr_add(fields_b, i * C_POINTER_SIZE)
                )
                if ptr_eq(va, vb) == 0:
                    if ptr_is_null(va) != 0:
                        return 0
                    if ptr_is_null(vb) != 0:
                        return 0
                    if py_obj_eq(va, vb) == 0:
                        return 0
                i = i + 1
            return 1

    if ta == PY_TYPE_NONE:
        return 0
    if tb == PY_TYPE_NONE:
        return 0

    return 0


# ---- Hash -----------------------------------------------------------

@c_abi_export("py_obj_hash")
def py_obj_hash(o) -> int:
    if ptr_is_null(o) != 0:
        return 0
    if is_tagged_int(o) != 0:
        v: int = untag_int(o)
        if v == -1:
            return -2
        return v
    tag: int = load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if tag == PY_TYPE_NONE:                      # NONE
        return 0
    if tag == PY_TYPE_BOOL:                      # BOOL
        if ptr_eq(o, global_load_ptr("py_True")) != 0:
            return 1
        return 0
    if tag == PY_TYPE_VALUEBOX:                    # VALUEBOX
        cls = pcc_gc_load_ptr(o, ptr_add(o, PYINSTANCEOBJECT_CLS_OFFSET))
        if ptr_is_null(cls) != 0:
            return 0
        n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
        if n_fields < 0:
            return 0
        h: int = n_fields
        fields = ptr_add(o, PYINSTANCEOBJECT_FIELDS_OFFSET)
        i: int = 0
        while i < n_fields:
            v = pcc_gc_load_ptr(o, ptr_add(fields, i * C_POINTER_SIZE))
            field_hash: int = 0
            if ptr_is_null(v) == 0:
                field_hash = py_obj_hash(v)
                if py_err_occurred() != 0:
                    return -1
            h = (h * 31 + (field_hash % 1000003)) % 1000000007
            i = i + 1
        if h == -1:
            return -2
        return h
    if tag == PY_TYPE_INT:                      # INT
        v: int = py_int_value_i64(o)
        if v == -1:
            return -2
        return v
    if tag == PY_TYPE_FLOAT:                      # FLOAT — read as i64 bits
        v: int = load_i64(o, PYFLOATOBJECT_VALUE_OFFSET)
        if v == -1:
            return -2
        return v
    if tag == PY_TYPE_STR:                      # STR — FNV-1a with cache @32
        cached: int = load_i64(o, PYSTROBJECT_HASH_OFFSET)
        if cached != -1:
            return cached
        bl: int = load_i64(o, PYSTROBJECT_BYTE_LEN_OFFSET)
        data_ptr = ptr_add(o, PYSTROBJECT_DATA_OFFSET)
        h: int = _fnv1a(data_ptr, bl)
        store_i64(o, PYSTROBJECT_HASH_OFFSET, h)
        return h
    if tag == PY_TYPE_BYTES:                     # BYTES
        bl: int = load_i64(o, PYBYTESOBJECT_BYTE_LEN_OFFSET)
        data_ptr = ptr_add(o, PYBYTESOBJECT_DATA_OFFSET)
        return _fnv1a(data_ptr, bl)
    if tag == PY_TYPE_LIST:
        py_raise(py_exc_new(3, cstr("unhashable type: 'list'")))
        return -1
    if tag == PY_TYPE_DICT:
        py_raise(py_exc_new(3, cstr("unhashable type: 'dict'")))
        return -1
    if tag == PY_TYPE_SET:
        py_raise(py_exc_new(3, cstr("unhashable type: 'set'")))
        return -1
    if tag == PY_TYPE_BYTEARRAY:
        py_raise(py_exc_new(3, cstr("unhashable type: 'bytearray'")))
        return -1
    if tag == PY_TYPE_TUPLE:                      # TUPLE
        n: int = load_i64(o, PYTUPLEOBJECT_LEN_OFFSET)
        h: int = 3527539
        mult: int = 1000003
        read_barrier_enabled: int = load_i32(
            global_addr("pcc_gc_read_barrier_enabled"), 0
        )
        i: int = 0
        while i < n:
            slot = ptr_add(o, PYTUPLEOBJECT_ITEMS_OFFSET + i * 8)
            el = load_ptr(slot, 0)
            if read_barrier_enabled != 0:
                el = pcc_gc_load_ptr(o, slot)
            el_hash: int = 0
            handled: int = 0
            if ptr_is_null(el) != 0:
                handled = 1
            else:
                if is_tagged_int(el) != 0:
                    v: int = untag_int(el)
                    el_hash = v
                    if v == -1:
                        el_hash = -2
                    handled = 1
                else:
                    el_tag: int = load_i32(el, PYOBJECTHEADER_TYPE_TAG_OFFSET)
                    if el_tag == PY_TYPE_NONE:
                        handled = 1
                    elif el_tag == PY_TYPE_BOOL:
                        if ptr_eq(el, global_load_ptr("py_True")) != 0:
                            el_hash = 1
                        handled = 1
                    elif el_tag == PY_TYPE_INT:
                        v2: int = py_int_value_i64(el)
                        el_hash = v2
                        if v2 == -1:
                            el_hash = -2
                        handled = 1
                    elif el_tag == PY_TYPE_STR:
                        cached: int = load_i64(el, PYSTROBJECT_HASH_OFFSET)
                        if cached != -1:
                            el_hash = cached
                        else:
                            bl: int = load_i64(el, PYSTROBJECT_BYTE_LEN_OFFSET)
                            data_ptr = ptr_add(el, PYSTROBJECT_DATA_OFFSET)
                            el_hash = _fnv1a(data_ptr, bl)
                            store_i64(el, PYSTROBJECT_HASH_OFFSET, el_hash)
                        handled = 1
            if handled == 0:
                el_hash = py_obj_hash(el)
                if py_err_occurred() != 0:
                    return -1
            h = ((h ^ el_hash) * mult + 82520 + i + i) & 9223372036854775807
            mult = mult + 82520 + i + i
            i = i + 1
        h = h + 97531
        if h == -1:
            return -2
        return h
    if tag == PY_TYPE_INSTANCE or tag >= PY_TYPE_USER_CLASS_START:
        return py_user_hash_dispatch(o, null())
    return 0


# ---- Comparison ops -------------------------------------------------

# Sets order by subset/superset (a PARTIAL order), not the total 3-way
# compare: ``a <= b`` is a.issubset(b), ``a < b`` is proper subset, etc.
def _both_sets(a, b) -> int:
    if _type_of(a) == PY_TYPE_SET and _type_of(b) == PY_TYPE_SET:  # PY_TYPE_SET
        return 1
    return 0


@c_abi_export("py_obj_lt")
def py_obj_lt(a, b) -> int:
    if (
        pcc_capi_is_cext_type_tag(_type_of(a)) != 0
        or pcc_capi_is_cext_type_tag(_type_of(b)) != 0
    ):
        if pcc_capi_cext_richcompare_bool(a, b, 0) > 0:
            return 1
        return 0
    if _both_sets(a, b) != 0:
        if py_set_issubset(a, b) != 0 and py_set_len(a) < py_set_len(b):
            return 1
        return 0
    if _cmp_threeway(a, b) < 0:
        return 1
    return 0


@c_abi_export("py_obj_le")
def py_obj_le(a, b) -> int:
    if (
        pcc_capi_is_cext_type_tag(_type_of(a)) != 0
        or pcc_capi_is_cext_type_tag(_type_of(b)) != 0
    ):
        if pcc_capi_cext_richcompare_bool(a, b, 1) > 0:
            return 1
        return 0
    if _both_sets(a, b) != 0:
        return py_set_issubset(a, b)
    if _cmp_threeway(a, b) <= 0:
        return 1
    return 0


@c_abi_export("py_obj_gt")
def py_obj_gt(a, b) -> int:
    if (
        pcc_capi_is_cext_type_tag(_type_of(a)) != 0
        or pcc_capi_is_cext_type_tag(_type_of(b)) != 0
    ):
        if pcc_capi_cext_richcompare_bool(a, b, 4) > 0:
            return 1
        return 0
    if _both_sets(a, b) != 0:
        if py_set_issuperset(a, b) != 0 and py_set_len(a) > py_set_len(b):
            return 1
        return 0
    if _cmp_threeway(a, b) > 0:
        return 1
    return 0


@c_abi_export("py_obj_ge")
def py_obj_ge(a, b) -> int:
    if (
        pcc_capi_is_cext_type_tag(_type_of(a)) != 0
        or pcc_capi_is_cext_type_tag(_type_of(b)) != 0
    ):
        if pcc_capi_cext_richcompare_bool(a, b, 5) > 0:
            return 1
        return 0
    if _both_sets(a, b) != 0:
        return py_set_issuperset(a, b)
    if _cmp_threeway(a, b) >= 0:
        return 1
    return 0


@c_abi_export("py_obj_min_max")
def py_obj_min_max(iterable, want_max: int):
    """Return the minimum or maximum owned item from an iterable."""
    if ptr_is_null(iterable) != 0:
        return null()
    it = py_obj_iter(iterable)
    if ptr_is_null(it) != 0:
        return null()

    best = py_obj_next(it)
    if ptr_is_null(best) != 0:
        if py_err_occurred() != 0:
            current = py_current_exception()
            stop = py_exc_builtin_class(8)  # PY_EXC_STOPITERATION
            if py_exc_matches(current, stop) != 0:
                py_clear_exception()
            else:
                py_decref(it)
                return null()
        py_decref(it)
        if want_max != 0:
            py_raise(py_exc_new(2, cstr("max() arg is an empty sequence")))
        else:
            py_raise(py_exc_new(2, cstr("min() arg is an empty sequence")))
        return null()

    done: int = 0
    while done == 0:
        element = py_obj_next(it)
        if ptr_is_null(element) != 0:
            if py_err_occurred() != 0:
                current = py_current_exception()
                stop = py_exc_builtin_class(8)  # PY_EXC_STOPITERATION
                if py_exc_matches(current, stop) != 0:
                    py_clear_exception()
                else:
                    py_decref(best)
                    py_decref(it)
                    return null()
            done = 1
        else:
            replace: int = 0
            if want_max != 0:
                replace = py_obj_lt(best, element)
            else:
                replace = py_obj_lt(element, best)
            if replace != 0:
                py_decref(best)
                best = element
            else:
                py_decref(element)

    py_decref(it)
    return best


# ---- sorted (insertion sort) — fixed break logic --------------------

@c_abi_export("py_obj_sorted")
def py_obj_sorted(x):
    if ptr_is_null(x) != 0:
        return null()
    n: int = py_obj_len(x)
    # py_obj_len is a sizing hint only; a custom iterator (no __len__) raises
    # from it, and a pending exception would abort the iterator loop below
    # (yielding []). Clear it — the iterator branch handles length-less srcs.
    if py_err_occurred() != 0:
        py_clear_exception()
        n = 0
    out = py_list_new(n)
    if ptr_is_null(out) != 0:
        return null()
    if _type_of(x) == PY_TYPE_SET:
        entries = load_ptr(x, 40)
        capacity: int = load_i64(x, 24)
        dummy = global_load_ptr("py_set_dummy")
        i: int = 0
        while i < capacity:
            key = _set_key(x, entries, i * 16)
            if ptr_is_null(key) == 0:
                if ptr_eq(key, dummy) == 0:
                    py_list_append(out, key)
            i = i + 1
    elif _type_of(x) == PY_TYPE_DICT:
        # dict -> sort its keys. py_obj_getitem(dict, int) would look up the
        # int as a KEY (returns NULL -> [<null>,...]); use py_dict_keys instead.
        # (The C py_obj_sorted uses the iterator protocol for all non-indexables;
        # the port handles the common dict case here — generator/range in
        # default mode still fall to the indexable else-path, a follow-on.)
        keys = py_dict_keys(x)
        if ptr_is_null(keys) == 0:
            nk: int = py_list_len(keys)
            ki: int = 0
            while ki < nk:
                el = py_list_get(keys, ki)
                py_list_append(out, el)
                py_decref(el)
                ki = ki + 1
            py_decref(keys)
    else:
        # General length-less iterable (custom __iter__/__next__ class,
        # generator, range): use the iterator protocol — matching the C
        # py_obj_sorted. The old index-based py_obj_len + py_obj_getitem
        # path yielded [] for anything without __len__/__getitem__.
        it = py_obj_iter(x)
        if ptr_is_null(it) == 0:
            it_done: int = 0
            while it_done == 0:
                el = py_obj_next(it)
                if ptr_is_null(el) != 0:
                    if py_err_occurred() != 0:
                        cur = py_current_exception()
                        stop = py_exc_builtin_class(8)
                        if py_exc_matches(cur, stop) != 0:
                            py_clear_exception()
                    it_done = 1
                else:
                    py_list_append(out, el)
                    py_decref(el)
            py_decref(it)
    m: int = py_list_len(out)
    if m > 1:
        # Bottom-up stable merge sort, mirroring the C runtime (was
        # insertion sort — O(n^2) comparisons dominated codegen-worker
        # profiles via sorted symbol lists). Ping-pong between ``out``
        # and a scratch py_list: elements are MOVED borrowed (raw
        # barrier loads, py_list_append stores without incref), every
        # element stays GC-visible in at least one list slot, and the
        # scratch is emptied by resetting its length field before
        # release so aliasing slots never trigger element decrefs.
        # Stability: the right run only wins when strictly smaller.
        scratch = py_list_new(m)
        if ptr_is_null(scratch) == 0:
            src_list = out
            dst_list = scratch
            width: int = 1
            while width < m:
                # Reset dst through the BALANCED slot store (py_list_set
                # -> pcc_gc_store_ptr increfs new / decrefs old): a bare
                # length=0 write would leak one reference per element
                # per pass. Every element stays alive via its other
                # list's slot.
                dlen: int = py_list_len(dst_list)
                di: int = 0
                while di < dlen:
                    py_list_set(dst_list, di, null())
                    di = di + 1
                store_i64(dst_list, 16, 0)
                src_items = load_ptr(src_list, PYLISTOBJECT_ITEMS_OFFSET)
                lo: int = 0
                while lo < m:
                    mid: int = lo + width
                    if mid > m:
                        mid = m
                    hi: int = mid + width
                    if hi > m:
                        hi = m
                    mi: int = lo
                    mj: int = mid
                    while mi < mid and mj < hi:
                        ea = pcc_gc_load_ptr(
                            src_list, ptr_add(src_items, mi * 8)
                        )
                        eb = pcc_gc_load_ptr(
                            src_list, ptr_add(src_items, mj * 8)
                        )
                        if _cmp_threeway(eb, ea) < 0:
                            py_list_append(dst_list, eb)
                            mj = mj + 1
                        else:
                            py_list_append(dst_list, ea)
                            mi = mi + 1
                    while mi < mid:
                        py_list_append(
                            dst_list,
                            pcc_gc_load_ptr(
                                src_list, ptr_add(src_items, mi * 8)
                            ),
                        )
                        mi = mi + 1
                    while mj < hi:
                        py_list_append(
                            dst_list,
                            pcc_gc_load_ptr(
                                src_list, ptr_add(src_items, mj * 8)
                            ),
                        )
                        mj = mj + 1
                    lo = lo + 2 * width
                tmp = src_list
                src_list = dst_list
                dst_list = tmp
                width = width * 2
            if ptr_eq(src_list, out) == 0:
                # Final ordering ended in the scratch: move it back
                # (balanced: clear out's stale aliases first — each
                # element stays held by the scratch slot).
                olen: int = py_list_len(out)
                oi: int = 0
                while oi < olen:
                    py_list_set(out, oi, null())
                    oi = oi + 1
                store_i64(out, 16, 0)
                back_items = load_ptr(src_list, PYLISTOBJECT_ITEMS_OFFSET)
                bi: int = 0
                while bi < m:
                    py_list_append(
                        out,
                        pcc_gc_load_ptr(
                            src_list, ptr_add(back_items, bi * 8)
                        ),
                    )
                    bi = bi + 1
            # Release the scratch's element references (balanced) and
            # the scratch itself; out's slots keep the elements.
            slen: int = py_list_len(scratch)
            si: int = 0
            while si < slen:
                py_list_set(scratch, si, null())
                si = si + 1
            store_i64(scratch, 16, 0)
            py_decref(scratch)
            return out
        # malloc-failure fallback: original insertion sort.
        j: int = 1
        while j < m:
            cur = py_list_get(out, j)
            k: int = j
            # Use a `done` flag in the loop condition (NOT mutating
            # k=0) — earlier port mutated k=0 to "break" then
            # unconditionally wrote cur to out[k].
            done: int = 0
            while k > 0 and done == 0:
                prev = py_list_get(out, k - 1)
                if _cmp_threeway(prev, cur) <= 0:
                    py_decref(prev)
                    done = 1
                else:
                    py_list_set(out, k, prev)
                    py_decref(prev)
                    k = k - 1
            py_list_set(out, k, cur)
            py_decref(cur)
            j = j + 1
    return out


# ---- Membership -----------------------------------------------------

@c_abi_export("py_obj_contains")
def py_obj_contains(container, item) -> int:
    if ptr_is_null(container) != 0:
        return 0
    tag: int = _type_of(container)
    if tag == PY_TYPE_LIST:                      # LIST
        if py_list_contains(container, item) != 0:
            return 1
        return 0
    if tag == PY_TYPE_TUPLE:                      # TUPLE — linear scan via py_obj_eq
        n: int = py_tuple_len(container)
        i: int = 0
        while i < n:
            el = py_tuple_get(container, i)
            if py_obj_eq(el, item) != 0:
                return 1
            i = i + 1
        return 0
    if tag == PY_TYPE_DICT:                      # DICT
        if py_dict_contains(container, item) != 0:
            return 1
        return 0
    if tag == PY_TYPE_SET:                      # SET
        if py_set_contains(container, item) != 0:
            return 1
        return 0
    if tag == PY_TYPE_STR:                      # STR
        if py_str_contains(container, item) != 0:
            return 1
        return 0
    if tag == PY_TYPE_INSTANCE or tag >= PY_TYPE_USER_CLASS_START:
        return py_user_contains_dispatch(container, item, null())
    return 0
