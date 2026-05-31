"""Delete-statement lowering for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, ClassType, Delete, DictType, DynType, IntType, ListType, Name, Slice, Subscript
from . import marshal
from .runtime_abi import declare_runtime_global


_I8 = ir.IntType(8)
_CSTR = _I8.as_pointer()


class DeleteLoweringMixin:
    def _emit_delete(self, stmt: Delete) -> None:
        """Lower ``del x`` / ``del d[k]`` / ``del xs[i]``."""
        for target in stmt.targets:
            if isinstance(target, Name):
                slot_info = self.env.get(target.ident)
                if (
                    target.ident in getattr(self, "_owned_local_names", set())
                    and slot_info is not None
                ):
                    alloca, ir_ty, _decl_ty = slot_info
                    if isinstance(ir_ty, ir.PointerType) and self._ir_type_matches(
                        ir_ty, _CSTR
                    ):
                        if target.ident in self._owned_local_has_value:
                            old = self.builder.load(
                                alloca,
                                name=self._fresh(f"del.{target.ident}"),
                            )
                            self._gc_release(old)
                            self.builder.store(ir.Constant(_CSTR, None), alloca)
                        self._discard_owned_local_gc_root(target.ident, alloca)
                        self._owned_local_names.discard(target.ident)
                        self._owned_local_has_value.discard(target.ident)
                self.env.pop(target.ident, None)
                if hasattr(self, "_cpy_env_flags"):
                    self._cpy_env_flags.pop(target.ident, None)
                if hasattr(self, "env_class_hint"):
                    self.env_class_hint.pop(target.ident, None)
                self._clear_native_module_object_alias(target.ident)
                self._weak_dict_env_flags.pop(target.ident, None)
                continue
            if isinstance(target, Subscript):
                if isinstance(target.idx, Slice):
                    obj = self._emit_expr(target.obj)
                    obj_ty = target.obj.ty
                    if obj not in getattr(self, "_cpy_values", ()):
                        lo_obj = self._emit_slice_bound_object(target.idx.lo)
                        hi_obj = self._emit_slice_bound_object(target.idx.hi)
                        step_obj = self._emit_slice_bound_object(target.idx.step)
                        if isinstance(obj_ty, ListType):
                            self.builder.call(
                                self.runtime["py_list_del_slice"],
                                [obj, lo_obj, hi_obj, step_obj],
                            )
                            return
                        if isinstance(obj_ty, DynType):
                            self.builder.call(
                                self.runtime["py_obj_del_slice"],
                                [obj, lo_obj, hi_obj, step_obj],
                            )
                            return
                        raise NotImplementedError(
                            f"Layer 1 slice delete on type "
                            f"{type(obj_ty).__name__} not supported"
                        )

                    obj_cpy = self.builder.call(
                        self.runtime["py_cpy_from_pcc_obj"],
                        [obj],
                        name=self._fresh("cpy.del.obj"),
                    )
                    slice_fn = self._load_cpython_builtin("slice")

                    def _as_cpy(e):
                        if e is None:
                            gv = declare_runtime_global(
                                self.module,
                                "py_None",
                            )
                            none = self.builder.load(gv, name="none")
                            return self.builder.call(
                                self.runtime["py_cpy_from_pcc_obj"],
                                [none],
                                name=self._fresh("cpy.none"),
                            )
                        v = self._emit_expr(e)
                        obj_v = marshal.marshal_to_object(
                            self.builder,
                            self.module,
                            self.runtime,
                            v,
                            e.ty,
                        )
                        return self.builder.call(
                            self.runtime["py_cpy_from_pcc_obj"],
                            [obj_v],
                            name=self._fresh("cpy.slice.arg"),
                        )

                    lo_cpy = _as_cpy(target.idx.lo)
                    hi_cpy = _as_cpy(target.idx.hi)
                    step_cpy = _as_cpy(target.idx.step)
                    slice_obj = self.builder.call(
                        self.runtime["py_cpy_call3"],
                        [slice_fn, lo_cpy, hi_cpy, step_cpy],
                        name=self._fresh("cpy.slice"),
                    )
                    delitem_gv = self._cstr_global(
                        "__delitem__",
                        ".cpy.attr.__delitem__",
                    )
                    delitem_fn = self.builder.call(
                        self.runtime["py_cpy_getattr"],
                        [obj_cpy, self._ptr_to_cstr(delitem_gv)],
                        name=self._fresh("cpy.delitem.fn"),
                    )
                    self.builder.call(
                        self.runtime["py_cpy_call1"],
                        [delitem_fn, slice_obj],
                        name=self._fresh("cpy.delitem"),
                    )
                    continue
                obj = self._emit_expr(target.obj)
                obj_ty = target.obj.ty
                idx_val = self._emit_expr(target.idx)
                idx_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    idx_val,
                    target.idx.ty,
                )
                if isinstance(obj_ty, DictType):
                    self.builder.call(
                        self.runtime["py_dict_del"],
                        [obj, idx_obj],
                    )
                    continue
                if isinstance(obj_ty, ListType):
                    idx_i64 = marshal.marshal_from_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        idx_obj,
                        IntType(name="int"),
                    )
                    popped = self.builder.call(
                        self.runtime["py_list_pop"],
                        [obj, idx_i64],
                        name=self._fresh("list.del.pop"),
                    )
                    self._gc_release(popped)
                    continue
                if isinstance(obj_ty, (ClassType, DynType)):
                    self.builder.call(
                        self.runtime["py_obj_delitem"],
                        [obj, idx_obj],
                    )
                    continue
                raise NotImplementedError(
                    f"Layer 1 'del' on subscript with container type "
                    f"{type(obj_ty).__name__} not yet wired"
                )
            if isinstance(target, Attr):
                if isinstance(target.obj, Name):
                    if (
                        hasattr(self, "class_lowering")
                        and target.obj.ident in self.class_lowering.classes
                    ):
                        class_info = self.class_lowering.classes[target.obj.ident]
                        if self._emit_metaclass_data_descriptor_delete(
                            class_info,
                            target.name,
                            target.span,
                        ):
                            continue
                    hint = self.env_class_hint.get(target.obj.ident)
                    if hint is not None:
                        info = self._resolve_property_deleter_mro(hint, target.name)
                        if info is not None:
                            deleter_fn = info.property_deleters[target.name]
                            obj_val = self._emit_expr(target.obj)
                            self._call_user(deleter_fn, [obj_val], "")
                            continue
                        if self._resolve_property_mro(hint, target.name) is not None:
                            self._emit_builtin_exception_and_branch(
                                "AttributeError",
                                "can't delete attribute",
                                target.span,
                            )
                            continue
                obj = self._emit_as_object(target.obj)
                status = self.builder.call(
                    self.runtime["py_obj_delattr"],
                    [obj, self._attr_name_ptr(target.name)],
                    name=self._fresh(f"delattr.{target.name}.rc"),
                )
                self._emit_attribute_error_if_status_failed(
                    status,
                    target.name,
                    target.span,
                )
                if (
                    isinstance(target.obj, Name)
                    and hasattr(self, "class_lowering")
                    and target.obj.ident in self.class_lowering.classes
                ):
                    if not hasattr(self, "_class_attr_runtime_state"):
                        self._class_attr_runtime_state = {}
                    class_info = self.class_lowering.classes[target.obj.ident]
                    if getattr(self, "_class_attr_mutation_in_loop_depth", 0):
                        state = "unknown"
                    else:
                        state = "deleted"
                    self._class_attr_runtime_state[
                        (class_info.name, target.name)
                    ] = state
                continue
            raise NotImplementedError(
                f"Layer 1 'del' on {type(target).__name__} target not supported"
            )
