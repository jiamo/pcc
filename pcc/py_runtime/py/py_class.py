"""Phase 4c.14: pcc-Python port of py_class.c.

PyClassObject layout (96 bytes):
    offset  0   PyObjectHeader   (16)
    offset 16   name             (const char *)
    offset 24   n_bases          (i32)
    offset 32   bases            (PyClassObject **)
    offset 40   n_mro            (i32)
    offset 48   mro              (PyClassObject **)
    offset 56   n_methods        (i32)
    offset 64   methods          (PyClassMethod *)
    offset 72   n_fields         (i32)
    offset 80   field_names      (const char **)
    offset 88   instance_size    (i32)
    offset 92   type_tag_alloc   (i32)

PyClassMethod (16 bytes):
    offset  0   name             (const char *)
    offset  8   func             (PyObject *)

PyInstanceObject:
    offset  0   PyObjectHeader   (16)
    offset 16   cls              (PyClassObject *)
    offset 24   fields[]         (PyObject* flexible array)

Constants (inlined):
    PY_FLAG_IMMORTAL = 1
    PY_TYPE_CLASS    = 10
    PY_TYPE_INSTANCE = 11
    PY_TYPE_USER     = 100

Notes on int width: py_class_new / py_instance_get_field / py_instance_
set_field / py_instance_setattr / py_isinstance use int32 in their C
ABI. pcc-Python's int → i64 default applies inside the function body
for arithmetic / comparison (auto-sext) but NOT for direct user-helper
calls. So we keep i32 args (via `: int` which the ABI forces to i32),
inline most logic, and only call extern (C) helpers where the C side
handles its own int width.
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
    cstr,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    realloc,
    store_i32,
    store_i64,
    store_ptr,
    strlen,
)

py_incref            = extern("py_incref",            (c_ptr,),                    c_void)
py_decref            = extern("py_decref",            (c_ptr,),                    c_void)
py_str_utf8          = extern("py_str_utf8",          (c_ptr,),                    c_ptr)
py_str_new           = extern("py_str_new",           (c_ptr, c_int64),            c_ptr)
py_tuple_new         = extern("py_tuple_new",         (c_int64,),                  c_ptr)
py_tuple_set_item    = extern("py_tuple_set_item",    (c_ptr, c_int64, c_ptr),     c_void)


# Helpers below take only ptrs / cstrs (no int args). They can be
# called from i32-param functions because pcc-Python doesn't need to
# sext anything at the call boundary.


def _alloc_user_tag() -> int:
    slot = global_addr("py_next_user_tag")
    tag: int = load_i32(slot, 0)
    store_i32(slot, 0, tag + 1)
    return tag


def _object_root():
    root = global_load_ptr("py_object_root_cache")
    if ptr_is_null(root) == 0:
        return root

    r = malloc(96)                     # sizeof(PyClassObject)
    if ptr_is_null(r) != 0:
        return null()
    memset(r, 0, 96)

    store_i64(r, 0, 1)                 # refcount
    store_i32(r, 8, 10)                # PY_TYPE_CLASS
    store_i32(r, 12, 1)                # PY_FLAG_IMMORTAL
    store_ptr(r, 16, cstr("object"))
    store_i32(r, 24, 0)                # n_bases
    store_ptr(r, 32, null())           # bases
    store_i32(r, 40, 1)                # n_mro

    mro = malloc(8)
    if ptr_is_null(mro) != 0:
        free(r)
        return null()
    store_ptr(mro, 0, r)
    store_ptr(r, 48, mro)

    store_i32(r, 56, 0)                # n_methods
    store_ptr(r, 64, null())           # methods
    store_i32(r, 72, 0)                # n_fields
    store_ptr(r, 80, null())           # field_names
    store_i32(r, 88, 24)               # sizeof(PyInstanceObject)
    store_i32(r, 92, 11)               # PY_TYPE_INSTANCE

    global_store_ptr("py_object_root_cache", r)
    return r


def _strs_eq(a, b) -> int:
    if ptr_is_null(a) != 0:
        return 0
    if ptr_is_null(b) != 0:
        return 0
    n: int = strlen(a)
    if strlen(b) != n:
        return 0
    i: int = 0
    while i < n:
        if (load_i8(a, i) & 0xFF) != (load_i8(b, i) & 0xFF):
            return 0
        i = i + 1
    return 1


def _cstr_is_dunder_name(s) -> int:
    if strlen(s) != 8:
        return 0
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if load_i8(s, 2) != 110:
        return 0
    if load_i8(s, 3) != 97:
        return 0
    if load_i8(s, 4) != 109:
        return 0
    if load_i8(s, 5) != 101:
        return 0
    if load_i8(s, 6) != 95:
        return 0
    if load_i8(s, 7) != 95:
        return 0
    return 1


def _cstr_is_dunder_mro(s) -> int:
    if strlen(s) != 7:
        return 0
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if load_i8(s, 2) != 109:
        return 0
    if load_i8(s, 3) != 114:
        return 0
    if load_i8(s, 4) != 111:
        return 0
    if load_i8(s, 5) != 95:
        return 0
    if load_i8(s, 6) != 95:
        return 0
    return 1


def _cstr_is_dunder_class(s) -> int:
    if strlen(s) != 9:
        return 0
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if load_i8(s, 2) != 99:
        return 0
    if load_i8(s, 3) != 108:
        return 0
    if load_i8(s, 4) != 97:
        return 0
    if load_i8(s, 5) != 115:
        return 0
    if load_i8(s, 6) != 115:
        return 0
    if load_i8(s, 7) != 95:
        return 0
    if load_i8(s, 8) != 95:
        return 0
    return 1


def _class_lookup_in_mro(cls, name):
    n_mro_i32: int = load_i32(cls, 40)
    mro = load_ptr(cls, 48)
    i: int = 0
    while i < n_mro_i32:
        m = load_ptr(mro, i * 8)
        if ptr_is_null(m) == 0:
            n_methods_i32: int = load_i32(m, 56)
            methods = load_ptr(m, 64)
            j: int = 0
            while j < n_methods_i32:
                m_off: int = j * 16
                m_name = load_ptr(methods, m_off)
                if _strs_eq(m_name, name) != 0:
                    return load_ptr(methods, m_off + 8)
                j = j + 1
        i = i + 1
    return null()


def _lookup_field_index(cls, name):
    if ptr_is_null(cls) != 0:
        return -1
    if ptr_is_null(name) != 0:
        return -1
    n_fields_i32: int = load_i32(cls, 72)
    field_names = load_ptr(cls, 80)
    if ptr_is_null(field_names) != 0:
        return -1
    i: int = 0
    while i < n_fields_i32:
        fn = load_ptr(field_names, i * 8)
        if _strs_eq(fn, name) != 0:
            return i
        i = i + 1
    return -1


@c_abi_export("py_class_lookup")
def py_class_lookup(cls, name):
    if ptr_is_null(cls) != 0:
        return null()
    if ptr_is_null(name) != 0:
        return null()
    if _cstr_is_dunder_name(name) != 0:
        cls_name = load_ptr(cls, 16)
        if ptr_is_null(cls_name) != 0:
            return py_str_new(name, 0)
        return py_str_new(cls_name, strlen(cls_name))
    if _cstr_is_dunder_mro(name) != 0:
        n_mro: int = load_i32(cls, 40)
        mro = load_ptr(cls, 48)
        t = py_tuple_new(n_mro)
        i: int = 0
        while i < n_mro:
            item = load_ptr(mro, i * 8)
            py_tuple_set_item(t, i, item)
            i = i + 1
        return t
    return _class_lookup_in_mro(cls, name)


@c_abi_export("py_class_add_method")
def py_class_add_method(cls, name, func) -> None:
    if ptr_is_null(cls) != 0:
        return
    if ptr_is_null(name) != 0:
        return
    n_methods_i32: int = load_i32(cls, 56)
    new_n: int = n_methods_i32 + 1
    methods = load_ptr(cls, 64)
    new_methods = realloc(methods, new_n * 16)
    if ptr_is_null(new_methods) != 0:
        return
    method_off: int = n_methods_i32 * 16
    store_ptr(new_methods, method_off, name)
    store_ptr(new_methods, method_off + 8, func)
    store_ptr(cls, 64, new_methods)
    store_i32(cls, 56, new_n)


@c_abi_export("py_instance_new")
def py_instance_new(cls):
    if ptr_is_null(cls) != 0:
        return null()
    n_fields_i32: int = load_i32(cls, 72)
    n_slots: int = n_fields_i32
    if n_slots < 0:
        n_slots = 0
    size: int = 24 + n_slots * 8
    inst = malloc(size)
    if ptr_is_null(inst) != 0:
        return null()
    memset(inst, 0, size)
    store_i64(inst, 0, 1)           # refcount
    type_tag_alloc: int = load_i32(cls, 92)
    store_i32(inst, 8, type_tag_alloc)
    store_i32(inst, 12, 0)
    store_ptr(inst, 16, cls)
    return inst


@c_abi_export("py_instance_get_field")
def py_instance_get_field(inst, idx: int):
    if ptr_is_null(inst) != 0:
        return null()
    if idx < 0:
        return null()
    cls = load_ptr(inst, 16)
    if ptr_is_null(cls) != 0:
        return null()
    n_fields: int = load_i32(cls, 72)
    if idx >= n_fields:
        return null()
    fields_base = ptr_add(inst, 24)
    v = load_ptr(fields_base, idx * 8)
    if ptr_is_null(v) == 0:
        py_incref(v)
    return v


@c_abi_export("py_instance_set_field")
def py_instance_set_field(inst, idx: int, value) -> None:
    if ptr_is_null(inst) != 0:
        return
    if idx < 0:
        return
    cls = load_ptr(inst, 16)
    if ptr_is_null(cls) != 0:
        return
    n_fields: int = load_i32(cls, 72)
    if idx >= n_fields:
        return
    fields_base = ptr_add(inst, 24)
    old = load_ptr(fields_base, idx * 8)
    if ptr_is_null(value) == 0:
        py_incref(value)
    store_ptr(fields_base, idx * 8, value)
    if ptr_is_null(old) == 0:
        py_decref(old)


@c_abi_export("py_instance_getattr")
def py_instance_getattr(inst, name):
    if ptr_is_null(inst) != 0:
        return null()
    if ptr_is_null(name) != 0:
        return null()
    cls = load_ptr(inst, 16)
    if _cstr_is_dunder_class(name) != 0:
        if ptr_is_null(cls) == 0:
            py_incref(cls)
        return cls
    idx: int = _lookup_field_index(cls, name)
    if idx >= 0:
        fields_base = ptr_add(inst, 24)
        v = load_ptr(fields_base, idx * 8)
        if ptr_is_null(v) == 0:
            py_incref(v)
        return v
    if ptr_is_null(cls) != 0:
        return null()
    return _class_lookup_in_mro(cls, name)


@c_abi_export("py_instance_setattr")
def py_instance_setattr(inst, name, value) -> int:
    if ptr_is_null(inst) != 0:
        return -1
    if ptr_is_null(name) != 0:
        return -1
    cls = load_ptr(inst, 16)
    idx: int = _lookup_field_index(cls, name)
    if idx < 0:
        return -1
    n_fields: int = load_i32(cls, 72)
    if idx >= n_fields:
        return -1
    fields_base = ptr_add(inst, 24)
    old = load_ptr(fields_base, idx * 8)
    if ptr_is_null(value) == 0:
        py_incref(value)
    store_ptr(fields_base, idx * 8, value)
    if ptr_is_null(old) == 0:
        py_decref(old)
    return 0


@c_abi_export("py_isinstance")
def py_isinstance(obj, cls) -> int:
    if ptr_is_null(obj) != 0:
        return 0
    if ptr_is_null(cls) != 0:
        return 0
    if is_tagged_int(obj) != 0:
        return 0
    tag: int = load_i32(obj, 8)
    if tag != 11:                   # PY_TYPE_INSTANCE
        if tag < 100:               # PY_TYPE_USER
            return 0
    obj_cls = load_ptr(obj, 16)
    if ptr_is_null(obj_cls) != 0:
        return 0
    n_mro: int = load_i32(obj_cls, 40)
    mro = load_ptr(obj_cls, 48)
    i: int = 0
    while i < n_mro:
        m = load_ptr(mro, i * 8)
        if ptr_eq(m, cls) != 0:
            return 1
        i = i + 1
    return 0


@c_abi_export("py_super_lookup")
def py_super_lookup(start_cls, from_cls, name):
    if ptr_is_null(start_cls) != 0:
        return null()
    if ptr_is_null(from_cls) != 0:
        return null()
    if ptr_is_null(name) != 0:
        return null()
    n_mro: int = load_i32(start_cls, 40)
    mro = load_ptr(start_cls, 48)
    start: int = -1
    i: int = 0
    while i < n_mro:
        m = load_ptr(mro, i * 8)
        if ptr_eq(m, from_cls) != 0:
            start = i
            i = n_mro       # force-exit
        i = i + 1

    j: int = start + 1
    while j < n_mro:
        m = load_ptr(mro, j * 8)
        if ptr_is_null(m) == 0:
            n_methods: int = load_i32(m, 56)
            methods = load_ptr(m, 64)
            k: int = 0
            while k < n_methods:
                m_off: int = k * 16
                m_name = load_ptr(methods, m_off)
                if _strs_eq(m_name, name) != 0:
                    return load_ptr(methods, m_off + 8)
                k = k + 1
        j = j + 1
    return null()


@c_abi_export("py_class_dealloc")
def py_class_dealloc(o) -> None:
    if ptr_is_null(o) != 0:
        return
    bases = load_ptr(o, 32)
    if ptr_is_null(bases) == 0:
        free(bases)
    mro = load_ptr(o, 48)
    if ptr_is_null(mro) == 0:
        free(mro)
    methods = load_ptr(o, 64)
    if ptr_is_null(methods) == 0:
        free(methods)
    field_names = load_ptr(o, 80)
    if ptr_is_null(field_names) == 0:
        free(field_names)
    free(o)


@c_abi_export("py_instance_dealloc")
def py_instance_dealloc(o) -> None:
    if ptr_is_null(o) != 0:
        return
    cls = load_ptr(o, 16)
    if ptr_is_null(cls) == 0:
        n_fields: int = load_i32(cls, 72)
        fields_base = ptr_add(o, 24)
        i: int = 0
        while i < n_fields:
            v = load_ptr(fields_base, i * 8)
            if ptr_is_null(v) == 0:
                py_decref(v)
            i = i + 1
    free(o)


@c_abi_export("py_dataclass_replace")
def py_dataclass_replace(obj, n_overrides: int, names, values):
    if ptr_is_null(obj) != 0:
        return null()
    if is_tagged_int(obj) != 0:
        return null()
    tag: int = load_i32(obj, 8)
    if tag != 11:                   # PY_TYPE_INSTANCE
        if tag < 100:               # PY_TYPE_USER
            return null()
    cls = load_ptr(obj, 16)
    if ptr_is_null(cls) != 0:
        return null()
    dst = py_instance_new(cls)
    if ptr_is_null(dst) != 0:
        return null()

    n_fields: int = load_i32(cls, 72)
    src_fields = ptr_add(obj, 24)
    dst_fields = ptr_add(dst, 24)
    i: int = 0
    while i < n_fields:
        v = load_ptr(src_fields, i * 8)
        if ptr_is_null(v) == 0:
            py_incref(v)
            store_ptr(dst_fields, i * 8, v)
        i = i + 1

    j: int = 0
    while j < n_overrides:
        name_ptr = null()
        if ptr_is_null(names) == 0:
            name_ptr = load_ptr(names, j * 8)
        val_ptr = null()
        if ptr_is_null(values) == 0:
            val_ptr = load_ptr(values, j * 8)
        idx: int = _lookup_field_index(cls, name_ptr)
        if idx < 0:
            py_decref(dst)
            return null()
        # Inline py_instance_set_field — avoid passing idx (which is
        # i64 here, but py_instance_set_field is i32 per ABI).
        if idx < n_fields:
            f_off: int = idx * 8
            old = load_ptr(dst_fields, f_off)
            if ptr_is_null(val_ptr) == 0:
                py_incref(val_ptr)
            store_ptr(dst_fields, f_off, val_ptr)
            if ptr_is_null(old) == 0:
                py_decref(old)
        j = j + 1
    return dst


@c_abi_export("py_dataclass_replace_from_dict")
def py_dataclass_replace_from_dict(obj, overrides):
    if ptr_is_null(obj) != 0:
        return null()
    if is_tagged_int(obj) != 0:
        return null()
    tag: int = load_i32(obj, 8)
    if tag != 11:                   # PY_TYPE_INSTANCE
        if tag < 100:               # PY_TYPE_USER
            return null()
    if ptr_is_null(overrides) != 0:
        return null()
    if is_tagged_int(overrides) != 0:
        return null()
    if load_i32(overrides, 8) != 6:  # PY_TYPE_DICT
        return null()

    cls = load_ptr(obj, 16)
    if ptr_is_null(cls) != 0:
        return null()
    dst = py_instance_new(cls)
    if ptr_is_null(dst) != 0:
        return null()

    n_fields: int = load_i32(cls, 72)
    src_fields = ptr_add(obj, 24)
    dst_fields = ptr_add(dst, 24)
    i: int = 0
    while i < n_fields:
        v = load_ptr(src_fields, i * 8)
        if ptr_is_null(v) == 0:
            py_incref(v)
            store_ptr(dst_fields, i * 8, v)
        i = i + 1

    entries = load_ptr(overrides, 40)
    entries_used: int = load_i64(overrides, 48)
    j: int = 0
    while j < entries_used:
        ent_off: int = j * 24
        key = load_ptr(entries, ent_off + 8)
        if ptr_is_null(key) == 0:
            val_ptr = load_ptr(entries, ent_off + 16)
            name_ptr = py_str_utf8(key)
            idx: int = _lookup_field_index(cls, name_ptr)
            if idx < 0:
                py_decref(dst)
                return null()
            if idx < n_fields:
                f_off: int = idx * 8
                old = load_ptr(dst_fields, f_off)
                if ptr_is_null(val_ptr) == 0:
                    py_incref(val_ptr)
                store_ptr(dst_fields, f_off, val_ptr)
                if ptr_is_null(old) == 0:
                    py_decref(old)
        j = j + 1
    return dst


# c3_linearize + py_class_new are the heaviest part. We allocate the
# MergeSeq array on the heap (16 bytes per seq: items@0 + head@8 +
# len@12) and write everything inline in py_class_new to avoid the
# i32→i64 user-helper call problem.

@c_abi_export("py_class_new")
def py_class_new(name, bases, n_bases: int, field_names, n_fields: int):
    c = malloc(96)
    if ptr_is_null(c) != 0:
        return null()
    memset(c, 0, 96)
    store_i64(c, 0, 1)
    store_i32(c, 8, 10)             # PY_TYPE_CLASS
    store_i32(c, 12, 0)
    store_ptr(c, 16, name)
    store_i32(c, 24, n_bases)
    store_i32(c, 72, n_fields)

    # Copy bases array.
    if n_bases > 0:
        if ptr_is_null(bases) == 0:
            bases_copy = malloc(n_bases * 8)
            if ptr_is_null(bases_copy) == 0:
                ii: int = 0
                while ii < n_bases:
                    bv = load_ptr(bases, ii * 8)
                    store_ptr(bases_copy, ii * 8, bv)
                    ii = ii + 1
                store_ptr(c, 32, bases_copy)

    # Copy field_names array.
    if n_fields > 0:
        if ptr_is_null(field_names) == 0:
            fn_copy = malloc(n_fields * 8)
            if ptr_is_null(fn_copy) == 0:
                jj: int = 0
                while jj < n_fields:
                    fv = load_ptr(field_names, jj * 8)
                    store_ptr(fn_copy, jj * 8, fv)
                    jj = jj + 1
                store_ptr(c, 80, fn_copy)

    user_tag: int = _alloc_user_tag()
    store_i32(c, 92, user_tag)

    n_slots: int = n_fields
    if n_slots < 0:
        n_slots = 0
    inst_size: int = 24 + n_slots * 8
    if inst_size > 0x7fffffff:
        inst_size = 0x7fffffff
    store_i32(c, 88, inst_size)

    # ---- C3 linearize inline ----
    # Allocate MergeSeq array (n_bases + 1 entries, 16 bytes each).
    tail = null()
    tail_len: int = 0
    if n_bases > 0:
        nseqs: int = n_bases + 1
        seqs = malloc(nseqs * 16)
        if ptr_is_null(seqs) != 0:
            # alloc failure — bail out, leak partial state
            free(c)
            return null()
        memset(seqs, 0, nseqs * 16)

        cap_total: int = 0
        kk: int = 0
        while kk < n_bases:
            b = load_ptr(bases, kk * 8)
            b_mro = load_ptr(b, 48)
            b_n_mro: int = load_i32(b, 40)
            seq_off: int = kk * 16
            store_ptr(seqs, seq_off, b_mro)
            store_i32(seqs, seq_off + 8, 0)
            store_i32(seqs, seq_off + 12, b_n_mro)
            cap_total = cap_total + b_n_mro
            kk = kk + 1
        last_off: int = n_bases * 16
        store_ptr(seqs, last_off, bases)
        store_i32(seqs, last_off + 8, 0)
        store_i32(seqs, last_off + 12, n_bases)
        cap_total = cap_total + n_bases

        if cap_total <= 0:
            cap_total = 1
        acc = malloc(cap_total * 8)
        if ptr_is_null(acc) != 0:
            free(seqs)
            free(c)
            return null()
        acc_len: int = 0

        # Outer merge loop.
        merge_done: int = 0
        while merge_done == 0:
            any_remaining: int = 0
            i_check: int = 0
            while i_check < nseqs:
                so2: int = i_check * 16
                hd: int = load_i32(seqs, so2 + 8)
                ln: int = load_i32(seqs, so2 + 12)
                if hd < ln:
                    any_remaining = 1
                    i_check = nseqs
                i_check = i_check + 1
            if any_remaining == 0:
                merge_done = 1
            else:
                # Pick candidate.
                cand = null()
                pick_i: int = 0
                while pick_i < nseqs:
                    so3: int = pick_i * 16
                    hd2: int = load_i32(seqs, so3 + 8)
                    ln2: int = load_i32(seqs, so3 + 12)
                    if hd2 < ln2:
                        items = load_ptr(seqs, so3)
                        c_cand = load_ptr(items, hd2 * 8)
                        ok: int = 1
                        check_j: int = 0
                        while check_j < nseqs:
                            if check_j != pick_i:
                                so4: int = check_j * 16
                                hd3: int = load_i32(seqs, so4 + 8)
                                ln3: int = load_i32(seqs, so4 + 12)
                                items3 = load_ptr(seqs, so4)
                                tail_i: int = hd3 + 1
                                while tail_i < ln3:
                                    tv = load_ptr(items3, tail_i * 8)
                                    if ptr_eq(tv, c_cand) != 0:
                                        ok = 0
                                        tail_i = ln3
                                    tail_i = tail_i + 1
                                if ok == 0:
                                    check_j = nseqs
                            check_j = check_j + 1
                        if ok != 0:
                            cand = c_cand
                            pick_i = nseqs
                    pick_i = pick_i + 1

                if ptr_is_null(cand) != 0:
                    # Inconsistent MRO — bail.
                    free(acc)
                    free(seqs)
                    free(c)
                    return null()
                store_ptr(acc, acc_len * 8, cand)
                acc_len = acc_len + 1
                # Consume head where matches cand.
                consume_i: int = 0
                while consume_i < nseqs:
                    so5: int = consume_i * 16
                    hd4: int = load_i32(seqs, so5 + 8)
                    ln4: int = load_i32(seqs, so5 + 12)
                    if hd4 < ln4:
                        items5 = load_ptr(seqs, so5)
                        cur = load_ptr(items5, hd4 * 8)
                        if ptr_eq(cur, cand) != 0:
                            store_i32(seqs, so5 + 8, hd4 + 1)
                    consume_i = consume_i + 1
        free(seqs)
        tail = acc
        tail_len = acc_len

    # MRO assembly.
    root = _object_root()
    append_root: int = 0
    if n_bases == 0:
        if ptr_eq(c, root) == 0:
            append_root = 1

    mro_len: int = 1 + tail_len + append_root
    mro = malloc(mro_len * 8)
    if ptr_is_null(mro) != 0:
        if ptr_is_null(tail) == 0:
            free(tail)
        free(c)
        return null()
    store_ptr(mro, 0, c)
    mi: int = 0
    while mi < tail_len:
        v = load_ptr(tail, mi * 8)
        store_ptr(mro, (1 + mi) * 8, v)
        mi = mi + 1
    if append_root != 0:
        store_ptr(mro, (mro_len - 1) * 8, root)
    store_ptr(c, 48, mro)
    store_i32(c, 40, mro_len)

    if ptr_is_null(tail) == 0:
        free(tail)
    return c
