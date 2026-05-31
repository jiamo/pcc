"""pcc.extern scaffold lowering helpers for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import Assign, BoolLit, Call, Name, StrLit, TupleExpr


_VOID = ir.VoidType()
_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
_CSTR = _I8.as_pointer()


class ExternScaffoldMixin:
    _EXTERN_CTYPE_IR = {
        "c_void": _VOID,
        "c_bool": _I1,
        "c_int8": ir.IntType(8),
        "c_int16": ir.IntType(16),
        "c_int32": _I32,
        "c_int": _I32,
        "c_int64": _I64,
        "c_long": _I64,
        "c_uint8": ir.IntType(8),
        "c_uint16": ir.IntType(16),
        "c_uint32": _I32,
        "c_uint64": _I64,
        "c_size_t": _I64,
        "c_float": ir.FloatType(),
        "c_double": _DOUBLE,
        "c_ptr": _CSTR,  # opaque i8*
        "c_str": _CSTR,
    }

    def _maybe_register_extern_assign(self, stmt: "Assign") -> bool:
        """If the RHS is a call to the imported ``extern`` factory,
        record the decl and suppress runtime emission. Returns True if
        handled."""
        bindings = getattr(self, "_extern_bindings", {})
        if not bindings:
            return False
        value = stmt.value
        # ``LLVMContextRef = c_ptr`` / ``c_int_alias = c_int`` —
        # module-level alias of an extern-imported type marker. pcc
        # doesn't materialise the marker at runtime, so treat the
        # assignment as a no-op. Also register the alias so later
        # ``LLVMContextRef`` references (e.g. in extern(...) decls)
        # resolve back to the same type marker.
        if (
            isinstance(value, Name)
            and value.ident in bindings
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], Name)
        ):
            bindings[stmt.targets[0].ident] = bindings[value.ident]
            return True
        if not isinstance(value, Call) or not isinstance(value.func, Name):
            return False
        if bindings.get(value.func.ident) != "extern":
            return False
        if not value.args:
            return False
        symbol_expr = value.args[0]
        if not isinstance(symbol_expr, StrLit):
            return False
        symbol = symbol_expr.value
        # Parse argtypes tuple and restype from kwargs or positional.
        argtype_exprs: tuple = ()
        restype_name: str = "c_void"
        variadic = False
        for k, kv in value.kwargs:
            if k == "argtypes" and isinstance(kv, TupleExpr):
                argtype_exprs = kv.elems
            elif k == "restype" and isinstance(kv, Name):
                restype_name = kv.ident
            elif k == "variadic" and isinstance(kv, BoolLit):
                variadic = kv.value
        if not argtype_exprs and len(value.args) >= 2:
            a = value.args[1]
            if isinstance(a, TupleExpr):
                argtype_exprs = a.elems
        if restype_name == "c_void" and len(value.args) >= 3:
            rt = value.args[2]
            if isinstance(rt, Name):
                restype_name = rt.ident
        argtype_names: list[str] = []
        for ae in argtype_exprs:
            if not isinstance(ae, Name):
                return False
            argtype_names.append(ae.ident)
        for target in stmt.targets:
            if not isinstance(target, Name):
                continue
            if not hasattr(self, "_extern_decls"):
                self._extern_decls: dict[str, tuple[str, list[str], str, bool]] = {}
            self._extern_decls[target.ident] = (
                symbol,
                argtype_names,
                restype_name,
                variadic,
            )
        return True

    def _emit_extern_call(
        self,
        decl: tuple[str, list[str], str, bool],
        args: tuple,
    ) -> ir.Value:
        symbol, argtype_names, restype_name, variadic = decl
        # Build / get the declared function.
        param_tys = [self._EXTERN_CTYPE_IR[n] for n in argtype_names]
        ret_ty = self._EXTERN_CTYPE_IR[restype_name]
        fnty = ir.FunctionType(ret_ty, param_tys, var_arg=variadic)
        fn = self.module.globals.get(symbol)
        if not isinstance(fn, ir.Function):
            fn = ir.Function(self.module, fnty, name=symbol)
            fn.linkage = "external"

        # Marshal each actual arg to the declared IR type. A bare
        # function ``Name`` passed to a ``c_ptr`` extern slot must be
        # lowered as the raw pcc function pointer — not wrapped via
        # ``py_cpy_wrap_pcc_<N>arg`` (which would leak a libpython
        # dependency into the no-libpython runtime archive) and not
        # boxed into a pcc ``PyFunc`` object (which the callee will
        # treat as an opaque pointer and dereference as a fn-ptr).
        ir_args: list[ir.Value] = []
        for i, a in enumerate(args):
            ctype = argtype_names[i] if i < len(argtype_names) else None
            v: ir.Value
            if ctype == "c_ptr" and isinstance(a, Name):
                fn_ir = self.functions.get(a.ident)
                if fn_ir is not None:
                    v = self.builder.bitcast(
                        fn_ir,
                        _CSTR,
                        name=self._fresh(f"extern.{a.ident}.fnptr"),
                    )
                    ir_args.append(v)
                    continue
            v = self._emit_expr(a)
            if i < len(argtype_names):
                want = self._EXTERN_CTYPE_IR[argtype_names[i]]
                v = self._coerce_to_extern(v, a.ty, want, argtype_names[i])
            ir_args.append(v)
        call_name = (
            ""
            if isinstance(ret_ty, ir.VoidType)
            else self._fresh(f"extern.{symbol}.ret")
        )
        return self.builder.call(fn, ir_args, name=call_name)

    def _coerce_to_extern(
        self,
        v: ir.Value,
        ty: "Type",
        want: ir.Type,
        ctype_name: str,
    ) -> ir.Value:
        """Narrow bridge between pcc-native scalar types and the
        extern declaration's IR type. Handles int→i32/i64 truncate+
        sext, pcc str → i8*, bool zext."""
        if isinstance(want, ir.VoidType):
            return v
        if ctype_name in {"c_str", "c_ptr"}:
            # pcc str is already i8* (points to PyStrObject); for the
            # narrow P6C.1 case we want the underlying C string, not
            # the PyStrObject. This requires a runtime helper — for
            # now pass through and document the sharp edge.
            return v
        if isinstance(want, ir.IntType):
            i64 = self._to_int64(v, ty)
            if want.width == 64:
                return i64
            if want.width < 64:
                return self.builder.trunc(
                    i64,
                    want,
                    name=self._fresh(f"extern.trunc{want.width}"),
                )
            return self.builder.sext(
                i64,
                want,
                name=self._fresh(f"extern.sext{want.width}"),
            )
        if isinstance(want, ir.DoubleType):
            return self._to_double(v, ty)
        return v
