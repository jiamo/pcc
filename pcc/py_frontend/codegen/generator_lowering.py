"""Generator lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Assign,
    Attr,
    AugAssign,
    Call,
    ClassDef,
    DynType,
    Expr,
    ExprStmt,
    For,
    FuncDef,
    If,
    Name,
    Return,
    Stmt,
    Try,
    TupleExpr,
    While,
    With,
)
from . import marshal
from .errors import L1CodegenError
from .runtime_abi import declare_runtime_global


_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_VOID = ir.VoidType()
_CSTR = _I8.as_pointer()
_STOP_ITERATION_TAG = 8


def _dataclass_field_value(obj, field_name: str, default=None):
    return getattr(obj, field_name, default)


def _dataclass_field_names(obj):
    if obj is None:
        return ()
    if isinstance(obj, Call):
        return ("span", "ty", "func", "args", "kwargs")
    if isinstance(obj, Name):
        return ("span", "ty", "ident")
    if isinstance(obj, TupleExpr):
        return ("span", "ty", "elems")
    if isinstance(obj, Expr):
        fields = getattr(obj, "__dataclass_fields__", None)
        if fields is not None:
            return fields.keys()
        return ()
    if isinstance(obj, Assign):
        return ("span", "targets", "value", "annotation")
    if isinstance(obj, AugAssign):
        return ("span", "target", "op", "value")
    if isinstance(obj, ExprStmt):
        return ("span", "expr")
    if isinstance(obj, If):
        return ("span", "cond", "body", "else_body")
    if isinstance(obj, While):
        return ("span", "cond", "body", "else_body")
    if isinstance(obj, For):
        return ("span", "target", "iter", "body", "else_body")
    if isinstance(obj, Return):
        return ("span", "value")
    if isinstance(obj, Try):
        return ("span", "body", "handlers", "else_body", "finally_body")
    if isinstance(obj, With):
        return ("span", "items", "body")
    if isinstance(obj, FuncDef):
        return (
            "span",
            "name",
            "args",
            "return_ty",
            "body",
            "decorators",
            "is_method",
            "is_async",
        )
    if isinstance(obj, ClassDef):
        return ("span", "name", "bases", "keywords", "body", "decorators")
    if isinstance(obj, Stmt):
        fields = getattr(obj, "__dataclass_fields__", None)
        if fields is not None:
            return fields.keys()
    return ()


class GeneratorLoweringMixin:
    def _vthread_suspension_call(self, expr: Expr) -> bool:
        if not isinstance(expr, Call):
            return False
        names = ("yield_now", "sleep_current", "block_current_on_fd")
        if isinstance(expr.func, Name):
            try:
                kind = self._native_builtin_value_for_name(expr.func.ident)
            except AttributeError:
                kind = None
            return (
                kind == "pcc.virtual_thread.yield_now"
                or kind == "pcc.virtual_thread.sleep_current"
                or kind == "pcc.virtual_thread.block_current_on_fd"
            )
        func = expr.func
        if not isinstance(func, Attr) or not isinstance(func.obj, Name):
            return False
        if func.name not in names:
            return False
        try:
            module = self._native_builtin_module_for_name(func.obj.ident)
        except AttributeError:
            module = None
        return module == "pcc.virtual_thread"

    def _funcdef_has_yield_sentinel(self, fd: FuncDef) -> bool:
        """Return True when ``fd`` contains a parser-lifted yield call.

        This is deliberately iterative rather than a nested walker so
        self-hosted pcc does not need closure conversion just to
        recognize generator functions.
        """
        cache_key = id(fd)
        if cache_key in self._funcdef_yield_sentinel_cache:
            return self._funcdef_yield_sentinel_cache[cache_key]
        stack = []
        for stmt in fd.body:
            stack.append(stmt)
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if isinstance(node, FuncDef) or isinstance(node, ClassDef):
                continue
            if (
                isinstance(node, Call)
                and isinstance(node.func, Name)
                and node.func.ident
                in (
                    "_yield",
                    "_yield_from",
                    "__yield__",
                    "__yield_from__",
                )
            ):
                self._funcdef_yield_sentinel_cache[cache_key] = True
                return True
            if self._vthread_suspension_call(node):
                self._funcdef_yield_sentinel_cache[cache_key] = True
                return True
            for slot in _dataclass_field_names(node):
                if slot == "span":
                    continue
                value = _dataclass_field_value(node, slot, None)
                if isinstance(value, tuple):
                    for item in value:
                        stack.append(item)
                else:
                    stack.append(value)
        self._funcdef_yield_sentinel_cache[cache_key] = False
        return False
    def _yield_sentinel_call(self, expr: Expr) -> Optional[tuple[str, Call]]:
        if (
            isinstance(expr, Call)
            and isinstance(expr.func, Name)
            and expr.func.ident
            in (
                "_yield",
                "__yield__",
                "_yield_from",
                "__yield_from__",
            )
        ):
            if expr.func.ident in ("_yield_from", "__yield_from__"):
                return ("yield_from", expr)
            return ("yield", expr)
        return None
    def _generator_yield_from_iter_name(self, expr: Expr) -> str:
        span = expr.span
        return f"__pcc_yield_from_iter_{span.line}_{span.col}"
    def _generator_for_iter_name(self, stmt: For) -> str:
        span = stmt.span
        return f"__pcc_for_iter_{span.line}_{span.col}"
    def _generator_enum_cnt_name(self, stmt: For) -> str:
        # ``for ... in enumerate(...)`` desugars (in _normalise_for_enumerate)
        # to a synthetic running counter that is created *during* _emit_for,
        # i.e. after _collect_generator_frame_names has already walked the
        # original AST.  Both sides agree on this deterministic span-keyed
        # name so the counter gets a persisted generator frame slot and
        # survives yields (otherwise the index resets to NULL on resume).
        span = stmt.span
        return f"__pcc_enum_cnt_{span.line}_{span.col}"
    def _collect_generator_target_names(
        self,
        names: list[str],
        target: Expr,
    ) -> None:
        stack = [target]
        idx = 0
        while idx < len(stack):
            cur = stack[idx]
            idx += 1
            if isinstance(cur, Name):
                if cur.ident and cur.ident not in names:
                    names.append(cur.ident)
                continue
            if isinstance(cur, TupleExpr):
                for item in cur.elems:
                    stack.append(item)
    def _collect_generator_frame_names(self, fd: FuncDef) -> list[str]:
        names: list[str] = []

        for a in fd.args:
            if a.name != "":
                if a.name not in names:
                    names.append(a.name)

        work = []
        for stmt in fd.body:
            work.append(stmt)
        idx = 0
        while idx < len(work):
            s = work[idx]
            idx += 1
            if isinstance(s, FuncDef) or isinstance(s, ClassDef):
                continue
            if isinstance(s, ExprStmt):
                sentinel = self._yield_sentinel_call(s.expr)
                if sentinel is not None and sentinel[0] == "yield_from":
                    hidden = self._generator_yield_from_iter_name(s.expr)
                    if hidden not in names:
                        names.append(hidden)
                continue
            if isinstance(s, Assign):
                for t in s.targets:
                    self._collect_generator_target_names(names, t)
                continue
            if isinstance(s, AugAssign):
                self._collect_generator_target_names(names, s.target)
                continue
            if isinstance(s, For):
                hidden = self._generator_for_iter_name(s)
                if hidden not in names:
                    names.append(hidden)
                it = s.iter
                if (
                    isinstance(it, Call)
                    and isinstance(it.func, Name)
                    and it.func.ident == "enumerate"
                ):
                    enum_cnt = self._generator_enum_cnt_name(s)
                    if enum_cnt not in names:
                        names.append(enum_cnt)
                self._collect_generator_target_names(names, s.target)
                for item in s.body:
                    work.append(item)
                for item in s.else_body:
                    work.append(item)
                continue
            if isinstance(s, If):
                for item in s.body:
                    work.append(item)
                for item in s.else_body:
                    work.append(item)
                continue
            if isinstance(s, While):
                for item in s.body:
                    work.append(item)
                for item in s.else_body:
                    work.append(item)
                continue
            if isinstance(s, With):
                for _ctx, as_var in s.items:
                    if as_var is not None:
                        self._collect_generator_target_names(names, as_var)
                for item in s.body:
                    work.append(item)
                continue
            if isinstance(s, Try):
                for item in s.body:
                    work.append(item)
                for h in s.handlers:
                    if h.name:
                        if h.name not in names:
                            names.append(h.name)
                    for item in h.body:
                        work.append(item)
                for item in s.else_body:
                    work.append(item)
                for item in s.finally_body:
                    work.append(item)

        return names
    def _emit_generator_wrapper_function(
        self,
        fd: FuncDef,
        fn: ir.Function,
        symbol_name: Optional[str] = None,
        class_info=None,
        method_kind: Optional[str] = None,
    ) -> None:
        frame_names = self._collect_generator_frame_names(fd)
        resume_fn = self._emit_generator_resume_function(
            fd,
            frame_names,
            symbol_name,
            class_info,
            method_kind,
        )

        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def
        saved_env = self.env
        saved_loops = self.loop_stack
        saved_box_int_locals = self._box_int_locals
        saved_exact_int_flags = self._exact_int_env_flags
        saved_global_names = self._current_global_names

        self.current_function = fn
        self.current_func_def = fd
        self._current_global_names = self._collect_explicit_global_names(fd.body)
        entry = fn.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(entry)
        self.env = {}
        self.loop_stack = []
        self._box_int_locals = self._should_box_python_ints()
        self._exact_int_env_flags = {}

        self._emit_thread_safepoint()

        frame = self.builder.call(
            self.runtime["py_list_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh("gen.frame"),
        )
        runtime_args = [a for a in fd.args if a.name != ""]
        arg_by_name = {
            ast_arg.name: (ir_arg, ast_arg)
            for ir_arg, ast_arg in zip(fn.args, runtime_args)
        }
        none_gv = declare_runtime_global(self.module, "py_None")
        none_obj = self.builder.load(none_gv, name=self._fresh("gen.none"))
        for name in frame_names:
            arg_entry = arg_by_name.get(name)
            if arg_entry is None:
                obj = none_obj
            else:
                ir_arg, ast_arg = arg_entry
                obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    ir_arg,
                    ast_arg.annotation or DynType(name="dyn"),
                )
            self.builder.call(self.runtime["py_list_append"], [frame, obj])

        resume_ptr = self.builder.bitcast(
            resume_fn,
            _CSTR,
            name=self._fresh(f"{fd.name}.resume.ptr"),
        )
        gen = self.builder.call(
            self.runtime["py_gen_new"],
            [resume_ptr, frame],
            name=self._fresh(f"{fd.name}.gen"),
        )
        self._gc_release(frame)
        self.builder.ret(gen)

        self.builder = saved_builder
        self.current_function = saved_fn
        self.current_func_def = saved_fd
        self.env = saved_env
        self.loop_stack = saved_loops
        self._box_int_locals = saved_box_int_locals
        self._exact_int_env_flags = saved_exact_int_flags
        self._current_global_names = saved_global_names
    def _emit_generator_resume_function(
        self,
        fd: FuncDef,
        frame_names: list[str],
        symbol_name: Optional[str] = None,
        class_info=None,
        method_kind: Optional[str] = None,
    ) -> ir.Function:
        base_name = symbol_name or self._user_symbol(fd.name)
        name = f"{base_name}__gen_resume"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_CSTR, [_CSTR, _CSTR])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "internal"
        fn.args[0].name = "gen"
        fn.args[1].name = "frame"

        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def
        saved_env = self.env
        saved_env_class_hint = self.env_class_hint
        saved_env_class_object_hint = self.env_class_object_hint
        saved_env_list_elem_class_hint = self.env_list_elem_class_hint
        saved_loops = self.loop_stack
        saved_box_int_locals = self._box_int_locals
        saved_exact_int_flags = self._exact_int_env_flags
        saved_global_names = self._current_global_names
        saved_class = self.current_class
        saved_method_kind = self.current_method_kind

        entry = fn.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(entry)
        self.current_function = fn
        self.current_func_def = fd
        self.env = {}
        self.env_class_hint = {}
        self.env_class_object_hint = {}
        self.env_list_elem_class_hint = {}
        self.loop_stack = []
        self._box_int_locals = self._should_box_python_ints()
        self._exact_int_env_flags = {}
        self._current_global_names = self._collect_explicit_global_names(fd.body)
        self.current_class = class_info
        self.current_method_kind = method_kind

        self._emit_thread_safepoint()

        frame_slots: dict[str, tuple[int, ir.Value]] = {}
        for idx, local_name in enumerate(frame_names):
            slot = self._alloca_in_entry(_CSTR, name=f"{local_name}.addr")
            item = self.builder.call(
                self.runtime["py_list_get"],
                [fn.args[1], ir.Constant(_I64, idx)],
                name=self._fresh(f"gen.frame.{local_name}"),
            )
            self.builder.store(item, slot)
            self.env[local_name] = (slot, _CSTR, DynType(name="dyn"))
            frame_slots[local_name] = (idx, slot)

        dispatch_bb = fn.append_basic_block(name="gen.dispatch")
        start_bb = fn.append_basic_block(name="gen.start")
        self.builder.branch(dispatch_bb)
        self.builder.position_at_end(dispatch_bb)
        state = self.builder.call(
            self.runtime["py_gen_state"],
            [fn.args[0]],
            name=self._fresh("gen.state"),
        )
        switch_inst = self.builder.switch(state, start_bb)
        self.builder.position_at_end(start_bb)
        self._generator_ctx_stack.append({
            "gen": fn.args[0],
            "frame": fn.args[1],
            "frame_slots": frame_slots,
            "dispatch_bb": dispatch_bb,
            "switch": switch_inst,
            "next_state": 1,
        })

        self._emit_stmts(fd.body)
        if not self._builder_block_is_terminated():
            self._emit_generator_finish()

        self.builder = saved_builder
        self.current_function = saved_fn
        self.current_func_def = saved_fd
        self.env = saved_env
        self.env_class_hint = saved_env_class_hint
        self.env_class_object_hint = saved_env_class_object_hint
        self.env_list_elem_class_hint = saved_env_list_elem_class_hint
        self.loop_stack = saved_loops
        self._box_int_locals = saved_box_int_locals
        self._exact_int_env_flags = saved_exact_int_flags
        self._current_global_names = saved_global_names
        self._generator_ctx_stack.pop()
        self.current_class = saved_class
        self.current_method_kind = saved_method_kind
        return fn
    def _emit_generator_add_case(
        self,
        state_id: int,
        target: ir.Block,
    ) -> None:
        ctx = self._generator_ctx_stack[-1]
        cur = self.builder._block
        self.builder.position_at_end(ctx["dispatch_bb"])
        ctx["switch"].add_case(ir.Constant(_I64, state_id), target)
        self.builder.position_at_end(cur)
    def _emit_generator_save_frame(self) -> None:
        ctx = self._generator_ctx_stack[-1]
        frame = ctx["frame"]
        for _name, (idx, slot) in ctx["frame_slots"].items():
            value = self.builder.load(slot, name=self._fresh("gen.save"))
            self.builder.call(
                self.runtime["py_list_set"],
                [frame, ir.Constant(_I64, idx), value],
            )
    def _emit_generator_stop_iteration(
        self,
        value: Optional[ir.Value] = None,
    ) -> None:
        msg = ir.Constant(_CSTR, None)
        tag = ir.Constant(_I64, _STOP_ITERATION_TAG)
        if value is None:
            exc = self.builder.call(
                self.runtime["py_exc_new"],
                [tag, msg],
                name=self._fresh("gen.stop.exc"),
            )
        else:
            exc = self.builder.call(
                self.runtime["py_exc_new_with_value"],
                [tag, value],
                name=self._fresh("gen.stop.exc"),
            )
        self.builder.call(self.runtime["py_raise"], [exc])
        self.builder.ret(ir.Constant(_CSTR, None))
    def _emit_generator_finish(
        self,
        value: Optional[ir.Value] = None,
    ) -> None:
        ctx = self._generator_ctx_stack[-1]
        self._emit_generator_save_frame()
        self.builder.call(self.runtime["py_gen_set_done"], [ctx["gen"]])
        self._emit_generator_stop_iteration(value)
    def _emit_generator_return(self, stmt: Return) -> None:
        value = None
        if stmt.value is not None:
            value = self._emit_as_object(stmt.value)
        self._emit_generator_finish(value)
    def _emit_generator_yield_value(self, value: ir.Value) -> None:
        ctx = self._generator_ctx_stack[-1]
        state_id = ctx["next_state"]
        ctx["next_state"] = state_id + 1
        cont_bb = self.current_function.append_basic_block(
            name=self._fresh(f"gen.resume.{state_id}"),
        )
        self._emit_generator_save_frame()
        self.builder.call(
            self.runtime["py_gen_set_state"],
            [ctx["gen"], ir.Constant(_I64, state_id)],
        )
        self._emit_generator_add_case(state_id, cont_bb)
        self.builder.ret(value)
        self.builder.position_at_end(cont_bb)
        pending = self.builder.call(
            self.runtime["py_err_occurred"],
            [],
            name=self._fresh("gen.resume.err"),
        )
        has_pending = self.builder.icmp_signed(
            "!=",
            pending,
            ir.Constant(_I64, 0),
            name=self._fresh("gen.resume.err.i1"),
        )
        ok_bb = self.current_function.append_basic_block(
            name=self._fresh("gen.resume.ok"),
        )
        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.cbranch(has_pending, err_target, ok_bb)
        self.builder.position_at_end(ok_bb)
    def _emit_generator_take_send(self) -> ir.Value:
        ctx = self._generator_ctx_stack[-1]
        return self.builder.call(
            self.runtime["py_gen_take_send"],
            [ctx["gen"]],
            name=self._fresh("gen.send.value"),
        )
    def _emit_generator_discard_send(self) -> None:
        value = self._emit_generator_take_send()
        self._gc_release(value)
    def _emit_generator_yield_expr(self, expr: Call) -> ir.Value:
        if len(expr.args) > 1:
            raise NotImplementedError("yield accepts at most one value")
        if expr.args:
            value = self._emit_as_object(expr.args[0])
        else:
            value = self._emit_none_literal()
        self._emit_generator_yield_value(value)
        return self._emit_generator_take_send()
    def _emit_generator_yield(self, expr: Call) -> None:
        sent = self._emit_generator_yield_expr(expr)
        self._gc_release(sent)
    def _emit_generator_yield_from(self, expr: Call) -> None:
        if len(expr.args) != 1:
            raise NotImplementedError("yield from expects one iterable")
        ctx = self._generator_ctx_stack[-1]
        hidden = self._generator_yield_from_iter_name(expr)
        frame_entry = ctx["frame_slots"].get(hidden)
        if frame_entry is None:
            raise L1CodegenError("yield from missing generator frame slot")
        iter_slot = frame_entry[1]
        iterable = self._emit_as_object(expr.args[0])
        iterator = self.builder.call(
            self.runtime["py_obj_iter"],
            [iterable],
            name=self._fresh("gen.yf.iter"),
        )
        self._emit_post_call_err_check(expr.span)
        self.builder.store(iterator, iter_slot)

        fn = self.current_function
        header_bb = fn.append_basic_block(name=self._fresh("gen.yf.next"))
        body_bb = fn.append_basic_block(name=self._fresh("gen.yf.body"))
        maybe_end_bb = fn.append_basic_block(name=self._fresh("gen.yf.maybe_end"))
        clear_bb = fn.append_basic_block(name=self._fresh("gen.yf.clear"))
        propagate_bb = fn.append_basic_block(name=self._fresh("gen.yf.propagate"))
        end_bb = fn.append_basic_block(name=self._fresh("gen.yf.end"))

        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        iterator_cur = self.builder.load(
            iter_slot,
            name=self._fresh("gen.yf.iter.cur"),
        )
        item = self.builder.call(
            self.runtime["py_obj_next"],
            [iterator_cur],
            name=self._fresh("gen.yf.item"),
        )
        is_null = self.builder.icmp_unsigned(
            "==",
            item,
            ir.Constant(_CSTR, None),
            name=self._fresh("gen.yf.null"),
        )
        self.builder.cbranch(is_null, maybe_end_bb, body_bb)

        self.builder.position_at_end(body_bb)
        self._emit_generator_yield_value(item)
        self._emit_generator_discard_send()
        if not self._builder_block_is_terminated():
            self.builder.branch(header_bb)

        self.builder.position_at_end(maybe_end_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("gen.yf.cur_exc"),
        )
        stop_cls = self.builder.call(
            self.runtime["py_exc_builtin_class"],
            [ir.Constant(_I64, _STOP_ITERATION_TAG)],
            name=self._fresh("gen.yf.stop_cls"),
        )
        match_i64 = self.builder.call(
            self.runtime["py_exc_matches"],
            [current_exc, stop_cls],
            name=self._fresh("gen.yf.stop_match"),
        )
        is_stop = self.builder.icmp_signed(
            "!=",
            match_i64,
            ir.Constant(_I64, 0),
            name=self._fresh("gen.yf.stop_i1"),
        )
        self.builder.cbranch(is_stop, clear_bb, propagate_bb)

        self.builder.position_at_end(clear_bb)
        self.builder.call(self.runtime["py_clear_exception"], [])
        self.builder.branch(end_bb)

        self.builder.position_at_end(propagate_bb)
        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)

        self.builder.position_at_end(end_bb)
