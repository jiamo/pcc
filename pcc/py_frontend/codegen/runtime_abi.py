"""Runtime-lib ABI declarations for pcc_py codegen.

Mirrors the C signatures in ``pcc/py_runtime/include/py_runtime.h``
(Section 3 of docs/plans/python-frontend-interfaces.md). Every function
declared here is a placeholder ``llvmlite.ir.Function`` with external
linkage — the definition lives in ``py_runtime.a`` and is linked in at
exe-build time.

Usage::

    from pcc.py_frontend.codegen.runtime_abi import declare_runtime
    module = ir.Module(name="my_module")
    rt = declare_runtime(module)
    builder.call(rt["py_print"], [obj])

Every entry in ``RUNTIME_SIGNATURES`` corresponds 1:1 with a prototype
in py_runtime.h. Changes to the C header MUST be reflected here (and
vice-versa) — the contract doc is the single source of truth.
"""
from __future__ import annotations

from pcc.llvm_capi.compat import ir


# -- Canonical LLVM IR types used in the runtime ABI -------------------------

_VOID = ir.VoidType()
_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
# Opaque pointer. llvmlite emits typed pointers, but we use i8* as the
# generic ptr everywhere the ABI takes/returns a PyObject*. Subsequent
# opt passes ignore the pointee type for opaque-pointer LLVM versions.
_PTR = _I8.as_pointer()
_I32_PTR = _I32.as_pointer()
_CSTR = _I8.as_pointer()          # const char*
_CSTR_PTR = _CSTR.as_pointer()    # const char**

# ``PyObject*`` is spelled ``_PTR`` at the LLVM-IR level.
_PYOBJ = _PTR


