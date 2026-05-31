"""Enum and dynamic type helper lowering for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, Call, DictExpr, Name, StrLit, TupleExpr
from . import marshal


_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()


class DynamicTypeLoweringMixin:
    def _maybe_emit_enum_member_attr(self, expr: Attr) -> Optional[ir.Value]:
        if expr.name not in ("name", "value"):
            return None
        if not (
            isinstance(expr.obj, Attr)
            and isinstance(expr.obj.obj, Name)
        ):
            return None
        class_name = expr.obj.obj.ident
        member_name = expr.obj.name
        info = self.class_lowering.classes.get(class_name)
        if info is None:
            return None
        enum_members = getattr(info, "enum_members", {})
        if member_name not in enum_members:
            return None
        if expr.name == "name":
            return self._emit_str_literal(member_name)
        return ir.Constant(_I64, int(enum_members[member_name]))

    def _maybe_emit_dynamic_type_constructor(self, expr: Call) -> Optional[ir.Value]:
        if expr.kwargs or len(expr.args) != 3:
            return None
        name_expr, bases_expr, ns_expr = expr.args
        if not isinstance(name_expr, StrLit):
            return None
        if not isinstance(bases_expr, TupleExpr):
            return None
        ns_runtime_expr = None
        if isinstance(ns_expr, DictExpr):
            ns_dict_expr = ns_expr
        elif isinstance(ns_expr, Name):
            ns_dict_expr = getattr(self, "_literal_dict_expr_bindings", {}).get(
                ns_expr.ident
            )
            if not isinstance(ns_dict_expr, DictExpr):
                ns_dict_expr = None
                ns_runtime_expr = ns_expr
        else:
            ns_dict_expr = None
            ns_runtime_expr = ns_expr

        base_values: list[ir.Value] = []
        for base in bases_expr.elems:
            if not isinstance(base, Name):
                return None
            base_info = self.class_lowering.classes.get(base.ident)
            if base_info is None:
                return None
            base_values.append(
                self.builder.load(
                    base_info.global_var,
                    name=self._fresh(f"type.base.{base.ident}"),
                )
            )
        if base_values:
            bases_ptr = self.class_lowering._load_bases_array(
                name_expr.value,
                base_values,
            )
            bases_ptr = self.builder.bitcast(
                bases_ptr,
                _CSTR,
                name=self._fresh("type.bases.i8p"),
            )
        else:
            bases_ptr = ir.Constant(_CSTR, None)

        cls_obj = self.builder.call(
            self.runtime["py_class_new"],
            [
                self._attr_name_ptr(name_expr.value),
                bases_ptr,
                ir.Constant(_I32, len(base_values)),
                ir.Constant(_CSTR, None),
                ir.Constant(_I32, 0),
            ],
            name=self._fresh(f"type.{name_expr.value}"),
        )
        if ns_dict_expr is not None:
            for key_expr, value_expr in ns_dict_expr.pairs:
                if not isinstance(key_expr, StrLit):
                    return None
                value = self._emit_expr_with_native_callable_values(value_expr)
                value_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    value,
                    value_expr.ty,
                )
                self.builder.call(
                    self.runtime["py_class_setattr"],
                    [cls_obj, self._attr_name_ptr(key_expr.value), value_obj],
                    name=self._fresh(f"type.attr.{key_expr.value}"),
                )
            return cls_obj

        if ns_runtime_expr is not None:
            ns_value = self._emit_expr(ns_runtime_expr)
            ns_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                ns_value,
                ns_runtime_expr.ty,
            )
            self.builder.call(
                self.runtime["py_class_apply_namespace_dict"],
                [cls_obj, ns_obj],
                name=self._fresh("type.namespace"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
        return cls_obj
