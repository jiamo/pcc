"""Attribute load lowering helpers for L1CodeGen."""
from __future__ import annotations

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
    StrType,
    Subscript,
    TupleType,
    Type,
)
from . import marshal
from .errors import L1CodegenError
from .runtime_abi import declare_runtime_global


_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()
_MATH_INF = 1e308 * 10.0
_MATH_NAN = (1e308 * 10.0) * 0.0


def _same_type_kind(a: Type, b: Type) -> bool:
    return type(a) is type(b)


class AttrLoadLoweringMixin:
    def _metaclass_data_descriptor_info(self, class_info, attr_name: str):
        metaclass_name = getattr(class_info, "metaclass_name", None)
        if metaclass_name is None:
            return None
        meta_info = self.class_lowering.classes.get(metaclass_name)
        if meta_info is None:
            return None
        if attr_name in meta_info.properties:
            return ("property", meta_info, None)
        desc = self._class_attr_descriptor_class(metaclass_name, attr_name)
        if desc is None:
            return None
        owner_info, desc_info = desc
        if "__get__" not in desc_info.methods:
            return None
        if "__set__" not in desc_info.methods and "__delete__" not in desc_info.methods:
            return None
        return ("descriptor", owner_info, desc_info)

    def _emit_metaclass_data_descriptor_get(
        self,
        class_info,
        attr_name: str,
    ):
        desc = self._metaclass_data_descriptor_info(class_info, attr_name)
        if desc is None:
            return None
        kind, owner_info, desc_info = desc
        cls_obj = self.builder.load(
            class_info.global_var,
            name=self._fresh(f"metaclass.descr.obj.{class_info.name}"),
        )
        if kind == "property":
            getter = owner_info.properties[attr_name]
            return self._call_user(
                getter,
                [cls_obj],
                self._fresh(f"metaclass.prop.get.{attr_name}"),
                None,
            )
        attr_entry = owner_info.class_attrs.get(attr_name)
        if attr_entry is None:
            return None
        desc_obj = self.builder.load(
            attr_entry[0],
            name=self._fresh(f"metaclass.descr.{attr_name}"),
        )
        meta_cls = self.builder.load(
            self.class_lowering.classes[getattr(class_info, "metaclass_name")].global_var,
            name=self._fresh(f"metaclass.descr.owner.{class_info.name}"),
        )
        return self._call_user(
            desc_info.methods["__get__"],
            [desc_obj, cls_obj, meta_cls],
            self._fresh(f"metaclass.descr.get.{attr_name}"),
            None,
        )

    def _emit_metaclass_data_descriptor_set(
        self,
        class_info,
        attr_name: str,
        value_obj: ir.Value,
    ) -> bool:
        desc = self._metaclass_data_descriptor_info(class_info, attr_name)
        if desc is None:
            return False
        kind, owner_info, desc_info = desc
        cls_obj = self.builder.load(
            class_info.global_var,
            name=self._fresh(f"metaclass.descr.set.obj.{class_info.name}"),
        )
        if kind == "property":
            setter = owner_info.property_setters.get(attr_name)
            if setter is None:
                self._emit_builtin_exception_and_branch(
                    "AttributeError",
                    "can't set attribute",
                    None,
                    open_dead_continuation=True,
                )
                return True
            self._call_user(setter, [cls_obj, value_obj], "")
            return True
        setter = desc_info.methods.get("__set__")
        if setter is None:
            return False
        attr_entry = owner_info.class_attrs.get(attr_name)
        if attr_entry is None:
            return False
        desc_obj = self.builder.load(
            attr_entry[0],
            name=self._fresh(f"metaclass.descr.set.{attr_name}"),
        )
        self._call_user(setter, [desc_obj, cls_obj, value_obj], "")
        return True

    def _emit_metaclass_data_descriptor_delete(
        self,
        class_info,
        attr_name: str,
        span,
    ) -> bool:
        desc = self._metaclass_data_descriptor_info(class_info, attr_name)
        if desc is None:
            return False
        kind, owner_info, desc_info = desc
        cls_obj = self.builder.load(
            class_info.global_var,
            name=self._fresh(f"metaclass.descr.del.obj.{class_info.name}"),
        )
        if kind == "property":
            deleter = owner_info.property_deleters.get(attr_name)
            if deleter is None:
                self._emit_builtin_exception_and_branch(
                    "AttributeError",
                    "can't delete attribute",
                    span,
                    open_dead_continuation=True,
                )
                return True
            self._call_user(deleter, [cls_obj], "")
            return True
        deleter = desc_info.methods.get("__delete__")
        if deleter is None:
            return False
        attr_entry = owner_info.class_attrs.get(attr_name)
        if attr_entry is None:
            return False
        desc_obj = self.builder.load(
            attr_entry[0],
            name=self._fresh(f"metaclass.descr.del.{attr_name}"),
        )
        self._call_user(deleter, [desc_obj, cls_obj], "")
        return True

    def _emit_bound_self_method_value(
        self,
        info,
        method_name: str,
        method_fn: ir.Function,
        self_val: ir.Value,
    ) -> ir.Value:
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        runtime_args = tuple(a for a in ast_fd.args if a.name != "") if ast_fd else ()
        user_args = runtime_args[1:] if runtime_args else ()
        adapter_name = (
            f"user_{(self.ast_module.name or 'mod').replace('.', '_')}"
            f"_{info.name}_{method_name}_bound_adapter"
        )
        existing = self.module.globals.get(adapter_name)
        if isinstance(existing, ir.Function):
            adapter = existing
        else:
            adapter_ty = ir.FunctionType(_CSTR, [_CSTR, _CSTR])
            adapter = ir.Function(self.module, adapter_ty, name=adapter_name)
            adapter.linkage = "internal"
            saved_builder = self.builder
            entry = adapter.append_basic_block(name="entry")
            self.builder = ir.IRBuilder(entry)

            bound_self = self.builder.call(
                self.runtime["py_tuple_get"],
                [adapter.args[0], ir.Constant(_I64, 0)],
                name="bound.self",
            )
            forwarded: list[ir.Value] = [bound_self]
            for i, ast_arg in enumerate(user_args):
                arg_obj = self.builder.call(
                    self.runtime["py_tuple_get"],
                    [adapter.args[1], ir.Constant(_I64, i)],
                    name=f"arg.{i}",
                )
                param_idx = i + 1
                param_ir_ty = method_fn.args[param_idx].type
                target_ty = ast_arg.annotation or DynType(name="dyn")
                if isinstance(param_ir_ty, ir.PointerType):
                    forwarded.append(arg_obj)
                else:
                    forwarded.append(
                        marshal.marshal_from_object(
                            self.builder,
                            self.module,
                            self.runtime,
                            arg_obj,
                            target_ty,
                        )
                    )

            ret_ty = method_fn.function_type.return_type
            if isinstance(ret_ty, ir.VoidType):
                self.builder.call(method_fn, forwarded)
                none_gv = declare_runtime_global(self.module, "py_None")
                self.builder.ret(self.builder.load(none_gv, name="none"))
            else:
                result = self.builder.call(method_fn, forwarded, name="result")
                if isinstance(ret_ty, ir.PointerType):
                    self.builder.ret(result)
                else:
                    boxed = marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        result,
                        ast_fd.return_ty if ast_fd is not None else DynType(name="dyn"),
                    )
                    self.builder.ret(boxed)
            self.builder = saved_builder

        captures = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, 1)],
            name=self._fresh("bound.method.captures"),
        )
        self.builder.call(
            self.runtime["py_tuple_set_item"],
            [captures, ir.Constant(_I64, 0), self_val],
        )
        fn_obj = self.builder.call(
            self.runtime["py_func_new_bound"],
            [adapter, captures, self._attr_name_ptr(method_name), self_val],
            name=self._fresh(f"bound.{method_name}.func"),
        )
        self._gc_release(captures)
        return fn_obj

    def _emit_bound_class_method_value(
        self,
        owner_info,
        method_name: str,
        method_fn: ir.Function,
        receiver_cls: ir.Value,
    ) -> ir.Value:
        ast_fd = self.class_lowering._find_method_def(owner_info.name, method_name)
        runtime_args = tuple(a for a in ast_fd.args if a.name != "") if ast_fd else ()
        user_args = runtime_args[1:] if runtime_args else ()
        adapter_name = (
            f"user_{(self.ast_module.name or 'mod').replace('.', '_')}"
            f"_{owner_info.name}_{method_name}_classmethod_attr_adapter"
        )
        existing = self.module.globals.get(adapter_name)
        if isinstance(existing, ir.Function):
            adapter = existing
        else:
            adapter_ty = ir.FunctionType(_CSTR, [_CSTR, _CSTR])
            adapter = ir.Function(self.module, adapter_ty, name=adapter_name)
            adapter.linkage = "internal"
            saved_builder = self.builder
            entry = adapter.append_basic_block(name="entry")
            self.builder = ir.IRBuilder(entry)

            bound_cls = self.builder.call(
                self.runtime["py_tuple_get"],
                [adapter.args[0], ir.Constant(_I64, 0)],
                name="bound.cls",
            )
            forwarded: list[ir.Value] = [bound_cls]
            for i, ast_arg in enumerate(user_args):
                arg_obj = self.builder.call(
                    self.runtime["py_tuple_get"],
                    [adapter.args[1], ir.Constant(_I64, i)],
                    name=f"arg.{i}",
                )
                param_idx = i + 1
                param_ir_ty = method_fn.args[param_idx].type
                target_ty = ast_arg.annotation or DynType(name="dyn")
                if isinstance(param_ir_ty, ir.PointerType):
                    forwarded.append(arg_obj)
                else:
                    forwarded.append(
                        marshal.marshal_from_object(
                            self.builder,
                            self.module,
                            self.runtime,
                            arg_obj,
                            target_ty,
                        )
                    )

            ret_ty = method_fn.function_type.return_type
            if isinstance(ret_ty, ir.VoidType):
                self.builder.call(method_fn, forwarded)
                none_gv = declare_runtime_global(self.module, "py_None")
                self.builder.ret(self.builder.load(none_gv, name="none"))
            else:
                result = self.builder.call(method_fn, forwarded, name="result")
                if isinstance(ret_ty, ir.PointerType):
                    self.builder.ret(result)
                else:
                    boxed = marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        result,
                        ast_fd.return_ty if ast_fd is not None else DynType(name="dyn"),
                    )
                    self.builder.ret(boxed)
            self.builder = saved_builder

        captures = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, 1)],
            name=self._fresh("bound.classmethod.captures"),
        )
        self.builder.call(
            self.runtime["py_tuple_set_item"],
            [captures, ir.Constant(_I64, 0), receiver_cls],
        )
        fn_obj = self.builder.call(
            self.runtime["py_func_new_bound"],
            [adapter, captures, self._attr_name_ptr(method_name), receiver_cls],
            name=self._fresh(f"bound.{method_name}.classmethod.func"),
        )
        self._gc_release(captures)
        return fn_obj

    def _emit_cached_zero_capture_func_value(
        self,
        adapter: ir.Function,
        cache_name: str,
        result_name: str,
        method_name: str,
    ) -> ir.Value:
        existing_cache = self.module.globals.get(cache_name)
        if isinstance(existing_cache, ir.GlobalVariable):
            cache_gv = existing_cache
        else:
            cache_gv = ir.GlobalVariable(self.module, _CSTR, name=cache_name)
            cache_gv.linkage = "internal"
            cache_gv.initializer = ir.Constant(_CSTR, None)

        cached = self.builder.load(
            cache_gv,
            name=self._fresh(f"{result_name}.cached"),
        )
        has_cached = self.builder.icmp_unsigned(
            "!=",
            cached,
            ir.Constant(_CSTR, None),
            name=self._fresh(f"{result_name}.has_cached"),
        )
        check_bb = self.builder._block
        create_bb = self.current_function.append_basic_block(
            name=self._fresh(f"{result_name}.create"),
        )
        done_bb = self.current_function.append_basic_block(
            name=self._fresh(f"{result_name}.done"),
        )
        self.builder.cbranch(has_cached, done_bb, create_bb)

        self.builder.position_at_end(create_bb)
        captures = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh(f"{result_name}.captures"),
        )
        created = self.builder.call(
            self.runtime["py_func_new_named"],
            [adapter, captures, self._attr_name_ptr(method_name)],
            name=self._fresh(result_name),
        )
        self._gc_release(captures)
        self.builder.call(self.runtime["pcc_gc_pin"], [created])
        self.builder.store(created, cache_gv)
        create_exit = self.builder._block
        self.builder.branch(done_bb)

        self.builder.position_at_end(done_bb)
        result = self.builder.phi(_CSTR, name=self._fresh(result_name))
        result.add_incoming(cached, check_bb)
        result.add_incoming(created, create_exit)
        return result

    def _emit_static_method_value(
        self,
        owner_info,
        method_name: str,
        method_fn: ir.Function,
        cache: bool = True,
    ) -> ir.Value:
        ast_fd = self.class_lowering._find_method_def(owner_info.name, method_name)
        runtime_args = tuple(a for a in ast_fd.args if a.name != "") if ast_fd else ()
        adapter_name = (
            f"user_{(self.ast_module.name or 'mod').replace('.', '_')}"
            f"_{owner_info.name}_{method_name}_staticmethod_attr_adapter"
        )
        existing = self.module.globals.get(adapter_name)
        if isinstance(existing, ir.Function):
            adapter = existing
        else:
            adapter_ty = ir.FunctionType(_CSTR, [_CSTR, _CSTR])
            adapter = ir.Function(self.module, adapter_ty, name=adapter_name)
            adapter.linkage = "internal"
            saved_builder = self.builder
            entry = adapter.append_basic_block(name="entry")
            self.builder = ir.IRBuilder(entry)

            forwarded: list[ir.Value] = []
            for i, ast_arg in enumerate(runtime_args):
                arg_obj = self.builder.call(
                    self.runtime["py_tuple_get"],
                    [adapter.args[1], ir.Constant(_I64, i)],
                    name=f"arg.{i}",
                )
                param_ir_ty = method_fn.args[i].type
                target_ty = ast_arg.annotation or DynType(name="dyn")
                if isinstance(param_ir_ty, ir.PointerType):
                    forwarded.append(arg_obj)
                else:
                    forwarded.append(
                        marshal.marshal_from_object(
                            self.builder,
                            self.module,
                            self.runtime,
                            arg_obj,
                            target_ty,
                        )
                    )

            ret_ty = method_fn.function_type.return_type
            if isinstance(ret_ty, ir.VoidType):
                self.builder.call(method_fn, forwarded)
                none_gv = declare_runtime_global(self.module, "py_None")
                self.builder.ret(self.builder.load(none_gv, name="none"))
            else:
                result = self.builder.call(method_fn, forwarded, name="result")
                if isinstance(ret_ty, ir.PointerType):
                    self.builder.ret(result)
                else:
                    boxed = marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        result,
                        ast_fd.return_ty if ast_fd is not None else DynType(name="dyn"),
                    )
                    self.builder.ret(boxed)
            self.builder = saved_builder

        if cache:
            cache_name = (
                f"user_{(self.ast_module.name or 'mod').replace('.', '_')}"
                f"_{owner_info.name}_{method_name}_staticmethod_attr_value_cache"
            )
            return self._emit_cached_zero_capture_func_value(
                adapter,
                cache_name,
                f"bound.{method_name}.staticmethod.func",
                method_name,
            )

        captures = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh("bound.staticmethod.captures"),
        )
        fn_obj = self.builder.call(
            self.runtime["py_func_new_named"],
            [adapter, captures, self._attr_name_ptr(method_name)],
            name=self._fresh(f"bound.{method_name}.staticmethod.func"),
        )
        self._gc_release(captures)
        return fn_obj

    def _emit_unbound_instance_method_value(
        self,
        owner_info,
        method_name: str,
        method_fn: ir.Function,
        cache: bool = True,
    ) -> ir.Value:
        ast_fd = self.class_lowering._find_method_def(owner_info.name, method_name)
        runtime_args = tuple(a for a in ast_fd.args if a.name != "") if ast_fd else ()
        adapter_name = (
            f"user_{(self.ast_module.name or 'mod').replace('.', '_')}"
            f"_{owner_info.name}_{method_name}_unbound_method_attr_adapter"
        )
        existing = self.module.globals.get(adapter_name)
        if isinstance(existing, ir.Function):
            adapter = existing
        else:
            adapter_ty = ir.FunctionType(_CSTR, [_CSTR, _CSTR])
            adapter = ir.Function(self.module, adapter_ty, name=adapter_name)
            adapter.linkage = "internal"
            saved_builder = self.builder
            entry = adapter.append_basic_block(name="entry")
            self.builder = ir.IRBuilder(entry)

            forwarded: list[ir.Value] = []
            for i, ast_arg in enumerate(runtime_args):
                arg_obj = self.builder.call(
                    self.runtime["py_tuple_get"],
                    [adapter.args[1], ir.Constant(_I64, i)],
                    name=f"arg.{i}",
                )
                param_ir_ty = method_fn.args[i].type
                target_ty = ast_arg.annotation or DynType(name="dyn")
                if isinstance(param_ir_ty, ir.PointerType):
                    forwarded.append(arg_obj)
                else:
                    forwarded.append(
                        marshal.marshal_from_object(
                            self.builder,
                            self.module,
                            self.runtime,
                            arg_obj,
                            target_ty,
                        )
                    )

            ret_ty = method_fn.function_type.return_type
            if isinstance(ret_ty, ir.VoidType):
                self.builder.call(method_fn, forwarded)
                none_gv = declare_runtime_global(self.module, "py_None")
                self.builder.ret(self.builder.load(none_gv, name="none"))
            else:
                result = self.builder.call(method_fn, forwarded, name="result")
                if isinstance(ret_ty, ir.PointerType):
                    self.builder.ret(result)
                else:
                    boxed = marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        result,
                        ast_fd.return_ty if ast_fd is not None else DynType(name="dyn"),
                    )
                    self.builder.ret(boxed)
            self.builder = saved_builder

        if cache:
            cache_name = (
                f"user_{(self.ast_module.name or 'mod').replace('.', '_')}"
                f"_{owner_info.name}_{method_name}_unbound_method_attr_value_cache"
            )
            return self._emit_cached_zero_capture_func_value(
                adapter,
                cache_name,
                f"unbound.{method_name}.func",
                method_name,
            )

        captures = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh("unbound.method.captures"),
        )
        fn_obj = self.builder.call(
            self.runtime["py_func_new_named"],
            [adapter, captures, self._attr_name_ptr(method_name)],
            name=self._fresh(f"unbound.{method_name}.func"),
        )
        self._gc_release(captures)
        return fn_obj

    def _maybe_emit_valueclass_payload_attr(self, expr: Attr):
        if not isinstance(expr.obj, Name):
            return None
        slot = self.env.get(expr.obj.ident)
        if slot is None:
            return None
        alloca, _ir_ty, declared_ty = slot
        if not self._is_valueclass_payload_type(declared_ty):
            return None
        field_info = self._valueclass_field_info(declared_ty, expr.name)
        if field_info is None:
            return None
        field_idx, _field_ty = field_info
        payload = self.builder.load(
            alloca,
            name=self._fresh(f"value.{expr.obj.ident}.payload"),
        )
        return self.builder.extract_value(
            payload,
            [field_idx],
            name=self._fresh(f"value.{expr.obj.ident}.{expr.name}"),
        )

    def _maybe_emit_valueclass_payload_attr_from_dyn(self, expr: Attr):
        if not isinstance(expr.obj, Name):
            return None
        if not hasattr(self, "class_lowering"):
            return None
        if expr.name is None:
            return None
        candidates: list[tuple[object, int]] = []
        hinted = self.env_class_hint.get(expr.obj.ident)
        if hinted is not None:
            info = self.class_lowering.classes.get(hinted)
            if info is not None and info.valueclass and expr.name in info.field_names:
                candidates.append((info, info.field_names.index(expr.name)))
        if not candidates:
            for info in self.class_lowering.classes.values():
                if not info.valueclass:
                    continue
                if expr.name in info.field_names:
                    candidates.append((info, info.field_names.index(expr.name)))
        if not candidates:
            return None
        obj = self._emit_expr(expr.obj)
        if not isinstance(obj.type, ir.PointerType):
            return None

        selected = ir.Constant(_CSTR, None)
        has_match = ir.Constant(_I1, 0)
        for info, field_idx in candidates:
            cls_ptr = self.builder.load(
                info.global_var,
                name=self._fresh(f"value.{info.name}.class"),
            )
            is_instance = self.builder.call(
                self.runtime["py_obj_isinstance"],
                [obj, cls_ptr],
                name=self._fresh(f"value.{expr.obj.ident}.class-match.{info.name}"),
            )
            is_instance_i1 = self.builder.icmp_unsigned(
                "!=",
                is_instance,
                ir.Constant(_I64, 0),
                name=self._fresh(f"value.{expr.obj.ident}.{info.name}.isinstance"),
            )
            value_field = self.builder.call(
                self.runtime["py_valuebox_get_field"],
                [obj, ir.Constant(_I32, field_idx)],
                name=self._fresh(f"value.{expr.obj.ident}.{expr.name}.{info.name}.field"),
            )
            take = self.builder.and_(
                is_instance_i1,
                self.builder.not_(has_match),
                name=self._fresh(f"value.{expr.obj.ident}.{expr.name}.{info.name}.take"),
            )
            selected = self.builder.select(take, value_field, selected)
            has_match = self.builder.or_(has_match, is_instance_i1)
        fallback = self.builder.call(
            self.runtime["py_obj_getattr"],
            [obj, self._attr_name_ptr(expr.name)],
            name=self._fresh(f"value.{expr.obj.ident}.{expr.name}.fallback"),
        )
        result = self.builder.select(
            has_match,
            selected,
            fallback,
            name=self._fresh(f"value.{expr.obj.ident}.{expr.name}.picked"),
        )
        self._emit_attribute_error_if_null(result, expr.name, expr.span)
        if isinstance(expr.ty, (IntType, FloatType, BoolType)):
            return marshal.marshal_from_object(
                self.builder,
                self.module,
                self.runtime,
                result,
                expr.ty,
            )
        return result

    def _emit_attr(self, expr: Attr) -> ir.Value:
        inspect_alias_attr = self._maybe_emit_inspect_alias_attr(expr)
        if inspect_alias_attr is not None:
            return inspect_alias_attr
        typing_alias_name = self._maybe_emit_typing_alias_name_attr(expr)
        if typing_alias_name is not None:
            return typing_alias_name
        enum_member_attr = self._maybe_emit_enum_member_attr(expr)
        if enum_member_attr is not None:
            return enum_member_attr
        if (
            isinstance(expr.obj, Attr)
            and isinstance(expr.obj.obj, Name)
            and expr.obj.name == "version_info"
            and self._native_builtin_module_for_name(expr.obj.obj.ident) == "sys"
        ):
            version_part = self._emit_sys_version_info_attr(expr.name)
            if version_part is not None:
                return version_part
        native_module_type_name = self._maybe_emit_native_module_type_name(expr)
        if native_module_type_name is not None:
            return native_module_type_name
        pathlib_suffix_attr = self._emit_native_pathlib_suffix_attr(expr)
        if pathlib_suffix_attr is not None:
            return pathlib_suffix_attr
        re_compile_method_attr = self._emit_native_re_compile_method_attr(expr)
        if re_compile_method_attr is not None:
            return re_compile_method_attr
        if self._native_builtin_value_kind_for_expr(expr.obj) == "os.path":
            # POSIX posixpath module constants (pcc targets POSIX). These are
            # plain literals — emitting them natively keeps ``os.path.<const>``
            # off the libpython fallback (generic B-P0-PKG fallback shrink).
            if expr.name == "sep":
                return self._emit_str_literal("/")
            if expr.name == "extsep":
                return self._emit_str_literal(".")
            if expr.name == "pathsep":
                return self._emit_str_literal(":")
            # NOTE: ``os.path.defpath`` is intentionally NOT lowered — its value
            # is platform/build-variable (observed ``/bin:/usr/bin`` here vs the
            # CPython posixpath source literal ``:/bin:/usr/bin``), so a
            # hardcoded constant would silently diverge from the host. Leave it
            # to fall back rather than emit a possibly-wrong value.
            if expr.name == "devnull":
                return self._emit_str_literal("/dev/null")
            if expr.name == "curdir":
                return self._emit_str_literal(".")
            if expr.name == "pardir":
                return self._emit_str_literal("..")
            if expr.name == "altsep":
                return self._emit_none_literal()
        if (
            expr.name == "__name__"
            and isinstance(expr.obj, Call)
            and isinstance(expr.obj.func, Name)
            and expr.obj.func.ident == "type"
            and len(expr.obj.args) == 1
            and not self._expr_looks_cpython(expr.obj.args[0])
        ):
            # NOTE: skip this native ``type(x).__name__`` fast path when ``x`` is
            # a CPython value (e.g. a numpy array): ``py_obj_type_name`` uses
            # pcc's native type model and mishandles a real CPython object,
            # silently failing. For cpy args fall through to the cpy Call-receiver
            # branch below, which routes ``type(a).__name__`` via libpython
            # (``py_cpy_getattr`` on the real type object). Inert in no-libpython.
            type_name = self._static_runtime_type_name(expr.obj.args[0].ty)
            if type_name is not None:
                return self._emit_str_literal(type_name)
            obj_val = self._emit_as_object(expr.obj.args[0])
            return self.builder.call(
                self.runtime["py_obj_type_name"],
                [obj_val],
                name=self._fresh("type.name"),
            )
        module_object_export = self._native_module_object_export_info(
            expr.obj,
            expr.name,
        )
        if module_object_export is not None:
            module_name, info = module_object_export
            return self._emit_native_module_export_value(
                module_name,
                expr.name,
                info,
            )
        if expr.name == "__annotations__" and isinstance(expr.obj, Name):
            annotations = self._emit_function_annotations_dict(expr.obj.ident)
            if annotations is not None:
                return annotations
        valueclass_attr = self._maybe_emit_valueclass_payload_attr(expr)
        if valueclass_attr is not None:
            return valueclass_attr
        valueclass_dyn_attr = self._maybe_emit_valueclass_payload_attr_from_dyn(expr)
        if valueclass_dyn_attr is not None:
            return valueclass_dyn_attr
        alias_export = self._native_module_expr_export_info(
            expr.obj,
            expr.name,
        )
        if alias_export is not None:
            module_name, info = alias_export
            kind = info.get("kind")
            if kind == "class":
                if isinstance(expr.obj, Name):
                    class_info = self._ensure_native_module_alias_class_export(
                        expr.obj.ident,
                        expr.name,
                    )
                else:
                    class_info = None
                if class_info is not None:
                    return self.builder.load(
                        class_info.global_var,
                        name=self._fresh(f"cls.{expr.name}"),
                    )
            elif kind == "function":
                return self._declare_extern_user_function(
                    module_name,
                    expr.name,
                    info,
                )
            elif kind == "constant":
                return self._emit_native_module_constant_or_override(
                    module_name,
                    expr.name,
                    info,
                )
            elif kind == "module_global":
                # ``mod.attr`` where ``attr`` is a module-level variable
                # (e.g. ``__all__ = [...]``) of a sibling-compiled module.
                # Without this branch the Attr lowering fell through to
                # the generic ``py_obj_getattr(<module_name_string>, attr)``
                # path, which failed because the module-alias is the
                # module's NAME STRING in pcc's compile (a string has no
                # ``__all__``).  Route through the existing
                # ``.modvar.<mod>.<attr>`` extern emitted by
                # ``_declare_native_module_extern_global``.  Surfaced by
                # numpy/__init__.py:681 ``set(_mat.__all__)`` where ``_mat``
                # aliases ``numpy.matrixlib``.  See investigation
                # ``docs/investigations/python-native-module-alias-module-global-attr-attribute-error.md``.
                return self._emit_native_module_global_attr_load(
                    module_name,
                    expr.name,
                    info,
                    expr.span,
                )
        if isinstance(expr.obj, Name):
            alias_export = self._native_module_alias_export_info(
                expr.obj.ident,
                expr.name,
            )
            if alias_export is not None:
                module_name, info = alias_export
                kind = info.get("kind")
                if kind == "class":
                    class_info = self._ensure_native_module_alias_class_export(
                        expr.obj.ident,
                        expr.name,
                    )
                    if class_info is not None:
                        return self.builder.load(
                            class_info.global_var,
                            name=self._fresh(f"cls.{expr.name}"),
                        )
                elif kind == "function":
                    return self._declare_extern_user_function(
                        module_name,
                        expr.name,
                        info,
                    )
                elif kind == "constant":
                    return self._emit_native_module_constant_or_override(
                        module_name,
                        expr.name,
                        info,
                    )
                elif kind == "module_global":
                    # Same fix as above; see comment there.
                    return self._emit_native_module_global_attr_load(
                        module_name,
                        expr.name,
                        info,
                        expr.span,
                    )
            builtin_module = self._native_builtin_module_for_name(expr.obj.ident)
            if builtin_module is not None:
                builtin_attr = self._emit_native_builtin_module_attr(
                    builtin_module,
                    expr.name,
                )
                if builtin_attr is not None:
                    return builtin_attr
                module_attr = self._emit_native_module_attr_load(
                    builtin_module,
                    expr.name,
                    expr.span,
                )
                if module_attr is not None:
                    return module_attr
                if builtin_module == "math":
                    if expr.name in ("pi", "e", "tau", "inf", "nan"):
                        return self._emit_native_module_constant(
                            {
                                "value_kind": "float",
                                "value": {
                                    "pi": 3.141592653589793,
                                    "e": 2.718281828459045,
                                    "tau": 6.283185307179586,
                                    "inf": _MATH_INF,
                                    "nan": _MATH_NAN,
                                }[expr.name],
                            },
                        )
                if builtin_module == "string":
                    if expr.name == "ascii_lowercase":
                        return self._emit_str_literal("abcdefghijklmnopqrstuvwxyz")
                    if expr.name == "ascii_uppercase":
                        return self._emit_str_literal("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                    if expr.name == "ascii_letters":
                        return self._emit_str_literal(
                            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        )
                    if expr.name == "digits":
                        return self._emit_str_literal("0123456789")
                    if expr.name == "hexdigits":
                        return self._emit_str_literal("0123456789abcdefABCDEF")
                    if expr.name == "octdigits":
                        return self._emit_str_literal("01234567")
                    if expr.name == "punctuation":
                        return self._emit_str_literal(
                            "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
                        )
                    if expr.name == "whitespace":
                        return self._emit_str_literal(" \t\n\r\x0b\x0c")
                    if expr.name == "printable":
                        return self._emit_str_literal(
                            "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ "
                            "\t\n\r\x0b\x0c"
                        )
                if builtin_module == "re":
                    _RE_CONSTS = {
                        "I": 2,
                        "IGNORECASE": 2,
                        "M": 8,
                        "MULTILINE": 8,
                        "S": 16,
                        "DOTALL": 16,
                    }
                    if expr.name in _RE_CONSTS:
                        return self.builder.call(
                            self.runtime["py_int_from_i64"],
                            [ir.Constant(_I64, _RE_CONSTS[expr.name])],
                            name=self._fresh(f"re.{expr.name}"),
                        )
                if builtin_module == "sys" and expr.name == "argv":
                    return self._emit_program_argv_list()
                if builtin_module == "sys" and expr.name == "executable":
                    return self.builder.call(
                        self.runtime["py_sys_executable_str"],
                        [],
                        name=self._fresh("sys.executable"),
                    )
                if builtin_module == "sys" and expr.name in ("prefix", "base_prefix"):
                    return self.builder.call(
                        self.runtime["py_sys_prefix_str"],
                        [
                            ir.Constant(
                                _I64,
                                1 if expr.name == "base_prefix" else 0,
                            )
                        ],
                        name=self._fresh(f"sys.{expr.name}"),
                    )
                if builtin_module == "sys" and expr.name == "platform":
                    return self.builder.call(
                        self.runtime["py_sys_platform_str"],
                        [],
                        name=self._fresh("sys.platform"),
                    )
                if builtin_module == "sys" and expr.name == "path":
                    # Closed-world ``sys.path`` — a list containing cwd
                    # as its only entry. Sufficient for ``sys.path[0]`` /
                    # ``len(sys.path)`` style probes; full CPython
                    # site-packages discovery would require boot-time
                    # import-machinery wiring that pcc's closed-world
                    # programs don't run.
                    return self.builder.call(
                        self.runtime["py_sys_path_list"],
                        [],
                        name=self._fresh("sys.path"),
                    )
                if builtin_module == "sys" and expr.name == "version_info":
                    return self._emit_sys_version_info_tuple()
                if builtin_module == "gc" and expr.name == "garbage":
                    return self._emit_gc_garbage()
                if builtin_module == "gc" and expr.name == "callbacks":
                    return self.builder.call(
                        self.runtime["py_gc_callbacks_list"],
                        [],
                        name=self._fresh("gc.callbacks"),
                    )
                if builtin_module == "os":
                    # POSIX access(2) mode constants — same on every
                    # platform pcc supports (F_OK=0, X_OK=1, W_OK=2,
                    # R_OK=4). Emit as direct PyInt constants instead
                    # of routing through CPython's os module.
                    _OS_ACCESS_CONSTS = {
                        "F_OK": 0,
                        "X_OK": 1,
                        "W_OK": 2,
                        "R_OK": 4,
                    }
                    if expr.name in _OS_ACCESS_CONSTS:
                        return self.builder.call(
                            self.runtime["py_int_from_i64"],
                            [ir.Constant(_I64, _OS_ACCESS_CONSTS[expr.name])],
                            name=self._fresh(f"os.{expr.name}"),
                        )
                    # `os.sep` — POSIX-only platforms keep this as "/"
                    # (Windows isn't a target). Emit the string literal
                    # directly so pipeline.py / cli code can self-host
                    # without dragging libpython for one constant.
                    if expr.name == "sep":
                        return self._emit_str_literal("/")
                    if expr.name == "linesep":
                        return self._emit_str_literal("\n")
                    if expr.name == "altsep":
                        return self._emit_none_literal()
                    if expr.name == "pathsep":
                        return self._emit_str_literal(":")
                return self._emit_cpy_attr(
                    self._emit_cpython_module_value(builtin_module),
                    expr.name,
                )
            if (
                hasattr(self, "class_lowering")
                and expr.obj.ident in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[expr.obj.ident]
                metaclass_descr = self._emit_metaclass_data_descriptor_get(
                    info,
                    expr.name,
                )
                if metaclass_descr is not None:
                    if isinstance(expr.ty, (IntType, FloatType, BoolType)):
                        return marshal.marshal_from_object(
                            self.builder,
                            self.module,
                            self.runtime,
                            metaclass_descr,
                            expr.ty,
                        )
                    return metaclass_descr
                method_owner = self._resolve_method_mro(info.name, expr.name)
                class_attr_known = (
                    self.class_lowering.lookup_class_attr(info, expr.name) is not None
                )
                class_attr_state = getattr(
                    self,
                    "_class_attr_runtime_state",
                    {},
                ).get((info.name, expr.name))
                class_attr_runtime_candidate = (
                    class_attr_known
                    or class_attr_state == "live"
                    or class_attr_state == "unknown"
                    or class_attr_state == "deleted"
                )
                if (
                    method_owner is not None
                    and expr.name in method_owner.methods
                ):
                    method_kind = method_owner.method_kinds.get(expr.name, "instance")
                    method_fn = method_owner.methods[expr.name]
                    if class_attr_runtime_candidate:
                        if method_kind == "static":
                            static_fallback = self._emit_static_method_value(
                                method_owner,
                                expr.name,
                                method_fn,
                                cache=False,
                            )
                        elif method_kind == "classmethod":
                            receiver_cls = self.builder.load(
                                info.global_var,
                                name=self._fresh(
                                    f"cls.{expr.obj.ident}.method.recv"
                                ),
                            )
                            static_fallback = self._emit_bound_class_method_value(
                                method_owner,
                                expr.name,
                                method_fn,
                                receiver_cls,
                            )
                        else:
                            static_fallback = self._emit_unbound_instance_method_value(
                                method_owner,
                                expr.name,
                                method_fn,
                                cache=False,
                            )
                        cls_obj = self.builder.load(
                            info.global_var,
                            name=self._fresh(f"cls.{expr.obj.ident}.attr.recv"),
                        )
                        runtime_value = self.builder.call(
                            self.runtime["py_obj_getattr"],
                            [cls_obj, self._attr_name_ptr(expr.name)],
                            name=self._fresh(f"cls.{expr.obj.ident}.{expr.name}"),
                        )
                        self._emit_attribute_error_if_null(
                            runtime_value,
                            expr.name,
                            expr.span,
                        )
                        raw_method_ptr = self.builder.bitcast(
                            method_fn,
                            _CSTR,
                            name=self._fresh(f"raw.{expr.name}.method"),
                        )
                        runtime_is_raw_method = self.builder.icmp_unsigned(
                            "==",
                            runtime_value,
                            raw_method_ptr,
                            name=self._fresh(f"raw.{expr.name}.selected"),
                        )
                        result = self.builder.select(
                            runtime_is_raw_method,
                            static_fallback,
                            runtime_value,
                            name=self._fresh(f"cls.{expr.obj.ident}.{expr.name}.value"),
                        )
                        if isinstance(expr.ty, (IntType, FloatType, BoolType)):
                            return marshal.marshal_from_object(
                                self.builder,
                                self.module,
                                self.runtime,
                                result,
                                expr.ty,
                            )
                        return result
                    if method_kind == "static":
                        return self._emit_static_method_value(
                            method_owner,
                            expr.name,
                            method_fn,
                        )
                    elif method_kind == "classmethod":
                        receiver_cls = self.builder.load(
                            info.global_var,
                            name=self._fresh(f"cls.{expr.obj.ident}.method.recv"),
                        )
                        return self._emit_bound_class_method_value(
                            method_owner,
                            expr.name,
                            method_fn,
                            receiver_cls,
                        )
                    else:
                        return self._emit_unbound_instance_method_value(
                            method_owner,
                            expr.name,
                            method_fn,
                        )
                cls_obj = self.builder.load(
                    info.global_var,
                    name=self._fresh(f"cls.{expr.obj.ident}.attr.recv"),
                )
                result = self.builder.call(
                    self.runtime["py_obj_getattr"],
                    [cls_obj, self._attr_name_ptr(expr.name)],
                    name=self._fresh(f"cls.{expr.obj.ident}.{expr.name}"),
                )
                self._emit_attribute_error_if_null(result, expr.name, expr.span)
                if isinstance(expr.ty, (IntType, FloatType, BoolType)):
                    return marshal.marshal_from_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        result,
                        expr.ty,
                    )
                return result
        # CPython-backed fast path: if the object evaluates to a
        # CPython ``PyObject*`` (either bound directly via a Name in
        # _cpy_module_env / _cpy_env_flags, or through a nested
        # ``a.b.c`` chain where an inner node is CPython), route the
        # attribute load through py_cpy_getattr. Otherwise fall
        # through to the pcc native path.
        if isinstance(expr.obj, Name):
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(expr.obj.ident)
            if cpy_gv is not None:
                mod_val = self.builder.load(
                    cpy_gv, name=self._fresh(f"cpy.{expr.obj.ident}")
                )
                return self._emit_cpy_attr(mod_val, expr.name)
            if getattr(self, "_cpy_env_flags", {}).get(expr.obj.ident, False):
                obj_val = self._emit_expr(expr.obj)
                return self._emit_cpy_attr(obj_val, expr.name)
        if isinstance(expr.obj, (Attr, Subscript, Call)):
            chain_val = self._emit_expr(expr.obj)
            if chain_val in getattr(self, "_cpy_values", ()):
                return self._emit_cpy_attr(chain_val, expr.name)
        if isinstance(expr.obj, BinOp) and self._expr_looks_cpython(expr.obj):
            # Attribute load on a BINARY-OP result that is itself a CPython value
            # (e.g. ``(a + b).dtype`` on numpy arrays): ``py_cpy_binop`` yields a
            # real CPython object, so the attribute must load via libpython. The
            # ``(Attr, Subscript, Call)`` branch above does not cover ``BinOp``;
            # gate STRUCTURALLY on ``_expr_looks_cpython`` (an operand is cpy) so
            # a non-cpy BinOp receiver is NOT pre-emitted here (avoids a double
            # eval of the binop in the native fall-through). Inert in no-libpython
            # (no CPython modules => never looks cpy => bootstrap unaffected).
            # Mirrors the BinOp-receiver method-call branch.
            chain_val = self._emit_expr(expr.obj)
            if chain_val in getattr(self, "_cpy_values", ()):
                return self._emit_cpy_attr(chain_val, expr.name)

        # Property getter fast path: if the attribute is a @property on
        # a hinted class, dispatch to the getter function.
        if isinstance(expr.obj, Name):
            hint = self.env_class_hint.get(expr.obj.ident)
            if hint is not None:
                info = self._resolve_property_mro(hint, expr.name)
                if info is not None:
                    getter = info.properties[expr.name]
                    obj_val = self._emit_expr(expr.obj)
                    return self.builder.call(
                        getter,
                        [obj_val],
                        name=self._fresh(f"prop.{expr.name}"),
                    )
                data_descr = self._class_attr_descriptor_class(
                    hint,
                    expr.name,
                )
                if data_descr is not None:
                    _owner_info, desc_info = data_descr
                    if "__get__" in desc_info.methods and (
                        "__set__" in desc_info.methods
                        or "__delete__" in desc_info.methods
                    ):
                        obj_val = self._emit_expr(expr.obj)
                        got = self._emit_data_descriptor_get(
                            hint,
                            expr.name,
                            obj_val,
                        )
                        if got is not None:
                            return got
                    # Non-data descriptors are lower priority than the
                    # instance dictionary. Let the generic runtime path
                    # try the instance dict instead of returning the
                    # class attribute object directly.
                    if "__get__" in desc_info.methods:
                        class_info = None
                    else:
                        class_info = self.class_lowering.classes.get(hint)
                else:
                    class_info = self.class_lowering.classes.get(hint)
                if class_info is not None:
                    class_attr = self.class_lowering.emit_class_attr_load(
                        class_info,
                        expr.name,
                    )
                    class_attr_state = getattr(
                        self,
                        "_class_attr_runtime_state",
                        {},
                    ).get((class_info.name, expr.name))
                    class_attr_runtime_candidate = class_attr_state in (
                        "live",
                        "unknown",
                        "deleted",
                    )
                    if class_attr is not None and not class_attr_runtime_candidate:
                        return class_attr

        # Fast path for ``self.<attr>`` inside a method body: use the
        # declared-field index when known, otherwise fall through to the
        # generic ``py_obj_getattr`` call.
        current_class = self.current_class
        if (
            current_class is not None
            and isinstance(expr.obj, Name)
            and expr.obj.ident == "self"
        ):
            receiver_class_name = self._self_receiver_class_name()
            receiver_info = None
            if receiver_class_name is not None:
                receiver_info = self.class_lowering.classes.get(receiver_class_name)
            if receiver_info is None:
                receiver_info = current_class
            # self.<prop> — dispatch to getter when present.
            info_p = self._resolve_property_mro(receiver_info.name, expr.name)
            if info_p is not None:
                getter = info_p.properties[expr.name]
                self_val = self.builder.load(
                    self.env["self"][0], name=self._fresh("self")
                )
                return self.builder.call(
                    getter,
                    [self_val],
                    name=self._fresh(f"self.prop.{expr.name}"),
                )
            self_val = self.builder.load(self.env["self"][0], name=self._fresh("self"))
            method_fn = receiver_info.methods.get(expr.name)
            if method_fn is not None:
                return self._emit_bound_self_method_value(
                    receiver_info,
                    expr.name,
                    method_fn,
                    self_val,
                )
            return self.class_lowering.emit_self_attr_load(
                receiver_info, expr.name, self_val
            )
        if (
            current_class is not None
            and isinstance(expr.obj, Name)
            and expr.obj.ident == "cls"
            and self.current_method_kind == "classmethod"
        ):
            cls_val = self._emit_expr(expr.obj)
            result = self.builder.call(
                self.runtime["py_obj_getattr"],
                [cls_val, self._attr_name_ptr(expr.name)],
                name=self._fresh(f"cls.attr.{expr.name}"),
            )
            self._emit_attribute_error_if_null(result, expr.name, expr.span)
            if isinstance(expr.ty, (IntType, FloatType, BoolType)):
                return marshal.marshal_from_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    result,
                    expr.ty,
                )
            return result

        if isinstance(expr.obj, Name):
            hint = self._class_hint_for_expr(expr.obj)
            if hint is not None:
                class_info = self.class_lowering.classes.get(hint)
                if (
                    class_info is not None
                    and self.class_lowering.lookup_field_index(
                        class_info,
                        expr.name,
                    )
                    is not None
                ):
                    obj_val = self._emit_expr(expr.obj)
                    return self.class_lowering.emit_self_attr_load(
                        class_info,
                        expr.name,
                        obj_val,
                    )

        obj = self._emit_expr(expr.obj)
        # Any object goes through py_obj_getattr; if the object is
        # ``None`` at runtime the runtime lib raises AttributeError —
        # that's the correct Python semantic (no segfault).
        name_ptr = self._attr_name_ptr(expr.name)
        result = self.builder.call(
            self.runtime["py_obj_getattr"],
            [obj, name_ptr],
            name=self._fresh(f"attr.{expr.name}"),
        )
        self._emit_attribute_error_if_null(result, expr.name, expr.span)
        if isinstance(expr.ty, (IntType, FloatType, BoolType)):
            return marshal.marshal_from_object(
                self.builder,
                self.module,
                self.runtime,
                result,
                expr.ty,
            )
        return result
