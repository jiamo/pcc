"""Exact Python integer object-boundary lowering for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    BinOp,
    BoolExpr,
    BoolLit,
    BoolType,
    Call,
    Compare,
    DictType,
    DynType,
    Expr,
    IfExpr,
    IntLit,
    IntType,
    ListType,
    Name,
    Slice,
    Subscript,
    TupleType,
    UnaryOp,
)
from . import marshal


_I32 = ir.IntType(32)
_I64 = ir.IntType(64)


class ExactIntLoweringMixin:
    def _maybe_emit_exact_int_object(
        self,
        expr: Expr,
    ) -> Optional[ir.Value]:
        if not isinstance(expr.ty, IntType):
            return None
        if (
            isinstance(expr, Name)
            and getattr(
                self,
                "_exact_int_env_flags",
                {},
            ).get(expr.ident, False)
        ):
            value = self._emit_expr(expr)
            if isinstance(value.type, ir.PointerType):
                return value
        if isinstance(expr, BinOp):
            if (
                expr.op == "**"
                and isinstance(expr.lhs, IntLit)
                and isinstance(expr.rhs, IntLit)
                and expr.rhs.value >= 0
            ):
                folded = pow(int(expr.lhs.value), int(expr.rhs.value))
                if -(1 << 63) <= folded <= (1 << 63) - 1:
                    return self._emit_int_literal_object(folded)
            fn_name = {
                "+": "py_int_add",
                "-": "py_int_sub",
                "*": "py_int_mul",
                "//": "py_int_floordiv",
                "%": "py_int_mod",
                "**": "py_int_pow",
                "&": "py_int_and",
                "|": "py_int_or",
                "^": "py_int_xor",
                "<<": "py_int_shl",
                ">>": "py_int_shr",
            }.get(expr.op)
            if fn_name is None:
                return None
            if not (
                isinstance(expr.lhs.ty, (IntType, BoolType))
                and isinstance(expr.rhs.ty, (IntType, BoolType))
            ):
                return None
            lhs = self._emit_exact_int_operand_object(expr.lhs)
            rhs = self._emit_exact_int_operand_object(expr.rhs)
            if expr.op == "<<" or expr.op == ">>":
                rhs_i64 = marshal.marshal_from_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    rhs,
                    IntType(name="int"),
                )
                self._emit_negative_shift_count_check(rhs_i64)
            if self._int_exprs_are_boxed():
                inline = self._emit_inline_tagged_int_binop_or_call(
                    expr.op,
                    lhs,
                    rhs,
                    fn_name,
                )
                if inline is not None:
                    return inline
            result = self.builder.call(
                self.runtime[fn_name],
                [lhs, rhs],
                name=self._fresh("exact.int"),
            )
            if expr.op == "<<" or expr.op == ">>":
                self._emit_post_call_err_check(None)
            if expr.op == "//" or expr.op == "%":
                # py_int_floordiv / py_int_mod return NULL (no exception) on a
                # zero divisor; surface ZeroDivisionError so it is catchable.
                self._emit_zero_division_if_null(result, "division by zero")
            return result
        if (
            isinstance(expr, UnaryOp)
            and expr.op == "-"
            and isinstance(expr.operand.ty, (IntType, BoolType))
        ):
            operand = self._emit_exact_int_operand_object(expr.operand)
            return self.builder.call(
                self.runtime["py_int_neg"],
                [operand],
                name=self._fresh("exact.int.neg"),
            )
        if isinstance(expr, IntLit):
            value = int(expr.value)
            if value < -(1 << 63) or value > (1 << 63) - 1:
                return self._emit_int_literal_object(value)
        if isinstance(expr, Subscript):
            return self._emit_subscript_load_object(expr)
        return None

    def _int_expr_needs_exact_object_boundary(self, expr: Expr) -> bool:
        if not isinstance(expr.ty, IntType):
            return False
        if isinstance(expr, Name):
            return getattr(self, "_exact_int_env_flags", {}).get(
                expr.ident,
                False,
            )
        if isinstance(expr, Call) and isinstance(expr.func, Name):
            fn = self.functions.get(expr.func.ident)
            if fn is not None and isinstance(
                fn.function_type.return_type, ir.PointerType
            ):
                return True
        if isinstance(expr, IntLit):
            value = int(expr.value)
            return value < -(1 << 63) or value > (1 << 63) - 1
        if isinstance(expr, BinOp):
            if expr.op == "**":
                return True
            if self._int_expr_needs_exact_object_boundary(
                expr.lhs
            ) or self._int_expr_needs_exact_object_boundary(expr.rhs):
                return True
            if isinstance(expr.lhs, IntLit) and isinstance(expr.rhs, IntLit):
                try:
                    lhs = int(expr.lhs.value)
                    rhs = int(expr.rhs.value)
                    if expr.op == "+":
                        value = lhs + rhs
                    elif expr.op == "-":
                        value = lhs - rhs
                    elif expr.op == "*":
                        value = lhs * rhs
                    elif expr.op == "//" and rhs != 0:
                        value = lhs // rhs
                    elif expr.op == "%" and rhs != 0:
                        value = lhs % rhs
                    elif expr.op == "<<":
                        value = lhs << rhs
                    elif expr.op == ">>":
                        value = lhs >> rhs
                    elif expr.op == "&":
                        value = lhs & rhs
                    elif expr.op == "|":
                        value = lhs | rhs
                    elif expr.op == "^":
                        value = lhs ^ rhs
                    else:
                        return False
                except (OverflowError, ValueError):
                    return True
                return value < -(1 << 63) or value > (1 << 63) - 1
            return False
        if isinstance(expr, UnaryOp) and expr.op == "-":
            if self._int_expr_needs_exact_object_boundary(expr.operand):
                return True
            if isinstance(expr.operand, IntLit):
                value = -int(expr.operand.value)
                return value < -(1 << 63) or value > (1 << 63) - 1
            return False
        return False

    def _emit_exact_int_compare(
        self,
        expr: Compare,
    ) -> Optional[ir.Value]:
        if expr.op not in ("==", "!=", "<", "<=", ">", ">="):
            return None
        if not (
            isinstance(expr.lhs.ty, (IntType, BoolType))
            and isinstance(expr.rhs.ty, (IntType, BoolType))
        ):
            return None
        if not (
            self._int_expr_needs_exact_object_boundary(expr.lhs)
            or self._int_expr_needs_exact_object_boundary(expr.rhs)
        ):
            return None
        lhs = self._emit_exact_int_operand_object(expr.lhs)
        rhs = self._emit_exact_int_operand_object(expr.rhs)
        cmp_i32 = self.builder.call(
            self.runtime["py_int_cmp"],
            [lhs, rhs],
            name=self._fresh("exact.int.cmp"),
        )
        zero = ir.Constant(_I32, 0)
        pred = {
            "==": "==",
            "!=": "!=",
            "<": "<",
            "<=": "<=",
            ">": ">",
            ">=": ">=",
        }[expr.op]
        return self.builder.icmp_signed(
            pred,
            cmp_i32,
            zero,
            name=self._fresh("exact.int.cmp.i1"),
        )

    def _emit_exact_int_operand_object(self, expr: Expr) -> ir.Value:
        exact = self._maybe_emit_exact_int_object(expr)
        if exact is not None:
            return exact
        if isinstance(expr, IntLit):
            return self._emit_int_literal_object(int(expr.value))
        if isinstance(expr, BoolLit):
            return self.builder.call(
                self.runtime["py_int_from_i64"],
                [ir.Constant(_I64, 1 if bool(expr.value) else 0)],
                name=self._fresh("print.int.box"),
            )
        value = self._emit_expr(expr)
        if isinstance(value.type, ir.PointerType):
            return value
        i64 = self._to_int64(value, expr.ty)
        return self.builder.call(
            self.runtime["py_int_from_i64"],
            [i64],
            name=self._fresh("exact.int.box"),
        )

    def _emit_expr_as_pcc_object(self, expr: Expr) -> ir.Value:
        if isinstance(expr.ty, IntType):
            exact = self._maybe_emit_exact_int_object(expr)
            if exact is not None:
                return exact
        if isinstance(expr, IfExpr):
            return self._emit_if_expr_as_pcc_object(expr)
        if isinstance(expr, BoolExpr):
            return self._emit_boolexpr_as_pcc_object(expr)
        valueclass_payload = self._maybe_emit_valueclass_constructor_payload(
            expr.ty,
            expr,
        )
        if valueclass_payload is not None:
            boxed_valueclass = self._emit_valueclass_payload_to_object(
                valueclass_payload,
                expr.ty,
                consume_fields=True,
            )
            if boxed_valueclass is not None:
                return boxed_valueclass
        value = self._emit_expr(expr)
        if value in getattr(self, "_cpy_values", ()):
            # CPython-bridge result (e.g. ``random.randint(...)`` as a
            # comprehension element) — convert to a pcc object before it
            # is stored into a pcc container, or the raw foreign pointer
            # would flow into native int/str ops and fail there.
            bridged = self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"],
                [value],
                name=self._fresh("as_pcc.cpy.bridge"),
            )
            self.builder.call(self.runtime["py_cpy_decref"], [value])
            return bridged
        boxed_valueclass = self._emit_valueclass_payload_to_object(value, expr.ty)
        if boxed_valueclass is not None:
            return boxed_valueclass
        return marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            value,
            expr.ty,
        )

    def _emit_subscript_load_object(self, expr: Subscript) -> Optional[ir.Value]:
        if isinstance(expr.idx, Slice):
            return None
        native_os_environ_item = self._emit_native_os_environ_subscript(expr)
        if native_os_environ_item is not None:
            return native_os_environ_item
        obj_ty = expr.obj.ty
        obj = self._emit_expr(expr.obj)
        if obj in self._cpy_values:
            return None
        exact_container = self._emit_exact_container_subscript_load_object(expr, obj)
        if exact_container is not None:
            return exact_container[0]
        if isinstance(obj_ty, DynType):
            key_obj = self._emit_subscript_key_object(expr.idx)
            got = self.builder.call(
                self.runtime["py_obj_getitem"],
                [obj, key_obj],
                name=self._fresh("obj.getitem.obj"),
            )
            self._gc_release_if_owned(obj, expr.obj)
            return got
        return None
