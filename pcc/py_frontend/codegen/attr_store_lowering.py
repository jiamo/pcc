"""Attribute-store lowering helpers for L1CodeGen."""

from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, DynType, Expr, IntType, Name, Type
from . import marshal


_I32 = ir.IntType(32)


class AttrStoreLoweringMixin:
    def _typed_instance_field_slot(self, target: Attr):
        """Slot index for ``<Name>.<field>`` when the receiver's class is
        statically known, the field is a declared instance field, and neither
        the class nor any base overrides ``__setattr__``; else ``None``."""
        if not isinstance(target.obj, Name) or not hasattr(self, "class_lowering"):
            return None
        if getattr(self, "_cpy_env_flags", {}).get(target.obj.ident, False):
            return None
        hint = self._class_hint_for_expr(target.obj)
        if hint is None:
            return None
        classes = self.class_lowering.classes
        info = classes.get(hint)
        if info is None:
            return None
        seen: set[str] = set()
        pending = [info]
        while pending:
            current = pending.pop()
            if current.name in seen:
                continue
            seen.add(current.name)
            if "__setattr__" in getattr(current, "methods", {}):
                return None
            for base in getattr(current, "bases_ast", ()) or ():
                base_name = getattr(base, "ident", None)
                base_info = classes.get(base_name) if base_name else None
                if base_info is not None:
                    pending.append(base_info)
        return self.class_lowering.lookup_field_index(info, target.name)

    def _emit_attr_store_value(
        self,
        target: Attr,
        value: ir.Value,
        value_ty: Type,
    ) -> None:
        runtime_attr_name = target.name
        lexical_class = self.current_class
        if lexical_class is not None and hasattr(self, "class_lowering"):
            runtime_attr_name = self.class_lowering.mangle_private_attr_name(
                lexical_class,
                target.name,
            )
        if isinstance(target.obj, Name):
            if (
                hasattr(self, "class_lowering")
                and target.obj.ident in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[target.obj.ident]
                metaclass_descr = self._metaclass_data_descriptor_info(
                    info,
                    target.name,
                )
                if metaclass_descr is not None:
                    value_obj = marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        value,
                        value_ty,
                    )
                    if self._emit_metaclass_data_descriptor_set(
                        info,
                        target.name,
                        value_obj,
                    ):
                        return
                if self.class_lowering.emit_class_attr_store(
                    info,
                    target.name,
                    value,
                    value_ty,
                ):
                    if not hasattr(self, "_class_attr_runtime_state"):
                        self._class_attr_runtime_state = {}
                    if getattr(self, "_class_attr_mutation_in_loop_depth", 0):
                        state = "unknown"
                    else:
                        state = "live"
                    self._class_attr_runtime_state[(info.name, target.name)] = state
                    return
            if (
                self.current_class is not None
                and target.obj.ident == "cls"
                and self.current_method_kind == "classmethod"
            ):
                cls_obj = self._emit_expr(target.obj)
                value_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    value,
                    value_ty,
                )
                status = self.builder.call(
                    self.runtime["py_obj_setattr"],
                    [cls_obj, self._attr_name_ptr(runtime_attr_name), value_obj],
                    name=self._fresh(f"cls.setattr.{target.name}.rc"),
                )
                self._emit_attribute_error_if_status_failed(
                    status,
                    target.name,
                    target.span,
                )
                return
            builtin_module = self._native_builtin_module_for_name(target.obj.ident)
            if builtin_module is not None:
                value_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    value,
                    value_ty,
                )
                gv = self._native_module_attr_global(builtin_module, target.name)
                old_value = self.builder.load(
                    gv,
                    name=self._fresh(f"modattr.{target.name}.old"),
                )
                self._gc_unpin(old_value)
                self._gc_pin(value_obj)
                self.builder.call(
                    self.runtime["pcc_gc_store_root"],
                    [
                        self._as_gc_ptr(
                            gv,
                            name=self._fresh(f"modattr.{target.name}.slot"),
                        ),
                        value_obj,
                    ],
                )
                return
            native_module = getattr(
                self,
                "_native_module_aliases",
                {},
            ).get(target.obj.ident)
            if native_module is not None:
                value_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    value,
                    value_ty,
                )
                gv = self._native_module_attr_global(native_module, target.name)
                old_value = self.builder.load(
                    gv,
                    name=self._fresh(f"modattr.{target.name}.old"),
                )
                self._gc_unpin(old_value)
                self._gc_pin(value_obj)
                self.builder.call(
                    self.runtime["pcc_gc_store_root"],
                    [
                        self._as_gc_ptr(
                            gv,
                            name=self._fresh(f"modattr.{target.name}.slot"),
                        ),
                        value_obj,
                    ],
                )
                return
        # Property setter fast path.
        if isinstance(target.obj, Name):
            hint = self.env_class_hint.get(target.obj.ident)
            if hint is not None:
                hint_info = self.class_lowering.classes.get(hint)
                in_init = (
                    self.current_func_def is not None
                    and self.current_func_def.name == "__init__"
                    and self.current_class is hint_info
                )
                if (
                    hint_info is not None
                    and getattr(hint_info, "dataclass_frozen", False)
                    and target.name in hint_info.field_names
                    and not in_init
                ):
                    self._emit_builtin_exception_and_branch(
                        "AttributeError",
                        "cannot assign to field",
                        target.span,
                    )
                    return
                info = self._resolve_property_setter_mro(hint, target.name)
                if info is not None:
                    setter_fn = info.property_setters[target.name]
                    obj_val = self._emit_expr(target.obj)
                    if len(setter_fn.args) >= 2:
                        param_ty = setter_fn.args[1].type
                        if isinstance(param_ty, ir.IntType) and param_ty.width == 64:
                            value = self._coerce(value, value_ty, IntType(name="int"))
                        elif isinstance(param_ty, ir.PointerType):
                            value = marshal.marshal_to_object(
                                self.builder,
                                self.module,
                                self.runtime,
                                value,
                                value_ty,
                            )
                    self._call_user(setter_fn, [obj_val, value], "")
                    return
                if self._resolve_property_mro(hint, target.name) is not None:
                    self._emit_builtin_exception_and_branch(
                        "AttributeError",
                        "can't set attribute",
                        target.span,
                    )
                    return
                data_descr = self._class_attr_descriptor_class(
                    hint,
                    target.name,
                )
                if data_descr is not None:
                    _owner_info, desc_info = data_descr
                    if "__set__" in desc_info.methods:
                        obj_val = self._emit_expr(target.obj)
                        value_obj = marshal.marshal_to_object(
                            self.builder,
                            self.module,
                            self.runtime,
                            value,
                            value_ty,
                        )
                        if self._emit_data_descriptor_set(
                            hint,
                            target.name,
                            obj_val,
                            value_obj,
                        ):
                            return

        current_class = self.current_class
        if (
            current_class is not None
            and isinstance(target.obj, Name)
            and target.obj.ident == "self"
        ):
            receiver_class_name = self._self_receiver_class_name()
            receiver_info = None
            if receiver_class_name is not None:
                receiver_info = self.class_lowering.classes.get(receiver_class_name)
            if receiver_info is None:
                receiver_info = current_class
            in_init = (
                self.current_func_def is not None
                and self.current_func_def.name == "__init__"
            )
            if (
                getattr(receiver_info, "dataclass_frozen", False)
                and target.name in receiver_info.field_names
                and not in_init
            ):
                self._emit_builtin_exception_and_branch(
                    "AttributeError",
                    "cannot assign to field",
                    target.span,
                )
                return
            self_val = self.builder.load(self.env["self"][0], name=self._fresh("self"))
            value = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                value,
                value_ty,
            )
            status = self.class_lowering.emit_self_attr_store(
                receiver_info, target.name, self_val, value
            )
            if status is not None:
                self._emit_attribute_error_if_status_failed(
                    status,
                    target.name,
                    target.span,
                )
            return
        obj = self._emit_expr(target.obj)
        name_ptr = self._attr_name_ptr(runtime_attr_name)
        class_object_hint = None
        if isinstance(target.obj, Name):
            class_object_hint = getattr(self, "env_class_object_hint", {}).get(
                target.obj.ident
            )
        if obj in getattr(self, "_cpy_values", ()) or (
            isinstance(target.obj, Name)
            and getattr(self, "_cpy_env_flags", {}).get(
                target.obj.ident,
                False,
            )
        ):
            cpy_value, owned = self._marshal_to_cpython(value, value_ty)
            self.builder.call(
                self.runtime["py_cpy_setattr"], [obj, name_ptr, cpy_value]
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_value])
            return
        value = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            value,
            value_ty,
        )
        slot_index = self._typed_instance_field_slot(target)
        if slot_index is not None:
            # Same primitive ``self.x = v`` has always used: the receiver's
            # class and the field's slot are statically known and no class in
            # the MRO overrides __setattr__, so the string-keyed
            # ``py_obj_setattr`` lookup (3661 instructions per store against
            # CPython's 385) is replaced by a balanced slot store.
            self.builder.call(
                self.runtime["py_instance_set_field"],
                [obj, ir.Constant(_I32, slot_index), value],
            )
            return
        status = self.builder.call(
            self.runtime["py_obj_setattr"],
            [obj, name_ptr, value],
            name=self._fresh(f"setattr.{target.name}.rc"),
        )
        self._emit_attribute_error_if_status_failed(
            status,
            target.name,
            target.span,
        )
        if (
            class_object_hint is not None
            and hasattr(self, "class_lowering")
            and class_object_hint in self.class_lowering.classes
        ):
            if not hasattr(self, "_class_attr_runtime_state"):
                self._class_attr_runtime_state = {}
            state = (
                "unknown"
                if getattr(self, "_class_attr_mutation_in_loop_depth", 0)
                else "live"
            )
            info = self.class_lowering.classes[class_object_hint]
            self._class_attr_runtime_state[(info.name, runtime_attr_name)] = state

    def _emit_attr_store(self, target: Attr, value_expr: Expr) -> None:
        prefer_native_callable = (
            isinstance(value_expr, Name) and value_expr.ident in self.functions
        )
        if prefer_native_callable:
            old_prefer_native = self._prefer_native_callable_values
            self._prefer_native_callable_values = True
            try:
                value = self._emit_expr(value_expr)
            finally:
                self._prefer_native_callable_values = old_prefer_native
        else:
            valueclass_payload = self._maybe_emit_valueclass_constructor_payload(
                value_expr.ty,
                value_expr,
            )
            if valueclass_payload is not None:
                boxed_valueclass = self._emit_valueclass_payload_to_object(
                    valueclass_payload,
                    value_expr.ty,
                    consume_fields=True,
                )
                if boxed_valueclass is not None:
                    self._emit_attr_store_value(
                        target,
                        boxed_valueclass,
                        DynType(name="dyn"),
                    )
                    self._gc_release(
                        boxed_valueclass,
                        self._release_expr_label("owned", value_expr),
                    )
                    return
            value = self._emit_expr(value_expr)
        self._emit_attr_store_value(target, value, value_expr.ty)
        self._gc_release_if_owned(value, value_expr)
