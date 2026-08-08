"""pcc-Python compiled-module registry and initialization ordering."""

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    calloc,
    call_void_ptr0,
    cstr,
    define_global_ptr_null,
    free,
    global_load_ptr,
    global_store_ptr,
    load_i8,
    load_i32,
    load_ptr,
    malloc,
    memcpy,
    null,
    ptr_add,
    ptr_is_null,
    store_i8,
    store_i32,
    store_ptr,
    strlen,
)


py_class_new = extern(
    "py_class_new", (c_ptr, c_ptr, c_int32, c_ptr, c_int32), c_ptr
)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_module_attrs_dict = extern("py_module_attrs_dict", (c_ptr, c_int64), c_ptr)
py_instance_new = extern("py_instance_new", (c_ptr,), c_ptr)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_instance_setattr = extern("py_instance_setattr", (c_ptr, c_ptr, c_ptr), c_int64)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)


define_global_ptr_null("pcc_runtime_module_class_cache")
define_global_ptr_null("pcc_compiled_modules")
define_global_ptr_null("pcc_compiled_module_inits")

# Both registries used to be plain singly-linked lists walked with a string
# compare per node.  With the pcc closure's 500+ modules that made every
# import and every init lookup O(modules) strcmps, and it was the single
# hottest leaf in a `pcc1 -> pcc2` build.  These bucket arrays turn the same
# lookups into one hash plus (typically) one compare.  The linear `next`
# chains are kept exactly as they were so registration order and any external
# expectations are unchanged; the buckets are a pure index over them.
define_global_ptr_null("pcc_compiled_modules_index")
define_global_ptr_null("pcc_compiled_module_inits_index")


def _cstr_equal(left, right) -> bool:
    if ptr_is_null(left) or ptr_is_null(right):
        return ptr_is_null(left) and ptr_is_null(right)
    i: int = 0
    while True:
        a: int = load_i8(left, i) & 255
        b: int = load_i8(right, i) & 255
        if a != b:
            return False
        if a == 0:
            return True
        i = i + 1
    return False


def _duplicate_cstr(value):
    size: int = strlen(value) + 1
    copy = malloc(size)
    if ptr_is_null(copy):
        return null()
    memcpy(copy, value, size)
    return copy


def _raise_no_memory() -> None:
    py_raise(py_exc_new(10, cstr("out of memory")))


@c_abi_export("pcc_runtime_module_class")
def pcc_runtime_module_class():
    cls = global_load_ptr("pcc_runtime_module_class_cache")
    if not ptr_is_null(cls):
        return cls
    cls = py_class_new(cstr("module"), null(), 0, null(), 0)
    if not ptr_is_null(cls):
        pcc_gc_pin(cls)
        global_store_ptr("pcc_runtime_module_class_cache", cls)
    return cls


def _cstr_hash_bucket(text) -> int:
    """djb2 over a NUL-terminated name, masked to 512 buckets.

    512 keeps the pcc closure's ~500 modules at roughly one node per bucket.
    """
    value: int = 5381
    index: int = 0
    byte: int = load_i8(text, 0)
    while byte != 0:
        value = ((value * 33) + byte) & 4294967295
        index = index + 1
        byte = load_i8(text, index)
    return value & 511


def _lookup_init_node(name):
    index = global_load_ptr("pcc_compiled_module_inits_index")
    if ptr_is_null(index):
        return null()
    node = load_ptr(ptr_add(index, _cstr_hash_bucket(name) * 8), 0)
    while not ptr_is_null(node):
        if _cstr_equal(load_ptr(node, 0), name):
            return node
        node = load_ptr(node, 32)
    return null()


@c_abi_export("py_compiled_module_register_init")
def py_compiled_module_register_init(name, init_fn) -> int:
    if ptr_is_null(name) or load_i8(name, 0) == 0 or ptr_is_null(init_fn):
        return -1
    existing = _lookup_init_node(name)
    if not ptr_is_null(existing):
        store_ptr(existing, 8, init_fn)
        return 0

    index = global_load_ptr("pcc_compiled_module_inits_index")
    if ptr_is_null(index):
        index = calloc(512, 8)
        if ptr_is_null(index):
            return -1
        global_store_ptr("pcc_compiled_module_inits_index", index)

    node = malloc(40)
    if ptr_is_null(node):
        return -1
    name_copy = _duplicate_cstr(name)
    if ptr_is_null(name_copy):
        free(node)
        return -1
    store_ptr(node, 0, name_copy)
    store_ptr(node, 8, init_fn)
    store_i32(node, 16, 0)
    store_ptr(node, 24, global_load_ptr("pcc_compiled_module_inits"))
    global_store_ptr("pcc_compiled_module_inits", node)
    bucket_slot = ptr_add(index, _cstr_hash_bucket(name_copy) * 8)
    store_ptr(node, 32, load_ptr(bucket_slot, 0))
    store_ptr(bucket_slot, 0, node)
    return 0


