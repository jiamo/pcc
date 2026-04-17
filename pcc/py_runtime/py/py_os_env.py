"""Phase 4c.9: pcc-Python port of py_os_env.c.

Environment-variable helpers (getenv / putenv / unsetenv). The path
helpers stay in py_os_path.c for now because they need mutable byte-
buffer growth that pcc-Python cannot yet express cleanly.

Coercion: keys/values may be any py object; non-str gets routed
through py_obj_str() to realize a PyStrObject before reading its
UTF-8 bytes.
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.unsafe import (
    getenv,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    null,
    ptr_is_null,
    setenv,
    strlen,
    unsetenv,
)


py_decref            = extern("py_decref",            (c_ptr,),                   c_void)
py_str_new           = extern("py_str_new",           (c_ptr, c_int64),           c_ptr)
py_str_utf8          = extern("py_str_utf8",          (c_ptr,),                   c_ptr)
py_obj_str           = extern("py_obj_str",           (c_ptr,),                   c_ptr)


# PY_TYPE_STR (=4) is inlined at every use site because pcc-Python
# initializes module-level integers in the auto-generated main(),
# which the Makefile strips for library .o builds.


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)


def _coerce_to_str(o):
    # Returns (item, owned) — item is the PyStrObject to read, owned
    # is the temporary we must decref after, or NULL.
    if ptr_is_null(o):
        return null(), null()
    if _type_of(o) == 4:           # PY_TYPE_STR
        return o, null()
    new_s = py_obj_str(o)
    return new_s, new_s


@c_abi_export("py_os_getenv")
def py_os_getenv(key, default_value):
    item, owned = _coerce_to_str(key)
    if ptr_is_null(item):
        if not ptr_is_null(owned):
            py_decref(owned)
        return default_value
    name = py_str_utf8(item)
    if ptr_is_null(name):
        if not ptr_is_null(owned):
            py_decref(owned)
        return default_value
    raw = getenv(name)
    if not ptr_is_null(owned):
        py_decref(owned)
    if ptr_is_null(raw):
        return default_value
    n: int = strlen(raw)
    return py_str_new(raw, n)


@c_abi_export("py_os_putenv")
def py_os_putenv(key, value):
    k_item, k_owned = _coerce_to_str(key)
    v_item, v_owned = _coerce_to_str(value)
    if ptr_is_null(k_item):
        if not ptr_is_null(k_owned):
            py_decref(k_owned)
        if not ptr_is_null(v_owned):
            py_decref(v_owned)
        return global_load_ptr("py_None")
    if ptr_is_null(v_item):
        if not ptr_is_null(k_owned):
            py_decref(k_owned)
        if not ptr_is_null(v_owned):
            py_decref(v_owned)
        return global_load_ptr("py_None")
    k_raw = py_str_utf8(k_item)
    v_raw = py_str_utf8(v_item)
    if not ptr_is_null(k_raw):
        if not ptr_is_null(v_raw):
            setenv(k_raw, v_raw, 1)
    if not ptr_is_null(k_owned):
        py_decref(k_owned)
    if not ptr_is_null(v_owned):
        py_decref(v_owned)
    return global_load_ptr("py_None")


@c_abi_export("py_os_unsetenv")
def py_os_unsetenv(key):
    item, owned = _coerce_to_str(key)
    if ptr_is_null(item):
        if not ptr_is_null(owned):
            py_decref(owned)
        return global_load_ptr("py_None")
    raw = py_str_utf8(item)
    if not ptr_is_null(raw):
        unsetenv(raw)
    if not ptr_is_null(owned):
        py_decref(owned)
    return global_load_ptr("py_None")
