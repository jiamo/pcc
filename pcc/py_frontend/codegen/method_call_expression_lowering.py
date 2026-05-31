"""Method-call expression lowering for L1CodeGen."""

from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    BinOp,
    BoolType,
    ByteArrayType,
    BytesType,
    Call,
    ClassType,
    DictType,
    DynType,
    Expr,
    FloatType,
    IntType,
    ListType,
    MemoryViewType,
    Name,
    NoneType,
    StrLit,
    StrType,
    Subscript,
    TupleType,
    Type,
)
from . import marshal
from .errors import L1CodegenError

_I1 = ir.IntType(1)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()
_PY_EXC_RUNTIMEERROR = 7
_DYN_LIST_METHOD_NATIVE = frozenset(
    {
        "append",
        "extend",
        "insert",
        "pop",
        "remove",
        "index",
        "count",
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
_STR_METHOD_NATIVE = frozenset(
    {
        "upper",
        "lower",
        "strip",
        "lstrip",
        "rstrip",
        "split",
        "join",
        "replace",
        "find",
        "count",
        "encode",
        "startswith",
        "endswith",
        "splitlines",
        "isdigit",
        "isalpha",
        "isspace",
        "isalnum",
    }
)


def _same_type_kind(a: Type, b: Type) -> bool:
    return type(a) is type(b)


def _method_node_kind(node) -> str:
    if node is None:
        return ""
    return type(node).__name__


def _method_is_name(node) -> bool:
    return isinstance(node, Name) or _method_node_kind(node) == "Name"


def _method_ident(node) -> Optional[str]:
    try:
        ident = node.ident
    except AttributeError:
        return None
    if ident is None:
        return None
    return str(ident)


def _method_first_arg_name(class_lowering, class_name: str, method_name: str) -> str:
    fd = class_lowering._find_method_def(class_name, method_name)
    if fd is None:
        return ""
    try:
        args = fd.args
    except AttributeError:
        return ""
    if not args:
        return ""
    try:
        return str(args[0].name)
    except AttributeError:
        return ""


class MethodCallExpressionLoweringMixin:
    def _emit_callable_attribute_call(
        self,
        obj_expr: Expr,
        attr_name: str,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
        span,
    ) -> ir.Value:
        obj_val = self._emit_expr(obj_expr)
        name_ptr = self._attr_name_ptr(attr_name)
        callable_obj = self.builder.call(
            self.runtime["py_obj_getattr"],
            [obj_val, name_ptr],
            name=self._fresh(f"callable.attr.{attr_name}"),
        )
        kwdict_unpack = self._split_starstar_kwargs_unpack(args)
        arg_exprs = args
        kwargs_expr = None
        if kwdict_unpack is not None:
            arg_exprs, kwargs_expr = kwdict_unpack
        args_owned = not self._is_starred_unpack(arg_exprs)
        args_tuple = self._emit_dynamic_call_args_tuple(arg_exprs)
        kwargs_obj = self._emit_dynamic_call_kwargs_object(
            kwargs,
            kwargs_expr,
            span,
        )
        result = self.builder.call(
            self.runtime["py_obj_call"],
            [callable_obj, args_tuple, kwargs_obj],
            name=self._fresh(f"callable.attr.{attr_name}.call"),
        )
        if args_owned:
            self._gc_release(args_tuple)
        if kwargs:
            self._gc_release(kwargs_obj)
        self._gc_release(callable_obj)
        self._emit_post_call_err_check(span)
        return result

    def _emit_method_call(self, expr: Call) -> ir.Value:
        """Lower ``obj.method(args)`` using the class method registry.

        Fast path: if ``obj`` is a Name bound in the local env to a
        ``DynType`` instance of a known class in the current module,
        and ``method`` is a direct member of that class (no MRO
        walking), dispatch to the declared pcc method function. The
        generic ``py_obj_call_method`` path is used otherwise.
        """
        attr = expr.func
        assert isinstance(attr, Attr)

        if _method_is_name(attr.obj):
            module_name = self._native_builtin_module_for_name(_method_ident(attr.obj))
            if module_name == "warnings" and attr.name in (
                "warn",
                "filterwarnings",
                "simplefilter",
            ):
                return self._emit_none_literal()

        if (
            attr.name == "__call__"
            and _method_is_name(attr.obj)
            and _method_ident(attr.obj) == "type"
            and len(expr.args) >= 1
        ):
            callable_obj = self._emit_as_object(expr.args[0])
            call_args = expr.args[1:]
            kwdict_unpack = self._split_starstar_kwargs_unpack(call_args)
            arg_exprs = call_args
            kwargs_expr = None
            if kwdict_unpack is not None:
                arg_exprs, kwargs_expr = kwdict_unpack
            args_owned = not self._is_starred_unpack(arg_exprs)
            args_tuple = self._emit_dynamic_call_args_tuple(arg_exprs)
            kwargs_obj = self._emit_dynamic_call_kwargs_object(
                expr.kwargs,
                kwargs_expr,
                self._expr_span_or_none(expr),
            )
            result = self.builder.call(
                self.runtime["py_obj_call"],
                [callable_obj, args_tuple, kwargs_obj],
                name=self._fresh("type.call"),
            )
            if args_owned:
                self._gc_release(args_tuple)
            if expr.kwargs:
                self._gc_release(kwargs_obj)
            self._emit_post_call_err_check(self._expr_span_or_none(expr))
            return result

        if (
            attr.name == "__new__"
            and _method_is_name(attr.obj)
            and _method_ident(attr.obj) == "type"
        ):
            if len(expr.args) >= 4:
                name_obj = self._emit_as_object(expr.args[1])
                bases_obj = self._emit_as_object(expr.args[2])
                ns_obj = self._emit_as_object(expr.args[3])
                result = self.builder.call(
                    self.runtime["py_class_new_from_objects"],
                    [name_obj, bases_obj, ns_obj],
                    name=self._fresh("type.new"),
                )
                self._emit_post_call_err_check(self._expr_span_or_none(expr))
                return result
            return self._emit_none_literal()
        if (
            attr.name == "__getattribute__"
            and _method_is_name(attr.obj)
            and _method_ident(attr.obj) == "object"
            and len(expr.args) == 2
            and not expr.kwargs
        ):
            obj_val = self._emit_as_object(expr.args[0])
            name_ptr = self._emit_attr_name_ptr_arg(
                expr.args[1],
                "object.getattribute.name",
            )
            return self.builder.call(
                self.runtime["py_obj_getattr_default"],
                [obj_val, name_ptr],
                name=self._fresh("object.getattribute"),
            )

        # Path A scaffold dispatch (Issue 1). ON mode routes
        # ``self.builder.X(...)`` and similar IRBuilder method calls
        # through the closed-world lowering instead of py_cpy_* dynamic
        # dispatch. Method coverage is added incrementally; anything
        # not yet implemented raises ScaffoldUnsupportedError with a
        # clear message rather than silently falling back.
        if self._ir_scaffold_enabled():
            scaffold_value = self._maybe_emit_ir_scaffold_call(expr)
            if scaffold_value is not None:
                return scaffold_value

        # Typed-container method dispatch on pcc-native containers —
        # stays on pcc runtime so the produced binary has no libpython
        # dep. Only a curated method set is recognised; anything else
        # surfaces as NotImplementedError rather than falling through
        # to the generic CPython helper.
        obj_ty0 = attr.obj.ty
        if isinstance(obj_ty0, ListType):
            native = self._maybe_emit_list_method(expr, obj_ty0)
            if native is not None:
                return native
        if isinstance(obj_ty0, TupleType):
            native = self._maybe_emit_tuple_method(expr)
            if native is not None:
                return native
        if isinstance(obj_ty0, DictType):
            native = self._maybe_emit_dict_method(expr, obj_ty0)
            if native is not None:
                return native
        if self._is_native_set_dyn(obj_ty0):
            native = self._maybe_emit_set_method(expr)
            if native is not None:
                return native
        if isinstance(obj_ty0, StrType):
            native = self._maybe_emit_str_method(expr)
            if native is not None:
                return native
        if (
            isinstance(obj_ty0, DynType)
            and obj_ty0.name == "coroutine"
            and attr.name == "close"
            and not expr.args
            and not expr.kwargs
        ):
            return self.builder.call(
                self.runtime["py_coroutine_close"],
                [self._emit_as_object(attr.obj)],
                name=self._fresh("coroutine.close"),
            )
        native_threading_method = self._maybe_emit_threading_instance_method(expr)
        if native_threading_method is not None:
            return native_threading_method
        builtin_type_call = self._maybe_emit_builtin_type_method(expr)
        if builtin_type_call is not None:
            return builtin_type_call
        native_inspect_call = self._maybe_emit_native_inspect_call(expr)
        if native_inspect_call is not None:
            return native_inspect_call
        native_module_alias_call = self._maybe_emit_native_module_alias_call(expr)
        if native_module_alias_call is not None:
            return native_module_alias_call
        native_os_call = self._emit_native_os_call(expr)
        if native_os_call is not None:
            return native_os_call
        native_platform_call = self._emit_native_platform_call(expr)
        if native_platform_call is not None:
            return native_platform_call
        native_builtin_module_call = self._emit_native_builtin_module_call(expr)
        if native_builtin_module_call is not None:
            return native_builtin_module_call
        native_math_call = self._emit_native_math_call(expr)
        if native_math_call is not None:
            return native_math_call
        native_json_call = self._emit_native_json_call(expr)
        if native_json_call is not None:
            return native_json_call
        native_re_call = self._emit_native_re_call(expr)
        if native_re_call is not None:
            return native_re_call
        native_gc_callbacks_method = self._emit_native_gc_callbacks_method(expr)
        if native_gc_callbacks_method is not None:
            return native_gc_callbacks_method
        native_gc_call = self._emit_native_gc_call(expr)
        if native_gc_call is not None:
            return native_gc_call
        native_weakref_call = self._emit_native_weakref_call(expr)
        if native_weakref_call is not None:
            return native_weakref_call
        native_asyncio_call = self._emit_native_asyncio_call(expr)
        if native_asyncio_call is not None:
            return native_asyncio_call
        native_threading_call = self._emit_native_threading_call(expr)
        if native_threading_call is not None:
            return native_threading_call
        native_virtual_thread_call = self._emit_native_virtual_thread_call(expr)
        if native_virtual_thread_call is not None:
            return native_virtual_thread_call
        native_subprocess_call = self._emit_native_subprocess_call(expr)
        if native_subprocess_call is not None:
            return native_subprocess_call
        native_shutil_call = self._emit_native_shutil_call(expr)
        if native_shutil_call is not None:
            return native_shutil_call
        native_shlex_call = self._emit_native_shlex_call(expr)
        if native_shlex_call is not None:
            return native_shlex_call
        native_sysconfig_call = self._emit_native_sysconfig_call(expr)
        if native_sysconfig_call is not None:
            return native_sysconfig_call
        native_os_environ_call = self._emit_native_os_environ_call(expr)
        if native_os_environ_call is not None:
            return native_os_environ_call
        native_sys_stream_call = self._emit_native_sys_stream_call(expr)
        if native_sys_stream_call is not None:
            return native_sys_stream_call
        native_file_method = self._emit_native_file_method(expr)
        if native_file_method is not None:
            return native_file_method
        native_fileinput_ctor = self._emit_native_fileinput_call(expr)
        if native_fileinput_ctor is not None:
            return native_fileinput_ctor
        native_fileinput_method = self._emit_native_fileinput_method(expr)
        if native_fileinput_method is not None:
            return native_fileinput_method
        native_sys_exit_call = self._emit_native_sys_exit_call(expr)
        if native_sys_exit_call is not None:
            return native_sys_exit_call
        native_os_path_call = self._emit_native_os_path_call(expr)
        if native_os_path_call is not None:
            return native_os_path_call
        if isinstance(attr.obj, Name):
            builtin_module = self._native_builtin_module_for_name(attr.obj.ident)
            if (
                builtin_module == "builtins"
                and attr.name == "int"
                and 1 <= len(expr.args) <= 2
            ):
                result = self._maybe_emit_int_builtin(expr)
                if result is not None:
                    return result
            if (
                builtin_module == "builtins"
                and attr.name == "str"
                and len(expr.args) == 1
                and not expr.kwargs
            ):
                return self._emit_str_builtin(expr)
            builtin_value = self._native_builtin_value_for_name(attr.obj.ident)
            if builtin_value == "os.path":
                return self._emit_cpy_method_call_src(
                    self._emit_cpy_attr(
                        self._emit_cpython_module_value("os"),
                        "path",
                    ),
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )

        # Case -1: ``<CPython value>.method(args)``.
        #
        # Chained access (``os.path.join``) lowers the inner attr chain
        # through ``_emit_attr``, which already routes through
        # ``py_cpy_getattr`` whenever the root is an imported module or
        # a CPython-flagged local. If the resulting SSA value lands in
        # ``_cpy_values``, dispatch the method call through libpython.
        if isinstance(attr.obj, Name):
            builtin_module = self._native_builtin_module_for_name(attr.obj.ident)
            if builtin_module is not None:
                if (
                    builtin_module == "builtins"
                    and attr.name == "int"
                    and 1 <= len(expr.args) <= 2
                ):
                    result = self._maybe_emit_int_builtin(expr)
                    if result is not None:
                        return result
                return self._emit_cpy_method_call_src(
                    self._emit_cpython_module_value(builtin_module),
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(attr.obj.ident)
            if cpy_gv is not None:
                return self._emit_cpy_method_call_src(
                    self.builder.load(cpy_gv, name=self._fresh("cpy.mod")),
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )
            if getattr(self, "_cpy_env_flags", {}).get(attr.obj.ident, False):
                if attr.name in _STR_METHOD_NATIVE:
                    native = self._maybe_emit_str_method_via_dyn(expr)
                    if native is not None:
                        return native
                return self._emit_cpy_method_call_src(
                    self._emit_expr(attr.obj),
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )
        if isinstance(attr.obj, Attr):
            # Evaluate the chain eagerly; if the result was tagged as a
            # CPython value (e.g. ``os.path``), dispatch there.
            chain_val = self._emit_expr(attr.obj)
            if chain_val in getattr(self, "_cpy_values", ()):
                return self._emit_cpy_method_call_src(
                    chain_val,
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )
        if isinstance(attr.obj, Call):
            # A method call on a CPython-call RESULT (e.g.
            # ``numpy.arange(10).sum()``): the result is a real CPython object,
            # so the method must dispatch through libpython. The bare-``Attr``
            # branch above does not fire because the receiver is a ``Call``, and
            # the native fall-through below would use ``py_obj_getattr`` (pcc's
            # object model) on the real CPython object -> AttributeError. Detect
            # the cpy call STRUCTURALLY (callable is a CPython-module attribute)
            # so a non-matching ``Call`` receiver is not pre-emitted (avoids
            # double-evaluating/calling it in the native path); the cpy path
            # emits the receiver exactly once. Inert in no-libpython mode (no
            # CPython modules => never matches => bootstrap unaffected).
            cfunc = attr.obj.func
            if (
                isinstance(cfunc, Attr)
                and isinstance(cfunc.obj, Name)
                and (
                    cfunc.obj.ident in getattr(self, "_cpy_module_env", {})
                    or getattr(self, "_cpy_env_flags", {}).get(
                        cfunc.obj.ident, False
                    )
                )
            ):
                return self._emit_cpy_method_call_src(
                    self._emit_expr(attr.obj),
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )
        if isinstance(attr.obj, (BinOp, Subscript, Call)) and self._expr_looks_cpython(
            attr.obj
        ):
            # A method call on a BINARY-OP, SUBSCRIPT, or (deep-chain) CALL result
            # that is itself a CPython value (e.g. ``(a + b).sum()``,
            # ``a[1:4].sum()``, or ``np.arange(4).reshape(2,2).sum()`` on numpy):
            # the binop (``py_cpy_binop``) / subscript / chained call yields a real
            # CPython object, so the method must dispatch through libpython. Detect
            # STRUCTURALLY (the receiver is cpy via ``_expr_looks_cpython``, which
            # recurses through BinOp operands, Subscript objects, and Call funcs)
            # so a non-cpy receiver is not pre-emitted; the cpy path emits the
            # receiver exactly once. The narrow Call-receiver branch above is a
            # fast path for the simple ``module.fn(...).method()`` shape and fires
            # first; this generalised branch additionally catches DEEPER chains
            # whose inner callable is itself a Call (where ``cfunc.obj`` is not a
            # Name). Inert in no-libpython (no CPython modules => never looks cpy
            # => bootstrap unaffected). Without this the native fall-through would
            # run ``py_obj_getattr`` (pcc's object model) on the real CPython
            # object -> AttributeError; the stored form ``c = a[1:4]; c.sum()``
            # already worked via the assignment's cpy-value tagging.
            return self._emit_cpy_method_call_src(
                self._emit_expr(attr.obj),
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
            )

        # Generator / coroutine intrinsics. The ``send``/``throw``/``close``
        # method names are not unique to generators — user classes (e.g.
        # ``io.StringIO.close``) define their own. Only short-circuit
        # when the receiver type is unknown enough (``DynType``) that
        # we can safely assume the caller meant the generator intrinsic.
        # When the receiver has a concrete ClassType / typed-container
        # type, dispatch falls through to the user-defined method.
        gen_intrinsic_ok = isinstance(attr.obj.ty, DynType)
        if (
            gen_intrinsic_ok
            and attr.name == "send"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            gen_obj = self._emit_as_object(attr.obj)
            value_obj = self._emit_as_object(expr.args[0])
            return self.builder.call(
                self.runtime["py_gen_send"],
                [gen_obj, value_obj],
                name=self._fresh("gen.send"),
            )
        if (
            gen_intrinsic_ok
            and attr.name == "throw"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            gen_obj = self._emit_as_object(attr.obj)
            exc_obj = self._emit_as_object(expr.args[0])
            return self.builder.call(
                self.runtime["py_gen_throw"],
                [gen_obj, exc_obj],
                name=self._fresh("gen.throw"),
            )
        if (
            gen_intrinsic_ok
            and attr.name == "close"
            and not expr.args
            and not expr.kwargs
        ):
            gen_obj = self._emit_as_object(attr.obj)
            return self.builder.call(
                self.runtime["py_gen_close"],
                [gen_obj],
                name=self._fresh("gen.close"),
            )

        # Case 0: ``super().method(args)`` inside a method body.
        # Resolve the method by walking the current class's declared
        # bases. The ``self`` argument is forwarded unchanged.
        current_class = self.current_class
        if (
            current_class is not None
            and isinstance(attr.obj, Call)
            and isinstance(attr.obj.func, Name)
            and attr.obj.func.ident == "super"
            and len(attr.obj.args) in (0, 2)
            and not attr.obj.kwargs
        ):
            super_args = attr.obj.args
            from_class = current_class
            if len(super_args) == 2:
                from_expr = super_args[0]
                if not isinstance(from_expr, Name):
                    from_class = None
                else:
                    local_class_hints = getattr(self, "env_class_object_hint", {})
                    if from_expr.ident in local_class_hints:
                        from_class_name = local_class_hints[from_expr.ident]
                    elif from_expr.ident in self.env:
                        from_class_name = None
                    else:
                        from_class_name = self._resolve_class_alias(from_expr.ident)
                    from_class = self.class_lowering.classes.get(from_class_name)
            parent_info = (
                self._resolve_super_method(from_class, attr.name)
                if from_class is not None
                else None
            )
            if parent_info is not None:
                receiver_name = "self"
                if (
                    getattr(self, "current_func_def", None) is not None
                    and self.current_func_def.args
                ):
                    receiver_name = self.current_func_def.args[0].name or "self"
                current_method_kind = (
                    getattr(self, "current_method_kind", None) or "instance"
                )
                if (
                    getattr(self, "current_func_def", None) is not None
                    and self.current_func_def.name in current_class.method_kinds
                ):
                    current_method_kind = current_class.method_kinds.get(
                        self.current_func_def.name,
                        current_method_kind,
                    )
                if current_method_kind == "static" and len(super_args) == 0:
                    msg = self._pooled_cstr_ptr(
                        "super(): no arguments",
                        ".super_static_error",
                    )
                    exc = self.builder.call(
                        self.runtime["py_exc_new"],
                        [ir.Constant(_I64, _PY_EXC_RUNTIMEERROR), msg],
                        name=self._fresh("super.static.error"),
                    )
                    self.builder.call(self.runtime["py_raise"], [exc])
                    self._emit_post_call_err_check(getattr(expr, "span", None))
                    return self._emit_none_literal()
                if len(super_args) == 2:
                    recv_expr = super_args[1]
                    recv_val = self._emit_expr(recv_expr)
                    recv_class_name = (
                        (
                            getattr(self, "env_class_object_hint", {}).get(
                                recv_expr.ident
                            )
                            or self._resolve_class_alias(recv_expr.ident)
                        )
                        if isinstance(recv_expr, Name)
                        else None
                    )
                    receiver_is_local_class_object = (
                        isinstance(recv_expr, Name)
                        and recv_expr.ident
                        in getattr(self, "env_class_object_hint", {})
                    )
                    receiver_is_named_class = (
                        isinstance(recv_expr, Name)
                        and recv_class_name in self.class_lowering.classes
                        and (
                            recv_expr.ident not in self.env
                            or receiver_is_local_class_object
                        )
                    )
                    receiver_is_class = (
                        (
                            current_method_kind == "classmethod"
                            and isinstance(recv_expr, Name)
                            and recv_expr.ident == receiver_name
                        )
                        or receiver_is_named_class
                    )
                else:
                    recv_slot = self.env.get(receiver_name)
                    if recv_slot is None:
                        raise L1CodegenError(
                            f"super() receiver {receiver_name!r} not bound in "
                            f"{self.current_func_def.name!r}"
                        )
                    recv_val = self.builder.load(
                        recv_slot[0], name=self._fresh(receiver_name)
                    )
                    receiver_is_class = current_method_kind == "classmethod"
                super_start_cls = self.class_lowering.emit_super_start_class(
                    recv_val,
                    receiver_is_class,
                )
                method_ptr = self.class_lowering.emit_super_lookup_from_start(
                    from_class,
                    super_start_cls,
                    attr.name,
                )
                self._emit_post_call_err_check(getattr(expr, "span", None))
                kind = parent_info.method_kinds.get(attr.name, "instance")
                if kind == "static":
                    method_fn = parent_info.methods[attr.name]
                    return self._emit_static_method_ptr_call(
                        method_ptr,
                        method_fn,
                        parent_info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                if kind == "classmethod":
                    method_fn = parent_info.methods[attr.name]
                    return self._emit_direct_method_ptr_call(
                        method_ptr,
                        method_fn,
                        super_start_cls,
                        parent_info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                method_fn = parent_info.methods[attr.name]
                return self._emit_direct_method_ptr_call(
                    method_ptr,
                    method_fn,
                    recv_val,
                    parent_info,
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )
            if from_class is not None and attr.name not in ("__init__", "__new__"):
                receiver_name = "self"
                if (
                    getattr(self, "current_func_def", None) is not None
                    and self.current_func_def.args
                ):
                    receiver_name = self.current_func_def.args[0].name or "self"
                current_method_kind = (
                    getattr(self, "current_method_kind", None) or "instance"
                )
                if (
                    getattr(self, "current_func_def", None) is not None
                    and self.current_func_def.name in current_class.method_kinds
                ):
                    current_method_kind = current_class.method_kinds.get(
                        self.current_func_def.name,
                        current_method_kind,
                    )
                if current_method_kind == "static" and len(super_args) == 0:
                    msg = self._pooled_cstr_ptr(
                        "super(): no arguments",
                        ".super_static_error",
                    )
                    exc = self.builder.call(
                        self.runtime["py_exc_new"],
                        [ir.Constant(_I64, _PY_EXC_RUNTIMEERROR), msg],
                        name=self._fresh("super.static.error"),
                    )
                    self.builder.call(self.runtime["py_raise"], [exc])
                    self._emit_post_call_err_check(getattr(expr, "span", None))
                    return self._emit_none_literal()
                if len(super_args) == 2:
                    recv_expr = super_args[1]
                    recv_val = self._emit_expr(recv_expr)
                    recv_class_name = (
                        (
                            getattr(self, "env_class_object_hint", {}).get(
                                recv_expr.ident
                            )
                            or self._resolve_class_alias(recv_expr.ident)
                        )
                        if isinstance(recv_expr, Name)
                        else None
                    )
                    receiver_is_local_class_object = (
                        isinstance(recv_expr, Name)
                        and recv_expr.ident
                        in getattr(self, "env_class_object_hint", {})
                    )
                    receiver_is_named_class = (
                        isinstance(recv_expr, Name)
                        and recv_class_name in self.class_lowering.classes
                        and (
                            recv_expr.ident not in self.env
                            or receiver_is_local_class_object
                        )
                    )
                    receiver_is_class = (
                        (
                            current_method_kind == "classmethod"
                            and isinstance(recv_expr, Name)
                            and recv_expr.ident == receiver_name
                        )
                        or receiver_is_named_class
                    )
                else:
                    recv_slot = self.env.get(receiver_name)
                    if recv_slot is None:
                        raise L1CodegenError(
                            f"super() receiver {receiver_name!r} not bound in "
                            f"{self.current_func_def.name!r}"
                        )
                    recv_val = self.builder.load(
                        recv_slot[0], name=self._fresh(receiver_name)
                    )
                    receiver_is_class = current_method_kind == "classmethod"
                super_start_cls = self.class_lowering.emit_super_start_class(
                    recv_val,
                    receiver_is_class,
                )
                self.class_lowering.emit_super_lookup_from_start(
                    from_class,
                    super_start_cls,
                    attr.name,
                )
                self._emit_post_call_err_check(getattr(expr, "span", None))
                return self._emit_none_literal()
            # Parent is a foreign base (e.g. ``Exception``) not tracked
            # by pcc's ClassInfo registry. For the well-known dunders
            # (``__init__`` / ``__new__``) we fall through quietly —
            # pcc-emitted classes already have their ctor state
            # populated by ``_pcc_py_module_init_*``, and calling an
            # unknown foreign super is typically only used for its
            # side effects which have no equivalent on the pcc side.
            if attr.name in ("__init__", "__new__"):
                # super().__init__(*args) to a builtin Exception base: the call
                # itself is a no-op on the pcc side, but BaseException stores
                # the args tuple on the instance, and str(e) / e.args read it.
                # Persist args so a raised user-exception-subclass instance
                # behaves like an exception (otherwise str(e) -> <null>,
                # e.args -> AttributeError).
                if attr.name == "__init__":
                    self._emit_store_exception_args(expr.args)
                return ir.Constant(_CSTR, None)

        # Case 1: ``self.method(...)`` inside a method body of the
        # currently-lowered class. Try the method on the class itself,
        # then walk the declared bases.
        if (
            current_class is not None
            and _method_is_name(attr.obj)
            and _method_ident(attr.obj) == "self"
        ):
            receiver_class_name = self._self_receiver_class_name()
            if receiver_class_name is None:
                receiver_class_name = current_class.name
            method_info = self._resolve_method_mro(receiver_class_name, attr.name)
            if method_info is not None:
                if attr.name not in method_info.methods:
                    return self._emit_callable_attribute_call(
                        attr.obj,
                        attr.name,
                        expr.args,
                        expr.kwargs,
                        expr.span,
                    )
                kind = method_info.method_kinds.get(attr.name, "instance")
                if kind == "static":
                    # ``self.static_method(args)`` — Python lets you
                    # call staticmethods via the instance; drop the
                    # self receiver and dispatch as a plain class call.
                    method_fn = method_info.methods[attr.name]
                    return self._emit_static_method_call(
                        method_fn,
                        method_info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                if kind == "classmethod":
                    recv_slot = self.env.get("cls")
                    if recv_slot is not None:
                        cls_ptr = self.builder.load(
                            recv_slot[0],
                            name=self._fresh(".cls.recv"),
                        )
                    else:
                        cls_ptr = self.builder.load(
                            current_class.global_var,
                            name=self._fresh(".cls.recv"),
                        )
                    method_fn = method_info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn,
                        cls_ptr,
                        method_info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                self_val = self.builder.load(
                    self.env["self"][0], name=self._fresh("self")
                )
                method_fn = method_info.methods[attr.name]
                try:
                    return self._emit_direct_method_call(
                        method_fn,
                        self_val,
                        method_info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                except L1CodegenError as e:
                    if "too many positional args" not in str(e):
                        raise
                    return self._emit_static_method_call(
                        method_fn,
                        method_info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )

        # Case 1b: ``cls.method(...)`` inside a ``@classmethod`` body.
        # ``cls`` is the class itself, so dispatch exactly like
        # ``ClassName.method`` — respect the target method's kind and
        # pass the class pointer as the receiver for classmethods /
        # drop it for staticmethods.
        if (
            current_class is not None
            and _method_is_name(attr.obj)
            and _method_ident(attr.obj) == "cls"
        ):
            method_info = self._resolve_method_mro(current_class.name, attr.name)
            if method_info is not None:
                kind = method_info.method_kinds.get(attr.name, "instance")
                if kind == "instance":
                    cls_ptr = self.builder.load(
                        method_info.global_var,
                        name=self._fresh(".cls.recv"),
                    )
                    method_fn = method_info.methods[attr.name]
                    try:
                        return self._emit_direct_method_call(
                            method_fn,
                            cls_ptr,
                            method_info,
                            attr.name,
                            expr.args,
                            kwargs=expr.kwargs,
                        )
                    except L1CodegenError as e:
                        if "too many positional args" not in str(e):
                            raise
                        return self._emit_static_method_call(
                            method_fn,
                            method_info,
                            attr.name,
                            expr.args,
                            kwargs=expr.kwargs,
                        )
                if kind == "static":
                    method_fn = method_info.methods[attr.name]
                    return self._emit_static_method_call(
                        method_fn,
                        method_info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                if kind == "classmethod":
                    cls_ptr = self.builder.load(
                        method_info.global_var,
                        name=self._fresh(".cls.recv"),
                    )
                    method_fn = method_info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn,
                        cls_ptr,
                        method_info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                # Instance method called via ``cls`` — pcc can't
                # construct a bound instance, so treat as a static
                # dispatch on the class. Matches CPython's
                # ``ClassName.instance_method(instance, …)`` when the
                # user reaches for it this way; the first arg is then
                # the real ``self``.
                method_fn = method_info.methods[attr.name]
                return self._emit_static_method_call(
                    method_fn,
                    method_info,
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )

        # Case 2: ``ClassName.method(...)`` — direct static/classmethod
        # dispatch on a bare class reference (no instance).
        obj_ident = _method_ident(attr.obj)
        if _method_is_name(attr.obj) and obj_ident in self.class_lowering.classes:
            class_info = self.class_lowering.classes[obj_ident]
            info = self._resolve_method_mro(obj_ident, attr.name)
            class_attr_known = (
                self.class_lowering.lookup_class_attr(class_info, attr.name)
                is not None
            )
            class_attr_state = getattr(
                self,
                "_class_attr_runtime_state",
                {},
            ).get((class_info.name, attr.name))
            class_attr_runtime_candidate = (
                class_attr_known
                or class_attr_state == "live"
                or class_attr_state == "unknown"
                or class_attr_state == "deleted"
            )
            if (
                class_attr_runtime_candidate
                and info is not None
                and attr.name in info.methods
            ):
                callable_obj = self._emit_attr(attr)
                kwdict_unpack = self._split_starstar_kwargs_unpack(expr.args)
                arg_exprs = expr.args
                kwargs_expr = None
                if kwdict_unpack is not None:
                    arg_exprs, kwargs_expr = kwdict_unpack
                args_owned = not self._is_starred_unpack(arg_exprs)
                args_tuple = self._emit_dynamic_call_args_tuple(arg_exprs)
                kwargs_obj = self._emit_dynamic_call_kwargs_object(
                    expr.kwargs,
                    kwargs_expr,
                    expr.span,
                )
                result = self.builder.call(
                    self.runtime["py_obj_call"],
                    [callable_obj, args_tuple, kwargs_obj],
                    name=self._fresh(f"callable.attr.{attr.name}.call"),
                )
                if args_owned:
                    self._gc_release(args_tuple)
                if expr.kwargs:
                    self._gc_release(kwargs_obj)
                self._gc_release(callable_obj)
                self._emit_post_call_err_check(expr.span)
                return result
            if class_attr_runtime_candidate:
                return self._emit_callable_attribute_call(
                    attr.obj,
                    attr.name,
                    expr.args,
                    expr.kwargs,
                    expr.span,
                )
            call_receiver = None
            if info is None:
                metaclass_name = getattr(class_info, "metaclass_name", None)
                if metaclass_name is not None:
                    meta_info = self.class_lowering.classes.get(metaclass_name)
                    if meta_info is not None and attr.name in meta_info.methods:
                        info = meta_info
                        call_receiver = self.builder.load(
                            class_info.global_var,
                            name=self._fresh(".meta.cls.recv"),
                        )
            if info is not None:
                kind = info.method_kinds.get(attr.name, "instance")
                if kind == "instance" and _method_first_arg_name(
                    self.class_lowering,
                    info.name,
                    attr.name,
                ) == "cls":
                    kind = "classmethod"
                if kind == "static":
                    method_fn = info.methods[attr.name]
                    return self._emit_static_method_call(
                        method_fn,
                        info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                if kind == "classmethod":
                    if call_receiver is not None:
                        cls_ptr = call_receiver
                    else:
                        cls_ptr = self.builder.load(
                            class_info.global_var,
                            name=self._fresh(".cls.recv"),
                        )
                    method_fn = info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn,
                        cls_ptr,
                        info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                # Explicit base-class instance dispatch such as
                # ``Base.method(self, ...)`` should pass the caller's
                # explicit receiver as the first positional argument.
                method_fn = info.methods[attr.name]
                if call_receiver is not None:
                    return self._emit_direct_method_call(
                        method_fn,
                        call_receiver,
                        info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                return self._emit_static_method_call(
                    method_fn,
                    info,
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )

        native_external = self._maybe_emit_class_lowering_extern_method(expr)
        if native_external is not None:
            return native_external

        if isinstance(attr.obj, Attr):
            class_export = self._native_module_expr_export_info(
                attr.obj.obj,
                attr.obj.name,
            )
            if (
                class_export is not None
                and isinstance(class_export[1], dict)
                and class_export[1].get("kind") == "class"
            ):
                receiver_hint = self._class_hint_for_expr(attr.obj)
                if receiver_hint is not None:
                    info = self._resolve_method_mro(receiver_hint, attr.name)
                    if info is not None and attr.name in info.methods:
                        kind = info.method_kinds.get(attr.name, "instance")
                        method_fn = info.methods[attr.name]
                        if kind == "static":
                            return self._emit_static_method_call(
                                method_fn,
                                info,
                                attr.name,
                                expr.args,
                                kwargs=expr.kwargs,
                            )
                        if kind == "classmethod":
                            cls_ptr = self.builder.load(
                                info.global_var,
                                name=self._fresh(".cls.recv"),
                            )
                            return self._emit_direct_method_call(
                                method_fn,
                                cls_ptr,
                                info,
                                attr.name,
                                expr.args,
                                kwargs=expr.kwargs,
                            )
                        return self._emit_static_method_call(
                            method_fn,
                            info,
                            attr.name,
                            expr.args,
                            kwargs=expr.kwargs,
                        )

        # Case 3: ``other_obj.method(...)`` — first try the class hint
        # recorded at assignment time, walking up the MRO of that
        # class for the first definition of the method. Fall back to
        # the first class in the module that declares the method so
        # single-class programs keep working when the hint is missing.
        if isinstance(attr.obj, Name):
            hint = self.env_class_hint.get(attr.obj.ident)
            if hint is not None:
                info = self._resolve_method_mro(hint, attr.name)
                if info is not None:
                    if attr.name not in info.methods:
                        return self._emit_callable_attribute_call(
                            attr.obj,
                            attr.name,
                            expr.args,
                            expr.kwargs,
                            expr.span,
                        )
                    kind = info.method_kinds.get(attr.name, "instance")
                    if kind == "static":
                        method_fn = info.methods[attr.name]
                        return self._emit_static_method_call(
                            method_fn,
                            info,
                            attr.name,
                            expr.args,
                            kwargs=expr.kwargs,
                        )
                    obj_val = self._emit_expr(attr.obj)
                    if kind == "classmethod":
                        obj_val = self.builder.load(
                            info.global_var, name=self._fresh(".cls.recv")
                        )
                    method_fn = info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn,
                        obj_val,
                        info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )

        receiver_hint = self._class_hint_for_expr(attr.obj)
        if receiver_hint is not None:
            info = self._resolve_method_mro(receiver_hint, attr.name)
            if info is not None:
                if attr.name not in info.methods:
                    return self._emit_callable_attribute_call(
                        attr.obj,
                        attr.name,
                        expr.args,
                        expr.kwargs,
                        expr.span,
                    )
                kind = info.method_kinds.get(attr.name, "instance")
                if kind == "static":
                    self._emit_expr(attr.obj)
                    method_fn = info.methods[attr.name]
                    return self._emit_static_method_call(
                        method_fn,
                        info,
                        attr.name,
                        expr.args,
                        kwargs=expr.kwargs,
                    )
                obj_val = self._emit_expr(attr.obj)
                if kind == "classmethod":
                    obj_val = self.builder.load(
                        info.global_var, name=self._fresh(".cls.recv")
                    )
                method_fn = info.methods[attr.name]
                return self._emit_direct_method_call(
                    method_fn,
                    obj_val,
                    info,
                    attr.name,
                    expr.args,
                    kwargs=expr.kwargs,
                )

        if isinstance(attr.obj, Name):
            # Last closed-world fallback: any class declaring the method.
            # Keep this after receiver_hint so typed loop variables like
            # ``for gv in self._globals: gv.render()`` dispatch to the
            # receiver's class instead of the first same-named method found.
            # Guard: if the receiver has a known primitive shape
            # (set/list/dict/str/...) the dispatch above already handled
            # it (or returned None on purpose). Falling through to "any
            # class with that method name" would let an unrelated
            # registered class (e.g. ``ir.IRBuilder.add``) shadow the
            # primitive's method, which is wrong now that more
            # cross-module classes are exported.
            receiver_is_unhinted_module_global = (
                attr.obj.ident in getattr(self, "_module_globals", {})
                and self.env_class_hint.get(attr.obj.ident) is None
                and attr.obj.ident not in self.class_lowering.classes
            )
            receiver_is_module_alias = (
                attr.obj.ident in getattr(self, "_native_module_aliases", {})
                or attr.obj.ident in getattr(self, "_native_module_object_aliases", {})
                or self._native_builtin_module_for_name(attr.obj.ident) is not None
            )
            obj_ty_for_guard = attr.obj.ty
            if isinstance(
                obj_ty_for_guard,
                (
                    ListType,
                    DictType,
                    TupleType,
                    StrType,
                    BytesType,
                    ByteArrayType,
                    MemoryViewType,
                ),
            ):
                obj_ty_for_guard = None
            elif isinstance(obj_ty_for_guard, DynType) and (
                attr.name in _DYN_LIST_METHOD_NATIVE
                or attr.name in _DYN_DICT_METHOD_NATIVE
                or attr.name in _DYN_SET_METHOD_NATIVE
                or attr.name in _STR_METHOD_NATIVE
            ):
                obj_ty_for_guard = None
            elif isinstance(obj_ty_for_guard, DynType) and obj_ty_for_guard.name in (
                "set",
                "frozenset",
            ):
                obj_ty_for_guard = None
            elif isinstance(obj_ty_for_guard, DynType):
                obj_ty_for_guard = None
            else:
                obj_ty_for_guard = "ok"
            if (
                obj_ty_for_guard is not None
                and not receiver_is_unhinted_module_global
                and not receiver_is_module_alias
            ):
                for info in self.class_lowering.classes.values():
                    if attr.name in info.methods:
                        kind = info.method_kinds.get(attr.name, "instance")
                        if kind == "static":
                            method_fn = info.methods[attr.name]
                            return self._emit_static_method_call(
                                method_fn,
                                info,
                                attr.name,
                                expr.args,
                                kwargs=expr.kwargs,
                            )
                        obj_val = self._emit_expr(attr.obj)
                        if kind == "classmethod":
                            obj_val = self.builder.load(
                                info.global_var, name=self._fresh(".cls.recv")
                            )
                        method_fn = info.methods[attr.name]
                        return self._emit_direct_method_call(
                            method_fn,
                            obj_val,
                            info,
                            attr.name,
                            expr.args,
                            kwargs=expr.kwargs,
                        )

        if (
            attr.name in ("__init__", "__new__")
            and isinstance(attr.obj, Name)
            and attr.obj.ident in getattr(self, "_module_globals", {})
            and self.env_class_hint.get(attr.obj.ident) is None
            and attr.obj.ident not in self.class_lowering.classes
        ):
            return self._emit_none_literal()

        # Last-resort CPython fallback: when the receiver is a DynType
        # value (typical for foreign / imported-module classes whose
        # annotations such as ``llvm.ModuleRef`` resolve to DynType at
        # type-inference time), dispatch the method via
        # ``PyObject_CallMethod``. This unlocks ``module.verify()``
        # and similar idioms without requiring a CPython-class registry
        # on the pcc side.
        #
        # Typed containers (list / dict / tuple / str) deliberately do
        # *not* fall through here: pcc's pure-self-host story requires
        # that typed-collection methods stay on pcc-native runtime paths
        # so the produced binary has no libpython dependency. Missing
        # methods there surface as NotImplementedError so we can add a
        # dedicated fast path rather than silently pulling libpython in.
        obj_ty = attr.obj.ty
        if isinstance(obj_ty, DynType):
            if attr.name == "clear" and not expr.args and not expr.kwargs:
                self.builder.call(
                    self.runtime["py_obj_clear"],
                    [self._emit_as_object(attr.obj)],
                    name=self._fresh("obj.clear"),
                )
                return self._emit_none_literal()
            native = self._maybe_emit_dict_method_via_dyn(expr)
            if native is not None:
                return native
            native = self._maybe_emit_list_method_via_dyn(expr)
            if native is not None:
                return native
            native = self._maybe_emit_set_method_via_dyn(expr)
            if native is not None:
                return native
            # DynType receiver: when the method is a known pcc-native
            # str helper, dispatch through the runtime (assumes the
            # value really is a str at runtime — matches CPython
            # behaviour which would raise AttributeError on type
            # mismatch; we emit a probable crash). Keeps the binary
            # libpython-free for the common ``DynType str result``
            # idiom (function return + splitlines / rstrip / …).
            native = self._maybe_emit_str_method_via_dyn(expr)
            if native is not None:
                return native
            # DynType numeric methods on a boxed value (e.g. a float from
            # py_obj_truediv, or a boxed int): handle .is_integer()/.bit_length()
            # natively before the getattr-based dynamic dispatch (which would
            # AttributeError, since boxed float/int have no method table).
            # py_float_is_integer / py_int_bit_length read the object by tag.
            # (Same "assume the runtime type matches the method" pragmatism as
            # the str_method_via_dyn path above.)
            if attr.name == "is_integer" and not expr.args and not expr.kwargs:
                obj = self._emit_as_object(attr.obj)
                i64v = self.builder.call(
                    self.runtime["py_float_is_integer"],
                    [obj],
                    name=self._fresh("dyn.is_integer"),
                )
                return self.builder.icmp_signed(
                    "!=",
                    i64v,
                    ir.Constant(_I64, 0),
                    name=self._fresh("dyn.is_integer.i1"),
                )
            if attr.name == "bit_length" and not expr.args and not expr.kwargs:
                obj = self._emit_as_object(attr.obj)
                return self.builder.call(
                    self.runtime["py_int_bit_length"],
                    [obj],
                    name=self._fresh("dyn.bit_length"),
                )
            recv_obj = self._emit_as_object(attr.obj)
            method_obj = self.builder.call(
                self.runtime["py_obj_getattr"],
                [recv_obj, self._attr_name_ptr(attr.name)],
                name=self._fresh(f"dyn.attr.{attr.name}"),
            )
            self._emit_attribute_error_if_null(method_obj, attr.name, attr.span)
            kwdict_unpack = self._split_starstar_kwargs_unpack(expr.args)
            arg_exprs = expr.args
            kwargs_expr = None
            if kwdict_unpack is not None:
                arg_exprs, kwargs_expr = kwdict_unpack
            args_owned = not self._is_starred_unpack(arg_exprs)
            args_tuple = self._emit_dynamic_call_args_tuple(arg_exprs)
            kwargs_obj = self._emit_dynamic_call_kwargs_object(
                expr.kwargs,
                kwargs_expr,
                expr.span,
            )
            result = self.builder.call(
                self.runtime["py_obj_call"],
                [method_obj, args_tuple, kwargs_obj],
                name=self._fresh(f"dyn.method.{attr.name}"),
            )
            if args_owned:
                self._gc_release(args_tuple)
            if expr.kwargs or kwargs_expr is not None:
                self._gc_release(kwargs_obj)
            self._emit_post_call_err_check(expr.span)
            return result
        if isinstance(obj_ty, (BytesType, ByteArrayType)) and attr.name == "decode":
            # decode() defaults to utf-8, and pcc str is utf-8 internally, so an
            # explicit "utf-8" encoding (+ optional "strict" errors) is identical
            # to the no-arg form. Other encodings / error modes fall back.
            encoding_arg = None
            errors_arg = None
            ok = True
            if len(expr.args) >= 1:
                encoding_arg = expr.args[0]
            if len(expr.args) >= 2:
                errors_arg = expr.args[1]
            if len(expr.args) > 2:
                ok = False
            for kname, kval in (expr.kwargs or ()):
                if kname == "encoding" and encoding_arg is None:
                    encoding_arg = kval
                elif kname == "errors" and errors_arg is None:
                    errors_arg = kval
                else:
                    ok = False
            if encoding_arg is not None and (
                not isinstance(encoding_arg, StrLit)
                or encoding_arg.value.lower().replace("-", "") != "utf8"
            ):
                ok = False
            if errors_arg is not None and (
                not isinstance(errors_arg, StrLit) or errors_arg.value != "strict"
            ):
                ok = False
            if not ok:
                raise NotImplementedError(
                    "bytes.decode() with a non-utf-8 encoding or non-strict "
                    "errors is not supported yet"
                )
            recv = self._emit_expr(attr.obj)
            return self.builder.call(
                self.runtime["py_bytes_decode"],
                [recv],
                name=self._fresh("bytes.decode"),
            )
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "hex"
            and not expr.args
            and not expr.kwargs
        ):
            # bytes.hex() -> lowercase two-hex-digits-per-byte string.
            recv = self._emit_expr(attr.obj)
            return self.builder.call(
                self.runtime["py_bytes_hex"],
                [recv],
                name=self._fresh("bytes.hex"),
            )
        if isinstance(obj_ty, StrType):
            # Typed-str receiver with a method not on the pcc-native
            # fast path (``encode`` / ``rsplit`` / ``format`` / …).
            # Pcc's pure-self-host policy prefers a native helper here,
            # but to let the solo-compile survey advance we route the
            # unknown method through the libpython fallback and leave
            # a TODO. The emitted binary then links libpython for this
            # specific use. Revisit and add the helper once the call
            # shows up in the self-host critical path.
            native = self._maybe_emit_str_method_via_dyn(expr)
            if native is not None:
                return native
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython(raw_val, obj_ty)
            result = self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return result
        if isinstance(obj_ty, NoneType):
            # Flow-insensitive inference can leave a guarded Optional[str]
            # receiver as ``NoneType`` inside branches like
            # ``x.strip() if x is not None else None``. Marshal the
            # runtime value to a CPython object and dispatch there so a
            # real str still works while an actual None preserves
            # CPython's AttributeError behavior.
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython(raw_val, obj_ty)
            result = self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return result
        if isinstance(obj_ty, (ListType, DictType, TupleType)):
            # Typed-collection receiver whose method isn't on the pcc-
            # native fast path (e.g. ``.reverse`` / ``.clear`` /
            # ``.update``). Fall through to the CPython fallback the
            # same way StrType does: pulls libpython for that specific
            # use but lets the solo-compile survey advance. Revisit
            # with a native helper when the call reaches the
            # self-host critical path.
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython(raw_val, obj_ty)
            result = self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return result
        if (
            isinstance(obj_ty, (FloatType, DynType))
            and attr.name == "is_integer"
            and not expr.args
            and not expr.kwargs
        ):
            # Native float.is_integer() — avoid the libpython fallback below so
            # it works under --python-libpython=off. Returns a bool (i1).
            # DynType is included so a boxed float from a dynamic expression
            # (e.g. ``(total / count).is_integer()`` where ``/`` is py_obj_truediv
            # -> a boxed float) works; py_float_is_integer reads the object via
            # py_float_to_f64 (float/int/bool). (A user class defining its own
            # is_integer would route here too, but that is vanishingly rare.)
            obj = self._emit_as_object(attr.obj)
            i64v = self.builder.call(
                self.runtime["py_float_is_integer"],
                [obj],
                name=self._fresh("float.is_integer"),
            )
            return self.builder.icmp_signed(
                "!=",
                i64v,
                ir.Constant(_I64, 0),
                name=self._fresh("float.is_integer.i1"),
            )
        if (
            isinstance(obj_ty, (IntType, DynType))
            and attr.name == "bit_length"
            and not expr.args
            and not expr.kwargs
        ):
            # Native int.bit_length() — avoid the libpython fallback so it works
            # under --python-libpython=off. Returns the bit count as an int (i64),
            # exact for bignums (py_int_bit_length). DynType included so a boxed
            # int from a dynamic expression works (py_int_bit_length reads the
            # object's tag: tagged int / bignum).
            obj = self._emit_as_object(attr.obj)
            return self.builder.call(
                self.runtime["py_int_bit_length"],
                [obj],
                name=self._fresh("int.bit_length"),
            )
        if isinstance(obj_ty, (IntType, FloatType, BoolType)):
            # Numeric method call (``int.to_bytes``, ``float.is_integer``,
            # ``bool.conjugate``, etc.) — box to a CPython object and
            # dispatch through the libpython fallback. Pulls libpython
            # for that specific use. The numeric value is marshalled
            # through the appropriate boxer so CPython sees a proper
            # Py_Long / Py_Float.
            raw_val = self._emit_expr(attr.obj)
            boxed = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                raw_val,
                obj_ty,
            )
            cpy_val = self.builder.call(
                self.runtime["py_cpy_from_pcc_obj"],
                [boxed],
                name=self._fresh(f"cpy.num.{attr.name}"),
            )
            return self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
            )
        if isinstance(obj_ty, ClassType):
            # A schema-bearing local class should have matched one of
            # the direct method cases above. If it did not, or the type
            # is only an unresolved imported annotation shell, preserve
            # Python semantics by dispatching the runtime object through
            # CPython instead of treating the annotation as a closed
            # pcc class registry entry.
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython(raw_val, obj_ty)
            result = self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return result

        return self._emit_callable_attribute_call(
            attr.obj,
            attr.name,
            expr.args,
            expr.kwargs,
            expr.span,
        )

    def _emit_store_exception_args(self, args) -> None:
        """Persist ``self.args = tuple(args)`` for ``super().__init__(*args)``
        to a builtin Exception base.

        The super-init call is a no-op on the pcc side, but BaseException stores
        the constructor args on the instance; ``str(e)`` derives its message
        from them and ``e.args`` returns the tuple. Without this, a raised user
        exception-subclass instance has no message (``str(e)`` -> ``<null>``)
        and no ``args`` attribute.
        """
        fd = getattr(self, "current_func_def", None)
        if fd is None or not fd.args:
            return
        self_name = fd.args[0].name or "self"
        recv_slot = self.env.get(self_name)
        if recv_slot is None:
            return
        self_val = self.builder.load(recv_slot[0], name=self._fresh("exc.self"))
        n = len(args)
        tup = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, n)],
            name=self._fresh("exc.args.tuple"),
        )
        idx = 0
        for a in args:
            a_obj = self._emit_as_object(a)
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [tup, ir.Constant(_I64, idx), a_obj],
            )
            idx += 1
        name_ptr = self._pooled_cstr_ptr("args", ".exc.args.name")
        self.builder.call(
            self.runtime["py_instance_setattr"],
            [self_val, name_ptr, tup],
        )
