"""Basic statement and expression control-flow lowering for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import Assign, Attr, BoolLit, BoolType, Break, Compare, If, IfExpr, Name, NoneLit, NoneType, Try, While
from . import marshal


class ControlFlowLoweringMixin:
    def _static_bool_condition(self, expr) -> object:
        if isinstance(expr, BoolLit):
            return bool(expr.value)
        if isinstance(expr, Name):
            if self._native_builtin_value_for_name(expr.ident) == "typing.TYPE_CHECKING":
                return False
        if (
            isinstance(expr, Attr)
            and expr.name == "TYPE_CHECKING"
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "typing"
        ):
            return False
        if isinstance(expr, Compare) and expr.op in ("is", "is not"):
            def optional_missing_none(e) -> bool:
                return (
                    isinstance(e, Name)
                    and self._native_builtin_value_for_name(e.ident)
                    == "pcc.optional_import_missing.None"
                )

            lhs_missing = optional_missing_none(expr.lhs)
            rhs_missing = optional_missing_none(expr.rhs)
            lhs_none = isinstance(expr.lhs, NoneLit) or isinstance(expr.lhs.ty, NoneType)
            rhs_none = isinstance(expr.rhs, NoneLit) or isinstance(expr.rhs.ty, NoneType)
            if (lhs_missing and rhs_none) or (rhs_missing and lhs_none):
                return expr.op == "is"
        return None

    def _emit_if(self, stmt: If) -> None:
        static_cond = self._static_bool_condition(stmt.cond)
        if static_cond is not None:
            if static_cond:
                self._emit_stmts(stmt.body)
            elif stmt.else_body:
                self._emit_stmts(stmt.else_body)
            return

        cond = self._emit_expr(stmt.cond)
        cond_i1 = self._truthy(cond, stmt.cond.ty)
        class_attr_state_before = dict(
            getattr(self, "_class_attr_runtime_state", {})
        )

        fn = self.current_function
        then_bb = fn.append_basic_block(name=self._fresh("if.then"))
        else_bb = fn.append_basic_block(name=self._fresh("if.else"))
        merge_bb = fn.append_basic_block(name=self._fresh("if.end"))

        self.builder.cbranch(cond_i1, then_bb, else_bb)

        self.builder.position_at_end(then_bb)
        self._emit_stmts(stmt.body)
        class_attr_state_then = dict(
            getattr(self, "_class_attr_runtime_state", {})
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(merge_bb)

        self.builder.position_at_end(else_bb)
        self._class_attr_runtime_state = dict(class_attr_state_before)
        if stmt.else_body:
            self._emit_stmts(stmt.else_body)
        class_attr_state_else = dict(
            getattr(self, "_class_attr_runtime_state", {})
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        merged_class_attr_state = {}
        merged_keys = []
        for key in class_attr_state_then:
            merged_keys.append(key)
        for key in class_attr_state_else:
            if key not in class_attr_state_then:
                merged_keys.append(key)
        for key in merged_keys:
            then_state = class_attr_state_then.get(key)
            else_state = class_attr_state_else.get(key)
            if then_state == else_state:
                if then_state is not None:
                    merged_class_attr_state[key] = then_state
            else:
                merged_class_attr_state[key] = "unknown"
        self._class_attr_runtime_state = merged_class_attr_state

    def _emit_if_expr(self, expr: IfExpr) -> ir.Value:
        """Lower ``then_e if cond else else_e`` into a diamond CFG plus phi."""
        static_cond = self._static_bool_condition(expr.cond)
        if static_cond is not None:
            selected = expr.then_e if static_cond else expr.else_e
            selected_val = self._emit_expr(selected)
            return self._coerce(selected_val, selected.ty, expr.ty)

        result_ty = expr.ty
        cond_val = self._emit_expr(expr.cond)
        cond_b = self._truthy(cond_val, expr.cond.ty)

        fn = self.current_function
        then_bb = fn.append_basic_block(name=self._fresh("ternary_true"))
        else_bb = fn.append_basic_block(name=self._fresh("ternary_false"))
        join_bb = fn.append_basic_block(name=self._fresh("ternary_end"))
        self.builder.cbranch(cond_b, then_bb, else_bb)

        self.builder.position_at_end(then_bb)
        then_val = self._emit_expr(expr.then_e)
        then_val = self._coerce(then_val, expr.then_e.ty, result_ty)
        then_exit = self.builder._block

        self.builder.position_at_end(else_bb)
        else_val = self._emit_expr(expr.else_e)
        else_val = self._coerce(else_val, expr.else_e.ty, result_ty)
        else_exit = self.builder._block

        phi_ty = self._storage_ir_type(result_ty)
        if isinstance(phi_ty, ir.PointerType):
            if not isinstance(then_val.type, ir.PointerType):
                self.builder.position_at_end(then_exit)
                then_val = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    then_val,
                    expr.then_e.ty,
                )
                then_exit = self.builder._block
            if not isinstance(else_val.type, ir.PointerType):
                self.builder.position_at_end(else_exit)
                else_val = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    else_val,
                    expr.else_e.ty,
                )
                else_exit = self.builder._block

        self.builder.position_at_end(then_exit)
        self.builder.branch(join_bb)
        self.builder.position_at_end(else_exit)
        self.builder.branch(join_bb)

        self.builder.position_at_end(join_bb)
        phi = self.builder.phi(phi_ty, name=self._fresh("ternary"))
        phi.add_incoming(then_val, then_exit)
        phi.add_incoming(else_val, else_exit)
        return phi

    def _emit_while(self, stmt: While) -> None:
        if stmt.else_body:
            from dataclasses import replace as _replace

            broke_name = self._fresh("whileelse_broke")
            span = stmt.span
            broke_lit_false = BoolLit(
                span=span,
                ty=BoolType(name="bool"),
                value=False,
            )
            broke_lit_true = BoolLit(
                span=span,
                ty=BoolType(name="bool"),
                value=True,
            )
            broke_ref = Name(
                span=span,
                ty=BoolType(name="bool"),
                ident=broke_name,
            )
            set_broke_true = Assign(
                span=span,
                targets=(broke_ref,),
                value=broke_lit_true,
                annotation=BoolType(name="bool"),
            )

            def tag_breaks(stmts):
                out = []
                for s in stmts:
                    if isinstance(s, Break):
                        out.append(set_broke_true)
                        out.append(s)
                        continue
                    if isinstance(s, If):
                        out.append(
                            _replace(
                                s,
                                body=tag_breaks(s.body),
                                else_body=tag_breaks(s.else_body),
                            )
                        )
                        continue
                    if isinstance(s, Try):
                        new_handlers_list = []
                        for h in s.handlers:
                            new_handlers_list.append(
                                _replace(h, body=tag_breaks(h.body))
                            )
                        new_handlers = tuple(new_handlers_list)
                        out.append(
                            _replace(
                                s,
                                body=tag_breaks(s.body),
                                else_body=tag_breaks(s.else_body),
                                finally_body=tag_breaks(s.finally_body),
                                handlers=new_handlers,
                            )
                        )
                        continue
                    out.append(s)
                return tuple(out)

            ir_ty = self._map_type(BoolType(name="bool"))
            alloca = self._alloca_in_entry(
                ir_ty,
                name=f"{broke_name}.addr",
            )
            self.builder.store(ir.Constant(ir_ty, 0), alloca)
            self.env[broke_name] = (alloca, ir_ty, BoolType(name="bool"))

            new_stmt = _replace(
                stmt,
                body=tag_breaks(stmt.body),
                else_body=(),
            )
            self._emit_while(new_stmt)
            broke_val = self.builder.load(
                alloca,
                name=self._fresh("whileelse.broke"),
            )
            should_else = self.builder.icmp_unsigned(
                "==",
                broke_val,
                ir.Constant(ir_ty, 0),
                name=self._fresh("whileelse.should_else"),
            )
            else_bb = self.current_function.append_basic_block(
                name=self._fresh("whileelse.else"),
            )
            end_bb = self.current_function.append_basic_block(
                name=self._fresh("whileelse.end"),
            )
            self.builder.cbranch(should_else, else_bb, end_bb)
            self.builder.position_at_end(else_bb)
            self._emit_stmts(stmt.else_body)
            if not self._builder_block_is_terminated():
                self.builder.branch(end_bb)
            self.builder.position_at_end(end_bb)
            return
        fn = self.current_function
        class_attr_state_before = dict(
            getattr(self, "_class_attr_runtime_state", {})
        )
        cond_bb = fn.append_basic_block(name=self._fresh("while.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("while.body"))
        latch_bb = fn.append_basic_block(name=self._fresh("while.latch"))
        end_bb = fn.append_basic_block(name=self._fresh("while.end"))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cond = self._emit_expr(stmt.cond)
        cond_i1 = self._truthy(cond, stmt.cond.ty)
        self.builder.cbranch(cond_i1, body_bb, end_bb)

        self.loop_stack.append((latch_bb, end_bb))
        self.builder.position_at_end(body_bb)
        self._class_attr_runtime_state = dict(class_attr_state_before)
        old_class_attr_loop_depth = getattr(
            self,
            "_class_attr_mutation_in_loop_depth",
            0,
        )
        self._class_attr_mutation_in_loop_depth = old_class_attr_loop_depth + 1
        self._emit_stmts(stmt.body)
        self._class_attr_mutation_in_loop_depth = old_class_attr_loop_depth
        class_attr_state_body = dict(
            getattr(self, "_class_attr_runtime_state", {})
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(latch_bb)
        self.loop_stack.pop()

        self.builder.position_at_end(latch_bb)
        self._emit_thread_safepoint()
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        merged_class_attr_state = dict(class_attr_state_before)
        for key in class_attr_state_body:
            before_state = class_attr_state_before.get(key)
            body_state = class_attr_state_body.get(key)
            if before_state == body_state:
                if body_state is not None:
                    merged_class_attr_state[key] = body_state
            else:
                merged_class_attr_state[key] = "unknown"
        self._class_attr_runtime_state = merged_class_attr_state
