"""Callable object materialization helpers for Layer-1 codegen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    DictExpr,
    DictType,
    DynType,
    Expr,
    Name,
    SourceSpan,
    StrLit,
    StrType,
    Type,
)
from . import marshal
from .runtime_abi import declare_runtime_global


_I8 = ir.IntType(8)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()


class CallObjectLoweringMixin:
    def _emit_call_args_tuple(self, args: tuple[Expr, ...]) -> ir.Value:
        tup = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, len(args))],
            name=self._fresh("call.args"),
        )
        for i, arg in enumerate(args):
            raw = self._emit_expr(arg)
            if raw in getattr(self, "_cpy_values", ()):
                obj = self.builder.call(
                    self.runtime["py_cpy_to_pcc_obj"],
                    [raw],
                    name=self._fresh("call.arg.bridge"),
                )
                self.builder.call(self.runtime["py_cpy_decref"], [raw])
            else:
                obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    raw,
                    arg.ty,
                )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [tup, ir.Constant(_I64, i), obj],
            )
        return tup

    def _emit_dynamic_call_args_tuple(self, args: tuple[Expr, ...]) -> ir.Value:
        """Materialize args for a pcc-native dynamic callable call.

        The bootstrap-observability shape ``fn(*args, **kwargs)`` passes
        an existing pcc tuple through directly. Mixed starred calls are
        still represented as a pcc list; full tuple-normalization belongs
        with the broader callable ABI work.
        """
        if self._is_starred_unpack(args):
            return self._emit_as_object(args[0].args[0])
        if self._has_starred_unpack(args):
            return self._emit_pcc_args_list(args, "dyn")
        return self._emit_call_args_tuple(args)

    def _emit_dynamic_call_kwargs_object(
        self,
        kwargs: tuple[tuple[str, Expr], ...],
        kwargs_expr: Optional[Expr],
        span: SourceSpan,
    ) -> ir.Value:
        if kwargs_expr is None and not kwargs:
            none_gv = declare_runtime_global(self.module, "py_None")
            return self.builder.load(none_gv, name=self._fresh("none"))
        if kwargs_expr is not None and not kwargs:
            return self._emit_as_object(kwargs_expr)
        pairs: list[tuple[Expr, Expr]] = []
        for kw_name, kw_expr in kwargs:
            pairs.append(
                (
                    StrLit(
                        span=kw_expr.span,
                        ty=StrType(name="str"),
                        value=kw_name,
                    ),
                    kw_expr,
                )
            )
        if kwargs_expr is not None:
            base = self._emit_dict_literal(
                DictExpr(
                    span=span,
                    ty=DictType(
                        name="dict",
                        key=StrType(name="str"),
                        value=DynType(name="dyn"),
                    ),
                    pairs=tuple(pairs),
                )
            )
            star = self._emit_as_object(kwargs_expr)
            merged = self.builder.call(
                self.runtime["py_call_merge_kwargs"],
                [base, star],
                name=self._fresh("call.kwargs.merge"),
            )
            self._gc_release(base)
            return merged
        return self._emit_dict_literal(
            DictExpr(
                span=span,
                ty=DictType(
                    name="dict",
                    key=StrType(name="str"),
                    value=DynType(name="dyn"),
                ),
                pairs=tuple(pairs),
            )
        )

    def _emit_object_tuple_from_values(
        self,
        values: tuple[tuple[ir.Value, Type], ...],
        *,
        name: str,
    ) -> ir.Value:
        tup = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, len(values))],
            name=self._fresh(name),
        )
        for i, (raw, ty) in enumerate(values):
            obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                raw,
                ty,
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [tup, ir.Constant(_I64, i), obj],
            )
        return tup

    def _emit_empty_tuple(self, name: str) -> ir.Value:
        return self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh(name),
        )