# Table of (return_type, [param_types], var_arg). The names and
# signatures mirror py_runtime.h line-for-line. Whenever adding a new
# runtime function, add the C prototype there first, then mirror here.
RUNTIME_SIGNATURES: dict[str, tuple[ir.Type, list[ir.Type], bool]] = {
    # ---- refcount --------------------------------------------------
    "py_incref": (_VOID, [_PYOBJ], False),
    "py_decref": (_VOID, [_PYOBJ], False),
    # ---- Bool ------------------------------------------------------
    "py_bool_from_bit": (_PYOBJ, [_I32], False),
    # ---- Int (tagged + bignum) ------------------------------------
    "py_int_from_i64": (_PYOBJ, [_I64], False),
    "py_int_from_cstr": (_PYOBJ, [_CSTR, _I32], False),
    "py_int_to_i64": (_I64, [_PYOBJ, _I32_PTR], False),
    "py_int_add": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_sub": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_mul": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_floordiv": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_truediv": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_mod": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_pow": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_neg": (_PYOBJ, [_PYOBJ], False),
    "py_int_and": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_or": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_xor": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_shl": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_shr": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_cmp": (_I32, [_PYOBJ, _PYOBJ], False),
    # ---- Float -----------------------------------------------------
    "py_float_from_f64": (_PYOBJ, [_DOUBLE], False),
    "py_float_to_f64": (_DOUBLE, [_PYOBJ], False),
    "py_float_add": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    # ---- Str -------------------------------------------------------
    "py_str_new": (_PYOBJ, [_CSTR, _I64], False),
    "py_str_len": (_I64, [_PYOBJ], False),
    "py_str_byte_len": (_I64, [_PYOBJ], False),
    "py_str_utf8": (_CSTR, [_PYOBJ], False),
    "py_str_ord": (_I64, [_PYOBJ], False),
    "py_str_concat": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_repeat": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_slice": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_str_index": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_eq": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_find": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_upper": (_PYOBJ, [_PYOBJ], False),
    "py_str_lower": (_PYOBJ, [_PYOBJ], False),
    "py_str_strip": (_PYOBJ, [_PYOBJ], False),
    "py_str_split": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_split_maxsplit": (_PYOBJ, [_PYOBJ, _PYOBJ, _I64], False),
    "py_str_splitlines": (_PYOBJ, [_PYOBJ], False),
    "py_str_splitlines_keepends": (_PYOBJ, [_PYOBJ, _I32], False),
    "py_str_lstrip": (_PYOBJ, [_PYOBJ], False),
    "py_str_rstrip": (_PYOBJ, [_PYOBJ], False),
    "py_str_strip_chars": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_lstrip_chars": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_rstrip_chars": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_count": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_isdigit": (_I64, [_PYOBJ], False),
    "py_str_isalpha": (_I64, [_PYOBJ], False),
    "py_str_isspace": (_I64, [_PYOBJ], False),
    "py_str_isalnum": (_I64, [_PYOBJ], False),
    "py_str_join": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_replace": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_str_replace_count": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _I64], False),
    "py_str_startswith": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_endswith": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_chr_from_i64": (_PYOBJ, [_I64], False),
    # ---- List ------------------------------------------------------
    "py_list_new": (_PYOBJ, [_I64], False),
    "py_list_append": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_list_get": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_list_set": (_VOID, [_PYOBJ, _I64, _PYOBJ], False),
    "py_list_len": (_I64, [_PYOBJ], False),
    "py_list_slice": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_list_concat": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_list_repeat": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_list_extend": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_list_insert": (_VOID, [_PYOBJ, _I64, _PYOBJ], False),
    "py_list_pop": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_list_remove": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_list_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_list_index": (_I64, [_PYOBJ, _PYOBJ], False),
    # ---- Dict ------------------------------------------------------
    "py_dict_new": (_PYOBJ, [], False),
    "py_dict_set": (_VOID, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_dict_get": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_dict_get_default": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_dict_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_dict_del": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_dict_len": (_I64, [_PYOBJ], False),
    "py_dict_keys": (_PYOBJ, [_PYOBJ], False),
    "py_dict_values": (_PYOBJ, [_PYOBJ], False),
    "py_dict_items": (_PYOBJ, [_PYOBJ], False),
    # ---- Tuple -----------------------------------------------------
    "py_tuple_new": (_PYOBJ, [_I64], False),
    "py_tuple_set_item": (_VOID, [_PYOBJ, _I64, _PYOBJ], False),
    "py_tuple_get": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_tuple_len": (_I64, [_PYOBJ], False),
    "py_tuple_concat": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_tuple_slice": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    # ---- Set -------------------------------------------------------
    "py_set_new": (_PYOBJ, [], False),
    "py_set_add": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_set_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_set_remove": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_set_len": (_I64, [_PYOBJ], False),
    # ---- Generic object ops ---------------------------------------
    "py_obj_call": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_obj_getattr": (_PYOBJ, [_PYOBJ, _CSTR], False),
    "py_obj_setattr": (_I64, [_PYOBJ, _CSTR, _PYOBJ], False),
    "py_obj_getitem": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_obj_slice": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_obj_setitem": (_I64, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_obj_delitem": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_len": (_I64, [_PYOBJ], False),
    "py_obj_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_sorted": (_PYOBJ, [_PYOBJ], False),
    "py_obj_truthy": (_I64, [_PYOBJ], False),
    "py_obj_type_tag": (_I64, [_PYOBJ], False),
    "py_obj_eq": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_lt": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_le": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_gt": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_ge": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_hash": (_I64, [_PYOBJ], False),
    "py_obj_repr": (_PYOBJ, [_PYOBJ], False),
    "py_obj_str": (_PYOBJ, [_PYOBJ], False),
    "py_obj_isinstance": (_I64, [_PYOBJ, _PYOBJ], False),
    # ---- File I/O --------------------------------------------------
    "py_file_open": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_file_read_all": (_PYOBJ, [_PYOBJ], False),
    "py_file_write": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_file_close": (_VOID, [_PYOBJ], False),
    # ---- Printing --------------------------------------------------
    "py_print": (_VOID, [_PYOBJ], False),
    "py_print_many": (_VOID, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_sys_stdout_write": (_PYOBJ, [_PYOBJ], False),
    "py_sys_stderr_write": (_PYOBJ, [_PYOBJ], False),
    # ---- Process startup -------------------------------------------
    "py_set_program_args": (_VOID, [_I32, _CSTR_PTR], False),
    "py_program_argc": (_I64, [], False),
    "py_program_argv": (_CSTR, [_I64], False),
    "py_process_exit": (_VOID, [_I64], False),
    "py_sys_executable_str": (_PYOBJ, [], False),
    "py_subprocess_check_output": (_PYOBJ, [_PYOBJ], False),
    "py_subprocess_run": (_I64, [_PYOBJ, _I32], False),
    "py_sysconfig_get_config_var": (_PYOBJ, [_PYOBJ], False),
    "py_os_listdir": (_PYOBJ, [_PYOBJ], False),
    "py_shlex_split": (_PYOBJ, [_PYOBJ], False),
    "py_shutil_which": (_PYOBJ, [_PYOBJ], False),
    "py_tempdir_new": (_PYOBJ, [_PYOBJ], False),
    "py_tempdir_cleanup": (_VOID, [_PYOBJ], False),
    # ---- Narrow os.path subset ------------------------------------
    "py_os_getenv": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_os_putenv": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_os_unsetenv": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_join": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_basename": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_dirname": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_exists": (_I32, [_PYOBJ], False),
    "py_os_path_isfile": (_I32, [_PYOBJ], False),
    "py_os_path_isdir":  (_I32, [_PYOBJ], False),
    "py_os_path_getmtime": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_abspath":  (_PYOBJ, [_PYOBJ], False),
    "py_os_getcwd_str":    (_PYOBJ, [], False),
    "py_os_access":        (_I32, [_PYOBJ, _I32], False),
    "py_sys_platform_str": (_PYOBJ, [], False),
    "py_platform_machine_str": (_PYOBJ, [], False),
    "py_platform_release_str": (_PYOBJ, [], False),
    # ---- Exceptions (Phase 3) -------------------------------------
    "py_raise": (_VOID, [_PYOBJ], False),
    "py_current_exception": (_PYOBJ, [], False),
    "py_clear_exception": (_VOID, [], False),
    "py_exc_new": (_PYOBJ, [_I64, _CSTR], False),
    "py_exc_new_with_class": (_PYOBJ, [_PYOBJ, _CSTR], False),
    # py_exc_builtin_class(tag) -> PyClassObject* for a builtin class tag.
    # i64 to match the pcc-Python port's default int lowering.
    "py_exc_builtin_class": (_PYOBJ, [_I64], False),
    # py_exc_matches(exc, class) -> 0/1 (walks MRO). i64 to match the
    # pcc-Python port's default `int` lowering.
    "py_exc_matches": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_exc_set_cause": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_exc_append_frame": (_VOID, [_PYOBJ, _CSTR, _CSTR, _I32], False),
    "py_exc_print_unhandled": (_VOID, [_PYOBJ], False),
    "py_exc_get_message": (_PYOBJ, [_PYOBJ], False),
    # Return-code exception check (post-call branch target). i64 to
    # match the pcc-Python port's default `int` lowering.
    "py_err_occurred": (_I64, [], False),
    # ---- Classes / Instances (Phase 3) ----------------------------
    # py_class_new(name: const char*,
    #              bases: PyClassObject**, n_bases: i32,
    #              field_names: const char**, n_fields: i32)
    #   -> PyClassObject*
    "py_class_new": (_PYOBJ, [_CSTR, _PTR, _I32, _PTR, _I32], False),
    "py_class_add_method": (_VOID, [_PYOBJ, _CSTR, _PYOBJ], False),
    "py_class_lookup": (_PYOBJ, [_PYOBJ, _CSTR], False),
    "py_instance_new": (_PYOBJ, [_PYOBJ], False),
    "py_instance_get_field": (_PYOBJ, [_PYOBJ, _I32], False),
    "py_instance_set_field": (_VOID, [_PYOBJ, _I32, _PYOBJ], False),
    "py_instance_getattr": (_PYOBJ, [_PYOBJ, _CSTR], False),
    "py_instance_setattr": (_I64, [_PYOBJ, _CSTR, _PYOBJ], False),
    "py_dataclass_replace": (_PYOBJ, [_PYOBJ, _I64, _PTR, _PTR], False),
    "py_dataclass_replace_from_dict": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_isinstance": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_super_lookup": (_PYOBJ, [_PYOBJ, _PYOBJ, _CSTR], False),
    # ---- GC --------------------------------------------------------
    "py_gc_init": (_VOID, [], False),
    "py_gc_collect": (_VOID, [], False),
    "py_gc_track": (_VOID, [_PYOBJ], False),
    "py_gc_untrack": (_VOID, [_PYOBJ], False),
    # ---- Phase 4: CPython C-API fallback ---------------------------
    # All CPython pointers show as ``i8*`` at the IR boundary; the
    # distinction from pcc ``PyObject*`` is tracked type-side only.
    "py_cpy_ensure_init": (_VOID, [], False),
    "py_cpy_import": (_PTR, [_CSTR], False),
    "py_cpy_getattr": (_PTR, [_PTR, _CSTR], False),
    "py_cpy_setattr": (_I32, [_PTR, _CSTR, _PTR], False),
    "py_cpy_main_exitcode": (_I32, [], False),
    "py_cpy_call_noargs": (_PTR, [_PTR], False),
    "py_cpy_call1": (_PTR, [_PTR, _PTR], False),
    "py_cpy_call2": (_PTR, [_PTR, _PTR, _PTR], False),
    "py_cpy_call3": (_PTR, [_PTR, _PTR, _PTR, _PTR], False),
    # (callable, n, argv[]) — PyTuple_SetItem steals each ref in argv.
    "py_cpy_call_argv": (_PTR, [_PTR, _I64, _PTR], False),
    # (callable, n_pos, argv[], n_kw, kw_names[], kw_vals[]) — pos
    # refs stolen; kw refs borrowed. kw_names are C strings.
    "py_cpy_call_kw": (
        _PTR, [_PTR, _I64, _PTR, _I64, _PTR, _PTR], False,
    ),
    # (callable, n_pos, argv[], kwargs_dict) — pos refs stolen into the
    # tuple; kwargs_dict is borrowed and passed through to PyObject_Call.
    "py_cpy_call_kwdict": (_PTR, [_PTR, _I64, _PTR, _PTR], False),
    # Like py_cpy_call_kwdict, but merges explicit kw pairs into the
    # borrowed kwargs_dict before the call.
    "py_cpy_call_kwdict_plus": (
        _PTR, [_PTR, _I64, _PTR, _I64, _PTR, _PTR, _PTR], False,
    ),
    # (callable, args_pcc_list) — convert pcc list/tuple to CPython tuple
    # then PyObject_Call. Used for ``fn(*args)`` unpack at call sites.
    "py_cpy_call_list": (_PTR, [_PTR, _PYOBJ], False),
    # Like py_cpy_call_list, but forwards a borrowed CPython kwargs
    # mapping to PyObject_Call as well.
    "py_cpy_call_list_kwdict": (_PTR, [_PTR, _PYOBJ, _PTR], False),
    # (fn_ptr) — wrap a pcc user FuncDef (DynType-in / DynType-out) as
    # a CPython callable via PyCapsule + PyCFunction. Used when a
    # lambda or nested def is passed as a value to a CPython API
    # (``sorted(xs, key=<fn>)``, ``re.sub(pat, <repl>, text)``, etc.).
    "py_cpy_wrap_pcc_0arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_1arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_2arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_3arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_4arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_5arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_6arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_7arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_8arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_9arg": (_PTR, [_PTR], False),
    "py_cpy_len": (_I64, [_PTR], False),
    "py_cpy_getitem": (_PTR, [_PTR, _PTR], False),
    "py_cpy_setitem": (_I32, [_PTR, _PTR, _PTR], False),
    "py_cpy_truthy": (_I32, [_PTR], False),
    "py_cpy_iter": (_PTR, [_PTR], False),
    "py_cpy_iter_next": (_PTR, [_PTR], False),
    "py_cpy_to_pcc_str": (_PYOBJ, [_PTR], False),
    "py_cpy_to_pcc_obj": (_PYOBJ, [_PTR], False),
    "py_cpy_decref": (_VOID, [_PTR], False),
    "py_cpy_incref": (_VOID, [_PTR], False),
    "py_cpy_from_i64": (_PTR, [_I64], False),
    "py_cpy_to_i64": (_I64, [_PTR], False),
    "py_cpy_from_f64": (_PTR, [_DOUBLE], False),
    "py_cpy_to_f64": (_DOUBLE, [_PTR], False),
    "py_cpy_from_pccstr": (_PTR, [_PYOBJ], False),
    "py_cpy_from_pcc_obj": (_PTR, [_PYOBJ], False),
}


# Global constants (extern) from py_runtime.h:
#   extern PyObject *const py_None;
#   extern PyObject *const py_True;
#   extern PyObject *const py_False;
RUNTIME_GLOBALS: dict[str, ir.Type] = {
    "py_None": _PYOBJ,
    "py_True": _PYOBJ,
    "py_False": _PYOBJ,
}


def declare_runtime(module: ir.Module) -> dict[str, ir.Function]:
    """Declare all runtime library functions in ``module``.

    Returns a mapping ``name -> ir.Function`` for every entry in
    :data:`RUNTIME_SIGNATURES`. Declarations are idempotent: calling
    twice on the same module returns the same ``ir.Function`` objects.

    The returned dict intentionally only contains functions. To fetch a
    runtime global (``py_None`` etc.), call :func:`declare_runtime_global`.
    """
    funcs: dict[str, ir.Function] = {}
    for name, (ret_ty, param_tys, var_arg) in RUNTIME_SIGNATURES.items():
        existing = module.globals.get(name)
        if existing is not None and isinstance(existing, ir.Function):
            funcs[name] = existing
            continue
        fnty = ir.FunctionType(ret_ty, param_tys, var_arg=var_arg)
        fn = ir.Function(module, fnty, name=name)
        fn.linkage = "external"
        funcs[name] = fn
    return funcs


def declare_runtime_global(module: ir.Module, name: str) -> ir.GlobalVariable:
    """Declare (or fetch) one of the runtime's extern constant globals.

    Raises :class:`KeyError` if ``name`` is not a known runtime global.
    """
    if name not in RUNTIME_GLOBALS:
        raise KeyError(f"unknown runtime global: {name!r}")
    existing = module.globals.get(name)
    if existing is not None and isinstance(existing, ir.GlobalVariable):
        return existing
    gv = ir.GlobalVariable(module, RUNTIME_GLOBALS[name], name=name)
    gv.linkage = "external"
    gv.global_constant = True
    return gv


__all__ = [
    "RUNTIME_SIGNATURES",
    "RUNTIME_GLOBALS",
    "declare_runtime",
    "declare_runtime_global",
]
