"""pcc-Python ownership of native compiled-module attribute storage."""

__pcc_runtime_port__ = True

from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_STR,
)

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr,
    define_global_ptr_null,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    null,
    ptr_is_null,
    strlen,
)


py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_set = extern("py_dict_set", (c_ptr, c_ptr, c_ptr), c_void)
py_dict_get = extern("py_dict_get", (c_ptr, c_ptr), c_ptr)
py_dict_del = extern("py_dict_del", (c_ptr, c_ptr), c_int64)
py_dict_len = extern("py_dict_len", (c_ptr,), c_int64)
py_dict_entries_used = extern("py_dict_entries_used", (c_ptr,), c_int64)
py_dict_entry_key_at = extern("py_dict_entry_key_at", (c_ptr, c_int64), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_obj_getattr = extern("py_obj_getattr", (c_ptr, c_ptr), c_ptr)
py_obj_len = extern("py_obj_len", (c_ptr,), c_int64)
py_obj_getitem_i64 = extern("py_obj_getitem_i64", (c_ptr, c_int64), c_ptr)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
# py_raise increfs; a caller that created the exception must release it.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)


define_global_ptr_null("pcc_module_attrs_cache")
define_global_ptr_null("py_func_code_class_cache")


def _module_name_key(module_name):
    if ptr_is_null(module_name):
        module_name = cstr("")
    return py_str_new(module_name, strlen(module_name))


def _module_cache(create: int):
    cache = global_load_ptr("pcc_module_attrs_cache")
    if ptr_is_null(cache) and create != 0:
        cache = py_dict_new()
        if ptr_is_null(cache):
            return null()
        pcc_gc_pin(cache)
        global_store_ptr("pcc_module_attrs_cache", cache)
    return cache


def _module_find(module_name):
    cache = _module_cache(0)
    if ptr_is_null(cache):
        return null()
    key = _module_name_key(module_name)
    if ptr_is_null(key):
        return null()
    attrs = py_dict_get(cache, key)
    py_decref(key)
    if not ptr_is_null(attrs):
        # The cache owns the lifetime.  Expose the same borrowed result as the
        # former linked-list node instead of leaking py_dict_get's owned ref.
        py_decref(attrs)
    return attrs


def _module_ensure(module_name):
    attrs = _module_find(module_name)
    if not ptr_is_null(attrs):
        return attrs
    cache = _module_cache(1)
    if ptr_is_null(cache):
        return null()
    key = _module_name_key(module_name)
    if ptr_is_null(key):
        return null()
    attrs = py_dict_new()
    if ptr_is_null(attrs):
        py_decref(key)
        return null()
    pcc_gc_pin(attrs)
    py_dict_set(cache, key, attrs)
    py_decref(key)
    py_decref(attrs)
    return attrs


@c_abi_export("py_module_attrs_dict")
def py_module_attrs_dict(module_name, create: int):
    if create != 0:
        return _module_ensure(module_name)
    return _module_find(module_name)


@c_abi_export("py_module_attr_set")
def py_module_attr_set(module_name, attr_name, value) -> int:
    if ptr_is_null(attr_name) or ptr_is_null(value):
        return -1
    attrs = _module_ensure(module_name)
    if ptr_is_null(attrs):
        return -1
    key = py_str_new(attr_name, strlen(attr_name))
    if ptr_is_null(key):
        return -1
    py_dict_set(attrs, key, value)
    py_decref(key)
    return 0


@c_abi_export("py_module_attr_get")
def py_module_attr_get(module_name, attr_name):
    if ptr_is_null(attr_name):
        return null()
    attrs = _module_find(module_name)
    if ptr_is_null(attrs):
        return null()
    key = py_str_new(attr_name, strlen(attr_name))
    if ptr_is_null(key):
        return null()
    value = py_dict_get(attrs, key)
    py_decref(key)
    return value


def _is_string(value) -> bool:
    if ptr_is_null(value) or is_tagged_int(value):
        return False
    return load_i32(value, 8) == PY_TYPE_STR


def _import_star_name(dest, source_module, source_attrs, name) -> int:
    if not _is_string(name):
        py_raise_owned(py_exc_new(3, cstr("module __all__ must contain only strings")))
        return -1
    value = py_dict_get(source_attrs, name)
    if ptr_is_null(value):
        attr_name = py_str_utf8(name)
        if not ptr_is_null(attr_name):
            value = py_obj_getattr(source_module, attr_name)
    if ptr_is_null(value):
        return -1
    py_dict_set(dest, name, value)
    py_decref(value)
    return 0


@c_abi_export("py_module_import_star")
def py_module_import_star(module_name, source_module) -> int:
    if ptr_is_null(module_name) or ptr_is_null(source_module):
        return -1
    source_attrs = py_obj_getattr(source_module, cstr("__dict__"))
    if ptr_is_null(source_attrs):
        return -1
    dest = _module_ensure(module_name)
    if ptr_is_null(dest):
        py_decref(source_attrs)
        return -1

    all_key = py_str_new(cstr("__all__"), 7)
    if ptr_is_null(all_key):
        py_decref(source_attrs)
        return -1
    all_names = py_dict_get(source_attrs, all_key)
    py_decref(all_key)
    if not ptr_is_null(all_names):
        count: int = py_obj_len(all_names)
        if count < 0:
            py_decref(all_names)
            py_decref(source_attrs)
            return -1
        i: int = 0
        while i < count:
            name = py_obj_getitem_i64(all_names, i)
            if ptr_is_null(name):
                py_decref(all_names)
                py_decref(source_attrs)
                return -1
            rc: int = _import_star_name(dest, source_module, source_attrs, name)
            py_decref(name)
            if rc != 0:
                py_decref(all_names)
                py_decref(source_attrs)
                return -1
            i = i + 1
        py_decref(all_names)
        py_decref(source_attrs)
        return 0

    entries: int = py_dict_entries_used(source_attrs)
    i = 0
    while i < entries:
        name = py_dict_entry_key_at(source_attrs, i)
        if not ptr_is_null(name) and _is_string(name):
            text = py_str_utf8(name)
            if not ptr_is_null(text) and load_i8(text, 0) != 95:
                if _import_star_name(dest, source_module, source_attrs, name) != 0:
                    py_decref(name)
                    py_decref(source_attrs)
                    return -1
        if not ptr_is_null(name):
            py_decref(name)
        i = i + 1
    py_decref(source_attrs)
    return 0


@c_abi_export("py_module_attr_value_or_default")
def py_module_attr_value_or_default(slot, default_value):
    if ptr_is_null(slot):
        return default_value
    value = pcc_gc_load_ptr(null(), slot)
    if ptr_is_null(value):
        return default_value
    if not ptr_is_null(default_value):
        py_decref(default_value)
    return value


@c_abi_export("py_module_attr_del")
def py_module_attr_del(module_name, attr_name) -> int:
    if ptr_is_null(attr_name):
        return -1
    attrs = _module_find(module_name)
    if ptr_is_null(attrs):
        return -1
    key = py_str_new(attr_name, strlen(attr_name))
    if ptr_is_null(key):
        return -1
    rc: int = py_dict_del(attrs, key)
    py_decref(key)
    return rc


@c_abi_export("py_module_attr_len")
def py_module_attr_len(module_name) -> int:
    attrs = _module_find(module_name)
    if ptr_is_null(attrs):
        return 0
    return py_dict_len(attrs)
