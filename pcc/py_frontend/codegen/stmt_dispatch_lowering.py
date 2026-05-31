"""Statement dispatch lowering for L1CodeGen."""
from __future__ import annotations

import os
import sys

from ..py_ast import (
    Assign,
    AugAssign,
    Break,
    ClassDef,
    Continue,
    Delete,
    ExprStmt,
    For,
    FuncDef,
    Global,
    If,
    Import,
    ImportFrom,
    Nonlocal,
    Pass,
    Raise,
    Return,
    Stmt,
    Try,
    While,
    With,
)
from .errors import L1CodegenError
from .layer1_support import (
    _is_import_from_stmt,
    _is_import_stmt,
    _stmt_kind_name,
)


def _stmt_has_targets(stmt) -> bool:
    return hasattr(stmt, "targets") and stmt.targets is not None


def _stmt_has_value(stmt) -> bool:
    return hasattr(stmt, "value") and stmt.value is not None


def _stmt_has_target(stmt) -> bool:
    return hasattr(stmt, "target") and stmt.target is not None


def _stmt_has_op(stmt) -> bool:
    return hasattr(stmt, "op") and stmt.op is not None


def _stmt_has_iter(stmt) -> bool:
    return hasattr(stmt, "iter") and stmt.iter is not None


def _stmt_has_body(stmt) -> bool:
    return hasattr(stmt, "body") and stmt.body is not None


def _stmt_has_expr(stmt) -> bool:
    return hasattr(stmt, "expr") and stmt.expr is not None


def _stmt_is_assign(stmt) -> bool:
    return _stmt_has_targets(stmt) and _stmt_has_value(stmt)


def _stmt_is_augassign(stmt) -> bool:
    return _stmt_has_target(stmt) and _stmt_has_op(stmt) and _stmt_has_value(stmt)


def _stmt_is_expr(stmt) -> bool:
    return _stmt_has_expr(stmt)


def _stmt_is_for(stmt) -> bool:
    return _stmt_has_target(stmt) and _stmt_has_iter(stmt) and _stmt_has_body(stmt)


def _stmt_is_return(stmt) -> bool:
    return isinstance(stmt, Return) or (
        _stmt_has_value(stmt)
        and not _stmt_has_targets(stmt)
        and not _stmt_has_target(stmt)
    )


class StmtDispatchLoweringMixin:
    def _emit_stmts_impl(self, stmts: tuple[Stmt, ...]) -> None:
        debug_codegen = bool(os.environ.get("PCC_DEBUG_CODEGEN_PHASES"))
        stmt_index = 0
        for stmt in stmts:
            if self._builder_block_is_terminated():
                # Dead code after a return/raise — silently drop.
                return
            try:
                self._codegen_trace_set_stmt_context(stmt_index, _stmt_kind_name(stmt))
            except Exception:
                pass
            if debug_codegen:
                mod_name = self.ast_module.name or "<module>"
                func_name = (
                    self.current_func_def.name
                    if self.current_func_def is not None
                    else "<top>"
                )
                span = getattr(stmt, "span", None)
                loc = ""
                if span is not None:
                    loc = ":" + str(span.line) + ":" + str(span.col)
                sys.stderr.write(
                    "[pcc.codegen] "
                    + mod_name
                    + ":"
                    + func_name
                    + ":stmt begin "
                    + str(stmt_index)
                    + " "
                    + _stmt_kind_name(stmt)
                    + loc
                    + "\n"
                )
            self._emit_stmt(stmt)
            if debug_codegen:
                mod_name = self.ast_module.name or "<module>"
                func_name = (
                    self.current_func_def.name
                    if self.current_func_def is not None
                    else "<top>"
                )
                sys.stderr.write(
                    "[pcc.codegen] "
                    + mod_name
                    + ":"
                    + func_name
                    + ":stmt end "
                    + str(stmt_index)
                    + " "
                    + _stmt_kind_name(stmt)
                    + "\n"
                )
            stmt_index += 1

    def _emit_stmt_impl(self, stmt: Stmt) -> None:
        if len(self._generator_ctx_stack) > 0:
            if _stmt_is_return(stmt):
                self._emit_generator_return(stmt)
                return
            if _stmt_is_expr(stmt) and not _stmt_is_for(stmt):
                sentinel = self._yield_sentinel_call(stmt.expr)
                if sentinel is not None:
                    _kind, call = sentinel
                    if _kind == "yield_from":
                        self._emit_generator_yield_from(call)
                    else:
                        self._emit_generator_yield(call)
                    return
        if isinstance(stmt, Pass):
            return
        if _stmt_is_assign(stmt):
            self._emit_assign(stmt)
            return
        if _stmt_is_augassign(stmt):
            self._emit_augassign(stmt)
            return
        if _stmt_is_for(stmt):
            self._emit_for(stmt)
            return
        if _stmt_is_return(stmt):
            self._emit_return(stmt)
            return
        if _stmt_is_expr(stmt):
            self._emit_expr_stmt(stmt)
            return
        if isinstance(stmt, Raise):
            self._emit_raise(stmt)
            return
        if isinstance(stmt, Try):
            self._emit_try(stmt)
            return
        if isinstance(stmt, With):
            self._emit_with(stmt)
            return
        if _is_import_stmt(stmt):
            if _is_import_from_stmt(stmt):
                self._emit_import_from(stmt)
            else:
                self._emit_import(stmt)
            return
        if isinstance(stmt, Import):
            self._emit_import(stmt)
            return
        if isinstance(stmt, ImportFrom):
            self._emit_import_from(stmt)
            return
        if isinstance(stmt, If):
            self._emit_if(stmt)
            return
        if isinstance(stmt, While):
            self._emit_while(stmt)
            return
        if isinstance(stmt, (Nonlocal, Global)):
            # pcc has no lexical-scope closure story — ``nonlocal`` /
            # ``global`` declarations are recorded for symbol-table
            # hygiene in CPython, but at the pcc level every read/write
            # of the referenced name already routes to ``self.env`` /
            # ``_module_globals``. Accept the directive as a no-op so
            # solo-compile doesn't abort on sources that include one.
            return
        if isinstance(stmt, Break):
            if not self.loop_stack:
                raise L1CodegenError("break outside loop")
            _, break_bb = self.loop_stack[-1]
            self.builder.branch(break_bb)
            return
        if isinstance(stmt, Continue):
            if not self.loop_stack:
                raise L1CodegenError("continue outside loop")
            cont_bb, _ = self.loop_stack[-1]
            self.builder.branch(cont_bb)
            return
        if isinstance(stmt, Delete):
            self._emit_delete(stmt)
            return
        if isinstance(stmt, FuncDef):
            # Module-scope block declarations are predeclared and emitted by
            # the module-generation pass. Nested FuncDefs are closure-hoisted
            # before codegen; the declaration statement itself has no native
            # side effect in pcc's static model.
            return
        if isinstance(stmt, ClassDef) and self.current_func_def is None:
            self.class_lowering.emit_class_statement_init(stmt)
            return
        raise NotImplementedError(
            f"Layer 1 does not handle statement {_stmt_kind_name(stmt)}"
        )
