"""Direct/static Python method call lowering for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import DynType, Expr, Type
from . import marshal
from .errors import L1CodegenError


class MethodCallLoweringMixin:
    def _emit_data_descriptor_get(
        self,
        class_name: str,
        attr_name: str,
        obj_val: ir.Value,
    ):
        desc = self._class_attr_descriptor_class(class_name, attr_name)
        if desc is None:
            return None
        owner_info, desc_info = desc
        if "__get__" not in desc_info.methods:
            return None
        if "__set__" not in desc_info.methods and "__delete__" not in desc_info.methods:
            return None
        attr_entry = owner_info.class_attrs.get(attr_name)
        if attr_entry is None:
            return None
        desc_obj = self.builder.load(
            attr_entry[0],
            name=self._fresh(f"descr.{attr_name}"),
        )
        owner_cls = self.builder.load(
            owner_info.global_var,
            name=self._fresh(f"descr.owner.{owner_info.name}"),
        )
        return self._call_user(
            desc_info.methods["__get__"],
            [desc_obj, obj_val, owner_cls],
            self._fresh(f"descr.get.{attr_name}"),
            None,
        )

    def _emit_data_descriptor_set(
        self,
        class_name: str,
        attr_name: str,
        obj_val: ir.Value,
        value_obj: ir.Value,
    ) -> bool:
        desc = self._class_attr_descriptor_class(class_name, attr_name)
        if desc is None:
            return False
        owner_info, desc_info = desc
        setter = desc_info.methods.get("__set__")
        if setter is None:
            return False
        attr_entry = owner_info.class_attrs.get(attr_name)
        if attr_entry is None:
            return False
        desc_obj = self.builder.load(
            attr_entry[0],
            name=self._fresh(f"descr.{attr_name}"),
        )
        self._call_user(
            setter,
            [desc_obj, obj_val, value_obj],
            "",
            None,
        )
        return True

    def _emit_static_method_call(
        self,
        method_fn: ir.Function,
        info,
        method_name: str,
        arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        """Lower ``ClassName.staticmethod(args)`` without any receiver
        and with argument coercion honouring declared annotations."""
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        # Always resolve kwargs + fill defaults so trailing params
        # with defaults land even when the call omitted them.
        if ast_fd is not None:
            try:
                arg_exprs = tuple(
                    self._resolve_call_kwargs(
                        arg_exprs,
                        kwargs,
                        ast_fd.args,
                    )
                )
            except L1CodegenError as e:
                raise L1CodegenError(
                    f"staticmethod {info.name}.{method_name}: {e}"
                ) from e
        elif kwargs:
            raise NotImplementedError(
                f"staticmethod {info.name}.{method_name} with kwargs "
                "needs a FuncDef to resolve parameter names"
            )
        declared = ast_fd.args if ast_fd else ()
        args_ir: list[ir.Value] = []
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    v,
                    arg_expr.ty,
                )
            args_ir.append(v)
        ret_ty = method_fn.function_type.return_type
        call_name = (
            ""
            if isinstance(ret_ty, ir.VoidType)
            else self._fresh(f"{info.name}.{method_name}.ret")
        )
        return self._call_user(method_fn, args_ir, call_name)

    def _emit_direct_method_call(
        self,
        method_fn: ir.Function,
        self_val: ir.Value,
        info,
        method_name: str,
        arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        args_ir: list[ir.Value] = [self_val]
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        # Always resolve positional → full arg list so defaults land on
        # omitted trailing params, not just when kwargs were supplied.
        # The earlier ``if kwargs:`` gate let calls like
        # ``self._mark(action, node)`` — where ``_mark(self, action,
        # node, detail="")`` has a default — slip through with only
        # 2 SSA operands, then clang rejected the resulting call as
        # ``not enough parameters specified``.
        if ast_fd is not None:
            try:
                arg_exprs = tuple(
                    self._resolve_call_kwargs(
                        arg_exprs,
                        kwargs,
                        ast_fd.args,
                        skip_self=True,
                    )
                )
            except L1CodegenError as e:
                span = None
                if arg_exprs:
                    span = getattr(arg_exprs[0], "span", None)
                where = (
                    f" at {span.file}:{span.line}:{span.col}"
                    if span is not None
                    else ""
                )
                raise L1CodegenError(f"{info.name}.{method_name}{where}: {e}") from e
        elif kwargs:
            raise NotImplementedError(
                f"method {info.name}.{method_name} with kwargs needs a "
                "FuncDef to resolve parameter names"
            )
        # Skip the receiver (``self`` / ``cls``) and the bare ``*``
        # kw-only separator when zipping against ``arg_exprs`` (which
        # ``_resolve_call_kwargs`` has already filtered).
        declared = [a for a in ast_fd.args[1:] if a.name != ""] if ast_fd else []
        if ast_fd is not None and ast_fd.is_async:
            values: list[tuple[ir.Value, Type]] = [
                (self_val, DynType(name="dyn")),
            ]
            for arg_expr in arg_exprs:
                values.append((self._emit_expr(arg_expr), arg_expr.ty))
            args_tuple = self._emit_object_tuple_from_values(
                tuple(values),
                name=f"{info.name}.{method_name}.async.args",
            )
            original_args = tuple(a for a in ast_fd.args if a.name != "")
            adapter = self._emit_native_func_adapter(
                f"{info.name}_{method_name}_async",
                method_fn,
                original_args,
                (),
                ast_fd.return_ty,
            )
            return self._emit_coroutine_from_adapter(
                f"{info.name}.{method_name}",
                adapter,
                args_tuple,
            )
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    v,
                    arg_expr.ty,
                )
            args_ir.append(v)
        ret_ty = method_fn.function_type.return_type
        call_name = (
            ""
            if isinstance(ret_ty, ir.VoidType)
            else self._fresh(f"{info.name}.{method_name}.ret")
        )
        return self._call_user(method_fn, args_ir, call_name)

    def _emit_static_method_ptr_call(
        self,
        method_ptr: ir.Value,
        method_fn: ir.Function,
        info,
        method_name: str,
        arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        if ast_fd is not None:
            arg_exprs = tuple(
                self._resolve_call_kwargs(
                    arg_exprs,
                    kwargs,
                    ast_fd.args,
                )
            )
        elif kwargs:
            raise NotImplementedError(
                f"staticmethod {info.name}.{method_name} with kwargs "
                "needs a FuncDef to resolve parameter names"
            )
        declared = ast_fd.args if ast_fd else ()
        args_ir: list[ir.Value] = []
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    v,
                    arg_expr.ty,
                )
            args_ir.append(v)
        callee = self.builder.bitcast(
            method_ptr,
            method_fn.type,
            name=self._fresh(f"{info.name}.{method_name}.super.fn"),
        )
        ret_ty = method_fn.function_type.return_type
        call_name = (
            ""
            if isinstance(ret_ty, ir.VoidType)
            else self._fresh(f"{info.name}.{method_name}.ret")
        )
        return self._call_user(callee, args_ir, call_name)

    def _emit_direct_method_ptr_call(
        self,
        method_ptr: ir.Value,
        method_fn: ir.Function,
        self_val: ir.Value,
        info,
        method_name: str,
        arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        args_ir: list[ir.Value] = [self_val]
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        if ast_fd is not None:
            arg_exprs = tuple(
                self._resolve_call_kwargs(
                    arg_exprs,
                    kwargs,
                    ast_fd.args,
                    skip_self=True,
                )
            )
        elif kwargs:
            raise NotImplementedError(
                f"method {info.name}.{method_name} with kwargs needs a "
                "FuncDef to resolve parameter names"
            )
        declared = [a for a in ast_fd.args[1:] if a.name != ""] if ast_fd else []
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    v,
                    arg_expr.ty,
                )
            args_ir.append(v)
        callee = self.builder.bitcast(
            method_ptr,
            method_fn.type,
            name=self._fresh(f"{info.name}.{method_name}.super.fn"),
        )
        ret_ty = method_fn.function_type.return_type
        call_name = (
            ""
            if isinstance(ret_ty, ir.VoidType)
            else self._fresh(f"{info.name}.{method_name}.ret")
        )
        return self._call_user(callee, args_ir, call_name)

    def _emit_direct_method_value_call(
        self,
        method_fn: ir.Function,
        self_val: ir.Value,
        info,
        method_name: str,
        arg_values: tuple[tuple[ir.Value, Type], ...],
    ) -> ir.Value:
        args_ir: list[ir.Value] = [self_val]
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        declared = [a for a in ast_fd.args[1:] if a.name != ""] if ast_fd else []
        if ast_fd is not None and ast_fd.is_async:
            values = ((self_val, DynType(name="dyn")),) + arg_values
            args_tuple = self._emit_object_tuple_from_values(
                values,
                name=f"{info.name}.{method_name}.async.args",
            )
            original_args = tuple(a for a in ast_fd.args if a.name != "")
            adapter = self._emit_native_func_adapter(
                f"{info.name}_{method_name}_async",
                method_fn,
                original_args,
                (),
                ast_fd.return_ty,
            )
            return self._emit_coroutine_from_adapter(
                f"{info.name}.{method_name}",
                adapter,
                args_tuple,
            )
        for i, (v, v_ty) in enumerate(arg_values):
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, v_ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    v,
                    v_ty,
                )
            args_ir.append(v)
        ret_ty = method_fn.function_type.return_type
        call_name = (
            ""
            if isinstance(ret_ty, ir.VoidType)
            else self._fresh(f"{info.name}.{method_name}.ret")
        )
        return self._call_user(method_fn, args_ir, call_name)
