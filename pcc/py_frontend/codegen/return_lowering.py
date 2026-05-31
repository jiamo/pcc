"""Return-statement lowering for L1CodeGen."""
from __future__ import annotations

import os
import sys

from pcc.llvm_capi.compat import ir

from ..py_ast import IntType, Name, Return
from . import marshal
from .errors import L1CodegenError


class ReturnLoweringMixin:
    def _return_log(self, label: str) -> None:
        if not os.environ.get("PCC_DEBUG_CODEGEN_PHASES"):
            return
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
            + ":return "
            + label
            + "\n"
        )

    def _emit_pending_finally_blocks(self) -> None:
        self._return_log("finally begin")
        stack = self._finally_stack
        if not stack or self._emitting_finally:
            self._return_log("finally skip")
            return
        prev = self._emitting_finally
        self._emitting_finally = True
        idx = len(stack) - 1
        while idx >= 0:
            if self._builder_block_is_terminated():
                self._emitting_finally = prev
                self._return_log("finally terminated")
                return
            self._emit_stmts(stack[idx])
            idx -= 1
        self._emitting_finally = prev
        self._return_log("finally end")

    def _return_value_needs_retain(self, value: ir.Value, stmt: Return) -> bool:
        """Return true when a PyObject* return value is borrowed locally.

        User/native pcc function calls use the normal Python/C-API ownership
        convention: the caller receives an owned reference.  Constructors,
        container literals, subscripts, and owned locals already satisfy that
        contract.  Parameters, module globals, and non-owned locals are borrowed
        in the callee, so returning them must promote the borrow to an owned
        result before caller-side cleanup may release it.
        """
        if stmt.value is None:
            return False
        if not isinstance(value.type, ir.PointerType):
            return False
        if value in getattr(self, "_cpy_values", ()):
            return False
        expr = stmt.value
        if self._expr_returns_unsafe_raw_pointer(expr):
            return False
        if isinstance(expr, Name):
            if expr.ident in getattr(self, "_owned_local_names", set()):
                return False
            if expr.ident in getattr(self, "_current_param_names", set()):
                return True
            if expr.ident in getattr(self, "_module_globals", {}):
                return True
            if expr.ident in self.env:
                return True
        if self._expr_returns_owned_object(expr):
            return False
        if (
            getattr(self, "_module_has_c_abi_export", False)
            and getattr(self, "_module_uses_raw_int_scaffold", False)
        ):
            return False
        expr_ty = getattr(expr, "ty", None)
        ret_decl_ty = getattr(self.current_func_def, "return_ty", None)
        return self._is_object(expr_ty) or self._is_object(ret_decl_ty)

    def _retain_borrowed_return_value(
        self,
        value: ir.Value,
        stmt: Return,
    ) -> ir.Value:
        if not self._return_value_needs_retain(value, stmt):
            return value
        return self._gc_retain(value, name=self._fresh("ret.retain"))

    def _emit_return(self, stmt: Return) -> None:
        self._return_log("begin")
        fn = self.current_function
        ret_ty = fn.function_type.return_type
        if stmt.value is None:
            if getattr(self, "_async_body_depth", 0) > 0 and isinstance(
                ret_ty, ir.PointerType
            ):
                self._return_log("bare async ptr")
                self._emit_pending_finally_blocks()
                if self._builder_block_is_terminated():
                    self._return_log("bare async terminated")
                    return
                self._return_log("bare async cleanup")
                self._emit_owned_local_cleanup()
                self.builder.ret(self._emit_none_literal())
                self._return_log("bare async end")
                return
            if isinstance(ret_ty, ir.VoidType):
                self._return_log("bare void")
                self._emit_pending_finally_blocks()
                if self._builder_block_is_terminated():
                    self._return_log("bare void terminated")
                    return
                self._return_log("bare void cleanup")
                self._emit_owned_local_cleanup()
                self.builder.ret_void()
                self._return_log("bare void end")
                return
            self._return_log("bare nonvoid")
            self._emit_pending_finally_blocks()
            if self._builder_block_is_terminated():
                self._return_log("bare nonvoid terminated")
                return
            self._return_log("bare nonvoid cleanup")
            self._emit_owned_local_cleanup()
            if isinstance(ret_ty, ir.PointerType):
                self.builder.ret(ir.Constant(ret_ty, None))
            elif isinstance(ret_ty, ir.IntType):
                self.builder.ret(ir.Constant(ret_ty, 0))
            elif isinstance(ret_ty, (ir.FloatType, ir.DoubleType)):
                self.builder.ret(ir.Constant(ret_ty, 0.0))
            else:
                raise L1CodegenError(f"bare 'return' fallback can't zero-init {ret_ty}")
            self._return_log("bare nonvoid end")
            return
        if isinstance(ret_ty, ir.VoidType):
            self._return_log("value void emit expr")
            value = self._emit_expr(stmt.value)
            self._return_log("value void release")
            self._gc_release_if_owned(value, stmt.value)
            self._return_log("value void finally")
            self._emit_pending_finally_blocks()
            if self._builder_block_is_terminated():
                self._return_log("value void terminated")
                return
            self._return_log("value void cleanup")
            self._emit_owned_local_cleanup()
            self._return_log("value void ret")
            self.builder.ret_void()
            self._return_log("value void end")
            return
        if isinstance(ret_ty, ir.PointerType) and isinstance(
            self.current_func_def.return_ty, IntType
        ):
            self._return_log("exact int object")
            value = self._emit_exact_int_operand_object(stmt.value)
            value = self._retain_borrowed_return_value(value, stmt)
            self._emit_pending_finally_blocks()
            if self._builder_block_is_terminated():
                self._return_log("exact int terminated")
                return
            skip_name = stmt.value.ident if isinstance(stmt.value, Name) else None
            self._return_log("exact int cleanup")
            self._emit_owned_local_cleanup(skip_name=skip_name)
            self.builder.ret(value)
            self._return_log("exact int end")
            return
        self._return_log("value emit expr")
        valueclass_payload = self._maybe_emit_valueclass_constructor_payload(
            self.current_func_def.return_ty,
            stmt.value,
        )
        if valueclass_payload is not None:
            value = valueclass_payload
        else:
            value = self._emit_expr(stmt.value)
        self._return_log("value coerce")
        value = self._coerce(value, stmt.value.ty, self.current_func_def.return_ty)
        if value.type != ret_ty:
            self._return_log("value fix ret type")
            if isinstance(ret_ty, ir.IntType) and isinstance(value.type, ir.IntType):
                if value.type.width > ret_ty.width:
                    value = self.builder.trunc(
                        value,
                        ret_ty,
                        name=self._fresh("ret.trunc"),
                    )
                elif value.type.width < ret_ty.width:
                    if value.type.width == 1:
                        value = self.builder.zext(
                            value,
                            ret_ty,
                            name=self._fresh("ret.zext"),
                        )
                    else:
                        value = self.builder.sext(
                            value,
                            ret_ty,
                            name=self._fresh("ret.sext"),
                        )
            if isinstance(ret_ty, ir.PointerType) and not isinstance(
                value.type, ir.PointerType
            ):
                value = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    value,
                    stmt.value.ty,
                )
        value = self._retain_borrowed_return_value(value, stmt)
        self._return_log("value finally")
        self._emit_pending_finally_blocks()
        if self._builder_block_is_terminated():
            self._return_log("value terminated")
            return
        skip_name = stmt.value.ident if isinstance(stmt.value, Name) else None
        self._return_log("value cleanup")
        self._emit_owned_local_cleanup(skip_name=skip_name)
        self._return_log("value ret")
        self.builder.ret(value)
        self._return_log("value end")
