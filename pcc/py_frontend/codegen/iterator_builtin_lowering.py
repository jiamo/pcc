"""Iterator builtin lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Call, Name


_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()
_STOP_ITERATION_TAG = 8


class IteratorBuiltinLoweringMixin:
    def _maybe_emit_iter_builtin(self, expr: Call) -> Optional[ir.Value]:
        if expr.kwargs or len(expr.args) != 1:
            return None
        raw = self._emit_expr(expr.args[0])
        obj = self._emit_value_as_pcc_object_or_bridge(
            raw,
            expr.args[0].ty,
            "iter.bridge",
        )
        result = self.builder.call(
            self.runtime["py_obj_iter"],
            [obj],
            name=self._fresh("iter"),
        )
        self._emit_post_call_err_check(expr.span)
        return result
    def _emit_next_default_or_propagate(
        self,
        expr: Call,
        item: ir.Value,
    ) -> ir.Value:
        fn = self.current_function
        null = ir.Constant(_CSTR, None)
        is_null = self.builder.icmp_unsigned(
            "==",
            item,
            null,
            name=self._fresh("next.null"),
        )
        ok_bb = fn.append_basic_block(name=self._fresh("next.ok"))
        null_bb = fn.append_basic_block(name=self._fresh("next.null_bb"))
        default_bb = fn.append_basic_block(name=self._fresh("next.default"))
        err_bb = fn.append_basic_block(name=self._fresh("next.err"))
        merge_bb = fn.append_basic_block(name=self._fresh("next.merge"))
        self.builder.cbranch(is_null, null_bb, ok_bb)

        self.builder.position_at_end(ok_bb)
        self.builder.branch(merge_bb)
        ok_exit = self.builder._block

        self.builder.position_at_end(null_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("next.cur_exc"),
        )
        stop_cls = self.builder.call(
            self.runtime["py_exc_builtin_class"],
            [ir.Constant(_I64, _STOP_ITERATION_TAG)],
            name=self._fresh("next.stop_cls"),
        )
        match_i64 = self.builder.call(
            self.runtime["py_exc_matches"],
            [current_exc, stop_cls],
            name=self._fresh("next.stop_match"),
        )
        is_stop = self.builder.icmp_signed(
            "!=",
            match_i64,
            ir.Constant(_I64, 0),
            name=self._fresh("next.stop_i1"),
        )
        self.builder.cbranch(is_stop, default_bb, err_bb)

        self.builder.position_at_end(default_bb)
        self.builder.call(self.runtime["py_clear_exception"], [])
        default_obj = self._emit_as_object(expr.args[1])
        self.builder.branch(merge_bb)
        default_exit = self.builder._block

        self.builder.position_at_end(err_bb)
        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)

        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(_CSTR, name=self._fresh("next.result"))
        phi.add_incoming(item, ok_exit)
        phi.add_incoming(default_obj, default_exit)
        return phi
    def _maybe_emit_next_builtin(self, expr: Call) -> Optional[ir.Value]:
        if expr.kwargs or not (1 <= len(expr.args) <= 2):
            return None
        source = expr.args[0]
        # pcc still lowers generator expressions to a materialised list.
        # Preserve next((x for ...), default) by selecting the first
        # element from that list.
        if (
            isinstance(source, Call)
            and isinstance(source.func, Name)
            and source.func.ident in ("_gen_comp", "__genexpr__")
        ):
            src_raw = self._emit_expr(source)
            src_obj = self._emit_value_as_pcc_object_or_bridge(
                src_raw,
                source.ty,
                "next.gen.bridge",
            )
            n_val = self.builder.call(
                self.runtime["py_obj_len"],
                [src_obj],
                name=self._fresh("next.gen.len"),
            )
            is_empty = self.builder.icmp_signed(
                "==",
                n_val,
                ir.Constant(_I64, 0),
                name=self._fresh("next.gen.empty"),
            )
            zero = self.builder.call(
                self.runtime["py_int_from_i64"],
                [ir.Constant(_I64, 0)],
                name=self._fresh("next.gen.zero"),
            )
            first = self.builder.call(
                self.runtime["py_obj_getitem"],
                [src_obj, zero],
                name=self._fresh("next.gen.first"),
            )
            if len(expr.args) == 2:
                default_obj = self._emit_as_object(expr.args[1])
            else:
                default_obj = self._emit_none_literal()
            return self.builder.select(
                is_empty,
                default_obj,
                first,
                name=self._fresh("next.gen"),
            )

        raw = self._emit_expr(source)
        obj = self._emit_value_as_pcc_object_or_bridge(
            raw,
            source.ty,
            "next.bridge",
        )
        item = self.builder.call(
            self.runtime["py_obj_next"],
            [obj],
            name=self._fresh("next"),
        )
        if len(expr.args) == 2:
            return self._emit_next_default_or_propagate(expr, item)
        self._emit_post_call_err_check(expr.span)
        return item
