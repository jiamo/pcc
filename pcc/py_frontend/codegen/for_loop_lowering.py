"""For-loop lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Assign,
    BoolLit,
    BoolType,
    Break,
    Call,
    DictType,
    DynType,
    Expr,
    For,
    If,
    IntType,
    ListType,
    Name,
    StrType,
    Try,
    TupleExpr,
    TupleType,
    Type,
)
from . import marshal
from .errors import L1CodegenError


_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()
_BUILTIN_EXC_TAG = {
    "StopIteration": 8,
    "StopAsyncIteration": 17,
}


def _for_loop_has_attr(obj, name: str) -> bool:
    return hasattr(obj, name)


def _for_loop_type_name(obj) -> str:
    try:
        return str(obj.ty.name)
    except AttributeError:
        return ""


def _for_loop_is_name(obj) -> bool:
    return isinstance(obj, Name) or _for_loop_has_attr(obj, "ident")


def _for_loop_is_tuple_expr(obj) -> bool:
    if isinstance(obj, TupleExpr):
        return True
    ty_name = _for_loop_type_name(obj)
    return _for_loop_has_attr(obj, "elems") and (
        ty_name == "tuple" or ty_name == "tuple_variadic"
    )


def _for_loop_is_call(obj) -> bool:
    return isinstance(obj, Call) or (
        _for_loop_has_attr(obj, "func")
        and _for_loop_has_attr(obj, "args")
        and _for_loop_has_attr(obj, "kwargs")
    )


def _for_loop_is_call_name(obj, names: tuple[str, ...]) -> bool:
    if not _for_loop_is_call(obj):
        return False
    try:
        return _for_loop_is_name(obj.func) and obj.func.ident in names
    except AttributeError:
        return False


class ForLoopLoweringMixin:
    def _emit_for_cpython_iter(
        self,
        stmt: For,
        iter_src_val: ir.Value,
    ) -> None:
        """Lower ``for <name> in <cpython_iterable>:`` via PyObject_GetIter
        + PyIter_Next. Each iteration binds the target name to the
        returned CPython PyObject* (tagged as cpy)."""
        fn = self.current_function
        iter_obj = self.builder.call(
            self.runtime["py_cpy_iter"],
            [iter_src_val],
            name=self._fresh("cpy.iter"),
        )

        header_bb = fn.append_basic_block(name=self._fresh("for.cpy.header"))
        body_bb = fn.append_basic_block(name=self._fresh("for.cpy.body"))
        latch_bb = fn.append_basic_block(name=self._fresh("for.cpy.latch"))
        after_bb = fn.append_basic_block(name=self._fresh("for.cpy.after"))

        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        item = self.builder.call(
            self.runtime["py_cpy_iter_next"],
            [iter_obj],
            name=self._fresh("cpy.next"),
        )
        is_null = self.builder.icmp_signed(
            "==",
            item,
            ir.Constant(_CSTR, None),
            name=self._fresh("cpy.next.isnull"),
        )
        self.builder.cbranch(is_null, after_bb, body_bb)

        self.builder.position_at_end(body_bb)
        # Bind the target name: alloca if new, then store.
        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            alloca = self._alloca_in_entry(_CSTR, name=f"{target_ident}.addr")
            self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))
            slot = self.env[target_ident]
        self.builder.store(item, slot[0])
        # Mark target as CPython-backed.
        if not hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags = {}
        self._cpy_env_flags[target_ident] = True

        # Loop control stack: continue -> header, break -> after.
        self.loop_stack.append((latch_bb, after_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            # Release item (we took ownership from PyIter_Next).
            # Note: storing into the slot didn't bump ref; we hold
            # exactly one.
            self.builder.branch(latch_bb)

        self.builder.position_at_end(latch_bb)
        self._emit_thread_safepoint()
        self.builder.branch(header_bb)

        self.builder.position_at_end(after_bb)
        self.builder.call(self.runtime["py_cpy_decref"], [iter_obj])
    def _emit_for_list_index(
        self,
        stmt: For,
        iter_val: ir.Value,
        iter_ty: Type,
    ) -> None:
        """Lower ``for <name> in <list|tuple>:`` via index + length.

        Covers ``ListType`` / ``TupleType`` iters where the runtime
        value is a PyObject* tuple/list. Element type flows from
        ``iter_ty.elem`` (list) or ``DynType`` (tuple — element types
        differ per slot, so we fall back to Dyn here).
        """
        fn = self.current_function
        iter_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            iter_val,
            iter_ty,
        )
        if isinstance(iter_ty, ListType):
            len_helper = "py_list_len"
            get_helper = "py_list_get"
            elem_ty: Type = iter_ty.elem
        else:
            len_helper = "py_tuple_len"
            get_helper = "py_tuple_get"
            elem_ty = DynType(name="dyn")
            if isinstance(iter_ty, TupleType) and iter_ty.elems:
                first = iter_ty.elems[0]
                if iter_ty.name == "tuple_variadic" or self._tuple_elems_are_uniform(
                    iter_ty.elems, first
                ):
                    elem_ty = first
        n_val = self.builder.call(
            self.runtime[len_helper],
            [iter_obj],
            name=self._fresh("for.len"),
        )

        idx_slot = self._alloca_in_entry(_I64, name="for.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            # Allocate as PyObject* when element is dyn, else native.
            if isinstance(elem_ty, DynType):
                target_ir_ty = _CSTR
            else:
                target_ir_ty = self._storage_ir_type(elem_ty)
            alloca = self._alloca_in_entry(
                target_ir_ty,
                name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, target_ir_ty, elem_ty)
            slot = self.env[target_ident]

        cond_bb = fn.append_basic_block(name=self._fresh("for.lst.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.lst.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.lst.step"))
        end_bb = fn.append_basic_block(name=self._fresh("for.lst.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("for.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("for.cond"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        target_alloca, target_ir_ty, _ = slot
        if (
            isinstance(iter_ty, ListType)
            and isinstance(elem_ty, IntType)
            and not isinstance(target_ir_ty, ir.PointerType)
        ):
            # Native-i64 fast path: valid only when the loop-variable slot is
            # itself native i64. Under int-boxing (_int_exprs_are_boxed),
            # _storage_ir_type(IntType) is a PyObject* (boxed PyInt), so the
            # slot is a pointer; storing a raw i64 from
            # py_list_get_i64_nonnegative into it is an "i64 but expected ptr"
            # type error (silently a zero-iteration loop under the self
            # backend). When the slot is boxed, fall through to the boxed
            # py_list_get path below, which stores the PyObject* element.
            native_val = self.builder.call(
                self.runtime["py_list_get_i64_nonnegative"],
                [iter_obj, cur],
                name=self._fresh("for.elem.i64"),
            )
            self.builder.store(native_val, target_alloca)
        else:
            elem_obj = self.builder.call(
                self.runtime[get_helper],
                [iter_obj, cur],
                name=self._fresh("for.elem"),
            )
            if isinstance(elem_ty, DynType) or isinstance(target_ir_ty, ir.PointerType):
                self.builder.store(elem_obj, target_alloca)
            else:
                native_val = marshal.marshal_from_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    elem_obj,
                    elem_ty,
                )
                self.builder.store(native_val, target_alloca)

        self.loop_stack.append((step_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("for.idx2"))
        nxt = self.builder.add(
            cur2,
            ir.Constant(_I64, 1),
            name=self._fresh("for.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self._emit_thread_safepoint()
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
    def _emit_for_obj_index(self, stmt: For, iter_val: ir.Value) -> None:
        """DynType for-loop: iterate by index using ``py_obj_len`` +
        ``py_obj_getitem``. Each iteration binds the target to a
        PyObject*; downstream callers see it as DynType."""
        fn = self.current_function
        # If inference pegged the iter as DynType but the IR value is
        # a native scalar (i1 from a short-circuit ``or`` branch,
        # i64 from an unboxed DynType int, etc.), box before calling
        # py_obj_len — the helper expects a pointer operand.
        if not isinstance(iter_val.type, ir.PointerType):
            iter_val = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                iter_val,
                stmt.iter.ty,
            )
        n_val = self.builder.call(
            self.runtime["py_obj_len"],
            [iter_val],
            name=self._fresh("for.obj.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="for.obj.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            alloca = self._alloca_in_entry(
                _CSTR,
                name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))
            slot = self.env[target_ident]

        cond_bb = fn.append_basic_block(name=self._fresh("for.obj.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.obj.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.obj.step"))
        end_bb = fn.append_basic_block(name=self._fresh("for.obj.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("for.obj.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("for.obj.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        # Box the index as a PyObject* int for py_obj_getitem.
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("for.obj.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"],
            [iter_val, idx_box],
            name=self._fresh("for.obj.elem"),
        )
        alloca, _, _ = slot
        self.builder.store(elem, alloca)
        self.loop_stack.append((step_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("for.obj.idx2"))
        nxt = self.builder.add(
            cur2,
            ir.Constant(_I64, 1),
            name=self._fresh("for.obj.next"),
        )
        self.builder.store(nxt, idx_slot)
        self._emit_thread_safepoint()
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
    def _emit_for_obj_iterator(self, stmt: For, iter_val: ir.Value) -> None:
        """DynType for-loop through the native iterator protocol."""
        fn = self.current_function
        if not isinstance(iter_val.type, ir.PointerType):
            iter_val = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                iter_val,
                stmt.iter.ty,
            )
        iterator = self.builder.call(
            self.runtime["py_obj_iter"],
            [iter_val],
            name=self._fresh("for.obj.iter"),
        )
        self._emit_post_call_err_check(stmt.span)
        iter_slot = None
        if len(self._generator_ctx_stack) > 0:
            hidden = self._generator_for_iter_name(stmt)
            frame_entry = self._generator_ctx_stack[-1]["frame_slots"].get(hidden)
            if frame_entry is None:
                raise L1CodegenError("generator for-loop missing iterator frame slot")
            iter_slot = frame_entry[1]
            self.builder.store(iterator, iter_slot)

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None or not self._ir_type_matches(slot[1], _CSTR):
            alloca = self._alloca_in_entry(
                _CSTR,
                name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))
            slot = self.env[target_ident]

        header_bb = fn.append_basic_block(name=self._fresh("for.obj.next"))
        body_bb = fn.append_basic_block(name=self._fresh("for.obj.body"))
        latch_bb = fn.append_basic_block(name=self._fresh("for.obj.latch"))
        maybe_end_bb = fn.append_basic_block(name=self._fresh("for.obj.maybe_end"))
        clear_bb = fn.append_basic_block(name=self._fresh("for.obj.clear"))
        propagate_bb = fn.append_basic_block(name=self._fresh("for.obj.propagate"))
        end_bb = fn.append_basic_block(name=self._fresh("for.obj.end"))

        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        iterator_cur = iterator
        if iter_slot is not None:
            iterator_cur = self.builder.load(
                iter_slot,
                name=self._fresh("for.obj.iter.cur"),
            )
        item = self.builder.call(
            self.runtime["py_obj_next"],
            [iterator_cur],
            name=self._fresh("for.obj.item"),
        )
        is_null = self.builder.icmp_unsigned(
            "==",
            item,
            ir.Constant(_CSTR, None),
            name=self._fresh("for.obj.null"),
        )
        self.builder.cbranch(is_null, maybe_end_bb, body_bb)

        self.builder.position_at_end(body_bb)
        self.builder.store(item, slot[0])
        self.loop_stack.append((latch_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(latch_bb)

        self.builder.position_at_end(latch_bb)
        self._emit_thread_safepoint()
        self.builder.branch(header_bb)

        self.builder.position_at_end(maybe_end_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("for.obj.cur_exc"),
        )
        stop_cls = self.builder.call(
            self.runtime["py_exc_builtin_class"],
            [ir.Constant(_I64, _BUILTIN_EXC_TAG["StopIteration"])],
            name=self._fresh("for.obj.stop_cls"),
        )
        match_i64 = self.builder.call(
            self.runtime["py_exc_matches"],
            [current_exc, stop_cls],
            name=self._fresh("for.obj.stop_match"),
        )
        is_stop = self.builder.icmp_signed(
            "!=",
            match_i64,
            ir.Constant(_I64, 0),
            name=self._fresh("for.obj.stop_i1"),
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
    def _emit_for_str_chars(self, stmt: For, iter_val: ir.Value) -> None:
        """StrType for-loop: iterate codepoints via ``py_str_slice(s, i, i+1, 1)``.
        Target binds to a 1-char StrType slice each iteration."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_str_len"],
            [iter_val],
            name=self._fresh("for.str.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="for.str.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        one_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [ir.Constant(_I64, 1)],
            name=self._fresh("for.str.step"),
        )

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            alloca = self._alloca_in_entry(
                _CSTR,
                name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, _CSTR, StrType(name="str"))
            slot = self.env[target_ident]

        cond_bb = fn.append_basic_block(name=self._fresh("for.str.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.str.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.str.step_bb"))
        end_bb = fn.append_basic_block(name=self._fresh("for.str.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("for.str.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("for.str.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        lo_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("for.str.lo"),
        )
        hi = self.builder.add(
            cur,
            ir.Constant(_I64, 1),
            name=self._fresh("for.str.hi.i64"),
        )
        hi_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [hi],
            name=self._fresh("for.str.hi"),
        )
        ch = self.builder.call(
            self.runtime["py_str_slice"],
            [iter_val, lo_box, hi_box, one_box],
            name=self._fresh("for.str.ch"),
        )
        alloca, _, _ = slot
        self.builder.store(ch, alloca)
        self.loop_stack.append((step_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("for.str.idx2"))
        nxt = self.builder.add(
            cur2,
            ir.Constant(_I64, 1),
            name=self._fresh("for.str.next"),
        )
        self.builder.store(nxt, idx_slot)
        self._emit_thread_safepoint()
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
    def _emit_for_native_iterator(
        self,
        stmt: For,
        iter_val: ir.Value,
        class_hint: str,
    ) -> None:
        iter_info = self._resolve_method_mro(class_hint, "__iter__")
        if iter_info is None:
            return self._emit_for_obj_index(stmt, iter_val)
        iter_fn = iter_info.methods["__iter__"]
        iterator = self._emit_direct_method_call(
            iter_fn,
            iter_val,
            iter_info,
            "__iter__",
            (),
        )
        iter_fd = self.class_lowering._find_method_def(
            iter_info.name,
            "__iter__",
        )
        iterator_hint = class_hint
        if iter_fd is not None:
            ann_hint = self._class_hint_from_annotation(iter_fd.return_ty)
            if ann_hint is not None:
                iterator_hint = ann_hint
        next_info = self._resolve_method_mro(iterator_hint, "__next__")
        if next_info is None:
            return self._emit_for_obj_index(stmt, iterator)
        next_fn = next_info.methods["__next__"]
        next_fd = self.class_lowering._find_method_def(
            next_info.name,
            "__next__",
        )
        target_ty: Type = DynType(name="dyn")
        if next_fd is not None and isinstance(next_fd.return_ty, Type):
            target_ty = next_fd.return_ty
        target_ir_ty = (
            _CSTR
            if isinstance(target_ty, IntType) and self._int_exprs_are_boxed()
            else self._storage_ir_type(target_ty)
        )

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None or slot[1] != target_ir_ty:
            alloca = self._alloca_in_entry(
                target_ir_ty,
                name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, target_ir_ty, target_ty)
            slot = self.env[target_ident]

        fn = self.current_function
        header_bb = fn.append_basic_block(name=self._fresh("for.iter.header"))
        body_bb = fn.append_basic_block(name=self._fresh("for.iter.body"))
        latch_bb = fn.append_basic_block(name=self._fresh("for.iter.latch"))
        err_bb = fn.append_basic_block(name=self._fresh("for.iter.err"))
        after_bb = fn.append_basic_block(name=self._fresh("for.iter.after"))

        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        prev_err_block = getattr(self, "_try_err_block", None)
        self._try_err_block = err_bb
        item = self._emit_direct_method_call(
            next_fn,
            iterator,
            next_info,
            "__next__",
            (),
        )
        self._try_err_block = prev_err_block
        if item.type != target_ir_ty:
            item = self._coerce(item, target_ty, target_ty)
        self.builder.store(item, slot[0])
        self.builder.branch(body_bb)

        self.builder.position_at_end(body_bb)
        self.loop_stack.append((latch_bb, after_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(latch_bb)

        self.builder.position_at_end(latch_bb)
        self._emit_thread_safepoint()
        self.builder.branch(header_bb)

        self.builder.position_at_end(err_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("for.iter.cur_exc"),
        )
        stop_cls = self.builder.call(
            self.runtime["py_exc_builtin_class"],
            [ir.Constant(_I64, _BUILTIN_EXC_TAG["StopIteration"])],
            name=self._fresh("for.iter.stop_cls"),
        )
        match_i64 = self.builder.call(
            self.runtime["py_exc_matches"],
            [current_exc, stop_cls],
            name=self._fresh("for.iter.stop_match"),
        )
        is_stop = self.builder.icmp_signed(
            "!=",
            match_i64,
            ir.Constant(_I64, 0),
            name=self._fresh("for.iter.stop_i1"),
        )
        clear_bb = fn.append_basic_block(name=self._fresh("for.iter.clear"))
        propagate_bb = fn.append_basic_block(name=self._fresh("for.iter.propagate"))
        self.builder.cbranch(is_stop, clear_bb, propagate_bb)
        self.builder.position_at_end(clear_bb)
        self.builder.call(self.runtime["py_clear_exception"], [])
        self.builder.branch(after_bb)
        self.builder.position_at_end(propagate_bb)
        outer = prev_err_block or self._ensure_fn_err_exit()
        self.builder.branch(outer)

        self.builder.position_at_end(after_bb)
    def _emit_async_for(self, stmt: For) -> None:
        if isinstance(stmt.target, TupleExpr):
            stmt = self._normalise_for_tuple_target(stmt)
        if not isinstance(stmt.target, Name):
            raise NotImplementedError(
                "Layer 1 async for target must be a plain Name or TupleExpr"
            )
        class_hint = self._class_hint_for_expr(stmt.iter)
        if class_hint is None:
            raise NotImplementedError(
                "Layer 1 async for needs a pcc-native async iterator"
            )
        aiter_info = self._resolve_method_mro(class_hint, "__aiter__")
        if aiter_info is None:
            raise NotImplementedError("async iterator needs __aiter__")
        aiter_fn = aiter_info.methods.get("__aiter__")
        if aiter_fn is None:
            raise NotImplementedError("async iterator needs __aiter__")

        src_obj = self._emit_expr(stmt.iter)
        iterator = self._emit_direct_method_call(
            aiter_fn,
            src_obj,
            aiter_info,
            "__aiter__",
            (),
        )
        iterator_hint = class_hint
        aiter_fd = self.class_lowering._find_method_def(
            aiter_info.name,
            "__aiter__",
        )
        if aiter_fd is not None:
            ann_hint = self._class_hint_from_annotation(aiter_fd.return_ty)
            if ann_hint is not None:
                iterator_hint = ann_hint
        anext_info = self._resolve_method_mro(iterator_hint, "__anext__")
        if anext_info is None:
            raise NotImplementedError("async iterator needs __anext__")
        anext_fn = anext_info.methods.get("__anext__")
        if anext_fn is None:
            raise NotImplementedError("async iterator needs __anext__")

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None or not self._ir_type_matches(slot[1], _CSTR):
            alloca = self._alloca_in_entry(
                _CSTR,
                name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))
            slot = self.env[target_ident]

        fn = self.current_function
        header_bb = fn.append_basic_block(name=self._fresh("async.for.next"))
        body_bb = fn.append_basic_block(name=self._fresh("async.for.body"))
        latch_bb = fn.append_basic_block(name=self._fresh("async.for.latch"))
        err_bb = fn.append_basic_block(name=self._fresh("async.for.err"))
        clear_bb = fn.append_basic_block(name=self._fresh("async.for.clear"))
        propagate_bb = fn.append_basic_block(name=self._fresh("async.for.propagate"))
        end_bb = fn.append_basic_block(name=self._fresh("async.for.end"))

        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        prev_err_block = getattr(self, "_try_err_block", None)
        self._try_err_block = err_bb
        next_coro = self._emit_direct_method_call(
            anext_fn,
            iterator,
            anext_info,
            "__anext__",
            (),
        )
        item = self.builder.call(
            self.runtime["py_await"],
            [next_coro],
            name=self._fresh("async.for.item"),
        )
        self._emit_post_call_err_check(stmt.span)
        self._try_err_block = prev_err_block
        self.builder.branch(body_bb)

        self.builder.position_at_end(body_bb)
        self.builder.store(item, slot[0])
        self.loop_stack.append((latch_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(latch_bb)

        self.builder.position_at_end(latch_bb)
        self._emit_thread_safepoint()
        self.builder.branch(header_bb)

        self.builder.position_at_end(err_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("async.for.cur_exc"),
        )
        stop_cls = self.builder.call(
            self.runtime["py_exc_builtin_class"],
            [ir.Constant(_I64, _BUILTIN_EXC_TAG["StopAsyncIteration"])],
            name=self._fresh("async.for.stop_cls"),
        )
        match_i64 = self.builder.call(
            self.runtime["py_exc_matches"],
            [current_exc, stop_cls],
            name=self._fresh("async.for.stop_match"),
        )
        is_stop = self.builder.icmp_signed(
            "!=",
            match_i64,
            ir.Constant(_I64, 0),
            name=self._fresh("async.for.stop_i1"),
        )
        self.builder.cbranch(is_stop, clear_bb, propagate_bb)

        self.builder.position_at_end(clear_bb)
        self.builder.call(self.runtime["py_clear_exception"], [])
        self.builder.branch(end_bb)

        self.builder.position_at_end(propagate_bb)
        outer = prev_err_block or self._ensure_fn_err_exit()
        self.builder.branch(outer)

        self.builder.position_at_end(end_bb)
    def _emit_for(self, stmt: For) -> None:
        if stmt.else_body:
            # Desugar for-else into a flag-guarded post-loop if:
            #
            #   for <t> in <iter>:
            #       <body>
            #   else:
            #       <else_body>
            #
            # becomes
            #
            #   __forelse_broke__<k> = False
            #   for <t> in <iter>:
            #       <body>  # any ``break`` in body also sets broke=True
            #   if not __forelse_broke__<k>:
            #       <else_body>
            #
            # Every ``break`` stmt inside ``<body>`` gets rewritten to
            # ``<broke> = True; break``. We don't descend into nested
            # For/While because those have their own iteration scope;
            # a break in a nested loop breaks the inner, not outer.
            from dataclasses import replace as _replace

            broke_name = self._fresh("forelse_broke")
            span = stmt.span
            broke_lit_false = BoolLit(span=span, ty=BoolType(name="bool"), value=False)
            broke_lit_true = BoolLit(span=span, ty=BoolType(name="bool"), value=True)
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
                        # Avoid generator expression — pcc-py self-host
                        # mis-hoists ``for h in ...`` here, leaving ``h``
                        # unbound in the synthesized closure.
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

            # Initialise the broke flag in the enclosing scope.
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
            self._emit_for(new_stmt)
            # Emit the post-loop ``if not broke:`` guard directly on
            # the native i1 flag. Routing this through a synthesized
            # Python-level bool comparison would box the values and can
            # accidentally use object comparison helpers.
            broke_val = self.builder.load(
                alloca,
                name=self._fresh("forelse.broke"),
            )
            should_else = self.builder.icmp_unsigned(
                "==",
                broke_val,
                ir.Constant(ir_ty, 0),
                name=self._fresh("forelse.should_else"),
            )
            else_bb = self.current_function.append_basic_block(
                name=self._fresh("forelse.else"),
            )
            end_bb = self.current_function.append_basic_block(
                name=self._fresh("forelse.end"),
            )
            self.builder.cbranch(should_else, else_bb, end_bb)
            self.builder.position_at_end(else_bb)
            self._emit_stmts(stmt.else_body)
            if not self._builder_block_is_terminated():
                self.builder.branch(end_bb)
            self.builder.position_at_end(end_bb)
            return
        if getattr(stmt, "is_async", False):
            self._emit_async_for(stmt)
            return
        # ``for (i, x) in enumerate(xs):`` — desugar to an indexed
        # iteration so the rest of this function never sees
        # ``enumerate`` as a special iter form.
        if self._for_iter_is_enumerate(stmt):
            stmt = self._normalise_for_enumerate(stmt)
        # ``for (a, b, ...) in zip(xs, ys, ...):`` — desugar to indexed
        # iteration over the shortest-length iterable. The strict=True
        # kwarg is accepted and dropped (pcc doesn't yet raise on
        # length mismatch, but CPython-matching min-length is close
        # enough for stdlib-style usage).
        if self._for_iter_is_zip(stmt):
            stmt = self._normalise_for_zip(stmt)
        # ``for (a, b) in items:`` — normalise by introducing a fresh
        # scalar target and prepending an unpack assign to the loop body.
        if _for_loop_is_tuple_expr(stmt.target):
            stmt = self._normalise_for_tuple_target(stmt)
        if not _for_loop_is_name(stmt.target):
            raise NotImplementedError(
                "Layer 1 for-loop target must be a plain Name or a "
                "TupleExpr of Names"
            )
        # ``for <name> in range(...)`` stays on the L1 fast path.
        is_range_call = _for_loop_is_call_name(stmt.iter, ("range", "xrange"))
        # Inside a generator, range(...) must NOT use the inline induction
        # fast path: that path keeps its loop counter in a raw entry-block
        # alloca which is not part of the persisted generator frame, so after
        # a ``yield`` the resume re-enters with a fresh (reset) counter and the
        # loop terminates after the first item. Materialise the range as a list
        # and drive it through the resumable object-iterator path, whose
        # iterator pointer IS stored in the per-loop frame slot and reloaded on
        # each resume (the same mechanism that makes list/tuple iteration work
        # inside generators). Regression:
        # tests/python/test_python_generator_parity.py
        # ::test_generator_range_loop_resumes.
        if is_range_call and len(self._generator_ctx_stack) > 0:
            range_list = self._emit_range_value_call(stmt.iter)
            return self._emit_for_obj_iterator(stmt, range_list)
        if not is_range_call:
            # CPython iterable? Use PyObject_GetIter + PyIter_Next.
            iter_val = self._emit_expr(stmt.iter)
            if iter_val in getattr(self, "_cpy_values", ()):
                return self._emit_for_cpython_iter(stmt, iter_val)
            if len(self._generator_ctx_stack) > 0 and isinstance(
                stmt.iter.ty,
                (ListType, TupleType, DictType, StrType, DynType),
            ):
                return self._emit_for_obj_iterator(stmt, iter_val)
            # ListType / TupleType iteration via index: length from
            # ``py_{list,tuple}_len``, element via ``py_{list,tuple}_get``.
            iter_ty = stmt.iter.ty
            if isinstance(iter_ty, (ListType, TupleType)):
                return self._emit_for_list_index(
                    stmt,
                    iter_val,
                    iter_ty,
                )
            # DictType: ``for k in d:`` iterates keys. Materialise
            # ``py_dict_keys(d)`` (returns a list) and reuse the
            # list-index loop with the key type.
            if isinstance(iter_ty, DictType):
                keys_val = self.builder.call(
                    self.runtime["py_dict_keys"],
                    [iter_val],
                    name=self._fresh("for.dict.keys"),
                )
                synthetic_ty = ListType(name="list", elem=iter_ty.key)
                return self._emit_for_list_index(
                    stmt,
                    keys_val,
                    synthetic_ty,
                )
            # StrType: ``for ch in s:`` iterates codepoints. Slice each
            # index into a 1-char str — keeps the whole loop libpython-
            # free. The bound target is typed str.
            if isinstance(iter_ty, StrType):
                return self._emit_for_str_chars(stmt, iter_val)
            class_hint = self._class_hint_for_expr(stmt.iter)
            if (
                class_hint is not None
                and self._resolve_method_mro(class_hint, "__next__") is not None
            ):
                return self._emit_for_native_iterator(
                    stmt,
                    iter_val,
                    class_hint,
                )
            # DynType: fall back to ``py_obj_len`` + ``py_obj_getitem``
            # — works for any pcc-native sequence (list, tuple, dict
            # keys, etc.) and stays libpython-free. The bound target is
            # tagged DynType, so subsequent uses see a PyObject*.
            if isinstance(iter_ty, DynType):
                return self._emit_for_obj_iterator(stmt, iter_val)
            if isinstance(iter_val.type, ir.PointerType):
                return self._emit_for_obj_iterator(stmt, iter_val)
            raise NotImplementedError(
                "Layer 1 only handles 'for <name> in range(...)', a "
                "CPython-backed iterable, a list/tuple/dict/dyn "
                "container; other iterables need L3"
            )
        call = stmt.iter
        if call.kwargs:
            raise NotImplementedError("Layer 1 range() has no keyword args")
        if len(call.args) == 1:
            start_val: ir.Value = ir.Constant(_I64, 0)
            stop_val = self._emit_expr_as_i64(call.args[0])
            step_val: ir.Value = ir.Constant(_I64, 1)
        elif len(call.args) == 2:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = ir.Constant(_I64, 1)
        elif len(call.args) == 3:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = self._emit_expr_as_i64(call.args[2])
        else:
            raise L1CodegenError(f"range() takes 1–3 args; got {len(call.args)}")

        # Allocate the loop variable. In normal Python-int mode the range
        # counter stays native i64 as the hot-loop fast path, while the
        # user-visible target is refreshed with a tagged int object on each
        # iteration.
        target_name = stmt.target.ident
        boxed_range_target = self._int_exprs_are_boxed()
        if boxed_range_target:
            counter_alloca = self._alloca_in_entry(
                _I64,
                name=f"{target_name}.range.addr",
            )
            existing = self.env.get(target_name)
            if existing is None or not isinstance(existing[1], ir.PointerType):
                target_alloca = self._alloca_in_entry(
                    _CSTR,
                    name=f"{target_name}.addr",
                )
                self.env[target_name] = (
                    target_alloca,
                    _CSTR,
                    IntType(name="int"),
                )
            else:
                target_alloca = existing[0]
        else:
            existing = self.env.get(target_name)
            if existing is None:
                counter_alloca = self._alloca_in_entry(
                    _I64,
                    name=f"{target_name}.addr",
                )
                self.env[target_name] = (
                    counter_alloca,
                    _I64,
                    IntType(name="int"),
                )
            else:
                counter_alloca, ir_ty, _decl = existing
                if ir_ty is not _I64:
                    # Python loop targets are normal rebindings. A prior
                    # object-typed binding must not poison a later range fast
                    # path for the same name.
                    counter_alloca = self._alloca_in_entry(
                        _I64,
                        name=f"{target_name}.addr",
                    )
                    self.env[target_name] = (
                        counter_alloca,
                        _I64,
                        IntType(name="int"),
                    )
        self.builder.store(start_val, counter_alloca)

        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("for.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.step"))
        end_bb = fn.append_basic_block(name=self._fresh("for.end"))

        # Hoist step as a stable SSA value — we already have it in
        # ``step_val`` so no further work.
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(counter_alloca, name=self._fresh(target_name))
        # Condition depends on step sign: positive step -> i<stop,
        # negative step -> i>stop. We emit both and select.
        zero64 = ir.Constant(_I64, 0)
        step_pos = self.builder.icmp_signed(
            ">", step_val, zero64, name=self._fresh("step_pos")
        )
        cond_pos = self.builder.icmp_signed(
            "<", cur, stop_val, name=self._fresh("fwd_cmp")
        )
        cond_neg = self.builder.icmp_signed(
            ">", cur, stop_val, name=self._fresh("bwd_cmp")
        )
        cond_i1 = self.builder.select(
            step_pos, cond_pos, cond_neg, name=self._fresh("for_cond")
        )
        self.builder.cbranch(cond_i1, body_bb, end_bb)

        self.loop_stack.append((step_bb, end_bb))
        self.builder.position_at_end(body_bb)
        if boxed_range_target:
            cur_body = self.builder.load(
                counter_alloca,
                name=self._fresh(f"{target_name}.body"),
            )
            cur_obj = self.builder.call(
                self.runtime["py_int_from_i64"],
                [cur_body],
                name=self._fresh("range.int.obj"),
            )
            self.builder.store(cur_obj, target_alloca)
            self._exact_int_env_flags[target_name] = True
        self._emit_stmts(stmt.body)
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)
        self.loop_stack.pop()

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(counter_alloca, name=self._fresh(target_name))
        next_val = self.builder.add(cur2, step_val, name=self._fresh("next"))
        self.builder.store(next_val, counter_alloca)
        self._emit_thread_safepoint()
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
    def _emit_range_loop(
        self,
        target: Name,
        call: Call,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        if call.kwargs:
            raise NotImplementedError(
                "range() with keyword args not supported in comprehension"
            )
        if len(call.args) == 1:
            start_val: ir.Value = ir.Constant(_I64, 0)
            stop_val = self._emit_expr_as_i64(call.args[0])
            step_val: ir.Value = ir.Constant(_I64, 1)
        elif len(call.args) == 2:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = ir.Constant(_I64, 1)
        elif len(call.args) == 3:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = self._emit_expr_as_i64(call.args[2])
        else:
            raise L1CodegenError(f"range() takes 1–3 args; got {len(call.args)}")
        target_name = target.ident
        existing = self.env.get(target_name)
        if existing is None:
            alloca = self._alloca_in_entry(_I64, name=f"{target_name}.addr")
            self.env[target_name] = (alloca, _I64, IntType(name="int"))
        else:
            alloca, ir_ty, _decl = existing
            if ir_ty is not _I64:
                # Python comprehension targets rebind like normal locals. A
                # previous object-typed binding should not poison a later
                # range fast path.
                alloca = self._alloca_in_entry(_I64, name=f"{target_name}.addr")
                self.env[target_name] = (alloca, _I64, IntType(name="int"))
        self.builder.store(start_val, alloca)
        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("comp.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.step"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(alloca, name=self._fresh(target_name))
        zero64 = ir.Constant(_I64, 0)
        step_pos = self.builder.icmp_signed(
            ">",
            step_val,
            zero64,
            name=self._fresh("step_pos"),
        )
        cond_pos = self.builder.icmp_signed(
            "<",
            cur,
            stop_val,
            name=self._fresh("fwd_cmp"),
        )
        cond_neg = self.builder.icmp_signed(
            ">",
            cur,
            stop_val,
            name=self._fresh("bwd_cmp"),
        )
        cond_i1 = self.builder.select(
            step_pos,
            cond_pos,
            cond_neg,
            name=self._fresh("comp_cond"),
        )
        self.builder.cbranch(cond_i1, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        self._emit_comprehension_after_bind(
            kind,
            container,
            generators,
            tuple_unpacks,
            idx,
            elt_expr,
            key_expr,
            val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(alloca, name=self._fresh(target_name))
        next_val = self.builder.add(cur2, step_val, name=self._fresh("next"))
        self.builder.store(next_val, alloca)
        self._emit_thread_safepoint()
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
    def _emit_cpy_iter_loop(
        self,
        target: Name,
        iter_src: ir.Value,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Shared CPython-iteration loop for comprehensions."""
        target_name = target.ident
        fn = self.current_function
        iter_obj = self.builder.call(
            self.runtime["py_cpy_iter"],
            [iter_src],
            name=self._fresh("comp.iter"),
        )
        cond_bb = fn.append_basic_block(name=self._fresh("comp.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.body"))
        latch_bb = fn.append_basic_block(name=self._fresh("comp.latch"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        nxt = self.builder.call(
            self.runtime["py_cpy_iter_next"],
            [iter_obj],
            name=self._fresh("comp.next"),
        )
        null_p = ir.Constant(nxt.type, None)
        is_done = self.builder.icmp_unsigned(
            "==",
            nxt,
            null_p,
            name=self._fresh("comp.done"),
        )
        self.builder.cbranch(is_done, end_bb, body_bb)
        self.builder.position_at_end(body_bb)
        existing = self.env.get(target_name)
        if existing is None:
            alloca = self._alloca_in_entry(
                nxt.type,
                name=f"{target_name}.addr",
            )
            self.env[target_name] = (alloca, nxt.type, DynType(name="dyn"))
            if not hasattr(self, "_cpy_env_flags"):
                self._cpy_env_flags = {}
            self._cpy_env_flags[target_name] = True
        else:
            alloca, _, _ = existing
        self.builder.store(nxt, alloca)
        self._emit_comprehension_after_bind(
            kind,
            container,
            generators,
            tuple_unpacks,
            idx,
            elt_expr,
            key_expr,
            val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(latch_bb)
        self.builder.position_at_end(latch_bb)
        self._emit_thread_safepoint()
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