def _run_compiled_module_init(name) -> int:
    node = _lookup_init_node(name)
    if ptr_is_null(node):
        return 0
    if load_i32(node, 16) != 0:
        return 0
    store_i32(node, 16, 1)
    call_void_ptr0(load_ptr(node, 8))
    if py_err_occurred() != 0:
        store_i32(node, 16, 0)
        return -1
    store_i32(node, 16, 2)
    return 0


def _compiled_module_has_init(name) -> bool:
    if ptr_is_null(name) or load_i8(name, 0) == 0:
        return False
    return not ptr_is_null(_lookup_init_node(name))


@c_abi_export("py_compiled_module_ensure_parent_packages")
def py_compiled_module_ensure_parent_packages(module_name) -> int:
    if ptr_is_null(module_name):
        return 0
    index: int = 0
    while load_i8(module_name, index) != 0:
        if load_i8(module_name, index) == 46:
            parent = malloc(index + 1)
            if ptr_is_null(parent):
                _raise_no_memory()
                return -1
            memcpy(parent, module_name, index)
            store_i8(parent, index, 0)
            rc: int = _run_compiled_module_init(parent)
            free(parent)
            if rc != 0:
                return -1
        index = index + 1
    return 0


def _run_compiled_module_init_with_parents(name) -> int:
    if py_compiled_module_ensure_parent_packages(name) != 0:
        return -1
    return _run_compiled_module_init(name)


@c_abi_export("py_compiled_module_import_by_name")
def py_compiled_module_import_by_name(name):
    if ptr_is_null(name) or load_i8(name, 0) == 0:
        return null()
    cached = global_load_ptr("pcc_compiled_modules_index")
    if not ptr_is_null(cached):
        node = load_ptr(ptr_add(cached, _cstr_hash_bucket(name) * 8), 0)
        while not ptr_is_null(node):
            if _cstr_equal(load_ptr(node, 0), name):
                module = load_ptr(node, 8)
                py_incref(module)
                return module
            node = load_ptr(node, 24)

    # py_module_attrs_dict is create-on-write. Unknown names must not become
    # successful empty modules merely because an attribute table can be made.
    if not _compiled_module_has_init(name):
        return null()

    if _run_compiled_module_init_with_parents(name) != 0:
        return null()
    attrs = py_module_attrs_dict(name, 0)
    if ptr_is_null(attrs):
        return null()
    cls = pcc_runtime_module_class()
    if ptr_is_null(cls):
        return null()
    module = py_instance_new(cls)
    if ptr_is_null(module):
        return null()

    # A module instance has zero declared fields, so offset 24 is its dynamic
    # attribute dictionary slot.  Share the live side table, as the C owner did.
    pcc_gc_store_ptr(module, ptr_add(module, 24), attrs)
    name_obj = py_str_new(name, strlen(name))
    if ptr_is_null(name_obj):
        py_decref(module)
        return null()
    name_rc: int = py_instance_setattr(module, cstr("__name__"), name_obj)
    py_decref(name_obj)
    if name_rc != 0:
        py_decref(module)
        return null()

    index = global_load_ptr("pcc_compiled_modules_index")
    if ptr_is_null(index):
        index = calloc(512, 8)
        if ptr_is_null(index):
            py_decref(module)
            return null()
        global_store_ptr("pcc_compiled_modules_index", index)
    node = malloc(32)
    if ptr_is_null(node):
        py_decref(module)
        return null()
    name_copy = _duplicate_cstr(name)
    if ptr_is_null(name_copy):
        free(node)
        py_decref(module)
        return null()
    store_ptr(node, 0, name_copy)
    store_ptr(node, 8, module)
    pcc_gc_pin(module)
    store_ptr(node, 16, global_load_ptr("pcc_compiled_modules"))
    global_store_ptr("pcc_compiled_modules", node)
    bucket_slot = ptr_add(index, _cstr_hash_bucket(name_copy) * 8)
    store_ptr(node, 24, load_ptr(bucket_slot, 0))
    store_ptr(bucket_slot, 0, node)
    py_incref(module)
    return module
