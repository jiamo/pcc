"""Phase 4c.14: pcc-Python port of py_class.c.

PyClassObject layout (120 bytes):
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
    offset 96   del_method       (PyObject *)
    offset 104  attrs            (PyObject *)
    offset 112  metaclass        (PyClassObject *, borrowed)

PyClassMethod (16 bytes):
    offset  0   name             (const char *)
    offset  8   func             (PyObject *)

PyInstanceObject:
    offset  0   PyObjectHeader   (16)
    offset 16   cls              (PyClassObject *)
    offset 24   fields[]         (PyObject* flexible array)
               fields[n_fields]  hidden dynamic-attribute dict slot

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
    call_ptr2,
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
    untag_int,
)

py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_dict_subclass_getattr = extern("py_dict_subclass_getattr", (c_ptr, c_ptr), c_ptr)
py_instance_bind_method = extern(
    "py_instance_bind_method", (c_ptr, c_ptr, c_ptr), c_ptr
)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_set = extern("py_dict_set", (c_ptr, c_ptr, c_ptr), c_void)
py_dict_get = extern("py_dict_get", (c_ptr, c_ptr), c_ptr)
py_dict_keys = extern("py_dict_keys", (c_ptr,), c_ptr)
py_dict_update = extern("py_dict_update", (c_ptr, c_ptr), c_void)
py_dict_del = extern("py_dict_del", (c_ptr, c_ptr), c_int64)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_gc_track = extern("py_gc_track", (c_ptr,), c_void)
py_user_del_dispatch = extern("py_user_del_dispatch", (c_ptr,), c_void)
py_weakref_invalidate = extern("py_weakref_invalidate", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
py_exc_matches = extern("py_exc_matches", (c_ptr, c_ptr), c_int64)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_note_relocation_read = extern("pcc_gc_note_relocation_read", (c_ptr,), c_ptr)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_note_store = extern("pcc_gc_note_store", (), c_void)
pcc_gc_note_write_barrier = extern(
    "pcc_gc_note_write_barrier",
    (c_ptr, c_ptr),
    c_void,
)
pcc_gc_note_slot_write_barrier = extern(
    "pcc_gc_note_slot_write_barrier",
    (c_ptr, c_ptr, c_ptr),
    c_void,
)
pcc_gc_backend4_zpage_register_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_register_owner_payload_span",
    (c_ptr, c_ptr, c_int64),
    c_int64,
)
pcc_gc_backend4_zpage_unregister_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_unregister_owner_payload_span",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_backend4_zpage_retarget_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_retarget_owner_payload_span",
    (c_ptr, c_ptr, c_ptr, c_int64),
    c_int64,
)
py_class_attrs_dict = extern("py_class_attrs_dict", (c_ptr, c_int64), c_ptr)
py_class_setattr = extern("py_class_setattr", (c_ptr, c_ptr, c_ptr), c_int64)
py_class_setattr_raw = extern("py_class_setattr_raw", (c_ptr, c_ptr, c_ptr), c_int64)


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

    r = malloc(120)  # sizeof(PyClassObject)
    if ptr_is_null(r) != 0:
        return null()
    memset(r, 0, 120)

    store_i64(r, 0, 1)  # refcount
    store_i32(r, 8, 10)  # PY_TYPE_CLASS
    store_i32(r, 12, 1)  # PY_FLAG_IMMORTAL
    store_ptr(r, 16, cstr("object"))
    store_i32(r, 24, 0)  # n_bases
    store_ptr(r, 32, null())  # bases
    store_i32(r, 40, 1)  # n_mro

    mro = malloc(8)
    if ptr_is_null(mro) != 0:
        free(r)
        return null()
    store_ptr(mro, 0, r)
    store_ptr(r, 48, mro)

    store_i32(r, 56, 0)  # n_methods
    store_ptr(r, 64, null())  # methods
    store_i32(r, 72, 0)  # n_fields
    store_ptr(r, 80, null())  # field_names
    store_i32(r, 88, 32)  # instance header + dyn dict slot
    store_i32(r, 92, 11)  # PY_TYPE_INSTANCE
    store_ptr(r, 96, null())  # del_method
    store_ptr(r, 104, null())  # attrs

    global_store_ptr("py_object_root_cache", r)
    return r


def _ptr_can_have_header(o) -> bool:
    if ptr_is_null(o) != 0:
        return False
    if is_tagged_int(o) != 0:
        return False
    bits: int = untag_int(o)
    if bits < 2048:
        return False
    if (bits & 3) != 0:
        return False
    if bits >= 140737488355328:
        return False
    return True


def _ptr_is_class(o) -> bool:
    o = pcc_gc_note_relocation_read(o)
    if not _ptr_can_have_header(o):
        return False
    return load_i32(o, 8) == 10


def _ptr_is_instance(o) -> bool:
    o = pcc_gc_note_relocation_read(o)
    if not _ptr_can_have_header(o):
        return False
    tag: int = load_i32(o, 8)
    if tag != 11:
        if tag < 100:
            return False
    cls = pcc_gc_load_ptr(o, ptr_add(o, 16))
    if ptr_is_null(cls) != 0:
        return False
    return _ptr_is_class(cls)


def _class_note_borrowed_metadata_store(cls, value) -> None:
    _class_note_borrowed_metadata_slot_store(cls, null(), value)


def _class_note_borrowed_metadata_slot_store(cls, slot, value) -> None:
    if not _ptr_is_class(cls):
        return
    backend: int = pcc_gc_backend()
    if backend == 1 or backend == 2 or backend == 3 or backend == 4:
        pcc_gc_note_store()
    pcc_gc_note_slot_write_barrier(cls, slot, value)


def _strs_eq(a, b) -> int:
    if ptr_eq(a, b) != 0:
        return 1
    if ptr_is_null(a) != 0:
        return 0
    if ptr_is_null(b) != 0:
        return 0
    a0: int = load_i8(a, 0) & 0xFF
    b0: int = load_i8(b, 0) & 0xFF
    if a0 != b0:
        return 0
    if a0 == 0:
        return 1
    a1: int = load_i8(a, 1) & 0xFF
    b1: int = load_i8(b, 1) & 0xFF
    if a1 != b1:
        return 0
    if a1 == 0:
        return 1
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


def _cstr_is_dunder_dict(s) -> int:
    if strlen(s) != 8:
        return 0
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if load_i8(s, 2) != 100:
        return 0
    if load_i8(s, 3) != 105:
        return 0
    if load_i8(s, 4) != 99:
        return 0
    if load_i8(s, 5) != 116:
        return 0
    if load_i8(s, 6) != 95:
        return 0
    if load_i8(s, 7) != 95:
        return 0
    return 1


def _class_lookup_in_mro(cls, name):
    n_mro_i32: int = load_i32(cls, 40)
    mro = load_ptr(cls, 48)
    i: int = 0
    while i < n_mro_i32:
        m = pcc_gc_load_ptr(cls, ptr_add(mro, i * 8))
        if ptr_is_null(m) == 0:
            n_methods_i32: int = load_i32(m, 56)
            methods = load_ptr(m, 64)
            j: int = 0
            while j < n_methods_i32:
                m_off: int = j * 16
                m_name = load_ptr(methods, m_off)
                if _strs_eq(m_name, name) != 0:
                    method_slot = ptr_add(methods, m_off + 8)
                    func = pcc_gc_note_relocation_read(load_ptr(method_slot, 0))
                    store_ptr(method_slot, 0, func)
                    return func
                j = j + 1
        i = i + 1
    return null()


def _lookup_field_index(cls, name):
    if not _ptr_is_class(cls):
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
        # print("  field[" + str(i) + "]=" + str(fn))
        if _strs_eq(fn, name) != 0:
            return i
        i = i + 1
    return -1


def _class_attr_cache_epoch() -> int:
    return load_i32(global_addr("py_class_attr_cache_epoch"), 0)


def _bump_class_attr_cache_epoch() -> None:
    slot = global_addr("py_class_attr_cache_epoch")
    store_i32(slot, 0, load_i32(slot, 0) + 1)


def _field_cache_slot(cls, name) -> int:
    return ((untag_int(cls) >> 4) ^ (untag_int(name) >> 4)) & 3


def _field_cache_lookup(cls, name) -> int:
    epoch: int = _class_attr_cache_epoch()
    if (
        load_i32(global_addr("py_inst_field_cache_epoch0"), 0) == epoch
        and ptr_eq(global_load_ptr("py_inst_field_cache_cls0"), cls) != 0
        and ptr_eq(global_load_ptr("py_inst_field_cache_name0"), name) != 0
    ):
        return load_i32(global_addr("py_inst_field_cache_idx0"), 0)
    if (
        load_i32(global_addr("py_inst_field_cache_epoch1"), 0) == epoch
        and ptr_eq(global_load_ptr("py_inst_field_cache_cls1"), cls) != 0
        and ptr_eq(global_load_ptr("py_inst_field_cache_name1"), name) != 0
    ):
        return load_i32(global_addr("py_inst_field_cache_idx1"), 0)
    if (
        load_i32(global_addr("py_inst_field_cache_epoch2"), 0) == epoch
        and ptr_eq(global_load_ptr("py_inst_field_cache_cls2"), cls) != 0
        and ptr_eq(global_load_ptr("py_inst_field_cache_name2"), name) != 0
    ):
        return load_i32(global_addr("py_inst_field_cache_idx2"), 0)
    if (
        load_i32(global_addr("py_inst_field_cache_epoch3"), 0) == epoch
        and ptr_eq(global_load_ptr("py_inst_field_cache_cls3"), cls) != 0
        and ptr_eq(global_load_ptr("py_inst_field_cache_name3"), name) != 0
    ):
        return load_i32(global_addr("py_inst_field_cache_idx3"), 0)
    return -1


def _field_cache_store(cls, name, idx: int) -> None:
    slot: int = _field_cache_slot(cls, name)
    epoch: int = _class_attr_cache_epoch()
    if slot == 0:
        global_store_ptr("py_inst_field_cache_cls0", cls)
        global_store_ptr("py_inst_field_cache_name0", name)
        store_i32(global_addr("py_inst_field_cache_idx0"), 0, idx)
        store_i32(global_addr("py_inst_field_cache_epoch0"), 0, epoch)
        return
    if slot == 1:
        global_store_ptr("py_inst_field_cache_cls1", cls)
        global_store_ptr("py_inst_field_cache_name1", name)
        store_i32(global_addr("py_inst_field_cache_idx1"), 0, idx)
        store_i32(global_addr("py_inst_field_cache_epoch1"), 0, epoch)
        return
    if slot == 2:
        global_store_ptr("py_inst_field_cache_cls2", cls)
        global_store_ptr("py_inst_field_cache_name2", name)
        store_i32(global_addr("py_inst_field_cache_idx2"), 0, idx)
        store_i32(global_addr("py_inst_field_cache_epoch2"), 0, epoch)
        return
    global_store_ptr("py_inst_field_cache_cls3", cls)
    global_store_ptr("py_inst_field_cache_name3", name)
    store_i32(global_addr("py_inst_field_cache_idx3"), 0, idx)
    store_i32(global_addr("py_inst_field_cache_epoch3"), 0, epoch)


def _dynamic_attr_slot(inst):
    if not _ptr_is_instance(inst):
        return null()
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, 16))
    flags: int = load_i32(cls, 12)
    if (flags & 2) != 0:
        return null()
    n_fields: int = load_i32(cls, 72)
    if n_fields < 0:
        n_fields = 0
    return ptr_add(inst, 24 + n_fields * 8)


@c_abi_export("py_instance_vars")
def py_instance_vars(inst):
    if not _ptr_is_instance(inst):
        py_raise(py_exc_new(3, cstr("vars() argument has no __dict__")))
        return null()
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, 16))
    if not _ptr_is_class(cls):
        py_raise(py_exc_new(3, cstr("vars() argument has no __dict__")))
        return null()
    out = py_dict_new()
    if ptr_is_null(out) != 0:
        return null()
    n_fields: int = load_i32(cls, 72)
    if n_fields < 0:
        n_fields = 0
    field_names = load_ptr(cls, 80)
    fields = ptr_add(inst, 24)
    i: int = 0
    while i < n_fields:
        field_name = null()
        if ptr_is_null(field_names) == 0:
            field_name = load_ptr(field_names, i * 8)
        if ptr_is_null(field_name) == 0:
            value = pcc_gc_load_ptr(inst, ptr_add(fields, i * 8))
            if ptr_is_null(value) == 0:
                key = py_str_new(field_name, strlen(field_name))
                if ptr_is_null(key) != 0:
                    py_decref(out)
                    return null()
                py_dict_set(out, key, value)
                py_decref(key)
        i = i + 1
    dyn_slot = _dynamic_attr_slot(inst)
    if ptr_is_null(dyn_slot) == 0:
        dyn = pcc_gc_load_ptr(inst, dyn_slot)
        if ptr_is_null(dyn) == 0:
            py_dict_update(out, dyn)
    return out


@c_abi_export("py_obj_vars")
def py_obj_vars(o):
    if ptr_is_null(o) != 0:
        py_raise(py_exc_new(3, cstr("vars() argument has no __dict__")))
        return null()
    if is_tagged_int(o) != 0:
        py_raise(py_exc_new(3, cstr("vars() argument has no __dict__")))
        return null()
    if _ptr_is_instance(o):
        return py_instance_vars(o)
    if _ptr_is_class(o):
        attrs = py_class_attrs_dict(o, 1)
        if ptr_is_null(attrs) == 0:
            py_incref(attrs)
            return attrs
    py_raise(py_exc_new(3, cstr("vars() argument has no __dict__")))
    return null()


def _class_attr_lookup_in_mro(cls, name):
    if not _ptr_is_class(cls):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    cls = pcc_gc_note_relocation_read(cls)
    key = py_str_new(name, strlen(name))
    if ptr_is_null(key) != 0:
        return null()
    n_mro: int = load_i32(cls, 40)
    mro = load_ptr(cls, 48)
    i: int = 0
    while i < n_mro:
        m = pcc_gc_load_ptr(cls, ptr_add(mro, i * 8))
        if ptr_is_null(m) == 0:
            attrs = py_class_attrs_dict(m, 0)
            if ptr_is_null(attrs) == 0:
                value = py_dict_get(attrs, key)
                if ptr_is_null(value) == 0:
                    py_decref(key)
                    return value
        i = i + 1
    py_decref(key)
    return null()


def _descriptor_method(descriptor, name):
    if ptr_is_null(descriptor) != 0:
        return null()
    if is_tagged_int(descriptor) != 0:
        return null()
    if not _ptr_is_instance(descriptor):
        return null()
    desc_cls = pcc_gc_load_ptr(descriptor, ptr_add(descriptor, 16))
    return _class_lookup_in_mro(desc_cls, name)


def _descriptor_is_data(descriptor) -> bool:
    if ptr_is_null(descriptor) == 0:
        if is_tagged_int(descriptor) == 0:
            if load_i32(descriptor, 8) == 101:  # PY_TYPE_PROPERTY
                return True
    if ptr_is_null(_descriptor_method(descriptor, cstr("__set__"))) == 0:
        return True
    if ptr_is_null(_descriptor_method(descriptor, cstr("__delete__"))) == 0:
        return True
    return False


def _descriptor_call_get(descriptor, obj, owner):
    if ptr_is_null(descriptor) == 0:
        if is_tagged_int(descriptor) == 0:
            if load_i32(descriptor, 8) == 101:  # PY_TYPE_PROPERTY
                fget = pcc_gc_load_ptr(descriptor, ptr_add(descriptor, 16))
                if ptr_is_null(fget) != 0:
                    py_raise(py_exc_new(6, cstr("unreadable attribute")))
                    return null()
                if ptr_eq(obj, global_load_ptr("py_None")) != 0:
                    py_incref(descriptor)
                    return descriptor
                args = py_tuple_new(1)
                if ptr_is_null(args) != 0:
                    return null()
                py_tuple_set_item(args, 0, obj)
                out = py_obj_call(fget, args, global_load_ptr("py_None"))
                py_decref(args)
                return out
    method = _descriptor_method(descriptor, cstr("__get__"))
    if ptr_is_null(method) != 0:
        return null()
    args = py_tuple_new(3)
    if ptr_is_null(args) != 0:
        return null()
    py_tuple_set_item(args, 0, descriptor)
    py_tuple_set_item(args, 1, obj)
    py_tuple_set_item(args, 2, owner)
    out = py_obj_call(method, args, global_load_ptr("py_None"))
    py_decref(args)
    return out


def _descriptor_call_set(descriptor, obj, value) -> int:
    if ptr_is_null(descriptor) == 0:
        if is_tagged_int(descriptor) == 0:
            if load_i32(descriptor, 8) == 101:  # PY_TYPE_PROPERTY
                fset = pcc_gc_load_ptr(descriptor, ptr_add(descriptor, 24))
                if ptr_is_null(fset) != 0:
                    py_raise(py_exc_new(6, cstr("can't set attribute")))
                    return -1
                args = py_tuple_new(2)
                if ptr_is_null(args) != 0:
                    return -1
                py_tuple_set_item(args, 0, obj)
                py_tuple_set_item(args, 1, value)
                out = py_obj_call(fset, args, global_load_ptr("py_None"))
                py_decref(args)
                if ptr_is_null(out) != 0:
                    return -1
                py_decref(out)
                return 0
    method = _descriptor_method(descriptor, cstr("__set__"))
    if ptr_is_null(method) != 0:
        return -1
    args = py_tuple_new(3)
    if ptr_is_null(args) != 0:
        return -1
    py_tuple_set_item(args, 0, descriptor)
    py_tuple_set_item(args, 1, obj)
    py_tuple_set_item(args, 2, value)
    out = py_obj_call(method, args, global_load_ptr("py_None"))
    py_decref(args)
    if ptr_is_null(out) != 0:
        return -1
    py_decref(out)
    return 0


def _descriptor_call_delete(descriptor, obj) -> int:
    if ptr_is_null(descriptor) == 0:
        if is_tagged_int(descriptor) == 0:
            if load_i32(descriptor, 8) == 101:  # PY_TYPE_PROPERTY
                fdel = pcc_gc_load_ptr(descriptor, ptr_add(descriptor, 32))
                if ptr_is_null(fdel) != 0:
                    py_raise(py_exc_new(6, cstr("can't delete attribute")))
                    return -1
                args = py_tuple_new(1)
                if ptr_is_null(args) != 0:
                    return -1
                py_tuple_set_item(args, 0, obj)
                out = py_obj_call(fdel, args, global_load_ptr("py_None"))
                py_decref(args)
                if ptr_is_null(out) != 0:
                    return -1
                py_decref(out)
                return 0
    method = _descriptor_method(descriptor, cstr("__delete__"))
    if ptr_is_null(method) != 0:
        return -1
    args = py_tuple_new(2)
    if ptr_is_null(args) != 0:
        return -1
    py_tuple_set_item(args, 0, descriptor)
    py_tuple_set_item(args, 1, obj)
    out = py_obj_call(method, args, global_load_ptr("py_None"))
    py_decref(args)
    if ptr_is_null(out) != 0:
        return -1
    py_decref(out)
    return 0


@c_abi_export("py_class_lookup")
def py_class_lookup(cls, name):
    if not _ptr_is_class(cls):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    cls = pcc_gc_note_relocation_read(cls)
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
            item = pcc_gc_load_ptr(cls, ptr_add(mro, i * 8))
            py_tuple_set_item(t, i, item)
            i = i + 1
        return t
    if _strs_eq(name, cstr("__base__")) != 0:
        n_bases: int = load_i32(cls, 24)
        bases = load_ptr(cls, 32)
        if n_bases <= 0 or ptr_is_null(bases) != 0:
            return global_load_ptr("py_None")
        return pcc_gc_load_ptr(cls, bases)
    return _class_lookup_in_mro(cls, name)


@c_abi_export("py_class_add_method")
def py_class_add_method(cls, name, func) -> None:
    if not _ptr_is_class(cls):
        return
    if ptr_is_null(name) != 0:
        return
    cls = pcc_gc_note_relocation_read(cls)
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
    payload_offset: int = -1
    if ptr_is_null(methods) == 0:
        if ptr_eq(methods, new_methods) == 0:
            payload_offset = pcc_gc_backend4_zpage_retarget_owner_payload_span(
                cls,
                methods,
                new_methods,
                new_n * 16,
            )
    if payload_offset < 0:
        if ptr_is_null(methods) == 0:
            if ptr_eq(methods, new_methods) == 0:
                pcc_gc_backend4_zpage_unregister_owner_payload_span(cls, methods)
        pcc_gc_backend4_zpage_register_owner_payload_span(cls, new_methods, new_n * 16)
    _class_note_borrowed_metadata_slot_store(
        cls,
        ptr_add(new_methods, method_off + 8),
        func,
    )
    if _strs_eq(name, cstr("__del__")) != 0:
        # Borrowed update-only alias for GC forwarding.  py_user_del_dispatch
        # deliberately resolves through py_class_lookup rather than treating
        # this slot as a separate semantic cache.
        store_ptr(cls, 96, func)
        _class_note_borrowed_metadata_slot_store(cls, ptr_add(cls, 96), func)


@c_abi_export("py_class_set_metaclass")
def py_class_set_metaclass(cls, metaclass) -> None:
    if not _ptr_is_class(cls):
        return
    cls = pcc_gc_note_relocation_read(cls)
    if ptr_is_null(metaclass) == 0:
        if not _ptr_is_class(metaclass):
            return
        metaclass = pcc_gc_note_relocation_read(metaclass)
    store_ptr(cls, 112, metaclass)
    _class_note_borrowed_metadata_slot_store(cls, ptr_add(cls, 112), metaclass)


@c_abi_export("py_instance_new")
def py_instance_new(cls) -> c_ptr:
    if not _ptr_is_class(cls):
        return null()
    cls = pcc_gc_note_relocation_read(cls)
    n_fields_i32: int = load_i32(cls, 72)
    n_slots: int = n_fields_i32 + 1
    if n_slots < 0:
        n_slots = 1
    size: int = 24 + n_slots * 8
    inst = pcc_gc_alloc(size, load_i32(cls, 92), 0)
    if ptr_is_null(inst) != 0:
        return null()
    memset(ptr_add(inst, 16), 0, size - 16)
    store_i64(inst, 0, 1)  # refcount
    type_tag_alloc: int = load_i32(cls, 92)
    store_i32(inst, 8, type_tag_alloc)
    store_ptr(inst, 16, cls)
    py_gc_track(inst)
    # The freshly allocated instance is already a new reference; keep this as
    # a raw pointer expression so return lowering does not retain it again.
    return ptr_add(inst, 0)


@c_abi_export("py_instance_get_field")
def py_instance_get_field(inst, idx: int):
    if not _ptr_is_instance(inst):
        return null()
    if idx < 0:
        return null()
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, 16))
    n_fields: int = load_i32(cls, 72)
    if idx >= n_fields:
        return null()
    fields_base = ptr_add(inst, 24)
    v = pcc_gc_load_ptr(inst, ptr_add(fields_base, idx * 8))
    if ptr_is_null(v) == 0:
        py_incref(v)
    return v


@c_abi_export("py_instance_set_field")
def py_instance_set_field(inst, idx: int, value) -> None:
    if not _ptr_is_instance(inst):
        return
    if idx < 0:
        return
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, 16))
    n_fields: int = load_i32(cls, 72)
    if idx >= n_fields:
        return
    fields_base = ptr_add(inst, 24)
    pcc_gc_store_ptr(inst, ptr_add(fields_base, idx * 8), value)


@c_abi_export("py_valuebox_new")
def py_valuebox_new(cls) -> c_ptr:
    box = py_instance_new(cls)
    if ptr_is_null(box) != 0:
        return null()
    store_i32(box, 8, 200)  # PY_TYPE_VALUEBOX
    # py_instance_new already returned a new reference.
    return ptr_add(box, 0)


@c_abi_export("py_valuebox_get_field")
def py_valuebox_get_field(box, idx: int):
    return py_instance_get_field(box, idx)


@c_abi_export("py_valuebox_set_field")
def py_valuebox_set_field(box, idx: int, value) -> None:
    py_instance_set_field(box, idx, value)


@c_abi_export("py_instance_getattr_default")
def py_instance_getattr_default(inst, name):
    if not _ptr_is_instance(inst):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, 16))
    if _cstr_is_dunder_class(name) != 0:
        if ptr_is_null(cls) == 0:
            py_incref(cls)
        return cls
    if _cstr_is_dunder_dict(name) != 0:
        dyn_slot = _dynamic_attr_slot(inst)
        if ptr_is_null(dyn_slot) != 0:
            return null()
        dyn = pcc_gc_load_ptr(inst, dyn_slot)
        if ptr_is_null(dyn) != 0:
            dyn = py_dict_new()
            if ptr_is_null(dyn) != 0:
                return null()
            pcc_gc_store_ptr(inst, dyn_slot, dyn)
            py_decref(dyn)
        py_incref(dyn)
        return dyn
    cached_idx: int = _field_cache_lookup(cls, name)
    if cached_idx >= 0:
        fields_base_cached = ptr_add(inst, 24)
        cached = pcc_gc_load_ptr(inst, ptr_add(fields_base_cached, cached_idx * 8))
        if ptr_is_null(cached) == 0:
            py_incref(cached)
        return cached
    class_attr = _class_attr_lookup_in_mro(cls, name)
    if ptr_is_null(class_attr) == 0:
        if _descriptor_is_data(class_attr):
            got = _descriptor_call_get(class_attr, inst, cls)
            py_decref(class_attr)
            if ptr_is_null(got) == 0:
                return got
            if py_err_occurred() != 0:
                return null()
    idx: int = _lookup_field_index(cls, name)
    if idx >= 0:
        _field_cache_store(cls, name, idx)
        fields_base = ptr_add(inst, 24)
        v = pcc_gc_load_ptr(inst, ptr_add(fields_base, idx * 8))
        if ptr_is_null(v) == 0:
            py_incref(v)
        return v
    dyn_slot = _dynamic_attr_slot(inst)
    if ptr_is_null(dyn_slot) == 0:
        dyn = pcc_gc_load_ptr(inst, dyn_slot)
        if ptr_is_null(dyn) == 0:
            key = py_str_new(name, strlen(name))
            got = py_dict_get(dyn, key)
            py_decref(key)
            if ptr_is_null(got) == 0:
                return got
    if ptr_is_null(class_attr) == 0:
        if is_tagged_int(class_attr) == 0:
            if load_i32(class_attr, 8) == 9:
                bound = py_instance_bind_method(class_attr, inst, name)
                py_decref(class_attr)
                return bound
        got = _descriptor_call_get(class_attr, inst, cls)
        if ptr_is_null(got) == 0:
            py_decref(class_attr)
            return got
        if py_err_occurred() != 0:
            py_decref(class_attr)
            return null()
        return class_attr
    if ptr_is_null(cls) != 0:
        return null()
    method = _class_lookup_in_mro(cls, name)
    if ptr_is_null(method) == 0:
        return py_instance_bind_method(method, inst, name)
    # dict-subclass inherited method fallback (get / keys / values / items /
    # pop / setdefault / clear). User methods/attrs above win; this only fires
    # for names the user class did not define. PY_CLASS_FLAG_DICT_SUBCLASS is
    # bit 2 (value 4) in the class header flags at offset 12. Returns a bound
    # native callable, or NULL -> fall through to __getattr__ / AttributeError.
    ds_flags: int = load_i32(cls, 12)
    if (ds_flags & 4) != 0:
        dm = py_dict_subclass_getattr(inst, name)
        if ptr_is_null(dm) == 0:
            return dm
        if py_err_occurred() != 0:
            return null()
    getattr_method = _class_lookup_in_mro(cls, cstr("__getattr__"))
    if ptr_is_null(getattr_method) != 0:
        return null()
    key = py_str_new(name, strlen(name))
    if ptr_is_null(key) != 0:
        return null()
    # The method-table slot for a compiled __getattr__ holds a PY_TYPE_FUNC
    # object, not a raw code pointer; invoke it via py_obj_call in that case.
    # call_ptr2 alone treats the object as a code address and crashes. Mirrors
    # class_call_binary_method in py_class.c.
    if is_tagged_int(getattr_method) == 0:
        if load_i32(getattr_method, 8) == 9:  # PY_TYPE_FUNC
            gargs = py_tuple_new(2)
            if ptr_is_null(gargs) != 0:
                py_decref(key)
                return null()
            py_tuple_set_item(gargs, 0, inst)
            py_tuple_set_item(gargs, 1, key)
            got = py_obj_call(getattr_method, gargs, null())
            py_decref(gargs)
            py_decref(key)
            return got
    got = call_ptr2(getattr_method, inst, key)
    py_decref(key)
    return got


@c_abi_export("py_instance_getattr")
def py_instance_getattr(inst, name):
    if not _ptr_is_instance(inst):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, 16))
    if ptr_is_null(cls) != 0:
        return null()
    getattribute_method = _class_lookup_in_mro(cls, cstr("__getattribute__"))
    if ptr_is_null(getattribute_method) == 0:
        key = py_str_new(name, strlen(name))
        if ptr_is_null(key) != 0:
            return null()
        got = call_ptr2(getattribute_method, inst, key)
        if ptr_is_null(got) == 0:
            py_decref(key)
            return got
        if py_err_occurred() != 0:
            cur = py_current_exception()
            attr_cls = py_exc_builtin_class(6)  # PY_EXC_ATTRIBUTEERROR
            if ptr_is_null(attr_cls) == 0:
                if py_exc_matches(cur, attr_cls) != 0:
                    getattr_method = _class_lookup_in_mro(cls, cstr("__getattr__"))
                    if ptr_is_null(getattr_method) == 0:
                        py_clear_exception()
                        fallback = call_ptr2(getattr_method, inst, key)
                        py_decref(key)
                        return fallback
        py_decref(key)
        return null()
    return py_instance_getattr_default(inst, name)


@c_abi_export("py_instance_setattr")
def py_instance_setattr(inst, name, value) -> int:
    if not _ptr_is_instance(inst):
        return -1
    if ptr_is_null(name) != 0:
        return -1
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, 16))
    class_attr = _class_attr_lookup_in_mro(cls, name)
    if ptr_is_null(class_attr) == 0:
        set_method = _descriptor_method(class_attr, cstr("__set__"))
        if ptr_is_null(set_method) == 0:
            rc: int = _descriptor_call_set(class_attr, inst, value)
            py_decref(class_attr)
            return rc
        py_decref(class_attr)
    idx: int = _lookup_field_index(cls, name)
    if idx >= 0:
        n_fields: int = load_i32(cls, 72)
        if idx >= n_fields:
            return -1
        fields_base = ptr_add(inst, 24)
        pcc_gc_store_ptr(inst, ptr_add(fields_base, idx * 8), value)
        return 0
    if ptr_is_null(value) != 0:
        return -1
    dyn_slot = _dynamic_attr_slot(inst)
    if ptr_is_null(dyn_slot) != 0:
        return -1
    dyn = pcc_gc_load_ptr(inst, dyn_slot)
    if ptr_is_null(dyn) != 0:
        dyn = py_dict_new()
        if ptr_is_null(dyn) != 0:
            return -1
        pcc_gc_store_ptr(inst, dyn_slot, dyn)
        py_decref(dyn)
    key = py_str_new(name, strlen(name))
    py_dict_set(dyn, key, value)
    py_decref(key)
    return 0


@c_abi_export("py_instance_delattr")
def py_instance_delattr(inst, name) -> int:
    if not _ptr_is_instance(inst):
        return -1
    if ptr_is_null(name) != 0:
        return -1
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, 16))
    class_attr = _class_attr_lookup_in_mro(cls, name)
    if ptr_is_null(class_attr) == 0:
        delete_method = _descriptor_method(class_attr, cstr("__delete__"))
        if ptr_is_null(delete_method) == 0:
            rc: int = _descriptor_call_delete(class_attr, inst)
            py_decref(class_attr)
            return rc
        py_decref(class_attr)
    idx: int = _lookup_field_index(cls, name)
    if idx >= 0:
        n_fields: int = load_i32(cls, 72)
        if idx >= n_fields:
            return -1
        fields_base = ptr_add(inst, 24)
        old = pcc_gc_load_ptr(inst, ptr_add(fields_base, idx * 8))
        if ptr_is_null(old) != 0:
            return -1
        store_ptr(fields_base, idx * 8, null())
        py_decref(old)
        return 0
    dyn_slot = _dynamic_attr_slot(inst)
    if ptr_is_null(dyn_slot) != 0:
        return -1
    dyn = pcc_gc_load_ptr(inst, dyn_slot)
    if ptr_is_null(dyn) != 0:
        return -1
    key = py_str_new(name, strlen(name))
    rc: int = py_dict_del(dyn, key)
    py_decref(key)
    return rc


def py_class_apply_namespace_dict(cls, ns) -> int:
    if not _ptr_is_class(cls):
        return -1
    if ptr_is_null(ns) != 0:
        py_raise(py_exc_new(3, cstr("type.__new__() argument 3 must be dict")))
        return -1
    if load_i32(ns, 8) != 6:  # PY_TYPE_DICT
        py_raise(py_exc_new(3, cstr("type.__new__() argument 3 must be dict")))
        return -1
    keys = py_dict_keys(ns)
    if ptr_is_null(keys) != 0:
        return -1
    n: int = py_list_len(keys)
    i: int = 0
    while i < n:
        key = py_list_get(keys, i)
        if ptr_is_null(key) != 0:
            py_decref(keys)
            return -1
        name = py_str_utf8(key)
        if ptr_is_null(name) != 0:
            py_decref(key)
            py_decref(keys)
            return -1
        value = py_dict_get(ns, key)
        if ptr_is_null(value) != 0:
            py_decref(key)
            py_decref(keys)
            return -1
        rc: int = py_class_setattr_raw(cls, name, value)
        py_decref(value)
        py_decref(key)
        if rc != 0:
            py_decref(keys)
            return rc
        i = i + 1
    py_decref(keys)
    return 0


@c_abi_export("py_isinstance")
def py_isinstance(obj, cls) -> int:
    obj = pcc_gc_note_relocation_read(obj)
    cls = pcc_gc_note_relocation_read(cls)
    if _ptr_can_have_header(obj):
        if load_i32(obj, 8) == 12:  # PY_TYPE_EXC: match via exc_class MRO
            if py_exc_matches(obj, cls) != 0:
                return 1
            return 0
    if not _ptr_is_instance(obj):
        return 0
    if not _ptr_is_class(cls):
        return 0
    obj_cls = pcc_gc_load_ptr(obj, ptr_add(obj, 16))
    if ptr_eq(obj_cls, cls) != 0:
        return 1
    n_mro: int = load_i32(obj_cls, 40)
    mro = load_ptr(obj_cls, 48)
    i: int = 0
    while i < n_mro:
        m = pcc_gc_load_ptr(obj_cls, ptr_add(mro, i * 8))
        if ptr_eq(m, cls) != 0:
            return 1
        i = i + 1
    return 0


@c_abi_export("py_super_lookup")
def py_super_lookup(start_cls, from_cls, name):
    if not _ptr_is_class(start_cls):
        return null()
    if not _ptr_is_class(from_cls):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    start_cls = pcc_gc_note_relocation_read(start_cls)
    from_cls = pcc_gc_note_relocation_read(from_cls)
    n_mro: int = load_i32(start_cls, 40)
    mro = load_ptr(start_cls, 48)
    start: int = -1
    i: int = 0
    while i < n_mro:
        m = pcc_gc_load_ptr(start_cls, ptr_add(mro, i * 8))
        if ptr_eq(m, from_cls) != 0:
            start = i
            i = n_mro  # force-exit
        i = i + 1
    if start < 0:
        exc = py_exc_new(
            3,
            cstr("super(type, obj): obj must be an instance or subtype of type"),
        )
        py_raise(exc)
        return null()

    j: int = start + 1
    while j < n_mro:
        m = pcc_gc_load_ptr(start_cls, ptr_add(mro, j * 8))
        if ptr_is_null(m) == 0:
            n_methods: int = load_i32(m, 56)
            methods = load_ptr(m, 64)
            k: int = 0
            while k < n_methods:
                m_off: int = k * 16
                m_name = load_ptr(methods, m_off)
                if _strs_eq(m_name, name) != 0:
                    method_slot = ptr_add(methods, m_off + 8)
                    func = pcc_gc_note_relocation_read(load_ptr(method_slot, 0))
                    store_ptr(method_slot, 0, func)
                    return func
                k = k + 1
        j = j + 1
    exc = py_exc_new(6, cstr("super object has no attribute"))
    py_raise(exc)
    return null()


@c_abi_export("py_class_dealloc")
def py_class_dealloc(o) -> None:
    if ptr_is_null(o) != 0:
        return
    attrs = pcc_gc_load_ptr(o, ptr_add(o, 104))
    if ptr_is_null(attrs) == 0:
        store_ptr(o, 104, null())
        py_decref(attrs)
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
    pcc_gc_free_object_memory(o)


@c_abi_export("py_instance_dealloc")
def py_instance_dealloc(o) -> None:
    if ptr_is_null(o) != 0:
        return
    py_weakref_invalidate(o)
    py_user_del_dispatch(o)
    if load_i64(o, 0) > 0:
        py_gc_track(o)
        return
    if _ptr_is_instance(o):
        cls = pcc_gc_load_ptr(o, ptr_add(o, 16))
        n_fields: int = load_i32(cls, 72)
        fields_base = ptr_add(o, 24)
        i: int = 0
        while i < n_fields:
            v = pcc_gc_load_ptr(o, ptr_add(fields_base, i * 8))
            if ptr_is_null(v) == 0:
                # Null the slot before decref so a finalizer that re-enters
                # py_instance_dealloc (its __del__ arg-tuple holds self and
                # drops self back to 0 on free) reads NULL and does not
                # double-release this field. Mirrors py_class_dealloc.
                store_ptr(fields_base, i * 8, null())
                py_decref(v)
            i = i + 1
        dyn_slot = _dynamic_attr_slot(o)
        if ptr_is_null(dyn_slot) == 0:
            dyn = pcc_gc_load_ptr(o, dyn_slot)
            if ptr_is_null(dyn) == 0:
                store_ptr(dyn_slot, 0, null())
                py_decref(dyn)
    pcc_gc_free_object_memory(o)


@c_abi_export("py_dataclass_replace")
def py_dataclass_replace(obj, n_overrides: int, names, values):
    if not _ptr_is_instance(obj):
        return null()
    cls = pcc_gc_load_ptr(obj, ptr_add(obj, 16))
    dst = py_instance_new(cls)
    if ptr_is_null(dst) != 0:
        return null()

    n_fields: int = load_i32(cls, 72)
    src_fields = ptr_add(obj, 24)
    dst_fields = ptr_add(dst, 24)
    i: int = 0
    while i < n_fields:
        v = pcc_gc_load_ptr(obj, ptr_add(src_fields, i * 8))
        if ptr_is_null(v) == 0:
            py_incref(v)
            store_ptr(dst_fields, i * 8, v)
        i = i + 1
    src_dyn_slot = _dynamic_attr_slot(obj)
    dst_dyn_slot = _dynamic_attr_slot(dst)
    if ptr_is_null(src_dyn_slot) == 0:
        dyn = pcc_gc_load_ptr(obj, src_dyn_slot)
        if ptr_is_null(dyn) == 0:
            py_incref(dyn)
            store_ptr(dst_dyn_slot, 0, dyn)

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
    if not _ptr_is_instance(obj):
        return null()
    if not _ptr_can_have_header(overrides):
        return null()
    if load_i32(overrides, 8) != 6:  # PY_TYPE_DICT
        return null()

    cls = pcc_gc_load_ptr(obj, ptr_add(obj, 16))
    dst = py_instance_new(cls)
    if ptr_is_null(dst) != 0:
        return null()

    n_fields: int = load_i32(cls, 72)
    src_fields = ptr_add(obj, 24)
    dst_fields = ptr_add(dst, 24)
    i: int = 0
    while i < n_fields:
        v = pcc_gc_load_ptr(obj, ptr_add(src_fields, i * 8))
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
    c = pcc_gc_alloc(120, 10, 0)
    if ptr_is_null(c) != 0:
        return null()
    memset(ptr_add(c, 16), 0, 104)
    store_i64(c, 0, 1)
    store_i32(c, 8, 10)  # PY_TYPE_CLASS
    # Class/type objects are effectively process-lifetime and are referenced
    # by every instance via a *borrowed* (uncounted) class pointer at inst+16.
    # Mark them PY_FLAG_IMMORTAL so a stray over-release cannot drive a live
    # class to refcount 0 and free it out from under its instances -- which
    # zeroes the class field table (n_fields/field_names) and makes every
    # subsequent getattr on any instance fail / segfault. Mirrors py_class.c.
    store_i32(c, 12, load_i32(c, 12) | 1)  # PY_FLAG_IMMORTAL
    store_ptr(c, 16, name)
    store_i32(c, 24, n_bases)
    store_i32(c, 72, n_fields)
    store_ptr(c, 104, null())  # attrs
    store_ptr(c, 112, null())  # metaclass

    # Copy bases array.
    if n_bases > 0:
        if ptr_is_null(bases) == 0:
            bases_copy = malloc(n_bases * 8)
            if ptr_is_null(bases_copy) == 0:
                ii: int = 0
                while ii < n_bases:
                    bv = pcc_gc_note_relocation_read(load_ptr(bases, ii * 8))
                    store_ptr(bases_copy, ii * 8, bv)
                    ii = ii + 1
                store_ptr(c, 32, bases_copy)
                pcc_gc_backend4_zpage_register_owner_payload_span(
                    c,
                    bases_copy,
                    n_bases * 8,
                )
        if ptr_is_null(load_ptr(c, 32)) != 0:
            free(c)
            return null()

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

    n_slots: int = n_fields + 1
    if n_slots < 0:
        n_slots = 1
    inst_size: int = 24 + n_slots * 8
    if inst_size > 0x7FFFFFFF:
        inst_size = 0x7FFFFFFF
    store_i32(c, 88, inst_size)

    # ---- C3 linearize inline ----
    # Allocate MergeSeq array (n_bases + 1 entries, 16 bytes each).
    tail = null()
    tail_len: int = 0
    if n_bases > 0:
        linear_bases = load_ptr(c, 32)
        if ptr_is_null(linear_bases) != 0:
            linear_bases = bases
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
            b = pcc_gc_note_relocation_read(load_ptr(linear_bases, kk * 8))
            b_mro = load_ptr(b, 48)
            b_n_mro: int = load_i32(b, 40)
            seq_off: int = kk * 16
            store_ptr(seqs, seq_off, b_mro)
            store_i32(seqs, seq_off + 8, 0)
            store_i32(seqs, seq_off + 12, b_n_mro)
            cap_total = cap_total + b_n_mro
            kk = kk + 1
        last_off: int = n_bases * 16
        store_ptr(seqs, last_off, linear_bases)
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
                        c_cand = pcc_gc_note_relocation_read(load_ptr(items, hd2 * 8))
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
                                    tv = pcc_gc_note_relocation_read(
                                        load_ptr(items3, tail_i * 8)
                                    )
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
                        cur = pcc_gc_note_relocation_read(load_ptr(items5, hd4 * 8))
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
    pcc_gc_backend4_zpage_register_owner_payload_span(c, mro, mro_len * 8)

    if ptr_is_null(tail) == 0:
        free(tail)
    return c


@c_abi_export("py_class_mark_slots_only")
def py_class_mark_slots_only(cls) -> None:
    if ptr_is_null(cls) != 0:
        return
    flags: int = load_i32(cls, 12)
    store_i32(cls, 12, flags | 2)


@c_abi_export("py_class_mark_dict_subclass")
def py_class_mark_dict_subclass(cls) -> None:
    # Set PY_CLASS_FLAG_DICT_SUBCLASS (bit 2, value 4): this class subclasses
    # the builtin ``dict``, so dict-inherited item storage / methods are routed
    # to a backing dict in the instance's __dict__ slot (see py_protocol.c).
    if ptr_is_null(cls) != 0:
        return
    flags: int = load_i32(cls, 12)
    store_i32(cls, 12, flags | 4)
