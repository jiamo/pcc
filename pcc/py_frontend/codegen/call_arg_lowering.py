"""Call argument shape helpers for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import Call, Expr, Name
from . import marshal


_I64 = ir.IntType(64)


class CallArgLoweringMixin:
    def _call_arg_node_kind(self, node: object) -> str:
        try:
            return type(node).__name__
        except AttributeError:
            return ""

    def _is_call_arg_call_node(self, node: object) -> bool:
        return isinstance(node, Call) or self._call_arg_node_kind(node) == "Call"

    def _is_call_arg_name_node(self, node: object) -> bool:
        return isinstance(node, Name) or self._call_arg_node_kind(node) == "Name"

    def _is_starred_unpack(self, arg_exprs: tuple) -> bool:
        if len(arg_exprs) != 1:
            return False
        arg = arg_exprs[0]
        return (
            self._is_call_arg_call_node(arg)
            and self._is_call_arg_name_node(arg.func)
            and arg.func.ident in ("*", "__starred__")
            and len(arg.args) == 1
            and not arg.kwargs
        )

    def _is_starred_unpack_expr(self, arg: Expr) -> bool:
        return (
            self._is_call_arg_call_node(arg)
            and self._is_call_arg_name_node(arg.func)
            and arg.func.ident in ("*", "__starred__")
            and len(arg.args) == 1
            and not arg.kwargs
        )

    def _has_starred_unpack(self, arg_exprs: tuple[Expr, ...]) -> bool:
        return any(self._is_starred_unpack_expr(arg) for arg in arg_exprs)

    def _split_starstar_kwargs_unpack(
        self,
        arg_exprs: tuple[Expr, ...],
    ) -> tuple[tuple[Expr, ...], Expr] | None:
        positional: list[Expr] = []
        kwargs_expr: Expr | None = None
        for arg in arg_exprs:
            if (
                isinstance(arg, Call)
                and isinstance(arg.func, Name)
                and arg.func.ident == "**"
                and len(arg.args) == 1
                and not arg.kwargs
            ):
                if kwargs_expr is not None:
                    return None
                kwargs_expr = arg.args[0]
                continue
            if kwargs_expr is not None:
                return None
            positional.append(arg)
        if kwargs_expr is None:
            return None
        return tuple(positional), kwargs_expr

    def _emit_pcc_args_list(
        self,
        arg_exprs: tuple[Expr, ...],
        name_hint: str,
    ) -> ir.Value:
        lst = self.builder.call(
            self.runtime["py_list_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh(f"call.args.{name_hint}"),
        )
        for arg in arg_exprs:
            if self._is_starred_unpack_expr(arg):
                inner = self._emit_expr(arg.args[0])
                self.builder.call(
                    self.runtime["py_list_extend"],
                    [lst, inner],
                )
                continue
            v = self._emit_expr(arg)
            v_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                v,
                arg.ty,
            )
            self.builder.call(
                self.runtime["py_list_append"],
                [lst, v_obj],
            )
        return lst
