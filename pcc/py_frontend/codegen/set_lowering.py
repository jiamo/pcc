"""Set lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    Call,
    ClassType,
    DictType,
    DynType,
    Expr,
    ListExpr,
    ListType,
    Name,
    SetType,
    StrType,
    TupleExpr,
    TupleType,
)
from . import marshal


_I1 = ir.IntType(1)
_I64 = ir.IntType(64)

_DYN_SET_METHOD_NATIVE = frozenset(
    {
        "add",
        "remove",
        "discard",
        "update",
        "issubset",
        "issuperset",
        "isdisjoint",
        "union",
        "intersection",
        "difference",
        "symmetric_difference",
        "intersection_update",
        "difference_update",
        "symmetric_difference_update",
        "copy",
        "pop",
    }
)


class SetLoweringMixin:
    def _maybe_emit_set_method_via_dyn(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name not in _DYN_SET_METHOD_NATIVE:
            return None
        return self._maybe_emit_set_method(expr)
    def _maybe_emit_set_method(self, expr: Call) -> Optional[ir.Value]:
        """Dispatch selected pcc-native set methods.

        Set/frozenset values carry a first-class ``SetType`` projection.
        """
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None
        name = attr.name
        if name not in (
            "add", "remove", "discard", "update", "issubset", "issuperset",
            "isdisjoint", "union", "intersection", "difference",
            "symmetric_difference", "intersection_update", "difference_update",
            "symmetric_difference_update", "copy", "pop",
        ):
            return None
        if name in ("copy", "pop"):
            if expr.args:
                return None
        elif len(expr.args) != 1:
            # The 1-arg form covers the common case; multi-arg union/
            # intersection/etc. fall back to the generic path.
            return None
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            return self._emit_cpy_method_call_src(
                recv,
                name,
                expr.args,
                kwargs=expr.kwargs,
            )
        if name == "update":
            self._spread_into_set(recv, expr.args[0])
            return self._emit_none_literal()
        if name in (
            "intersection_update",
            "difference_update",
            "symmetric_difference_update",
        ):
            # In-place mutators: the runtime helper rewrites the receiver's
            # contents in place (preserving receiver identity so aliases see
            # the change) using the corresponding
            # py_set_intersection/difference/symmetric_difference result.
            # They return None, matching CPython.
            fn_name = {
                "intersection_update": "py_set_intersection_update",
                "difference_update": "py_set_difference_update",
                "symmetric_difference_update": "py_set_symmetric_difference_update",
            }[name]
            self.builder.call(
                self.runtime[fn_name],
                [recv, self._emit_as_object(expr.args[0])],
            )
            return self._emit_none_literal()
        if name in ("issubset", "issuperset"):
            fn_name = (
                "py_set_issubset" if name == "issubset" else "py_set_issuperset"
            )
            result = self.builder.call(
                self.runtime[fn_name],
                [recv, self._emit_as_object(expr.args[0])],
                name=self._fresh(f"set.{name}"),
            )
            return self.builder.icmp_signed(
                "!=",
                result,
                ir.Constant(_I64, 0),
                name=self._fresh(f"set.{name}.i1"),
            )
        if name in ("intersection", "difference", "symmetric_difference"):
            # Each returns a NEW set; the runtime helpers already do so.
            fn_name = {
                "intersection": "py_set_intersection",
                "difference": "py_set_difference",
                "symmetric_difference": "py_set_symmetric_difference",
            }[name]
            return self.builder.call(
                self.runtime[fn_name],
                [recv, self._emit_as_object(expr.args[0])],
                name=self._fresh(f"set.{name}"),
            )
        if name in ("union", "copy"):
            # ``a.union(b)`` / ``a.copy()`` build a NEW set: seed it from recv
            # (and, for union, the argument) via py_set_update.
            new_set = self.builder.call(
                self.runtime["py_set_new"], [], name=self._fresh(f"set.{name}.new"),
            )
            self.builder.call(self.runtime["py_set_update"], [new_set, recv])
            if name == "union":
                self.builder.call(
                    self.runtime["py_set_update"],
                    [new_set, self._emit_as_object(expr.args[0])],
                )
            return new_set
        if name == "pop":
            result = self.builder.call(
                self.runtime["py_set_pop"],
                [recv],
                name=self._fresh("set.pop"),
            )
            self._emit_post_call_err_check(expr.span)
            return result
        if name == "isdisjoint":
            inter = self.builder.call(
                self.runtime["py_set_intersection"],
                [recv, self._emit_as_object(expr.args[0])],
                name=self._fresh("set.isdisjoint.inter"),
            )
            n_val = self.builder.call(
                self.runtime["py_obj_len"],
                [inter],
                name=self._fresh("set.isdisjoint.len"),
            )
            return self.builder.icmp_signed(
                "==",
                n_val,
                ir.Constant(_I64, 0),
                name=self._fresh("set.isdisjoint.i1"),
            )
        item = self._emit_expr_as_pcc_object(expr.args[0])
        if name == "add":
            self.builder.call(self.runtime["py_set_add"], [recv, item])
            return self._emit_none_literal()
        removed = self.builder.call(
            self.runtime["py_set_remove"],
            [recv, item],
            name=self._fresh("set.remove"),
        )
        if name == "discard":
            return self._emit_none_literal()
        missing = self.builder.icmp_signed(
            "<",
            removed,
            ir.Constant(_I64, 0),
            name=self._fresh("set.remove.missing"),
        )
        ok_bb = self.current_function.append_basic_block(
            name=self._fresh("set.remove.ok"),
        )
        miss_bb = self.current_function.append_basic_block(
            name=self._fresh("set.remove.miss"),
        )
        end_bb = self.current_function.append_basic_block(
            name=self._fresh("set.remove.end"),
        )
        self.builder.cbranch(missing, miss_bb, ok_bb)
        self.builder.position_at_end(miss_bb)
        exc = self.builder.call(
            self.runtime["py_exc_new"],
            [
                ir.Constant(_I64, 12),
                self._ptr_to_cstr(
                    self._cstr_global(
                        "set.remove(x): x not in set",
                        ".err.set.remove",
                    )
                ),
            ],
            name=self._fresh("set.remove.exc"),
        )
        self.builder.call(self.runtime["py_raise"], [exc])
        self.builder.branch(end_bb)
        self.builder.position_at_end(ok_bb)
        self.builder.branch(end_bb)
        self.builder.position_at_end(end_bb)
        return self._emit_none_literal()
    def _maybe_emit_set_builtin(self, expr: Call) -> Optional[ir.Value]:
        """``set()`` / ``set([a, b])`` / ``set((a, b, c))`` / ``set(iterable)``.

        - no args → empty ``py_set_new``.
        - literal list/tuple → allocate + add each element.
        - any other iterable (ListType / TupleType / DictType /
          DynType) → materialise as PyObject*, iterate via the
          generic ``py_obj_len`` + ``py_obj_getitem``, and add
          each element to the set.
        """
        new_set = self.builder.call(
            self.runtime["py_set_new"],
            [],
            name=self._fresh("set.new"),
        )
        if not expr.args:
            return new_set
        arg = expr.args[0]
        if isinstance(arg, (ListExpr, TupleExpr)):
            for el in arg.elems:
                if (
                    isinstance(el, Call)
                    and isinstance(el.func, Name)
                    and el.func.ident in ("*", "__starred__")
                    and len(el.args) == 1
                ):
                    # ``{x, *iterable}`` — spread iterable into the
                    # new set by iterating py_obj_len / py_obj_getitem
                    # and adding each element. Matches the list-literal
                    # splat ergonomics.
                    self._spread_into_set(new_set, el.args[0])
                    continue
                v_obj = self._emit_expr_as_pcc_object(el)
                self.builder.call(
                    self.runtime["py_set_add"],
                    [new_set, v_obj],
                )
            return new_set
        arg_ty = arg.ty
        if isinstance(arg_ty, SetType):
            src_val = self._emit_expr(arg)
            self.builder.call(
                self.runtime["py_set_update"],
                [new_set, src_val],
            )
            return new_set
        if isinstance(arg_ty, ClassType):
            # A user-class instance (custom __iter__/__next__, no __len__):
            # build a list via the iterator protocol, then add each element to
            # the set. Matches CPython set(x)/frozenset(x) via iter(x). Without
            # this, set(<custom iterator>) forced the libpython fallback.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, src_val, arg_ty,
            )
            tmp_list = self.builder.call(
                self.runtime["py_list_new"],
                [ir.Constant(_I64, 0)],
                name=self._fresh("set.iter.list"),
            )
            self._emit_list_append_via_iter(
                tmp_list, src_obj, getattr(arg, "span", None),
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_list_len"],
                [tmp_list],
                name=self._fresh("set.iter.len"),
            )
            idx_slot = self._alloca_in_entry(_I64, name="set.iter.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("set.iter.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("set.iter.body"))
            step_bb = fn.append_basic_block(name=self._fresh("set.iter.step"))
            end_bb = fn.append_basic_block(name=self._fresh("set.iter.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("set.iter.i"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("set.iter.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            elem = self.builder.call(
                self.runtime["py_list_get"],
                [tmp_list, cur],
                name=self._fresh("set.iter.elem"),
            )
            self.builder.call(self.runtime["py_set_add"], [new_set, elem])
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1), name=self._fresh("set.iter.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_set
        if isinstance(
            arg_ty,
            (ListType, TupleType, DictType, SetType, DynType, StrType),
        ):
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
                name=self._fresh("set.src.len"),
            )
            idx_slot = self._alloca_in_entry(_I64, name="set.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("set.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("set.body"))
            step_bb = fn.append_basic_block(name=self._fresh("set.step"))
            end_bb = fn.append_basic_block(name=self._fresh("set.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("set.idx"))
            cond = self.builder.icmp_signed(
                "<",
                cur,
                n_val,
                name=self._fresh("set.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"],
                [cur],
                name=self._fresh("set.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"],
                [src_obj, idx_box],
                name=self._fresh("set.elem"),
            )
            self.builder.call(
                self.runtime["py_set_add"],
                [new_set, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur,
                ir.Constant(_I64, 1),
                name=self._fresh("set.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_set
        return None
    def _emit_set_union_values(
        self,
        lhs: ir.Value,
        rhs: ir.Value,
    ) -> ir.Value:
        new_set = self.builder.call(
            self.runtime["py_set_new"],
            [],
            name=self._fresh("set.union"),
        )
        self.builder.call(self.runtime["py_set_update"], [new_set, lhs])
        self.builder.call(self.runtime["py_set_update"], [new_set, rhs])
        return new_set
    def _spread_into_set(self, dst_set: ir.Value, src_expr: "Expr") -> None:
        """Iterate ``src_expr`` and ``py_set_add`` each element to
        ``dst_set``. Used to lower the set-literal splat element
        ``{x, *iterable}`` (see ``_maybe_emit_set_builtin``)."""
        src_val = self._emit_expr(src_expr)
        if isinstance(src_expr.ty, SetType):
            self.builder.call(self.runtime["py_set_update"], [dst_set, src_val])
            return
        src_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            src_val,
            src_expr.ty,
        )
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_obj_len"],
            [src_obj],
            name=self._fresh("set.spread.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="set.spread.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        cond_bb = fn.append_basic_block(name=self._fresh("set.spread.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("set.spread.body"))
        step_bb = fn.append_basic_block(name=self._fresh("set.spread.step"))
        end_bb = fn.append_basic_block(name=self._fresh("set.spread.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("set.spread.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("set.spread.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("set.spread.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"],
            [src_obj, idx_box],
            name=self._fresh("set.spread.elem"),
        )
        self.builder.call(
            self.runtime["py_set_add"],
            [dst_set, elem],
        )
        self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        nxt = self.builder.add(
            cur,
            ir.Constant(_I64, 1),
            name=self._fresh("set.spread.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
