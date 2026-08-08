"""pcc-Python owners for the no-libpython C-API import surface.

Replaces the PyImport_* block of py_capi_shim.c.  Delegates to the migrated
extension/compiled-module loaders (py_extension_loader_runtime /
py_compiled_module_runtime) and to PyUnicode_AsUTF8.

Owned surface (stable C ABI names):

  PyImport_ImportModule, PyImport_Import, py_builtin_import

Constants (inlined per the pcc-Python runtime-module contract):
  PY_EXC_RUNTIMEERROR = 7, PY_EXC_TYPEERROR = 3, PY_EXC_VALUEERROR = 2,
  PY_EXC_MODULENOTFOUNDERROR = 21
"""
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_STR,
)

from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr,
    free,
    global_load_ptr,
    is_tagged_int,
    load_i8,
    malloc,
    memcpy,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i8,
    strlen,
)

py_native_extension_import_by_name = extern(
    "py_native_extension_import_by_name", (c_ptr,), c_ptr
)
py_compiled_module_import_by_name = extern(
    "py_compiled_module_import_by_name", (c_ptr,), c_ptr
)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
PyUnicode_AsUTF8 = extern("PyUnicode_AsUTF8", (c_ptr,), c_ptr)


def _not_found_message(name) -> c_ptr:
    # "PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython] module not found: <name>"
    prefix = cstr("PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython] module not found: ")
    plen = strlen(prefix)
    nlen = strlen(name)
    buf = malloc(plen + nlen + 1)
    if ptr_is_null(buf):
        return null()
    memcpy(buf, prefix, plen)
    memcpy(ptr_add(buf, plen), name, nlen)
    store_i8(buf, plen + nlen, 0)
    return buf


def _python_not_found_message(name) -> c_ptr:
    prefix = cstr("No module named '")
    suffix = cstr("'")
    plen = strlen(prefix)
    nlen = strlen(name)
    slen = strlen(suffix)
    buf = malloc(plen + nlen + slen + 1)
    if ptr_is_null(buf):
        return null()
    memcpy(buf, prefix, plen)
    memcpy(ptr_add(buf, plen), name, nlen)
    memcpy(ptr_add(buf, plen + nlen), suffix, slen)
    store_i8(buf, plen + nlen + slen, 0)
    return buf


@c_abi_typed_export("PyImport_ImportModule", "ptr", ("ptr",))
def PyImport_ImportModule(name) -> c_ptr:
    if ptr_is_null(name) or load_i8(name, 0) == 0:
        py_raise(py_exc_new(2, cstr("empty module name")))  # PY_EXC_VALUEERROR
        return null()
    module = py_native_extension_import_by_name(name)
    if ptr_is_null(module) and py_err_occurred() == 0:
        module = py_compiled_module_import_by_name(name)
    if ptr_is_null(module) and py_err_occurred() == 0:
        message = _not_found_message(name)
        if ptr_is_null(message):
            py_raise(
                py_exc_new(7, cstr("PCC-PYEXT-IMPORT-001 module not found"))
            )
        else:
            exc = py_exc_new(7, message)
            free(message)
            py_raise(exc)
    return module


@c_abi_typed_export("PyImport_Import", "ptr", ("ptr",))
def PyImport_Import(name) -> c_ptr:
    if ptr_is_null(name):
        py_raise(py_exc_new(3, cstr("import name required")))  # PY_EXC_TYPEERROR
        return null()
    cname = PyUnicode_AsUTF8(name)
    if ptr_is_null(cname):
        return null()
    return PyImport_ImportModule(cname)


# --- py_builtin_import ------------------------------------------------

py_type_of = extern("pcc_py_type_of", (c_ptr,), c_int64)
py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)
py_obj_len = extern("py_obj_len", (c_ptr,), c_int64)
py_decref = extern("py_decref", (c_ptr,), c_void)
PyMem_Malloc = extern("PyMem_Malloc", (c_int64,), c_ptr)
PyMem_Free = extern("PyMem_Free", (c_ptr,), c_void)


@c_abi_typed_export("py_builtin_import", "ptr", ("ptr", "ptr"))
def py_builtin_import(name, fromlist) -> c_ptr:
    if ptr_is_null(name) or is_tagged_int(name) or py_type_of(name) != PY_TYPE_STR:
        py_raise(py_exc_new(3, cstr("module name must be a string")))
        return null()
    cname = py_str_utf8(name)
    if ptr_is_null(cname):
        return null()
    if py_str_byte_len(name) == 0:
        py_raise(py_exc_new(2, cstr("Empty module name")))
        return null()
    if strlen(cname) != py_str_byte_len(name):
        py_raise(py_exc_new(2, cstr("module name contains a null character")))
        return null()
    # Python-level __import__ owns ModuleNotFoundError.  The public C-API entry
    # above deliberately keeps its PCC-PYEXT RuntimeError diagnostic, so query
    # the two native registries directly here rather than inheriting that type.
    module = py_native_extension_import_by_name(cname)
    if ptr_is_null(module) and py_err_occurred() == 0:
        module = py_compiled_module_import_by_name(cname)
    if ptr_is_null(module):
        if py_err_occurred() == 0:
            message = _python_not_found_message(cname)
            if ptr_is_null(message):
                py_raise(py_exc_new(21, cstr("module not found")))
            else:
                exc = py_exc_new(21, message)
                free(message)
                py_raise(exc)
        return null()
    fromlist_count: int = 0
    if not ptr_is_null(fromlist) and not ptr_eq(fromlist, global_load_ptr("py_None")):
        fromlist_count = py_obj_len(fromlist)
        if fromlist_count < 0:
            py_decref(module)
            return null()
    if fromlist_count > 0:
        return module
    dot = _cstr_find_char(cname, 46)  # '.'
    if dot < 0:
        return module
    top_len: int = dot
    top_name = PyMem_Malloc(top_len + 1)
    if ptr_is_null(top_name):
        py_decref(module)
        py_raise(py_exc_new(19, cstr("out of memory importing module")))  # MEMORYERROR
        return null()
    memcpy(top_name, cname, top_len)
    store_i8(top_name, top_len, 0)
    top_module = PyImport_ImportModule(top_name)
    PyMem_Free(top_name)
    py_decref(module)
    return top_module


def _cstr_find_char(s, ch: int) -> int:
    i: int = 0
    while True:
        c: int = load_i8(s, i)
        if c == 0:
            return -1
        if c == ch:
            return i
        i += 1
