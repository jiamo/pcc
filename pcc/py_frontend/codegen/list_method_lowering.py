"""List method lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    BoolLit,
    Call,
    DynType,
    Expr,
    IntLit,
    Lambda,
    ListType,
    Name,
    StrType,
    Subscript,
    TupleExpr,
    Type,
    UnaryOp,
)
from . import marshal


_I1 = ir.IntType(1)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()

# No-arg str methods usable as an inline ``key=lambda s: s.<m>()`` over a list
# of str (case-insensitive sort etc.); each maps to ``py_str_<m>``.
_NOARG_STR_KEY_METHODS = frozenset(
    {"lower", "upper", "casefold", "title", "capitalize", "swapcase", "strip"}
)

_DYN_LIST_METHOD_NATIVE = frozenset(
    {
        "append",
        "extend",
        "insert",
        "pop",
        "remove",
        "index",
        "count",
        "sort",
        "clear",
    }
)


def _list_method_box(host, e: Expr) -> ir.Value:
    v = host._emit_expr(e)
    return marshal.marshal_to_object(
        host.builder,
        host.module,
        host.runtime,
        v,
        e.ty,
    )


class ListMethodLoweringMixin:
    def _maybe_emit_list_method_via_dyn(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name not in _DYN_LIST_METHOD_NATIVE:
            return None
        list_ty = ListType(name="list", elem=DynType(name="dyn"))
        return self._maybe_emit_list_method(expr, list_ty)
    def _maybe_emit_list_method(
        self,
        expr: Call,
        list_ty: ListType,
    ) -> Optional[ir.Value]:
        """Dispatch selected ``list`` methods directly to runtime helpers
        (libpython-free). Returns None when the method isn't in the fast
        path so callers can fall through to the generic dispatch."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs and not (
            attr.name == "sort"
            and all(
                k == "reverse" and isinstance(v, BoolLit)
                for k, v in expr.kwargs
            )
        ):
            return None  # generic path handles or errors
            # (sort(reverse=<bool const>) is allowed through and handled below)
        # Starred argument (``lst.method(*args)``) — bail to generic
        # CPython dispatch which handles splats via py_cpy_call_list.
        if any(
            isinstance(a, Call)
            and isinstance(a.func, Name)
            and a.func.ident in ("*", "__starred__")
            for a in expr.args
        ):
            return None
        name = attr.name
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            return self._emit_cpy_method_call_src(
                recv,
                name,
                expr.args,
                kwargs=expr.kwargs,
            )

        if name == "sort":
            if expr.args:
                return None
            # sort(reverse=<bool const>): sort then reverse in place. key= and a
            # non-constant reverse were already bounced by the kwargs guard.
            reverse_const = False
            for k, v in (expr.kwargs or ()):
                if k == "reverse" and isinstance(v, BoolLit):
                    reverse_const = bool(v.value)
            elem_hint = None
            if isinstance(attr.obj, Name):
                elem_hint = self.env_list_elem_class_hint.get(attr.obj.ident)
            if elem_hint is not None and not reverse_const:
                return self._emit_list_sort_with_dunder_lt(
                    recv,
                    elem_hint,
                    list_ty.elem,
                )
            if elem_hint is not None and reverse_const:
                return None  # custom-class element + reverse: rare, fall back
            sorted_list = self.builder.call(
                self.runtime["py_obj_sorted"],
                [recv],
                name=self._fresh("list.sort.sorted"),
            )
            if reverse_const:
                self.builder.call(
                    self.runtime["py_list_reverse"],
                    [sorted_list],
                )
            none_obj = self._emit_none_literal()
            self.builder.call(
                self.runtime["py_list_set_slice"],
                [recv, none_obj, none_obj, none_obj, sorted_list],
            )
            self._gc_release(sorted_list)
            return none_obj

        if name == "append":
            if len(expr.args) != 1:
                return None
            if isinstance(attr.obj, Name):
                item_kind = self._threading_constructor_kind_for_expr(expr.args[0])
                if item_kind is not None:
                    current_kind = self._threading_list_elem_flags.get(
                        attr.obj.ident
                    )
                    if current_kind is None or current_kind == item_kind:
                        self._threading_list_elem_flags[attr.obj.ident] = item_kind
                    else:
                        self._threading_list_elem_flags.pop(attr.obj.ident, None)
            item = _list_method_box(self, expr.args[0])
            self.builder.call(
                self.runtime["py_list_append"],
                [recv, item],
            )
            if self._container_store_temp_needs_release(
                expr.args[0],
                expr.args[0].ty,
                False,
            ):
                self._gc_release(item)
            self._gc_release_if_owned(recv, attr.obj)
            return ir.Constant(_I1, 0)
        if name == "extend":
            if len(expr.args) != 1:
                return None
            self.builder.call(
                self.runtime["py_list_extend"],
                [recv, _list_method_box(self, expr.args[0])],
            )
            return ir.Constant(_I1, 0)
        if name == "insert":
            if len(expr.args) != 2:
                return None
            idx_val = self._emit_expr_as_i64(expr.args[0])
            self.builder.call(
                self.runtime["py_list_insert"],
                [recv, idx_val, _list_method_box(self, expr.args[1])],
            )
            return ir.Constant(_I1, 0)
        if name == "pop":
            if len(expr.args) == 0:
                idx_val = ir.Constant(_I64, -1)
            elif len(expr.args) == 1:
                idx_val = self._emit_expr_as_i64(expr.args[0])
            else:
                return None
            popped = self.builder.call(
                self.runtime["py_list_pop"],
                [recv, idx_val],
                name=self._fresh("list.pop"),
            )
            if not isinstance(list_ty.elem, DynType):
                return marshal.marshal_from_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    popped,
                    list_ty.elem,
                )
            return popped
        if name == "remove":
            if len(expr.args) != 1:
                return None
            self.builder.call(
                self.runtime["py_list_remove"],
                [recv, _list_method_box(self, expr.args[0])],
            )
            return ir.Constant(_I1, 0)
        if name == "clear":
            if len(expr.args) != 0:
                return None
            self.builder.call(self.runtime["py_list_clear"], [recv])
            return ir.Constant(_I1, 0)
        if name == "index":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_list_index"],
                [recv, _list_method_box(self, expr.args[0])],
                name=self._fresh("list.index"),
            )
        if name == "count":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_list_count"],
                [recv, _list_method_box(self, expr.args[0])],
                name=self._fresh("list.count"),
            )
        if name == "reverse":
            if len(expr.args) != 0:
                return None
            self.builder.call(self.runtime["py_list_reverse"], [recv])
            return ir.Constant(_I1, 0)
        return None
    def _emit_list_sort_with_dunder_lt(
        self,
        recv: ir.Value,
        elem_hint: str,
        elem_ty: Type,
    ) -> ir.Value:
        lt_info = self._resolve_method_mro(elem_hint, "__lt__")
        if lt_info is None:
            return self._emit_none_literal()
        lt_fn = lt_info.methods["__lt__"]

        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_list_len"],
            [recv],
            name=self._fresh("list.sort.len"),
        )
        i_slot = self._alloca_in_entry(_I64, name="list.sort.i.addr")
        j_slot = self._alloca_in_entry(_I64, name="list.sort.j.addr")
        cur_slot = self._alloca_in_entry(_CSTR, name="list.sort.cur.addr")
        self.builder.store(ir.Constant(_I64, 1), i_slot)

        outer_cond = fn.append_basic_block(name=self._fresh("list.sort.cond"))
        outer_body = fn.append_basic_block(name=self._fresh("list.sort.body"))
        inner_cond = fn.append_basic_block(name=self._fresh("list.sort.inner"))
        inner_cmp = fn.append_basic_block(name=self._fresh("list.sort.cmp"))
        inner_shift = fn.append_basic_block(name=self._fresh("list.sort.shift"))
        inner_done = fn.append_basic_block(name=self._fresh("list.sort.place"))
        outer_step = fn.append_basic_block(name=self._fresh("list.sort.step"))
        done_bb = fn.append_basic_block(name=self._fresh("list.sort.done"))

        self.builder.branch(outer_cond)
        self.builder.position_at_end(outer_cond)
        i_val = self.builder.load(i_slot, name=self._fresh("list.sort.i"))
        outer_ok = self.builder.icmp_signed(
            "<",
            i_val,
            n_val,
            name=self._fresh("list.sort.outer_ok"),
        )
        self.builder.cbranch(outer_ok, outer_body, done_bb)

        self.builder.position_at_end(outer_body)
        cur = self.builder.call(
            self.runtime["py_list_get"],
            [recv, i_val],
            name=self._fresh("list.sort.cur"),
        )
        self.builder.store(cur, cur_slot)
        self.builder.store(i_val, j_slot)
        self.builder.branch(inner_cond)

        self.builder.position_at_end(inner_cond)
        j_val = self.builder.load(j_slot, name=self._fresh("list.sort.j"))
        j_gt_zero = self.builder.icmp_signed(
            ">",
            j_val,
            ir.Constant(_I64, 0),
            name=self._fresh("list.sort.j_gt_zero"),
        )
        self.builder.cbranch(j_gt_zero, inner_cmp, inner_done)

        self.builder.position_at_end(inner_cmp)
        j_prev = self.builder.sub(
            j_val,
            ir.Constant(_I64, 1),
            name=self._fresh("list.sort.j_prev"),
        )
        prev = self.builder.call(
            self.runtime["py_list_get"],
            [recv, j_prev],
            name=self._fresh("list.sort.prev"),
        )
        cur = self.builder.load(cur_slot, name=self._fresh("list.sort.cur2"))
        less = self._emit_direct_method_value_call(
            lt_fn,
            cur,
            lt_info,
            "__lt__",
            ((prev, elem_ty),),
        )
        if less.type is not _I1:
            if isinstance(less.type, ir.IntType):
                less = self.builder.icmp_signed(
                    "!=",
                    less,
                    ir.Constant(less.type, 0),
                    name=self._fresh("list.sort.less_i1"),
                )
            else:
                truth = self.builder.call(
                    self.runtime["py_obj_truthy"],
                    [less],
                    name=self._fresh("list.sort.less_truthy"),
                )
                less = self.builder.icmp_signed(
                    "!=",
                    truth,
                    ir.Constant(_I32, 0),
                    name=self._fresh("list.sort.less_truthy_i1"),
                )
        self.builder.cbranch(less, inner_shift, inner_done)

        self.builder.position_at_end(inner_shift)
        self.builder.call(self.runtime["py_list_set"], [recv, j_val, prev])
        self.builder.store(j_prev, j_slot)
        self.builder.branch(inner_cond)

        self.builder.position_at_end(inner_done)
        j_final = self.builder.load(j_slot, name=self._fresh("list.sort.j_final"))
        cur_final = self.builder.load(cur_slot, name=self._fresh("list.sort.cur3"))
        self.builder.call(
            self.runtime["py_list_set"],
            [recv, j_final, cur_final],
        )
        self.builder.branch(outer_step)

        self.builder.position_at_end(outer_step)
        i_next = self.builder.add(
            self.builder.load(i_slot, name=self._fresh("list.sort.i2")),
            ir.Constant(_I64, 1),
            name=self._fresh("list.sort.i_next"),
        )
        self.builder.store(i_next, i_slot)
        self.builder.branch(outer_cond)

        self.builder.position_at_end(done_bb)
        return self._emit_none_literal()

    def _sorted_key_spec_from_lambda(self, key_lambda, elem_ty=None):
        """Detect a simple single-param ``key=`` lambda we can inline without
        first-class-function boxing. Returns ('attr', (parts,...)) for
        ``lambda x: x.a.b``, ('index', N) for ``lambda x: x[N]`` (int literal),
        ('strmethod', name) for ``lambda s: s.lower()`` when ``elem_ty`` is str,
        ('tuple', (subspec,...)) for ``lambda x: (x.a, x.b)`` (multi-key sort),
        or None (caller falls through to the libpython path). ``elem_ty`` is the
        iterable's element type (enables the str-method shape only on str)."""
        if not isinstance(key_lambda, Lambda):
            return None
        if len(key_lambda.params) != 1 or key_lambda.params[0].name == "":
            return None
        param = key_lambda.params[0].name
        body = key_lambda.body
        # Tuple key: (x.a, x.b) -> compare element keys lexicographically (the
        # runtime py_obj_lt already orders tuples). Each component must itself
        # be a simple attr/index subspec (no str-method components).
        if isinstance(body, TupleExpr):
            if not body.elems:
                return None
            subspecs = []
            for el in body.elems:
                sub = self._simple_key_subspec(el, param)
                if sub is None:
                    return None
                subspecs.append(sub)
            return ("tuple", tuple(subspecs))
        return self._simple_key_subspec(body, param, elem_ty)

    def _simple_key_subspec(self, expr, param, elem_ty=None):
        """('attr', parts) for ``param.a.b`` / ('index', N) for ``param[N]``
        (int literal) / ('strmethod', name) for a no-arg str method
        ``param.lower()`` when ``elem_ty`` is str / ('neg', subspec) for
        ``-param.a`` / ``-param[N]`` (descending key), else None. The scalar key
        shapes that compose a tuple key or stand alone."""
        # Unary negation of an attr/index key -> descending sort by that key
        # (e.g. key=lambda kv: -kv[1], or as a tuple component (-kv[1], kv[0])).
        # Emitted as the generic 0 - key (py_obj_sub), correct for int+float.
        if isinstance(expr, UnaryOp) and expr.op == "-":
            inner = self._simple_key_subspec(expr.operand, param, elem_ty)
            if inner is not None and inner[0] in ("attr", "index"):
                return ("neg", inner)
            return None
        parts: list[str] = []
        cur = expr
        while isinstance(cur, Attr):
            parts.append(cur.name)
            cur = cur.obj
        if isinstance(cur, Name) and cur.ident == param and parts:
            parts.reverse()
            return ("attr", tuple(parts))
        if (
            isinstance(expr, Subscript)
            and isinstance(expr.obj, Name)
            and expr.obj.ident == param
            and isinstance(expr.idx, IntLit)
        ):
            return ("index", expr.idx.value)
        # No-arg str method on a str element: lambda s: s.lower() etc. Gated on
        # str elements so py_str_<m> is safe; non-str / unknown -> None (the
        # caller falls back to libpython, which handles it correctly).
        if (
            isinstance(elem_ty, StrType)
            and isinstance(expr, Call)
            and not expr.args
            and not expr.kwargs
            and isinstance(expr.func, Attr)
            and isinstance(expr.func.obj, Name)
            and expr.func.obj.ident == param
            and expr.func.name in _NOARG_STR_KEY_METHODS
        ):
            return ("strmethod", expr.func.name)
        return None

    def _emit_key_of(self, obj, key_spec):
        """Inline the simple key extraction described by ``key_spec`` against
        element object ``obj`` (PyObject*), returning the key PyObject*."""
        kind, data = key_spec
        if kind == "tuple":
            # Build a tuple of the component keys; py_obj_lt orders tuples
            # lexicographically. Mirrors the tuple-literal lowering (no extra
            # incref before py_tuple_set_item).
            tup = self.builder.call(
                self.runtime["py_tuple_new"],
                [ir.Constant(_I64, len(data))],
                name=self._fresh("sortkey.tuple"),
            )
            for i, subspec in enumerate(data):
                sub = self._emit_key_of(obj, subspec)
                self.builder.call(
                    self.runtime["py_tuple_set_item"],
                    [tup, ir.Constant(_I64, i), sub],
                )
            return tup
        if kind == "attr":
            cur = obj
            for part in data:
                cur = self.builder.call(
                    self.runtime["py_obj_getattr"],
                    [cur, self._attr_name_ptr(part)],
                    name=self._fresh("sortkey.attr"),
                )
            return cur
        if kind == "strmethod":
            # No-arg str method (gated to str elements at spec time):
            # py_str_<m>(obj) returns the transformed str key.
            return self.builder.call(
                self.runtime["py_str_" + data],
                [obj],
                name=self._fresh("sortkey.strmethod"),
            )
        if kind == "neg":
            # Descending key: 0 - subkey via the generic py_obj_sub (handles
            # int and float keys). ``data`` is the wrapped attr/index subspec.
            sub = self._emit_key_of(obj, data)
            zero = self.builder.call(
                self.runtime["py_int_from_i64"],
                [ir.Constant(_I64, 0)],
                name=self._fresh("sortkey.neg.zero"),
            )
            return self.builder.call(
                self.runtime["py_obj_sub"],
                [zero, sub],
                name=self._fresh("sortkey.neg"),
            )
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [ir.Constant(_I64, data)],
            name=self._fresh("sortkey.idx.box"),
        )
        return self.builder.call(
            self.runtime["py_obj_getitem"],
            [obj, idx_box],
            name=self._fresh("sortkey.item"),
        )

    def _emit_sorted_with_key_lambda(self, expr, key_lambda, reverse_const):
        """``sorted(xs, key=<simple lambda>)`` without first-class-function
        boxing: build a COPY (sorted() is non-mutating) and insertion-sort it
        comparing inline-extracted keys via py_obj_lt (correct for int/str/
        float keys; a custom-__lt__ key object would hit the cmp_threeway
        limitation, but sort keys are virtually always primitives). Returns the
        new list, or None when the lambda is not a simple attr/index shape
        (caller then falls through to the libpython path)."""
        elem_ty = (
            expr.args[0].ty.elem
            if isinstance(expr.args[0].ty, ListType)
            else None
        )
        key_spec = self._sorted_key_spec_from_lambda(key_lambda, elem_ty)
        if key_spec is None:
            return None
        src_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            self._emit_expr(expr.args[0]),
            expr.args[0].ty,
        )
        new_list = self.builder.call(
            self.runtime["py_list_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh("sorted.key.copy"),
        )
        self.builder.call(self.runtime["py_list_extend"], [new_list, src_obj])
        self._emit_list_insertion_sort_by_key(new_list, key_spec)
        if reverse_const:
            self.builder.call(self.runtime["py_list_reverse"], [new_list])
        return new_list

    def _emit_list_insertion_sort_by_key(self, recv, key_spec):
        """In-place insertion sort of list ``recv`` ordering by
        ``py_obj_lt(key(elem), key(prev))``. Mirrors
        _emit_list_sort_with_dunder_lt's CFG, swapping the per-pair compare."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_list_len"], [recv], name=self._fresh("sortkey.len")
        )
        i_slot = self._alloca_in_entry(_I64, name="sortkey.i.addr")
        j_slot = self._alloca_in_entry(_I64, name="sortkey.j.addr")
        cur_slot = self._alloca_in_entry(_CSTR, name="sortkey.cur.addr")
        self.builder.store(ir.Constant(_I64, 1), i_slot)
        outer_cond = fn.append_basic_block(name=self._fresh("sortkey.ocond"))
        outer_body = fn.append_basic_block(name=self._fresh("sortkey.obody"))
        inner_cond = fn.append_basic_block(name=self._fresh("sortkey.icond"))
        inner_cmp = fn.append_basic_block(name=self._fresh("sortkey.icmp"))
        inner_shift = fn.append_basic_block(name=self._fresh("sortkey.ishift"))
        inner_done = fn.append_basic_block(name=self._fresh("sortkey.iplace"))
        outer_step = fn.append_basic_block(name=self._fresh("sortkey.ostep"))
        done_bb = fn.append_basic_block(name=self._fresh("sortkey.odone"))

        self.builder.branch(outer_cond)
        self.builder.position_at_end(outer_cond)
        i_val = self.builder.load(i_slot, name=self._fresh("sortkey.i"))
        outer_ok = self.builder.icmp_signed(
            "<", i_val, n_val, name=self._fresh("sortkey.outer_ok")
        )
        self.builder.cbranch(outer_ok, outer_body, done_bb)

        self.builder.position_at_end(outer_body)
        cur = self.builder.call(
            self.runtime["py_list_get"], [recv, i_val], name=self._fresh("sortkey.cur")
        )
        self.builder.store(cur, cur_slot)
        self.builder.store(i_val, j_slot)
        self.builder.branch(inner_cond)

        self.builder.position_at_end(inner_cond)
        j_val = self.builder.load(j_slot, name=self._fresh("sortkey.j"))
        j_gt_zero = self.builder.icmp_signed(
            ">", j_val, ir.Constant(_I64, 0), name=self._fresh("sortkey.j_gt_zero")
        )
        self.builder.cbranch(j_gt_zero, inner_cmp, inner_done)

        self.builder.position_at_end(inner_cmp)
        j_prev = self.builder.sub(
            j_val, ir.Constant(_I64, 1), name=self._fresh("sortkey.j_prev")
        )
        prev = self.builder.call(
            self.runtime["py_list_get"], [recv, j_prev], name=self._fresh("sortkey.prev")
        )
        cur = self.builder.load(cur_slot, name=self._fresh("sortkey.cur2"))
        key_cur = self._emit_key_of(cur, key_spec)
        key_prev = self._emit_key_of(prev, key_spec)
        less_i64 = self.builder.call(
            self.runtime["py_obj_lt"],
            [key_cur, key_prev],
            name=self._fresh("sortkey.cmp.lt"),
        )
        less = self.builder.icmp_signed(
            "!=",
            less_i64,
            ir.Constant(less_i64.type, 0),
            name=self._fresh("sortkey.cmp.i1"),
        )
        self.builder.cbranch(less, inner_shift, inner_done)

        self.builder.position_at_end(inner_shift)
        self.builder.call(self.runtime["py_list_set"], [recv, j_val, prev])
        self.builder.store(j_prev, j_slot)
        self.builder.branch(inner_cond)

        self.builder.position_at_end(inner_done)
        j_final = self.builder.load(j_slot, name=self._fresh("sortkey.j_final"))
        cur_final = self.builder.load(cur_slot, name=self._fresh("sortkey.cur3"))
        self.builder.call(self.runtime["py_list_set"], [recv, j_final, cur_final])
        self.builder.branch(outer_step)

        self.builder.position_at_end(outer_step)
        i_next = self.builder.add(
            self.builder.load(i_slot, name=self._fresh("sortkey.i2")),
            ir.Constant(_I64, 1),
            name=self._fresh("sortkey.i_next"),
        )
        self.builder.store(i_next, i_slot)
        self.builder.branch(outer_cond)

        self.builder.position_at_end(done_bb)
