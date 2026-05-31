"""Tuple and zip builtin lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    Call,
    ClassType,
    DictType,
    DynType,
    ListExpr,
    ListType,
    TupleExpr,
    TupleType,
)
from . import marshal


_I64 = ir.IntType(64)


def _type_name(ty: object) -> str:
    try:
        name = ty.name
    except AttributeError:
        return ""
    return str(name)


def _expr_kind(expr: object) -> str:
    return type(expr).__name__


def _expr_elems(expr: object):
    try:
        return expr.elems
    except AttributeError:
        return None


def _is_literal_tuple_or_list(expr: object) -> bool:
    if isinstance(expr, (ListExpr, TupleExpr)):
        return True
    kind = _expr_kind(expr)
    if kind not in ("ListExpr", "TupleExpr"):
        return False
    return _expr_elems(expr) is not None


def _dict_key_type(ty: object):
    try:
        return ty.key
    except AttributeError:
        return DynType(name="dyn")


class TupleZipLoweringMixin:
    def _maybe_emit_tuple_method(self, expr: Call) -> Optional[ir.Value]:
        """``t.count(x)`` / ``t.index(x)`` for a tuple, via the runtime
        py_tuple_count / py_tuple_index helpers (both return i64). index()
        raises ValueError when absent. Other methods fall back."""
        attr = expr.func
        if not isinstance(attr, Attr):
            return None
        if expr.kwargs or len(expr.args) != 1:
            return None
        name = attr.name
        if name not in ("count", "index"):
            return None
        recv = self._emit_as_object(attr.obj)
        item = self._emit_as_object(expr.args[0])
        if name == "count":
            return self.builder.call(
                self.runtime["py_tuple_count"],
                [recv, item],
                name=self._fresh("tuple.count"),
            )
        res = self.builder.call(
            self.runtime["py_tuple_index"],
            [recv, item],
            name=self._fresh("tuple.index"),
        )
        self._emit_post_call_err_check(getattr(expr, "span", None))
        return res

    def _maybe_emit_tuple_builtin(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        """``tuple()`` / ``tuple([a, b])`` — small subset that matches
        pcc's own usage. Literal lists/tuples fold inline into a new
        ``py_tuple_new`` + per-element ``py_tuple_set_item``. Other
        iterable shapes return None (caller surfaces the original
        unknown-builtin error)."""
        if not expr.args:
            n_val = ir.Constant(_I64, 0)
            return self.builder.call(
                self.runtime["py_tuple_new"],
                [n_val],
                name=self._fresh("tuple.new"),
            )
        arg = expr.args[0]
        elems = _expr_elems(arg)
        if self.module.name == "pcc.parse.py_lift":
            import os
            import sys

            if os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE"):
                try:
                    arg_type = type(arg).__name__
                except AttributeError:
                    arg_type = ""
                try:
                    arg_ty = arg.ty
                    arg_ty_type = type(arg_ty).__name__
                except AttributeError:
                    arg_ty = None
                    arg_ty_type = "<missing>"
                sys.stderr.write(
                    "debug: tuple_builtin arg_type="
                    + str(arg_type)
                    + " arg_ty_type="
                    + str(arg_ty_type)
                    + " arg_ty_name="
                    + str(_type_name(arg_ty))
                    + " elems="
                    + str(elems is not None)
                    + "\n"
                )
        if _is_literal_tuple_or_list(arg) and elems is not None:
            n = len(elems)
            n_val = ir.Constant(_I64, n)
            tup = self.builder.call(
                self.runtime["py_tuple_new"],
                [n_val],
                name=self._fresh("tuple.new"),
            )
            for i, el in enumerate(elems):
                v = self._emit_expr(el)
                v_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    v,
                    el.ty,
                )
                self.builder.call(
                    self.runtime["py_tuple_set_item"],
                    [tup, ir.Constant(_I64, i), v_obj],
                )
            return tup
        # DynType / ListType / generic iterable: get the length,
        # allocate a tuple of that size, fill via py_obj_getitem.
        # DictType iterates keys (matching ``tuple(dict)`` semantics);
        # we materialise the keys as a list first then build the tuple
        # over that list.
        arg_ty = arg.ty
        if isinstance(arg_ty, DictType) or _type_name(arg_ty) == "dict":
            src_val = self._emit_expr(arg)
            src_val = self.builder.call(
                self.runtime["py_dict_keys"],
                [src_val],
                name=self._fresh("tuple.dict.keys"),
            )
            arg_ty = ListType(name="list", elem=_dict_key_type(arg_ty))
            n_val = self.builder.call(
                self.runtime["py_list_len"],
                [src_val],
                name=self._fresh("tuple.dict.keys.len"),
            )
            tup = self.builder.call(
                self.runtime["py_tuple_new"],
                [n_val],
                name=self._fresh("tuple.new"),
            )
            fn = self.current_function
            idx_slot = self._alloca_in_entry(_I64, name="tuple.dk.idx")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("tuple.dk.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("tuple.dk.body"))
            step_bb = fn.append_basic_block(name=self._fresh("tuple.dk.step"))
            end_bb = fn.append_basic_block(name=self._fresh("tuple.dk.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("tuple.dk.i"))
            cond = self.builder.icmp_signed(
                "<",
                cur,
                n_val,
                name=self._fresh("tuple.dk.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            elem = self.builder.call(
                self.runtime["py_list_get"],
                [src_val, cur],
                name=self._fresh("tuple.dk.elem"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [tup, cur, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur,
                ir.Constant(_I64, 1),
                name=self._fresh("tuple.dk.inc"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return tup
        if isinstance(arg_ty, ClassType):
            # A user-class instance (custom __iter__/__next__, no __len__):
            # build a list via the iterator protocol, then convert to a tuple.
            # Matches CPython tuple(x) which iterates via iter(x). Without this,
            # tuple(<custom iterator>) forced the libpython fallback.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, src_val, arg_ty,
            )
            tmp_list = self.builder.call(
                self.runtime["py_list_new"],
                [ir.Constant(_I64, 0)],
                name=self._fresh("tuple.iter.list"),
            )
            self._emit_list_append_via_iter(
                tmp_list, src_obj, getattr(arg, "span", None),
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_list_len"],
                [tmp_list],
                name=self._fresh("tuple.iter.len"),
            )
            tup = self.builder.call(
                self.runtime["py_tuple_new"],
                [n_val],
                name=self._fresh("tuple.iter.new"),
            )
            idx_slot = self._alloca_in_entry(_I64, name="tuple.iter.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("tuple.iter.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("tuple.iter.body"))
            step_bb = fn.append_basic_block(name=self._fresh("tuple.iter.step"))
            end_bb = fn.append_basic_block(name=self._fresh("tuple.iter.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("tuple.iter.i"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("tuple.iter.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            elem = self.builder.call(
                self.runtime["py_list_get"],
                [tmp_list, cur],
                name=self._fresh("tuple.iter.elem"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"], [tup, cur, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1), name=self._fresh("tuple.iter.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return tup
        if isinstance(arg_ty, (ListType, TupleType, DynType)) or _type_name(
            arg_ty
        ) in ("list", "tuple", "tuple_variadic", "dyn"):
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                src_val,
                arg_ty,
            )
            n_val = self.builder.call(
                self.runtime["py_obj_len"],
                [src_obj],
                name=self._fresh("tuple.src.len"),
            )
            tup = self.builder.call(
                self.runtime["py_tuple_new"],
                [n_val],
                name=self._fresh("tuple.new"),
            )
            fn = self.current_function
            idx_slot = self._alloca_in_entry(_I64, name="tuple.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("tuple.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("tuple.body"))
            step_bb = fn.append_basic_block(name=self._fresh("tuple.step"))
            end_bb = fn.append_basic_block(name=self._fresh("tuple.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("tuple.idx"))
            cond = self.builder.icmp_signed(
                "<",
                cur,
                n_val,
                name=self._fresh("tuple.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"],
                [cur],
                name=self._fresh("tuple.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"],
                [src_obj, idx_box],
                name=self._fresh("tuple.elem"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [tup, cur, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur,
                ir.Constant(_I64, 1),
                name=self._fresh("tuple.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return tup
        return None
    def _maybe_emit_zip_builtin(self, expr: Call) -> Optional[ir.Value]:
        """``zip(a, b, ...)`` materialised as a pcc-native list of tuples.

        The Python frontend already normalises ``for ... in zip(...)`` to
        indexed loops. This builtin path covers value-position uses such
        as ``list(zip(xs, ys))`` and dict construction helpers in the
        bootstrap pipeline without pulling in CPython's iterator object.
        """
        if expr.kwargs or not expr.args:
            return None
        if self._is_starred_unpack(expr.args):
            # zip(*rows): runtime-variadic transpose. The static path below
            # fixes the tuple width at len(expr.args); a *splat needs the
            # runtime number of rows, so route to the py_zip_star helper.
            rows_expr = expr.args[0].args[0]
            rows = self._emit_value_as_pcc_object_or_bridge(
                self._emit_expr(rows_expr),
                rows_expr.ty,
                "zip.star.rows",
            )
            return self.builder.call(
                self.runtime["py_zip_star"],
                [rows],
                name=self._fresh("zip.star"),
            )
        src_objs: list[ir.Value] = []
        lengths: list[ir.Value] = []
        for i, arg in enumerate(expr.args):
            raw = self._emit_expr(arg)
            obj = self._emit_value_as_pcc_object_or_bridge(
                raw,
                arg.ty,
                f"zip.arg.{i}.bridge",
            )
            src_objs.append(obj)
            lengths.append(
                self.builder.call(
                    self.runtime["py_obj_len"],
                    [obj],
                    name=self._fresh(f"zip.len.{i}"),
                )
            )
        min_len = lengths[0]
        for i, n_val in enumerate(lengths[1:], start=1):
            take_n = self.builder.icmp_signed(
                "<",
                n_val,
                min_len,
                name=self._fresh(f"zip.min.cmp.{i}"),
            )
            min_len = self.builder.select(
                take_n,
                n_val,
                min_len,
                name=self._fresh(f"zip.min.{i}"),
            )
        result = self.builder.call(
            self.runtime["py_list_new"],
            [min_len],
            name=self._fresh("zip.list"),
        )
        fn = self.current_function
        idx_slot = self._alloca_in_entry(_I64, name="zip.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        cond_bb = fn.append_basic_block(name=self._fresh("zip.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("zip.body"))
        step_bb = fn.append_basic_block(name=self._fresh("zip.step"))
        end_bb = fn.append_basic_block(name=self._fresh("zip.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("zip.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            min_len,
            name=self._fresh("zip.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("zip.idx.box"),
        )
        item = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, len(src_objs))],
            name=self._fresh("zip.item"),
        )
        for i, src_obj in enumerate(src_objs):
            elem = self.builder.call(
                self.runtime["py_obj_getitem"],
                [src_obj, idx_box],
                name=self._fresh(f"zip.elem.{i}"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [item, ir.Constant(_I64, i), elem],
            )
        self.builder.call(self.runtime["py_list_append"], [result, item])
        self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        nxt = self.builder.add(
            cur,
            ir.Constant(_I64, 1),
            name=self._fresh("zip.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        return result
