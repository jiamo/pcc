"""Phase 4c.15b: pcc-Python port of py_obj_ops_compare.c.

Equality / hashing / three-way compare / sorted / contains.

The earlier port had an insertion-sort bug: I used `k = 0` to "break"
the inner loop, then unconditionally wrote cur to out[k] — which
overwrote slot 0 with later elements instead of leaving them at slot
j. The fix uses a `done` flag so the post-loop k is the correct slot.

Type tags (inlined per the module-init gotcha):
    PY_TYPE_NONE  = 0   PY_TYPE_BOOL  = 1   PY_TYPE_INT   = 2
    PY_TYPE_FLOAT = 3   PY_TYPE_STR   = 4   PY_TYPE_LIST  = 5
    PY_TYPE_DICT  = 6   PY_TYPE_TUPLE = 7   PY_TYPE_SET   = 8

Object layouts:
    PyStrObject:  byte_len@16 (i64), cp_len@24, hash@32 (i64, -1=unset), data@40
    PyListObject: length@16   (i64),  capacity@24, items@32 (ptr)
    PyTupleObject: len@16     (i64),  items[]@24  (flex)

FNV-1a constants (verified to work in pcc-Python signed-i64):
    offset basis: 0xcbf29ce484222325 = -3750763034362895579 (signed)
    prime:        0x100000001b3      =  1099511628211
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
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
)

py_int_value_i64     = extern("py_int_value_i64",     (c_ptr,),                    c_int64)
py_int_from_i64      = extern("py_int_from_i64",      (c_int64,),                  c_ptr)
py_int_cmp           = extern("py_int_cmp",           (c_ptr, c_ptr),              c_int32)

py_str_eq            = extern("py_str_eq",            (c_ptr, c_ptr),              c_int32)
py_str_contains      = extern("py_str_contains",      (c_ptr, c_ptr),              c_int32)
py_str_len           = extern("py_str_len",           (c_ptr,),                    c_int64)

py_list_new          = extern("py_list_new",          (c_int64,),                  c_ptr)
py_list_append       = extern("py_list_append",       (c_ptr, c_ptr),              c_void)
py_list_get          = extern("py_list_get",          (c_ptr, c_int64),            c_ptr)
py_list_set          = extern("py_list_set",          (c_ptr, c_int64, c_ptr),     c_void)
py_list_len          = extern("py_list_len",          (c_ptr,),                    c_int64)
py_list_contains     = extern("py_list_contains",     (c_ptr, c_ptr),              c_int64)

py_tuple_get         = extern("py_tuple_get",         (c_ptr, c_int64),            c_ptr)
py_tuple_len         = extern("py_tuple_len",         (c_ptr,),                    c_int64)

py_dict_contains     = extern("py_dict_contains",     (c_ptr, c_ptr),              c_int64)
py_set_contains      = extern("py_set_contains",      (c_ptr, c_ptr),              c_int64)

py_obj_len           = extern("py_obj_len",           (c_ptr,),                    c_int64)
py_obj_getitem       = extern("py_obj_getitem",       (c_ptr, c_ptr),              c_ptr)

py_decref            = extern("py_decref",            (c_ptr,),                    c_void)


def _type_of(o) -> int:
    if is_tagged_int(o) != 0:
        return 2          # PY_TYPE_INT
    return load_i32(o, 8)


def _is_bool(o) -> int:
    if ptr_eq(o, global_load_ptr("py_True")) != 0:
        return 1
    if ptr_eq(o, global_load_ptr("py_False")) != 0:
        return 1
    return 0


def _is_int_like_tag(tag: int) -> int:
    if tag == 2:
        return 1
    if tag == 1:
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
            if ta == 2:
                if tb == 2:
                    return py_int_cmp(a, b)
            av: int = _int_or_bool_as_i64(a)
            bv: int = _int_or_bool_as_i64(b)
            if av < bv:
                return -1
            if av > bv:
                return 1
            return 0

    if ta == 4:                       # STR
        if tb == 4:
            # Use byte-by-byte cmp via load_i8 over min(len_a, len_b).
            la: int = load_i64(a, 16)
            lb: int = load_i64(b, 16)
            n: int = la
            if lb < n:
                n = lb
            da = ptr_add(a, 40)
            db = ptr_add(b, 40)
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

    if ta == 7:                       # TUPLE
        if tb == 7:
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

    if ta == 5:                       # LIST
        if tb == 5:
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

    if ta == 0:
        if tb == 0:
            return 0

    return 0


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

    if ta == 1:                       # BOOL ↔ BOOL: distinct singletons
        if tb == 1:
            return 0

    if _is_int_like_tag(ta) != 0:
        if _is_int_like_tag(tb) != 0:
            if ta == 2:
                if tb == 2:
                    if py_int_cmp(a, b) == 0:
                        return 1
                    return 0
            if _int_or_bool_as_i64(a) == _int_or_bool_as_i64(b):
                return 1
            return 0

    if ta == 4:                       # STR
        if tb == 4:
            if py_str_eq(a, b) != 0:
                return 1
            return 0

    if ta == 7:                       # TUPLE
        if tb == 7:
            la: int = py_tuple_len(a)
            lb: int = py_tuple_len(b)
            if la != lb:
                return 0
            i: int = 0
            while i < la:
                ea = py_tuple_get(a, i)
                eb = py_tuple_get(b, i)
                if py_obj_eq(ea, eb) == 0:
                    return 0
                i = i + 1
            return 1

    if ta == 5:                       # LIST
        if tb == 5:
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

    if ta == 0:
        return 0
    if tb == 0:
        return 0

    return 0


# ---- Hash -----------------------------------------------------------

@c_abi_export("py_obj_hash")
def py_obj_hash(o) -> int:
    if ptr_is_null(o) != 0:
        return 0
    if is_tagged_int(o) != 0:
        v: int = py_int_value_i64(o)
        if v == -1:
            return -2
        return v
    tag: int = load_i32(o, 8)
    if tag == 0:                      # NONE
        return 0
    if tag == 1:                      # BOOL
        if ptr_eq(o, global_load_ptr("py_True")) != 0:
            return 1
        return 0
    if tag == 2:                      # INT
        v: int = py_int_value_i64(o)
        if v == -1:
            return -2
        return v
    if tag == 3:                      # FLOAT — read as i64 bits
        v: int = load_i64(o, 16)
        if v == -1:
            return -2
        return v
    if tag == 4:                      # STR — FNV-1a with cache @32
        cached: int = load_i64(o, 32)
        if cached != -1:
            return cached
        bl: int = load_i64(o, 16)
        data_ptr = ptr_add(o, 40)
        h: int = _fnv1a(data_ptr, bl)
        store_i64(o, 32, h)
        return h
    if tag == 7:                      # TUPLE
        n: int = py_tuple_len(o)
        h: int = 0
        i: int = 0
        while i < n:
            el = py_tuple_get(o, i)
            h = h ^ py_obj_hash(el)
            i = i + 1
        if h == -1:
            return -2
        return h
    return 0


# ---- Comparison ops -------------------------------------------------

@c_abi_export("py_obj_lt")
def py_obj_lt(a, b) -> int:
    if _cmp_threeway(a, b) < 0:
        return 1
    return 0


@c_abi_export("py_obj_le")
def py_obj_le(a, b) -> int:
    if _cmp_threeway(a, b) <= 0:
        return 1
    return 0


@c_abi_export("py_obj_gt")
def py_obj_gt(a, b) -> int:
    if _cmp_threeway(a, b) > 0:
        return 1
    return 0


@c_abi_export("py_obj_ge")
def py_obj_ge(a, b) -> int:
    if _cmp_threeway(a, b) >= 0:
        return 1
    return 0


# ---- sorted (insertion sort) — fixed break logic --------------------

@c_abi_export("py_obj_sorted")
def py_obj_sorted(x):
    if ptr_is_null(x) != 0:
        return null()
    n: int = py_obj_len(x)
    out = py_list_new(n)
    if ptr_is_null(out) != 0:
        return null()
    if _type_of(x) == 8:
        entries = load_ptr(x, 40)
        capacity: int = load_i64(x, 24)
        dummy = global_load_ptr("py_set_dummy")
        i: int = 0
        while i < capacity:
            key = load_ptr(entries, i * 16 + 8)
            if ptr_is_null(key) == 0:
                if ptr_eq(key, dummy) == 0:
                    py_list_append(out, key)
            i = i + 1
    else:
        i: int = 0
        while i < n:
            idx_box = py_int_from_i64(i)
            el = py_obj_getitem(x, idx_box)
            py_list_append(out, el)
            py_decref(idx_box)
            i = i + 1
    m: int = py_list_len(out)
    j: int = 1
    while j < m:
        cur = py_list_get(out, j)
        k: int = j
        # Walk leftward shifting larger-than-cur elements one slot right.
        # Use a `done` flag in the loop condition (NOT mutating k=0)
        # — earlier port mutated k=0 to "break" then unconditionally
        # wrote cur to out[k], causing [a,b,c,d] → [d,b,c,d].
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
    if tag == 5:                      # LIST
        if py_list_contains(container, item) != 0:
            return 1
        return 0
    if tag == 7:                      # TUPLE — linear scan via py_obj_eq
        n: int = py_tuple_len(container)
        i: int = 0
        while i < n:
            el = py_tuple_get(container, i)
            if py_obj_eq(el, item) != 0:
                return 1
            i = i + 1
        return 0
    if tag == 6:                      # DICT
        if py_dict_contains(container, item) != 0:
            return 1
        return 0
    if tag == 8:                      # SET
        if py_set_contains(container, item) != 0:
            return 1
        return 0
    if tag == 4:                      # STR
        if py_str_contains(container, item) != 0:
            return 1
        return 0
    return 0
