"""Misc statement/expression lowering helpers for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    Call,
    ExprStmt,
    Name,
    Subscript,
)
from .errors import L1CodegenError


class StmtMiscLoweringMixin:
    def _emit_expr_stmt(self, stmt: ExprStmt) -> None:
        # Special-case top-level ``print(...)``.
        expr = stmt.expr
        if (
            isinstance(expr, Call)
            and isinstance(expr.func, Name)
            and expr.func.ident == "print"
        ):
            self._emit_print_call(expr)
            return
        if isinstance(expr, Call) and self._maybe_emit_static_test_runner_stmt(expr):
            return
        if isinstance(expr, Call) and self._emit_native_subprocess_run_stmt(expr):
            return
        # Otherwise evaluate for side-effects.
        value = self._emit_expr(expr)
        self._gc_release_if_owned(value, expr)

    def _emit_walrus(self, expr: Call) -> ir.Value:
        """Lower ``x := value`` from the ``_walrus`` sentinel emitted
        by ``pcc.parse.py_lift._e_Assign`` — evaluate the value,
        store into ``x`` via the name-assign helper, return the value
        for use in the surrounding expression."""
        if len(expr.args) != 2:
            raise L1CodegenError("_walrus sentinel expects (target, value) args")
        target, value_expr = expr.args
        value = self._emit_expr(value_expr)
        if isinstance(target, Call) and self._is_walrus_sentinel(target):
            self._store_walrus_chain_target(target, value, value_expr.ty)
            return value
        if not isinstance(target, (Name, Attr, Subscript)):
            raise NotImplementedError(
                "walrus target must be a plain Name, Attr, or Subscript"
            )
        if isinstance(target, Name):
            self._store_value_at_name(
                target,
                value,
                value_expr.ty,
                value_is_owned=(
                    self._raw_scaffold_object_rhs_is_owned(value_expr)
                    and self._expr_returns_owned_object(value_expr)
                ),
            )
        else:
            if isinstance(target, Attr):
                self._emit_attr_store_value(target, value, value_expr.ty)
            else:
                self._emit_subscript_store_value(target, value, value_expr.ty)
        return value

    def _is_walrus_sentinel(self, expr: Call) -> bool:
        return (
            isinstance(expr.func, Name)
            and expr.func.ident in ("_walrus", "__walrus__")
            and len(expr.args) == 2
        )

    def _store_walrus_single_target(self, target, value: ir.Value, value_ty) -> None:
        if isinstance(target, Name):
            self._store_value_at_name(
                target,
                value,
                value_ty,
                value_is_owned=False,
            )
            return
        if isinstance(target, Attr):
            self._emit_attr_store_value(target, value, value_ty)
            return
        if isinstance(target, Subscript):
            self._emit_subscript_store_value(target, value, value_ty)
            return
        if isinstance(target, Call) and self._is_walrus_sentinel(target):
            self._store_walrus_chain_target(target, value, value_ty)
            return
        raise NotImplementedError(
            "walrus target must be a plain Name, Attr, or Subscript"
        )

    def _store_walrus_chain_target(self, target: Call, value: ir.Value, value_ty) -> None:
        """Store one value into a chained assignment encoded by py_lift.

        ``a = b = c`` is represented as ``a = _walrus(b, c)``. Longer
        chains can put a nested ``_walrus`` in target position; in that case
        the right-hand name is another assignment target, not a value to
        re-evaluate.
        """
        lhs, rhs_target = target.args
        if isinstance(rhs_target, Call) and self._is_walrus_sentinel(rhs_target):
            self._store_walrus_chain_target(rhs_target, value, value_ty)
        elif isinstance(rhs_target, (Name, Attr, Subscript)):
            self._store_walrus_single_target(rhs_target, value, value_ty)
        self._store_walrus_single_target(lhs, value, value_ty)
