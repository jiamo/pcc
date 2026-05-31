"""Dict builtin and method lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    Call,
    DictExpr,
    DictType,
    DynType,
    Expr,
    Name,
    StrLit,
    StrType,
)
from . import marshal


_I1 = ir.IntType(1)
_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()

_DYN_DICT_METHOD_NATIVE = frozenset(
    {
        "get",
        "keys",
        "values",
        "items",
        "setdefault",
        "pop",
    }
)


def _dict_method_box(host, e: Expr) -> ir.Value:
    v = host._emit_expr(e)
    return marshal.marshal_to_object(
        host.builder,
        host.module,
        host.runtime,
        v,
        e.ty,
    )


class DictLoweringMixin:
    def _maybe_emit_dict_method_via_dyn(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name not in _DYN_DICT_METHOD_NATIVE:
            return None
        dict_ty = DictType(
            name="dict",
            key=DynType(name="dyn"),
            value=DynType(name="dyn"),
        )
        return self._maybe_emit_dict_method(expr, dict_ty)
    def _maybe_emit_dict_method(
        self,
        expr: Call,
        dict_ty: DictType,
    ) -> Optional[ir.Value]:
        """Dispatch selected ``dict`` methods directly to runtime helpers."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs and attr.name != "update":
            return None  # d.update(k=v, ...) is handled below; others fall back
        name = attr.name
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            return self._emit_cpy_method_call_src(
                recv,
                name,
                expr.args,
                kwargs=expr.kwargs,
            )

        if name == "get":
            if len(expr.args) == 1:
                default = self._emit_none_literal()
                return self.builder.call(
                    self.runtime["py_dict_get_default"],
                    [recv, _dict_method_box(self, expr.args[0]), default],
                    name=self._fresh("dict.get"),
                )
            if len(expr.args) == 2:
                return self.builder.call(
                    self.runtime["py_dict_get_default"],
                    [
                        recv,
                        _dict_method_box(self, expr.args[0]),
                        _dict_method_box(self, expr.args[1]),
                    ],
                    name=self._fresh("dict.get.dflt"),
                )
            return None
        if name == "keys":
            if expr.args:
                return None
            return self.builder.call(
                self.runtime["py_dict_keys"],
                [recv],
                name=self._fresh("dict.keys"),
            )
        if name == "values":
            if expr.args:
                return None
            return self.builder.call(
                self.runtime["py_dict_values"],
                [recv],
                name=self._fresh("dict.values"),
            )
        if name == "items":
            if expr.args:
                return None
            return self.builder.call(
                self.runtime["py_dict_items"],
                [recv],
                name=self._fresh("dict.items"),
            )
        if name == "clear":
            if expr.args:
                return None
            self.builder.call(
                self.runtime["py_dict_clear"],
                [recv],
                name=self._fresh("dict.clear"),
            )
            return self._emit_none_literal()
        if name == "update":
            if len(expr.args) > 1:
                return None
            # named keyword pairs only — a ** splat (empty/`*`-prefixed key)
            # falls back to the generic path.
            for kname, _kv in (expr.kwargs or ()):
                if not kname or kname.startswith("*"):
                    return None
            if expr.args:
                self.builder.call(
                    self.runtime["py_dict_update"],
                    [recv, _dict_method_box(self, expr.args[0])],
                    name=self._fresh("dict.update"),
                )
            for kname, kv in (expr.kwargs or ()):
                self.builder.call(
                    self.runtime["py_dict_set"],
                    [
                        recv,
                        self._emit_str_literal(kname),
                        _dict_method_box(self, kv),
                    ],
                    name=self._fresh("dict.update.kw"),
                )
            return self._emit_none_literal()
        if name == "setdefault" and len(expr.args) == 2:
            # ``d.setdefault(k, default)`` — if ``k`` exists, return
            # its value; otherwise insert and return ``default``.
            # Compile to: existing = py_dict_get(d, k); if existing is
            # NULL then py_dict_set(d, k, default); existing = default;
            # return existing.
            k_obj = _dict_method_box(self, expr.args[0])
            default_obj = _dict_method_box(self, expr.args[1])
            fn = self.current_function
            existing = self.builder.call(
                self.runtime["py_dict_get"],
                [recv, k_obj],
                name=self._fresh("setdefault.get"),
            )
            null_p = ir.Constant(_CSTR, None)
            is_missing = self.builder.icmp_signed(
                "==",
                existing,
                null_p,
                name=self._fresh("setdefault.miss"),
            )
            miss_bb = fn.append_basic_block(
                name=self._fresh("setdefault.miss"),
            )
            join_bb = fn.append_basic_block(
                name=self._fresh("setdefault.join"),
            )
            cur_bb = self.builder._block
            self.builder.cbranch(is_missing, miss_bb, join_bb)
            self.builder.position_at_end(miss_bb)
            self.builder.call(
                self.runtime["py_dict_set"],
                [recv, k_obj, default_obj],
            )
            miss_exit = self.builder._block
            self.builder.branch(join_bb)
            self.builder.position_at_end(join_bb)
            phi = self.builder.phi(
                _CSTR,
                name=self._fresh("setdefault.result"),
            )
            phi.add_incoming(default_obj, miss_exit)
            phi.add_incoming(existing, cur_bb)
            return phi
        if name == "pop" and len(expr.args) == 1:
            result = self.builder.call(
                self.runtime["py_dict_pop"],
                [recv, _dict_method_box(self, expr.args[0])],
                name=self._fresh("dict.pop"),
            )
            self._emit_post_call_err_check(expr.span)
            return result
        if name == "pop" and len(expr.args) == 2:
            k_obj = _dict_method_box(self, expr.args[0])
            default_obj = _dict_method_box(self, expr.args[1])
            fn = self.current_function
            existing = self.builder.call(
                self.runtime["py_dict_get"],
                [recv, k_obj],
                name=self._fresh("dict.pop.get"),
            )
            null_p = ir.Constant(_CSTR, None)
            is_missing = self.builder.icmp_signed(
                "==",
                existing,
                null_p,
                name=self._fresh("dict.pop.miss"),
            )
            hit_bb = fn.append_basic_block(name=self._fresh("dict.pop.hit"))
            miss_bb = fn.append_basic_block(name=self._fresh("dict.pop.miss"))
            join_bb = fn.append_basic_block(name=self._fresh("dict.pop.join"))
            self.builder.cbranch(is_missing, miss_bb, hit_bb)
            self.builder.position_at_end(hit_bb)
            self.builder.call(
                self.runtime["py_dict_del"],
                [recv, k_obj],
                name=self._fresh("dict.pop.del"),
            )
            hit_exit = self.builder._block
            self.builder.branch(join_bb)
            self.builder.position_at_end(miss_bb)
            miss_exit = self.builder._block
            self.builder.branch(join_bb)
            self.builder.position_at_end(join_bb)
            phi = self.builder.phi(_CSTR, name=self._fresh("dict.pop.result"))
            phi.add_incoming(existing, hit_exit)
            phi.add_incoming(default_obj, miss_exit)
            return phi
        return None
    def _maybe_emit_dict_builtin(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        """``dict()`` → empty dict. ``dict(k1=v1, k2=v2)`` → set
        each kwarg. ``dict(another_dict)`` where arg is DictType
        → shallow copy via iterator-over-keys.
        Iterable-of-pairs form isn't supported yet."""
        new_dict = self.builder.call(
            self.runtime["py_dict_new"],
            [],
            name=self._fresh("dict.new"),
        )
        # kwargs form
        if not expr.args and expr.kwargs:
            for kw_name, kw_expr in expr.kwargs:
                k_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    self._emit_str_literal(kw_name),
                    StrType(name="str"),
                )
                v = self._emit_expr(kw_expr)
                v_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    v,
                    kw_expr.ty,
                )
                self.builder.call(
                    self.runtime["py_dict_set"],
                    [new_dict, k_obj, v_obj],
                )
            return new_dict
        if not expr.args:
            return new_dict
        arg = expr.args[0]
        arg_ty = arg.ty
        if isinstance(arg_ty, DictType) or isinstance(arg_ty, DynType):
            # Shallow copy of a dict — iterate keys, get values,
            # insert into the new dict.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                src_val,
                arg_ty,
            )
            keys_list = self.builder.call(
                self.runtime["py_dict_keys"],
                [src_obj],
                name=self._fresh("dict.copy.keys"),
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"],
                [keys_list],
                name=self._fresh("dict.copy.len"),
            )
            idx_slot = self._alloca_in_entry(
                _I64,
                name="dict.copy.idx.addr",
            )
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.cond"),
            )
            body_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.body"),
            )
            step_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.step"),
            )
            end_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.end"),
            )
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("idx"))
            cond = self.builder.icmp_signed(
                "<",
                cur,
                n_val,
                name=self._fresh("cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            k_elem = self.builder.call(
                self.runtime["py_list_get"],
                [keys_list, cur],
                name=self._fresh("dict.copy.key"),
            )
            v_elem = self.builder.call(
                self.runtime["py_dict_get"],
                [src_obj, k_elem],
                name=self._fresh("dict.copy.val"),
            )
            self.builder.call(
                self.runtime["py_dict_set"],
                [new_dict, k_elem, v_elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur,
                ir.Constant(_I64, 1),
                name=self._fresh("idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_dict
        return None
