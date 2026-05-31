"""Native ``weakref`` module lowering helpers."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, Call, Expr, Lambda, Name
from .runtime_abi import declare_runtime_global



class NativeWeakrefLoweringMixin:
    def _emit_native_weakref_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "weakref"
        ):
            return None
        return self._emit_native_weakref_value_call(
            "weakref." + attr.name,
            expr.args,
            expr.kwargs,
        )

    def _emit_native_weakref_value_call(self,
        kind: str,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if kwargs:
            return None
        if kind == "weakref.WeakValueDictionary":
            if args:
                return None
            return self.builder.call(
                self.runtime["py_weak_value_dict_new"],
                [],
                name=self._fresh("weak.value.dict"),
            )
        if kind == "weakref.WeakKeyDictionary":
            if args:
                return None
            return self.builder.call(
                self.runtime["py_weak_key_dict_new"],
                [],
                name=self._fresh("weak.key.dict"),
            )
        if kind == "weakref.proxy":
            if len(args) != 1:
                return None
            target = self._emit_as_object(args[0])
            none_gv = declare_runtime_global(self.module, "py_None")
            callback = self.builder.load(
                none_gv,
                name=self._fresh("weakref.none"),
            )
            return self.builder.call(
                self.runtime["py_weakref_new"],
                [target, callback],
                name=self._fresh("weakref.proxy"),
            )
        if kind != "weakref.ref" or len(args) not in (1, 2):
            return None
        target = self._emit_as_object(args[0])
        if len(args) == 2:
            callback = self._emit_weakref_callback_object(args[1])
        else:
            none_gv = declare_runtime_global(self.module, "py_None")
            callback = self.builder.load(
                none_gv,
                name=self._fresh("weakref.none"),
            )
        return self.builder.call(
            self.runtime["py_weakref_new"],
            [target, callback],
            name=self._fresh("weakref.ref"),
        )

    def _weak_dict_constructor_kind_for_expr(self, expr: Expr) -> Optional[str]:
        if not isinstance(expr, Call) or expr.kwargs or expr.args:
            return None
        kind = self._native_builtin_value_kind_for_expr(expr.func)
        if kind == "weakref.WeakValueDictionary":
            return "value"
        if kind == "weakref.WeakKeyDictionary":
            return "key"
        return None

    def _weak_dict_kind_for_expr(self, expr: Expr) -> Optional[str]:
        if isinstance(expr, Name):
            return getattr(self, "_weak_dict_env_flags", {}).get(expr.ident)
        return None

    def _emit_weakref_callback_object(self, expr: Expr) -> ir.Value:
        if isinstance(expr, Name):
            fn_ir = self.functions.get(expr.ident)
            if fn_ir is not None:
                return self._emit_native_func_value(expr.ident, expr.ident, fn_ir, ())
        if isinstance(expr, Lambda):
            native_lambda = self._emit_native_lambda_callback_object(expr)
            if native_lambda is not None:
                return native_lambda
        return self._emit_as_object(expr)


__all__ = ["NativeWeakrefLoweringMixin"]
