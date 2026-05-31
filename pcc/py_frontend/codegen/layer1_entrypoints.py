"""Public entrypoint wrappers for ``L1CodeGen``."""
from __future__ import annotations

import os
import sys
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Expr, Module, Stmt
from .expr_dispatch_lowering import ExprDispatchLoweringMixin
from .generation_lowering import GenerationLoweringMixin
from .layer1_init import Layer1InitMixin
from .stmt_dispatch_lowering import StmtDispatchLoweringMixin


class L1CodeGenEntrypointMixin:
    def __init__(
        self,
        module: Module,
        emit_cpy_main_exitcode: bool = False,
        ir_scaffold_mode: str = "off",
    ):
        Layer1InitMixin._init_l1_state(
            self,
            module,
            emit_cpy_main_exitcode,
            ir_scaffold_mode,
        )

    def generate(self, module: Optional[Module] = None) -> str:
        if self._codegen_trace_is_enabled():
            target_module = self.ast_module if module is None else module
            self._codegen_trace_push(
                "module",
                -1,
                "",
                "",
                self._codegen_trace_span(target_module),
            )
        try:
            return GenerationLoweringMixin._generate_impl(self, module)
        except BaseException as exc:
            self._codegen_trace_dump(exc)
            raise

    def _codegen_trace_span(self, node: object) -> str:
        try:
            span = node.span  # type: ignore[attr-defined]
        except Exception:
            return ""
        if span is None:
            return ""
        try:
            return (
                str(span.file)
                + ":"
                + str(span.line)
                + ":"
                + str(span.col)
                + "-"
                + str(span.end_line)
                + ":"
                + str(span.end_col)
            )
        except Exception:
            return ""

    def _codegen_trace_module(self) -> str:
        mod_name = ""
        try:
            mod_name = self.ast_module.name  # type: ignore[attr-defined]
        except Exception:
            mod_name = ""
        if mod_name:
            return mod_name
        return "<module>"

    def _codegen_trace_function(self) -> str:
        if self.current_func_def is None:
            return "<top>"
        return self.current_func_def.name

    def _codegen_trace_is_enabled(self) -> bool:
        try:
            return bool(self._codegen_trace_enabled)  # type: ignore[attr-defined]
        except Exception:
            return False

    def _codegen_trace_set_stmt_context(self, stmt_index: int, stmt_kind: str) -> None:
        if not self._codegen_trace_is_enabled():
            return
        try:
            self._codegen_current_stmt_index = stmt_index
            self._codegen_current_stmt_kind = stmt_kind
        except Exception:
            pass

    def _codegen_trace_push(
        self,
        boundary: str,
        stmt_index: int,
        stmt_kind: str,
        expr_kind: str,
        span: str,
    ) -> None:
        if not self._codegen_trace_is_enabled():
            return
        capacity = int(getattr(self, "_codegen_trace_capacity", 0))
        if capacity <= 0:
            return
        ring = getattr(self, "_codegen_trace_ring", None)
        if not isinstance(ring, list):
            return
        trace_next = int(getattr(self, "_codegen_trace_next", 0))
        if trace_next < 0:
            trace_next = 0
        if span is None:
            span = ""
        entry = (
            boundary,
            self._codegen_trace_module(),
            self._codegen_trace_function(),
            str(stmt_index),
            str(stmt_kind),
            str(expr_kind),
            span,
        )
        if len(ring) < capacity:
            ring.append(entry)
            self._codegen_trace_next = (trace_next + 1) % capacity  # type: ignore[attr-defined]
            return
        trace_next = trace_next % capacity
        ring[trace_next] = entry
        self._codegen_trace_next = (trace_next + 1) % capacity  # type: ignore[attr-defined]

    def _codegen_trace_dump(self, exc: BaseException) -> None:
        if not self._codegen_trace_is_enabled():
            return
        if bool(getattr(self, "_codegen_trace_diagnosed", False)):
            return
        try:
            self._codegen_trace_diagnosed = True  # type: ignore[attr-defined]
        except Exception:
            pass

        def _write_line(line: str) -> None:
            try:
                sys.stderr.write(line)
            except Exception:
                try:
                    os.write(2, line.encode("utf-8"))
                except Exception:
                    pass

        module_name = self._codegen_trace_module()
        function_name = self._codegen_trace_function()
        exc_type = type(exc).__name__
        _write_line(
            "PCC_CODEGEN_EXCEPTION type="
            + exc_type
            + " module="
            + module_name
            + " function="
            + function_name
            + "\n"
        )
        try:
            current_stmt = self._codegen_current_stmt_index
        except Exception:
            current_stmt = -1
        try:
            current_stmt_kind = self._codegen_current_stmt_kind
        except Exception:
            current_stmt_kind = ""
        try:
            current_expr_kind = self._codegen_current_expr_kind
        except Exception:
            current_expr_kind = ""
        try:
            _write_line(
                "PCC_CODEGEN_EXCEPTION_CONTEXT "
                + "stmt_index="
                + str(current_stmt)
                + " stmt_kind="
                + current_stmt_kind
                + " expr_kind="
                + current_expr_kind
                + "\n"
            )
        except Exception:
            pass
        ring = getattr(self, "_codegen_trace_ring", [])
        if not isinstance(ring, list):
            ring = []
        if len(ring) == 0:
            return
        capacity = int(getattr(self, "_codegen_trace_capacity", len(ring)))
        if capacity <= 0 or len(ring) < capacity:
            ordered = ring
        else:
            ordered = []
            i = int(getattr(self, "_codegen_trace_next", 0))
            if i < 0:
                i = 0
            for _ in range(len(ring)):
                ordered.append(ring[i])
                i = (i + 1) % len(ring)
        for idx in range(len(ordered)):
            (
                boundary,
                b_module,
                b_function,
                b_stmt_index,
                b_stmt_kind,
                b_expr_kind,
                b_span,
            ) = ordered[idx]
            line = (
                "PCC_CODEGEN_BREADCRUMB "
                + str(idx)
                + " "
                + boundary
                + " module="
                + b_module
                + " function="
                + b_function
                + " stmt_index="
                + b_stmt_index
                + " stmt_kind="
                + b_stmt_kind
                + " expr_kind="
                + b_expr_kind
                + " span="
                + b_span
                + "\n"
            )
            _write_line(line)

    def _codegen_trace_set_context_for_expr(self, expr: Expr) -> None:
        try:
            self._codegen_current_expr_kind = type(expr).__name__
        except Exception:
            self._codegen_current_expr_kind = ""

    def _emit_stmts(self, stmts: tuple[Stmt, ...]) -> None:
        StmtDispatchLoweringMixin._emit_stmts_impl(self, stmts)

    def _emit_stmt(self, stmt: Stmt) -> None:
        if self._codegen_trace_is_enabled():
            stmt_kind = ""
            stmt_index = self._codegen_current_stmt_index
            try:
                stmt_kind = type(stmt).__name__
            except Exception:
                stmt_kind = ""
            self._codegen_current_stmt_kind = stmt_kind
            self._codegen_trace_push(
                "stmt",
                stmt_index,
                stmt_kind,
                "",
                self._codegen_trace_span(stmt),
            )
        try:
            StmtDispatchLoweringMixin._emit_stmt_impl(self, stmt)
        except BaseException as exc:
            self._codegen_trace_dump(exc)
            raise

    def _emit_expr(self, expr: Expr) -> ir.Value:
        if self._codegen_trace_is_enabled():
            expr_kind = ""
            try:
                expr_kind = type(expr).__name__
            except Exception:
                expr_kind = ""
            self._codegen_current_expr_kind = expr_kind
            self._codegen_trace_set_context_for_expr(expr)
            self._codegen_trace_push(
                "expr",
                self._codegen_current_stmt_index,
                self._codegen_current_stmt_kind,
                expr_kind,
                self._codegen_trace_span(expr),
            )
        try:
            return ExprDispatchLoweringMixin._emit_expr_impl(self, expr)
        except BaseException as exc:
            self._codegen_trace_dump(exc)
            raise


__all__ = ["L1CodeGenEntrypointMixin"]
