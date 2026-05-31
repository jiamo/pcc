"""Assignment storage helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, DynType, Expr, Name, Subscript, TupleExpr, Type
from . import marshal


_I8 = ir.IntType(8)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()


class AssignmentStoreLoweringMixin:
    def _coerce_unpack_name_like(self, lhs: Expr) -> Expr:
        if isinstance(lhs, Name):
            return lhs
        try:
            ident = lhs.ident
        except AttributeError:
            return lhs
        if ident is None:
            return lhs
        try:
            span = lhs.span
        except AttributeError:
            span = None
        try:
            ty = lhs.ty
        except AttributeError:
            ty = DynType(name="dyn")
        return Name(
            span=span,
            ty=ty,
            ident=ident,
        )

    def _store_unpack_target(
        self,
        lhs: Expr,
        value: ir.Value,
        value_ty: Type,
        value_is_owned: Optional[bool] = None,
    ) -> None:
        lhs = self._coerce_unpack_name_like(lhs)
        if value_is_owned is None:
            unpack_value_is_owned = self._unpack_target_value_is_owned(value_ty)
        else:
            unpack_value_is_owned = value_is_owned
        if isinstance(lhs, Subscript):
            self._store_value_at_subscript(
                lhs,
                value,
                value_ty,
                release_value=unpack_value_is_owned,
            )
            return
        if isinstance(lhs, Attr):
            self._store_value_at_attr(
                lhs,
                value,
                value_ty,
                release_value=unpack_value_is_owned,
            )
            return
        if isinstance(lhs, Name):
            slot = self.env.get(lhs.ident)
            target_ty = lhs.ty
            if slot is None and target_ty is not None:
                if self._is_object(target_ty):
                    ir_ty = self._storage_ir_type(target_ty)
                    if isinstance(ir_ty, ir.PointerType) and self._ir_type_matches(
                        ir_ty, _CSTR
                    ):
                        alloca = self._alloca_in_entry(
                            ir_ty,
                            name=f"{lhs.ident}.addr",
                            init_null=True,
                        )
                        self.env[lhs.ident] = (alloca, ir_ty, target_ty)
                        slot = self.env[lhs.ident]
            slot_for_release = slot
            if slot_for_release is not None and unpack_value_is_owned:
                alloca, ir_ty, _decl_ty = slot_for_release
                if (
                    isinstance(ir_ty, ir.PointerType)
                    and self._ir_type_matches(ir_ty, _CSTR)
                    and lhs.ident not in getattr(self, "_current_global_names", set())
                    and lhs.ident not in getattr(self, "_current_param_names", set())
                    and not getattr(self, "_cpy_env_flags", {}).get(lhs.ident, False)
                ):
                    old = self.builder.load(
                        alloca,
                        name=self._fresh(f"unpack.overwrite.{lhs.ident}"),
                    )
                    self._gc_release(old)
            self._store_value_at_name(
                lhs,
                value,
                value_ty,
                value_is_owned=unpack_value_is_owned,
            )
            if not unpack_value_is_owned:
                if slot_for_release is not None:
                    alloca, _ir_ty, _decl_ty = slot_for_release
                    self._discard_owned_local_gc_root(lhs.ident, alloca)
                self._owned_local_names.discard(lhs.ident)
                self._owned_local_has_value.discard(lhs.ident)
            self._mark_owned_local_for_unpack_target(
                lhs,
                value_ty,
                unpack_value_is_owned,
            )
            if lhs.ident in self._owned_local_names:
                self._owned_local_has_value.add(lhs.ident)
            else:
                self._owned_local_has_value.discard(lhs.ident)
            return
        lhs_kind = type(lhs).__name__
        if (
            isinstance(lhs, TupleExpr)
            or lhs_kind == "TupleExpr"
        ):
            for i, sub in enumerate(lhs.elems):
                idx_box = self.builder.call(
                    self.runtime["py_int_from_i64"],
                    [ir.Constant(_I64, i)],
                    name=self._fresh("unpack.nested.idx.box"),
                )
                elem = self.builder.call(
                    self.runtime["py_obj_getitem"],
                    [value, idx_box],
                    name=self._fresh(f"unpack.nested.{i}"),
                )
                self._store_unpack_target(
                    sub,
                    elem,
                    DynType(name="dyn"),
                    value_is_owned=True,
                )
            return
        raise NotImplementedError(
            "Layer 1 tuple-unpack target kind "
            + lhs_kind
            + " not supported"
        )

    def _store_value_at_name(
        self,
        target: Name,
        value: ir.Value,
        value_ty: Type,
        *,
        value_is_owned: bool = False,
    ) -> None:
        """Store a pre-computed SSA value to a local / module global."""
        self.env_class_hint.pop(target.ident, None)
        if not hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags = {}
        if value in getattr(self, "_cpy_values", ()):
            self._cpy_env_flags[target.ident] = True
        else:
            self._cpy_env_flags.pop(target.ident, None)

        module_globals = self._module_globals
        if (
            self.current_func_def is not None
            and target.ident in self._current_global_names
        ):
            self._ensure_module_global_name(target.ident, target.ty)
        if target.ident in module_globals and (
            self.current_func_def is None or target.ident in self._current_global_names
        ):
            gv, declared_ty = module_globals[target.ident]
            value = self._coerce(value, value_ty, declared_ty)
            if value in getattr(self, "_cpy_values", ()):
                self._cpy_module_flags[target.ident] = True
                is_cpy_value = True
            else:
                self._cpy_module_flags.pop(target.ident, None)
                is_cpy_value = False
            self._store_module_global_root_value(
                gv,
                value,
                value_is_owned=value_is_owned,
                is_cpy_value=is_cpy_value,
            )
            return

        slot = self.env.get(target.ident)
        if slot is None:
            target_ty = target.ty
            if not (self._is_scalar(target_ty) or self._is_object(target_ty)):
                raise NotImplementedError(
                    f"Layer 1 tuple-unpack target {target.ident!r} has "
                    f"unsupported type {type(target_ty).__name__}"
                )
            ir_ty = self._storage_ir_type(target_ty)
            init_null = isinstance(ir_ty, ir.PointerType) and self._ir_type_matches(
                ir_ty, _CSTR
            )
            alloca = self._alloca_in_entry(
                ir_ty,
                name=f"{target.ident}.addr",
                init_null=init_null,
            )
            self.env[target.ident] = (alloca, ir_ty, target_ty)
            slot = self.env[target.ident]

        alloca, _ir_ty, declared_ty = slot
        value = self._coerce(value, value_ty, declared_ty)
        self.builder.store(value, alloca)

    def _store_value_at_subscript(
        self,
        target: Subscript,
        value: ir.Value,
        value_ty: Type,
        *,
        release_value: bool = False,
    ) -> None:
        """Runtime subscript store given a pre-computed value."""
        obj = self._emit_expr(target.obj)
        idx_val = self._emit_expr(target.idx)
        v_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            value,
            value_ty,
        )
        k_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            idx_val,
            target.idx.ty,
        )
        self.builder.call(
            self.runtime["py_obj_setitem"],
            [obj, k_obj, v_obj],
        )
        if release_value and isinstance(v_obj.type, ir.PointerType):
            if v_obj not in getattr(self, "_cpy_values", ()):
                self._gc_release(v_obj)

    def _store_value_at_attr(
        self,
        target: Attr,
        value: ir.Value,
        value_ty: Type,
        *,
        release_value: bool = False,
    ) -> None:
        """Runtime attribute store given a pre-computed value."""
        obj = self._emit_expr(target.obj)
        v_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            value,
            value_ty,
        )
        name_ptr = self._attr_name_ptr(target.name)
        status = self.builder.call(
            self.runtime["py_obj_setattr"],
            [obj, name_ptr, v_obj],
            name=self._fresh(f"setattr.{target.name}.rc"),
        )
        self._emit_attribute_error_if_status_failed(
            status,
            target.name,
            target.span,
        )
        if release_value and isinstance(v_obj.type, ir.PointerType):
            if v_obj not in getattr(self, "_cpy_values", ()):
                self._gc_release(v_obj)
