"""Unary and residual call helper lowering for L1CodeGen."""

from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    BoolType,
    Call,
    ClassType,
    ComplexType,
    DynType,
    Expr,
    FloatType,
    IntType,
    Name,
    SetType,
    SourceSpan,
    Subscript,
    Type,
    UnaryOp,
)
from . import marshal

_DOUBLE = ir.DoubleType()
_CPY_BUILTIN_TYPE_NAMES = frozenset(
    {
        "BaseException",
        "Exception",
        "bool",
        "bytearray",
        "bytes",
        "complex",
        "dict",
        "float",
        "frozenset",
        "int",
        "list",
        "object",
        "set",
        "str",
        "tuple",
        "type",
    }
)


class UnaryCallLoweringMixin:
    def _emit_unary(self, expr: UnaryOp) -> ir.Value:
        ty = expr.operand.ty
        if expr.op in ("-", "~") and isinstance(ty, ClassType):
            # CPython dispatches __neg__/__invert__; the numeric coercion
            # below crashed with "cannot coerce ClassType to int" (found on
            # numpy _pep440's `-Infinity`). Dispatch BEFORE emitting the
            # operand: the dunder helper emits the receiver itself.
            dunder_name = "__neg__" if expr.op == "-" else "__invert__"
            dunder = self._try_dispatch_dunder_unary(
                expr.operand,
                dunder_name,
                (),
            )
            if dunder is not None:
                return dunder
            raise NotImplementedError(
                f"Layer 1 cannot resolve {dunder_name} for unary "
                f"{expr.op!r} on a class instance"
            )
        operand = self._emit_expr(expr.operand)
        if expr.op == "+":
            return operand
        if expr.op == "-":
            if isinstance(ty, ComplexType):
                obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, operand, ty
                )
                return self.builder.call(
                    self.runtime["py_complex_neg"],
                    [obj],
                    name=self._fresh("complex.neg"),
                )
            if isinstance(ty, FloatType):
                zero = ir.Constant(_DOUBLE, 0.0)
                return self.builder.fsub(zero, operand, name=self._fresh("fneg"))
            if self._int_exprs_are_boxed() and isinstance(ty, (IntType, BoolType)):
                operand_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    operand,
                    ty,
                )
                return self.builder.call(
                    self.runtime["py_int_neg"],
                    [operand_obj],
                    name=self._fresh("int.obj.neg"),
                )
            ival = self._to_int64(operand, ty)
            return self.builder.neg(ival, name=self._fresh("neg"))
        if expr.op == "~":
            if self._int_exprs_are_boxed() and isinstance(ty, (IntType, BoolType)):
                operand_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    operand,
                    ty,
                )
                minus_one = self._emit_int_literal_object(-1)
                return self.builder.call(
                    self.runtime["py_int_xor"],
                    [operand_obj, minus_one],
                    name=self._fresh("int.obj.invert"),
                )
            ival = self._to_int64(operand, ty)
            return self.builder.not_(ival, name=self._fresh("bnot"))
        if expr.op == "not":
            b = self._truthy(operand, ty)
            return self.builder.not_(b, name=self._fresh("not"))
        raise NotImplementedError(f"Layer 1 unary {expr.op!r} not supported")

    # -- Compare -------------------------------------------------------

    # -- BoolExpr ------------------------------------------------------

    # -- Call ----------------------------------------------------------

    def _call_user(
        self,
        fn: ir.Function,
        args_ir: list[ir.Value],
        call_name: str,
        span: Optional[SourceSpan] = None,
        root_result: bool = False,
    ) -> ir.Value:
        """Call a user function; after the call, check py_err_occurred()
        and branch to the active error-propagation block if a Python
        exception is pending. When we're inside a try block that block
        is the except-dispatch (self._try_err_block); otherwise it is
        the enclosing function's err-exit epilogue.

        This replaces an earlier Itanium-ABI design that used `invoke`
        + landingpad to route exceptions via libc++abi. Return-code
        style (CPython ceval.c) is portable, debuggable, and keeps
        libc++abi out of the runtime link.
        """
        result = self.builder.call(fn, args_ir, name=call_name)
        root_slot = None
        root_ptr = None
        if (
            root_result
            and isinstance(result.type, ir.PointerType)
            and not getattr(self, "_suppress_implicit_gc_roots", False)
            and not (self.ast_module.name or "").startswith("pcc.py_runtime.py.")
        ):
            root_slot = self.builder.alloca(
                result.type,
                name=self._fresh("call.ret.root"),
            )
            self.builder.store(ir.Constant(result.type, None), root_slot)
            root_ptr = self._as_gc_ptr(
                root_slot,
                name=self._fresh("call.ret.root.ptr"),
            )
            self.builder.call(
                self.runtime["pcc_gc_store_root"],
                [root_ptr, result],
            )
            self._emit_current_gc_frame_enter_lifo(
                self._gc_one_slot_frame_map(),
                root_slot,
            )
        self._emit_post_call_err_check(span)
        if root_slot is not None and root_ptr is not None:
            result = self.builder.call(
                self.runtime["pcc_gc_load_ptr"],
                [ir.Constant(result.type, None), root_ptr],
                name=self._fresh("call.ret.current"),
            )
            self.builder.call(
                self.runtime["pcc_gc_store_root"],
                [root_ptr, ir.Constant(result.type, None)],
            )
            self._emit_gc_frame_leave_lifo_for_slot(root_slot)
        return result

    def _emit_arg_for_abi_param(
        self,
        ast_arg: Expr,
        target_ty: Type,
        param_ir_ty: ir.Type,
    ) -> ir.Value:
        self._last_call_arg_owned_temp = False
        if isinstance(target_ty, IntType):
            if isinstance(param_ir_ty, ir.PointerType):
                raw = self._emit_expr(ast_arg)
                if raw in getattr(self, "_cpy_values", ()):
                    self._last_call_arg_owned_temp = True
                    return self.builder.call(
                        self.runtime["py_cpy_to_pcc_obj"],
                        [raw],
                        name=self._fresh("cpy.arg.to_pcc"),
                    )
                return self._emit_exact_int_operand_object(ast_arg)
            if isinstance(param_ir_ty, ir.IntType) and param_ir_ty.width == 64:
                return self._emit_expr_as_i64(ast_arg)
        if isinstance(target_ty, BoolType):
            if isinstance(param_ir_ty, ir.IntType) and param_ir_ty.width == 1:
                raw = self._emit_expr(ast_arg)
                return self._truthy(raw, ast_arg.ty)
        if self._is_object(target_ty) and isinstance(ast_arg, Call):
            payload = self._maybe_emit_valueclass_constructor_payload(
                ast_arg.ty,
                ast_arg,
            )
            if payload is not None:
                boxed_valueclass = self._emit_valueclass_payload_to_object(
                    payload,
                    ast_arg.ty,
                    consume_fields=True,
                )
                if boxed_valueclass is not None:
                    self._last_call_arg_owned_temp = True
                    return boxed_valueclass
                return self._coerce(payload, ast_arg.ty, target_ty)
        if (
            isinstance(param_ir_ty, ir.PointerType)
            and isinstance(ast_arg, Name)
            and (
                self._name_returns_owned_function_value(ast_arg.ident)
                or self._name_returns_native_builtin_callable_value(ast_arg.ident)
            )
        ):
            v = self._emit_expr_with_native_callable_values(ast_arg)
            return self._coerce(v, ast_arg.ty, target_ty)
        v = self._emit_expr(ast_arg)
        if isinstance(param_ir_ty, ir.PointerType) and self._is_object(target_ty):
            source_ty = ast_arg.ty
            if isinstance(ast_arg, Name):
                env_entry = self.env.get(ast_arg.ident)
                if env_entry is not None and len(env_entry) >= 3:
                    source_ty = env_entry[2]
            boxed_valueclass = self._emit_valueclass_payload_to_object(v, source_ty)
            if boxed_valueclass is not None:
                return boxed_valueclass
        if v in getattr(self, "_cpy_values", ()) and isinstance(
            param_ir_ty,
            ir.PointerType,
        ):
            self._last_call_arg_owned_temp = True
            return self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"],
                [v],
                name=self._fresh("cpy.arg.to_pcc"),
            )
        return self._coerce(v, ast_arg.ty, target_ty)

    def _is_native_set_dyn(self, ty: Type) -> bool:
        return isinstance(ty, SetType)

    _DYN_LIST_METHOD_NATIVE = frozenset(
        {
            "append",
            "extend",
            "insert",
            "pop",
            "remove",
            "index",
            "sort",
            "clear",
        }
    )

    _DYN_DICT_METHOD_NATIVE = frozenset(
        {
            "get",
            "keys",
            "values",
            "items",
            "setdefault",
            "pop",
        }
    )

    _DYN_SET_METHOD_NATIVE = frozenset(
        {
            "add",
            "remove",
            "discard",
            "update",
        }
    )

    def _maybe_emit_builtin_type_method(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        """Handle ``int.__new__(cls, x)``-style builtin type dispatch.

        Without this guard, the generic "any class declaring the
        method" fallback can accidentally resolve ``int.__new__`` to a
        user class's own ``__new__`` in the same module.
        """
        attr = expr.func
        assert isinstance(attr, Attr)
        if not isinstance(attr.obj, Name):
            return None
        builtin_name = attr.obj.ident
        if builtin_name not in _CPY_BUILTIN_TYPE_NAMES:
            return None
        if attr.name == "__new__":
            ctor_args = expr.args
            if (
                ctor_args
                and isinstance(ctor_args[0], Name)
                and ctor_args[0].ident == "cls"
                and builtin_name == "object"
                and self.current_class is not None
            ):
                return self.class_lowering.emit_instantiate(
                    self.current_class.name,
                    (),
                    self,
                )
            if (
                ctor_args
                and isinstance(ctor_args[0], Name)
                and ctor_args[0].ident in ("cls", "self")
            ):
                ctor_args = ctor_args[1:]
            return self._emit_call(
                Call(
                    span=getattr(expr, "span", None),
                    ty=expr.ty,
                    func=Name(
                        span=getattr(attr.obj, "span", None),
                        ty=attr.obj.ty,
                        ident=builtin_name,
                    ),
                    args=ctor_args,
                    kwargs=expr.kwargs,
                )
            )
        if (
            builtin_name == "dict"
            and attr.name == "fromkeys"
            and 1 <= len(expr.args) <= 2
            and not expr.kwargs
        ):
            # Native dict.fromkeys(iterable[, value]) — avoid the libpython
            # fallback so it works under --python-libpython=off.
            iter_obj = self._emit_as_object(expr.args[0])
            if len(expr.args) == 2:
                val_obj = self._emit_as_object(expr.args[1])
            else:
                val_obj = self._emit_none_literal()
            return self.builder.call(
                self.runtime["py_dict_fromkeys"],
                [iter_obj, val_obj],
                name=self._fresh("dict.fromkeys"),
            )
        if (
            builtin_name == "int"
            and attr.name == "from_bytes"
            and len(expr.args) == 2
            and not expr.kwargs
        ):
            # Native int.from_bytes(bytes, byteorder) — unsigned form;
            # raises ValueError/TypeError per CPython, so emit the
            # post-call err check. signed= falls through (rejected
            # honestly under --python-libpython=off).
            bytes_obj = self._emit_as_object(expr.args[0])
            order_obj = self._emit_as_object(expr.args[1])
            result = self.builder.call(
                self.runtime["py_int_from_bytes"],
                [bytes_obj, order_obj],
                name=self._fresh("int.from_bytes"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if (
            builtin_name == "str"
            and attr.name == "maketrans"
            and len(expr.args) == 2
            and not expr.kwargs
        ):
            # Native str.maketrans(x, y) -> {ord(x[i]):ord(y[i])} (2-arg form);
            # avoids the libpython fallback. Raises ValueError on length
            # mismatch, so emit the post-call err check.
            x_obj = self._emit_as_object(expr.args[0])
            y_obj = self._emit_as_object(expr.args[1])
            result = self.builder.call(
                self.runtime["py_str_maketrans"],
                [x_obj, y_obj],
                name=self._fresh("str.maketrans"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if (
            builtin_name == "bytes"
            and attr.name == "maketrans"
            and len(expr.args) == 2
            and not expr.kwargs
        ):
            x_obj = self._emit_as_object(expr.args[0])
            y_obj = self._emit_as_object(expr.args[1])
            result = self.builder.call(
                self.runtime["py_bytes_maketrans"],
                [x_obj, y_obj],
                name=self._fresh("bytes.maketrans"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if (
            builtin_name == "bytes"
            and attr.name == "fromhex"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            text_obj = self._emit_as_object(expr.args[0])
            result = self.builder.call(
                self.runtime["py_bytes_fromhex"],
                [text_obj],
                name=self._fresh("bytes.fromhex"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if (
            builtin_name == "float"
            and attr.name == "fromhex"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            # Native float.fromhex(str) — parses a hexadecimal floating-point
            # string exactly like CPython (see py_float_fromhex.c). Avoids the
            # libpython fallback so it works under --python-libpython=off.
            # Raises ValueError / OverflowError, so emit the post-call err
            # check and branch to the error path.
            text_obj = self._emit_as_object(expr.args[0])
            result = self.builder.call(
                self.runtime["py_float_fromhex"],
                [text_obj],
                name=self._fresh("float.fromhex"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if (
            builtin_name == "bytes"
            and attr.name == "translate"
            and len(expr.args) == 2
            and not expr.kwargs
        ):
            src_obj = self._emit_as_object(expr.args[0])
            table_obj = self._emit_as_object(expr.args[1])
            result = self.builder.call(
                self.runtime["py_bytes_translate"],
                [src_obj, table_obj],
                name=self._fresh("bytes.translate"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        fn_val = self._load_cpython_builtin(builtin_name)
        return self._emit_cpy_method_call_src(
            fn_val,
            attr.name,
            expr.args,
            kwargs=expr.kwargs,
        )

    def _try_dispatch_dunder_unary(
        self,
        host_expr: "Expr",
        dunder_name: str,
        arg_exprs: tuple["Expr", ...],
    ) -> Optional[ir.Value]:
        """If ``host_expr`` is a Name bound to a hinted class that
        defines ``dunder_name`` (via MRO), emit the direct method call
        with ``arg_exprs`` and return the result. Otherwise return None.
        """
        if isinstance(host_expr, Subscript):
            host = host_expr.obj
        else:
            host = host_expr
        hint = None
        receiver_expr = host
        if isinstance(host, Name):
            hint = self.env_class_hint.get(host.ident)
            if hint is None and host.ident in ("self", "cls"):
                current_class = getattr(self, "current_class", None)
                if current_class is not None:
                    hint = current_class.name
        if hint is None:
            hint = self._class_hint_for_expr(host_expr)
            receiver_expr = host_expr
        if hint is None:
            return None
        info = self._resolve_method_mro(hint, dunder_name)
        if info is None:
            return None
        obj_val = self._emit_expr(receiver_expr)
        method_fn = info.methods[dunder_name]
        return self._emit_direct_method_call(
            method_fn,
            obj_val,
            info,
            dunder_name,
            arg_exprs,
        )
