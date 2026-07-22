"""List builtin lowering helpers for L1CodeGen."""

from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Call,
    ClassType,
    DictType,
    DynType,
    Lambda,
    ListExpr,
    ListType,
    Name,
    NoneLit,
    SetType,
    TupleExpr,
    TupleType,
)
from . import marshal

_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()


class ListBuiltinLoweringMixin:
    def _emit_reversed_builtin(self, expr: Call) -> ir.Value:
        src_val = self._emit_expr(expr.args[0])
        src_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            src_val,
            expr.args[0].ty,
        )
        if isinstance(expr.args[0].ty, DictType):
            # reversed(dict) iterates the KEYS in reverse insertion order.
            # py_obj_getitem on a dict is key lookup (dict[i]), not positional,
            # so the positional reverse loop below would index by integer and
            # return <null>. Reverse the insertion-ordered keys list instead.
            src_obj = self.builder.call(
                self.runtime["py_dict_keys"],
                [src_obj],
                name=self._fresh("reversed.dict.keys"),
            )
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_obj_len"],
            [src_obj],
            name=self._fresh("reversed.len"),
        )
        out = self.builder.call(
            self.runtime["py_list_new"],
            [n_val],
            name=self._fresh("reversed.list"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="reversed.idx.addr")
        start = self.builder.sub(
            n_val,
            ir.Constant(_I64, 1),
            name=self._fresh("reversed.start"),
        )
        self.builder.store(start, idx_slot)
        cond_bb = fn.append_basic_block(name=self._fresh("reversed.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("reversed.body"))
        step_bb = fn.append_basic_block(name=self._fresh("reversed.step"))
        end_bb = fn.append_basic_block(name=self._fresh("reversed.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("reversed.idx"))
        keep_going = self.builder.icmp_signed(
            ">=",
            cur,
            ir.Constant(_I64, 0),
            name=self._fresh("reversed.keep"),
        )
        self.builder.cbranch(keep_going, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("reversed.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"],
            [src_obj, idx_box],
            name=self._fresh("reversed.elem"),
        )
        self.builder.call(self.runtime["py_list_append"], [out, elem])
        self._gc_release(idx_box)
        self._gc_release(elem)
        self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        next_idx = self.builder.sub(
            cur,
            ir.Constant(_I64, 1),
            name=self._fresh("reversed.next"),
        )
        self.builder.store(next_idx, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
        return out

    def _maybe_emit_list_builtin(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        """``list()`` / ``list([a, b])`` / ``list((a, b))`` / ``list(dict_keys)``.

        - no args → empty ``py_list_new(0)``.
        - list/tuple literal → alloc + per-element ``py_list_append``.
        - list-typed arg → same (materialises a copy).
        - dict-typed arg → ``py_dict_keys(d)`` (already a list).
        """
        new_list = self.builder.call(
            self.runtime["py_list_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh("list.new"),
        )
        if not expr.args:
            return new_list
        arg = expr.args[0]
        if isinstance(arg, Call):
            mapped = self._maybe_emit_list_from_map_filter(arg, new_list)
            if mapped is not None:
                return mapped
        if isinstance(arg, (ListExpr, TupleExpr)):
            for el in arg.elems:
                v_obj = self._emit_expr_as_pcc_object(el)
                self.builder.call(
                    self.runtime["py_list_append"],
                    [new_list, v_obj],
                )
            return new_list
        arg_ty = arg.ty
        if isinstance(arg_ty, DictType):
            obj = self._emit_expr(arg)
            return self.builder.call(
                self.runtime["py_dict_keys"],
                [obj],
                name=self._fresh("list.from_dict"),
            )
        if isinstance(arg_ty, ListType):
            src_val = self._emit_expr(arg)
            n_val = self.builder.call(
                self.runtime["py_list_len"],
                [src_val],
                name=self._fresh("list.copy.len"),
            )
            fn = self.current_function
            idx_slot = self._alloca_in_entry(_I64, name="list.copy.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("list.copy.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("list.copy.body"))
            step_bb = fn.append_basic_block(name=self._fresh("list.copy.step"))
            end_bb = fn.append_basic_block(name=self._fresh("list.copy.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("list.copy.idx"))
            cond = self.builder.icmp_signed(
                "<",
                cur,
                n_val,
                name=self._fresh("list.copy.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            elem = self.builder.call(
                self.runtime["py_list_get"],
                [src_val, cur],
                name=self._fresh("list.copy.elem"),
            )
            self.builder.call(
                self.runtime["py_list_append"],
                [new_list, elem],
            )
            self._gc_release(
                elem,
                self._release_context_label("list.copy.elem"),
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur,
                ir.Constant(_I64, 1),
                name=self._fresh("list.copy.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_list
        if isinstance(arg_ty, (DynType, ClassType, SetType)):
            # DynType / a user-class instance / a set may be iterator-only
            # (generator, custom __iter__/__next__, or unordered set: no
            # positional __getitem__).
            # Consume via the iterator protocol — matching CPython's list(x),
            # the statement for-loop, and the comprehension path. Previously a
            # ClassType went through the py_obj_len + py_obj_getitem arm below,
            # so list(CustomIterator()) (no __len__) yielded an empty list. See
            # docs/investigations/sequence-builtins-len-getitem-not-iterator-protocol.md
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                src_val,
                arg_ty,
            )
            return self._emit_list_append_via_iter(
                new_list, src_obj, getattr(arg, "span", None)
            )
        if isinstance(arg_ty, TupleType):
            # Iterate source via py_obj_len + py_obj_getitem and
            # append to a fresh list. Works for any pcc-native
            # container that supports length + index access.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                src_val,
                arg_ty,
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"],
                [src_obj],
                name=self._fresh("list.src.len"),
            )
            idx_slot = self._alloca_in_entry(_I64, name="list.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("list.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("list.body"))
            step_bb = fn.append_basic_block(name=self._fresh("list.step"))
            end_bb = fn.append_basic_block(name=self._fresh("list.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("list.idx"))
            cond = self.builder.icmp_signed(
                "<",
                cur,
                n_val,
                name=self._fresh("list.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"],
                [cur],
                name=self._fresh("list.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"],
                [src_obj, idx_box],
                name=self._fresh("list.elem"),
            )
            self.builder.call(
                self.runtime["py_list_append"],
                [new_list, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur,
                ir.Constant(_I64, 1),
                name=self._fresh("list.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_list
        return None

    def _emit_list_append_via_iter(self, new_list, src_obj, span):
        """Append every item of an iterable to ``new_list`` via the iterator
        protocol (``py_obj_iter`` / ``py_obj_next``). Used for DynType sources
        such as generators that have no length / ``__getitem__``. Mirrors the
        statement for-loop and the comprehension obj-iterator path, clearing a
        terminal StopIteration (tag 8) and propagating any other exception."""
        fn = self.current_function
        iterator = self.builder.call(
            self.runtime["py_obj_iter"],
            [src_obj],
            name=self._fresh("list.iter.obj"),
        )
        self._emit_post_call_err_check(span)
        header_bb = fn.append_basic_block(name=self._fresh("list.iter.next"))
        body_bb = fn.append_basic_block(name=self._fresh("list.iter.body"))
        maybe_end_bb = fn.append_basic_block(name=self._fresh("list.iter.maybe_end"))
        clear_bb = fn.append_basic_block(name=self._fresh("list.iter.clear"))
        propagate_bb = fn.append_basic_block(name=self._fresh("list.iter.propagate"))
        end_bb = fn.append_basic_block(name=self._fresh("list.iter.end"))
        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        item = self.builder.call(
            self.runtime["py_obj_next"],
            [iterator],
            name=self._fresh("list.iter.item"),
        )
        is_null = self.builder.icmp_unsigned(
            "==",
            item,
            ir.Constant(_CSTR, None),
            name=self._fresh("list.iter.null"),
        )
        self.builder.cbranch(is_null, maybe_end_bb, body_bb)
        self.builder.position_at_end(body_bb)
        self.builder.call(self.runtime["py_list_append"], [new_list, item])
        self.builder.branch(header_bb)
        self.builder.position_at_end(maybe_end_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("list.iter.cur_exc"),
        )
        stop_cls = self.builder.call(
            self.runtime["py_exc_builtin_class"],
            [ir.Constant(_I64, 8)],  # StopIteration
            name=self._fresh("list.iter.stop_cls"),
        )
        match_i64 = self.builder.call(
            self.runtime["py_exc_matches"],
            [current_exc, stop_cls],
            name=self._fresh("list.iter.stop_match"),
        )
        is_stop = self.builder.icmp_signed(
            "!=",
            match_i64,
            ir.Constant(_I64, 0),
            name=self._fresh("list.iter.stop_i1"),
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
        return new_list

    def _maybe_emit_list_from_map_filter(
        self,
        call: Call,
        out_list: ir.Value,
    ) -> Optional[ir.Value]:
        # ``filter(None, iterable)`` keeps the truthy elements (no predicate
        # function); args[0] is a NoneLit, not a Name.
        filter_none = (
            isinstance(call.func, Name)
            and call.func.ident == "filter"
            and not call.kwargs
            and len(call.args) == 2
            and isinstance(call.args[0], NoneLit)
        )
        # ``map``/``filter`` with an inline ``lambda`` (1 positional param):
        # bind the param to each element and emit the lambda body inline (it
        # closes over the current scope). args[0] is a Lambda, not a Name.
        lam = None
        if (
            not filter_none
            and isinstance(call.func, Name)
            and call.func.ident in ("map", "filter")
            and not call.kwargs
            and len(call.args) == 2
            and isinstance(call.args[0], Lambda)
            and len(call.args[0].params) == 1
            and call.args[0].params[0].kind in ("pos", "pos_only")
        ):
            lam = call.args[0]
        if not filter_none and lam is None:
            if (
                not isinstance(call.func, Name)
                or call.func.ident not in ("map", "filter")
                or call.kwargs
                or len(call.args) != 2
                or not isinstance(call.args[0], Name)
            ):
                return None
        mode = call.func.ident
        if filter_none or lam is not None:
            func_name = ""
            fn = None
            builtin_map_str = False
        else:
            func_name = call.args[0].ident
            fn = self.functions.get(func_name)
            builtin_map_str = mode == "map" and func_name == "str"
            builtin_map_chr = mode == "map" and func_name == "chr"
            if fn is None and not builtin_map_str and not builtin_map_chr:
                return None
        if filter_none or lam is not None:
            builtin_map_chr = False
        ast_fd = self._find_user_funcdef(func_name) if fn is not None else None
        src_expr = call.args[1]
        src_val = self._emit_expr(src_expr)
        src_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            src_val,
            src_expr.ty,
        )
        n_val = self.builder.call(
            self.runtime["py_obj_len"],
            [src_obj],
            name=self._fresh(f"{mode}.src.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name=f"{mode}.idx.addr")
        item_slot = None
        temp_name = ""
        lam_param = ""
        lam_saved = None
        lam_had_binding = False
        if lam is not None:
            # Bind the lambda's single param to the per-element slot; restore the
            # outer binding (if any) after the loop body is emitted.
            item_slot = self._alloca_in_entry(_CSTR, name=f"{mode}.item.addr")
            lam_param = lam.params[0].name
            if lam_param in self.env:
                lam_had_binding = True
                lam_saved = self.env[lam_param]
            self.env[lam_param] = (item_slot, _CSTR, DynType(name="dyn"))
        elif fn is not None:
            item_slot = self._alloca_in_entry(_CSTR, name=f"{mode}.item.addr")
            temp_name = f"__pcc_{mode}_item_{len(self.env)}"
            self.env[temp_name] = (item_slot, _CSTR, DynType(name="dyn"))
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        fn_cur = self.current_function
        cond_bb = fn_cur.append_basic_block(name=self._fresh(f"{mode}.cond"))
        body_bb = fn_cur.append_basic_block(name=self._fresh(f"{mode}.body"))
        step_bb = fn_cur.append_basic_block(name=self._fresh(f"{mode}.step"))
        end_bb = fn_cur.append_basic_block(name=self._fresh(f"{mode}.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh(f"{mode}.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh(f"{mode}.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh(f"{mode}.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"],
            [src_obj, idx_box],
            name=self._fresh(f"{mode}.elem"),
        )
        if lam is not None:
            # Inline the lambda body with its param bound to the element.
            self.builder.store(elem, item_slot)
            result = self._emit_expr(lam.body)
            result_ty = getattr(lam.body, "ty", None) or DynType(name="dyn")
        elif filter_none:
            # filter(None, ...): keep elements that are truthy themselves.
            result = elem
            result_ty = DynType(name="dyn")
        elif fn is not None:
            self.builder.store(elem, item_slot)
            arg_expr = Name(
                span=call.span,
                ty=DynType(name="dyn"),
                ident=temp_name,
            )
            result = self._emit_direct_user_function_call(
                display_name=func_name,
                fn=fn,
                ast_func_def=ast_fd,
                args=(arg_expr,),
                kwargs=(),
            )
            result_ty = ast_fd.return_ty or DynType(name="dyn")
        elif builtin_map_chr:
            codepoint = self.builder.call(
                self.runtime["py_obj_index_i64"],
                [elem],
                name=self._fresh("map.chr.index"),
            )
            result = self.builder.call(
                self.runtime["py_chr_from_i64"],
                [codepoint],
                name=self._fresh("map.chr"),
            )
            result_ty = DynType(name="dyn")
        else:
            result = self.builder.call(
                self.runtime["py_obj_str"],
                [elem],
                name=self._fresh("map.str"),
            )
            result_ty = DynType(name="dyn")
        if mode == "map":
            result_obj = self._emit_value_as_pcc_object_or_bridge(
                result,
                result_ty,
                f"{mode}.result",
            )
            self.builder.call(
                self.runtime["py_list_append"],
                [out_list, result_obj],
            )
            self.builder.branch(step_bb)
        else:
            keep = self._truthy(result, result_ty)
            append_bb = fn_cur.append_basic_block(name=self._fresh("filter.keep"))
            self.builder.cbranch(keep, append_bb, step_bb)
            self.builder.position_at_end(append_bb)
            self.builder.call(
                self.runtime["py_list_append"],
                [out_list, elem],
            )
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        nxt = self.builder.add(
            cur,
            ir.Constant(_I64, 1),
            name=self._fresh(f"{mode}.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
        if lam is not None:
            # Restore the outer binding for the lambda param name.
            if lam_had_binding:
                self.env[lam_param] = lam_saved
            else:
                self.env.pop(lam_param, None)
        return out_list
