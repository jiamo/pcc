"""Name expression lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    BoolType,
    ByteArrayType,
    BytesType,
    ClassType,
    ComplexType,
    DictType,
    DynType,
    FloatType,
    IntType,
    ListType,
    MemoryViewType,
    Name,
    NoneType,
    StrType,
    TupleType,
    Type,
)
from .runtime_abi import declare_runtime_global


_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()
_CPY_BUILTIN_TYPE_NAMES = frozenset(
    {
        "int",
        "str",
        "list",
        "dict",
        "tuple",
        "float",
        "bool",
        "bytes",
        "bytearray",
        "set",
        "frozenset",
        "complex",
        "object",
        "type",
        "Exception",
        "BaseException",
    }
)


class NameLoweringMixin:
    def _static_runtime_type_name(self, ty: Type) -> Optional[str]:
        if isinstance(ty, StrType):
            return "str"
        if isinstance(ty, BytesType):
            return "bytes"
        if isinstance(ty, ByteArrayType):
            return "bytearray"
        if isinstance(ty, MemoryViewType):
            return "memoryview"
        if isinstance(ty, BoolType):
            return "bool"
        if isinstance(ty, IntType):
            return "int"
        if isinstance(ty, FloatType):
            return "float"
        if isinstance(ty, ComplexType):
            return "complex"
        if isinstance(ty, ListType):
            return "list"
        if isinstance(ty, DictType):
            return "dict"
        if isinstance(ty, TupleType):
            return "tuple"
        if isinstance(ty, NoneType):
            return "NoneType"
        if isinstance(ty, ClassType):
            # Class types (including exceptions) are subclassable; their dynamic type at
            # runtime can differ from the static type. We return None so that
            # type(obj).__name__ resolves dynamically via py_obj_type_name.
            return None
        return None
    def _annotation_runtime_name(self, ann: object) -> str:
        if isinstance(ann, IntType):
            return "int"
        if isinstance(ann, FloatType):
            return "float"
        if isinstance(ann, BoolType):
            return "bool"
        if isinstance(ann, StrType):
            return "str"
        if isinstance(ann, NoneType):
            return "None"
        if isinstance(ann, ListType):
            return "list"
        if isinstance(ann, DictType):
            return "dict"
        if isinstance(ann, TupleType):
            return "tuple"
        if isinstance(ann, ClassType):
            return ann.name
        if isinstance(ann, DynType):
            return "dyn"
        if isinstance(ann, Name):
            return ann.ident
        return type(ann).__name__
    def _emit_name(self, expr: Name) -> ir.Value:
        slot = self.env.get(expr.ident)
        if slot is None:
            # Method-body ``__class__`` is a compiler-created cell in
            # CPython. For the currently supported direct method lowering,
            # current_class is the defining class, so expose that class
            # object when no local binding shadows the name.
            if expr.ident == "__class__":
                current_class = getattr(self, "current_class", None)
                if current_class is not None:
                    return self.builder.load(
                        current_class.global_var,
                        name=self._fresh(f"cls.{current_class.name}"),
                    )
            # Module-level dunder that pcc can resolve at compile time
            # when the file is being compiled as a top-level script.
            # Matches CPython's behavior for ``python myscript.py``.
            if expr.ident == "__name__":
                return self._emit_str_literal("__main__")
            if expr.ident == "__file__":
                # Compile-time approximation — CPython sets ``__file__``
                # to the absolute path of the compiled script. pcc
                # doesn't have the source path here at codegen time;
                # return the sanitized module name instead so code that
                # logs / path-derives from ``__file__`` keeps working.
                return self._emit_str_literal(
                    (self.ast_module.name or "pcc_py_module") + ".py"
                )
            if expr.ident == "__doc__":
                return self._emit_str_literal("")
            if expr.ident == "Ellipsis":
                # ``...`` / ``Ellipsis`` used as an expression — pcc
                # doesn't have a distinct Ellipsis type; reuse the
                # None-literal emitter so code that stashes
                # ``Ellipsis`` as a sentinel keeps working.
                return self._emit_none_literal()
            if expr.ident == "NotImplemented":
                gv = declare_runtime_global(self.module, "py_NotImplemented")
                return self.builder.load(gv, name=self._fresh("notimplemented"))
            if expr.ident in ("True", "False"):
                return ir.Constant(_I1, 1 if expr.ident == "True" else 0)
            # Built-in type names at value position (``isinstance(x,
            # int)`` already folds compile-time; this covers the
            # residual ``obj_type = int`` / ``self.ty = str`` uses).
            # Route through CPython's ``builtins`` module so the value
            # is a real type object.
            if expr.ident in _CPY_BUILTIN_TYPE_NAMES:
                return self._load_cpython_builtin(expr.ident)
            builtin_value = self._native_builtin_value_for_name(expr.ident)
            if builtin_value == "os.sep":
                return self._emit_str_literal("/")
            if builtin_value == "os.linesep":
                return self._emit_str_literal("\n")
            if builtin_value == "os.altsep":
                return self._emit_none_literal()
            if builtin_value == "os.pathsep":
                return self._emit_str_literal(":")
            if builtin_value in ("sys.prefix", "sys.base_prefix"):
                return self.builder.call(
                    self.runtime["py_sys_prefix_str"],
                    [
                        ir.Constant(
                            _I64,
                            1 if builtin_value == "sys.base_prefix" else 0,
                        )
                    ],
                    name=self._fresh(builtin_value),
                )
            if builtin_value == "pcc.optional_import_missing.None":
                return self._emit_none_literal()
            # Module-level constant? Emit a load of the global.
            module_globals = self._module_globals
            if expr.ident in module_globals:
                gv, _declared_ty = module_globals[expr.ident]
                val = self.builder.load(
                    gv,
                    name=self._fresh(expr.ident),
                )
                if self._cpy_module_flags.get(expr.ident, False):
                    if not hasattr(self, "_cpy_values"):
                        self._cpy_values = set()
                    self._cpy_values.add(val)
                return val
            # User class reference at value position — load the class
            # global so ``ClassName.ATTR`` and similar look-ups work.
            if (
                hasattr(self, "class_lowering")
                and expr.ident in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[expr.ident]
                return self.builder.load(
                    info.global_var,
                    name=self._fresh(f"cls.{expr.ident}"),
                )
            native_alias_module = getattr(
                self,
                "_native_module_aliases",
                {},
            ).get(expr.ident)
            if native_alias_module is not None:
                return self._emit_native_module_placeholder(native_alias_module)
            native_constant = getattr(
                self,
                "_native_module_constant_bindings",
                {},
            ).get(expr.ident)
            if native_constant is not None:
                return self._emit_native_module_constant(native_constant)
            native_ext_gv = getattr(self, "_native_extension_module_env", {}).get(
                expr.ident
            )
            if native_ext_gv is not None:
                return self.builder.load(
                    native_ext_gv,
                    name=self._fresh(f"pcc.ext.{expr.ident}"),
                )
            # Fall back to the module-wide CPython import registry for
            # ``from os import sep`` / ``import sys`` style bindings.
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(expr.ident)
            if cpy_gv is not None:
                val = self.builder.load(cpy_gv, name=self._fresh(f"cpy.{expr.ident}"))
                if not hasattr(self, "_cpy_values"):
                    self._cpy_values = set()
                self._cpy_values.add(val)
                return val
            builtin_module = self._native_builtin_module_for_name(expr.ident)
            if builtin_module is not None:
                return self._emit_cpython_module_value(builtin_module)
            star_val = self._load_from_cpy_star_imports(expr.ident)
            if star_val is not None:
                return star_val
            # User FuncDef at value position: wrap the pcc function
            # pointer as a CPython PyCFunction so it can be passed to
            # ``re.sub(pat, <repl>, text)`` / ``am.register(KEY, <fn>)``
            # / ``{c_ast.FileAST: _children_FileAST}`` / any other
            # CPython API that consumes a callable. Covers 1 / 2 / 3
            # arg DynType-in / DynType-out. Higher arity still falls
            # through.
            resolved_name = expr.ident
            fn_ir = self.functions.get(expr.ident)
            if fn_ir is None:
                direct_hoist = f"__nested_{expr.ident}"
                if direct_hoist in self.functions:
                    resolved_name = direct_hoist
                    fn_ir = self.functions[direct_hoist]
                else:
                    matches = [
                        name
                        for name in self.functions
                        if name.startswith(f"{direct_hoist}_")
                    ]
                    if len(matches) == 1:
                        resolved_name = matches[0]
                        fn_ir = self.functions[resolved_name]
            # Adapter-wrap path: the ident may originally have been
            # a nested def flagged for captures-via-globals wrap.
            # ``rename_map`` at hoist time remapped the original name
            # to the hoisted one already, but the metadata dict is
            # keyed on the original name. Try both.
            adapter_entry = None
            for candidate in (expr.ident, resolved_name):
                adapter_entry = getattr(
                    self,
                    "_hoist_wrap_caps",
                    {},
                ).get(candidate)
                if adapter_entry is not None:
                    break
            if fn_ir is None and adapter_entry is not None:
                hoisted_name = adapter_entry.get("hoisted_name")
                if hoisted_name:
                    fn_ir = self.functions.get(hoisted_name)
                    resolved_name = hoisted_name
            if (
                adapter_entry is None
                and fn_ir is not None
                and resolved_name != expr.ident
            ):
                free_names = getattr(
                    self,
                    "_hoisted_capture_params",
                    {},
                ).get(resolved_name, ())
                if free_names:
                    fnty = getattr(fn_ir, "function_type", None)
                    total_arity = len(getattr(fnty, "args", ()))
                    adapter_entry = {
                        "original_arity": max(total_arity - len(free_names), 0),
                        "free_names": tuple(free_names),
                        "hoisted_name": resolved_name,
                        "original_name": expr.ident,
                    }
            if fn_ir is not None:
                native_free_names = getattr(
                    self,
                    "_hoisted_capture_params",
                    {},
                ).get(resolved_name)
                if native_free_names is not None or getattr(
                    self,
                    "_prefer_native_callable_values",
                    False,
                ):
                    return self._emit_native_func_value(
                        expr.ident,
                        resolved_name,
                        fn_ir,
                        tuple(native_free_names or ()),
                    )
                fnty = getattr(fn_ir, "function_type", None)
                all_ptr_args = fnty is not None and all(
                    isinstance(a, ir.PointerType) for a in fnty.args
                )
                ret_ok = fnty is not None and isinstance(
                    fnty.return_type, ir.PointerType
                )
                ret_void = fnty is not None and isinstance(
                    fnty.return_type, ir.VoidType
                )
                ret_int_width = (
                    fnty.return_type.width
                    if fnty is not None
                    and isinstance(
                        fnty.return_type,
                        ir.IntType,
                    )
                    else 0
                )
                wrap_helper = None
                arity = None
                if all_ptr_args and (ret_ok or ret_void or ret_int_width in (1, 64)):
                    arity = len(fnty.args)
                    # Captures-adapter has original arity.
                    if adapter_entry is not None and adapter_entry.get("hoisted_name"):
                        arity = adapter_entry["original_arity"]
                    wrap_helper = {
                        0: "py_cpy_wrap_pcc_0arg",
                        1: "py_cpy_wrap_pcc_1arg",
                        2: "py_cpy_wrap_pcc_2arg",
                        3: "py_cpy_wrap_pcc_3arg",
                        4: "py_cpy_wrap_pcc_4arg",
                        5: "py_cpy_wrap_pcc_5arg",
                        6: "py_cpy_wrap_pcc_6arg",
                        7: "py_cpy_wrap_pcc_7arg",
                        8: "py_cpy_wrap_pcc_8arg",
                        9: "py_cpy_wrap_pcc_9arg",
                    }.get(arity)
                if wrap_helper is not None:
                    target_fn = fn_ir
                    if adapter_entry is not None and adapter_entry.get("free_names"):
                        # Hoisted-captures adapter. ``_emit_hoist_adapter``
                        # already boxes non-ptr returns internally.
                        target_fn = self._emit_hoist_adapter(
                            expr.ident,
                            fn_ir,
                            adapter_entry,
                        )
                    elif not ret_ok:
                        # Standalone adapter for a value-position ref to
                        # a pcc FuncDef whose return is void / bool / int.
                        # Box the result via the appropriate py_* helper.
                        adapter_name = f"{fn_ir.name}_v2pyobj_{arity}"
                        existing_adapter = self.module.globals.get(adapter_name)
                        if isinstance(existing_adapter, ir.Function):
                            target_fn = existing_adapter
                        else:
                            adapter_fnty = ir.FunctionType(
                                _CSTR,
                                [_CSTR] * arity,
                            )
                            target_fn = ir.Function(
                                self.module,
                                adapter_fnty,
                                name=adapter_name,
                            )
                            target_fn.linkage = "internal"
                            ab = target_fn.append_basic_block("entry")
                            ab_b = ir.IRBuilder(ab)
                            if ret_void:
                                ab_b.call(fn_ir, list(target_fn.args))
                                py_none_gv = declare_runtime_global(
                                    self.module,
                                    "py_None",
                                )
                                ab_b.ret(ab_b.load(py_none_gv, name="none"))
                            elif ret_int_width == 1:
                                raw = ab_b.call(
                                    fn_ir,
                                    list(target_fn.args),
                                    name="raw",
                                )
                                bit = ab_b.zext(raw, _I32, name="b2i32")
                                boxed = ab_b.call(
                                    self.runtime["py_bool_from_bit"],
                                    [bit],
                                    name="boxed",
                                )
                                ab_b.ret(boxed)
                            else:
                                # ret_int_width == 64
                                raw = ab_b.call(
                                    fn_ir,
                                    list(target_fn.args),
                                    name="raw",
                                )
                                boxed = ab_b.call(
                                    self.runtime["py_int_from_i64"],
                                    [raw],
                                    name="boxed",
                                )
                                ab_b.ret(boxed)
                    fn_ptr = self.builder.bitcast(
                        target_fn,
                        _CSTR,
                        name=self._fresh(f"{expr.ident}.fnptr"),
                    )
                    result = self.builder.call(
                        self.runtime[wrap_helper],
                        [fn_ptr],
                        name=self._fresh(f"cpy.{expr.ident}"),
                    )
                    if not hasattr(self, "_cpy_values"):
                        self._cpy_values = set()
                    self._cpy_values.add(result)
                    return result
            msg = self._pooled_cstr_ptr(
                "name '" + expr.ident + "' is not defined",
                ".name_error",
            )
            exc = self.builder.call(
                self.runtime["py_exc_new"],
                [ir.Constant(_I64, 10), msg],
                name=self._fresh("name_error"),
            )
            self.builder.call(self.runtime["py_raise"], [exc])
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return self._emit_none_literal()
        alloca, ir_ty, _ = slot
        val = self.builder.load(alloca, name=self._fresh(expr.ident))
        # Re-tag as a CPython value when the binding was recorded as
        # one. Without this, downstream coercions see a bare DynType
        # and route through the pcc (non-CPython) unbox path.
        if getattr(self, "_cpy_env_flags", {}).get(expr.ident, False):
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(val)
        return val
