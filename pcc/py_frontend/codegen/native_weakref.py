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
            proxy_ref = self.builder.call(
                self.runtime["py_weakref_new"],
                [target, callback],
                name=self._fresh("weakref.proxy"),
            )
            # py_weakref_new raises (TypeError on valueclass payloads);
            # without this check the pending exception skips enclosing
            # try/except blocks.
            self._emit_post_call_err_check(args[0].span)
            return proxy_ref
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
        new_ref = self.builder.call(
            self.runtime["py_weakref_new"],
            [target, callback],
            name=self._fresh("weakref.ref"),
        )
        # See proxy note above: the constructor can raise.
        self._emit_post_call_err_check(args[0].span)
        return new_ref

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

    def _weakref_constructor_kind_for_expr(self, expr: Expr) -> Optional[str]:
        if not isinstance(expr, Call) or expr.kwargs:
            return None
        kind = self._native_builtin_value_kind_for_expr(expr.func)
        if kind == "weakref.ref" and len(expr.args) in (1, 2):
            return "ref"
        return None

    def _weakref_call_expr_returns_owned_object(self, expr: Expr) -> bool:
        if not isinstance(expr, Call) or expr.args or expr.kwargs:
            return False
        if isinstance(expr.func, Name):
            return bool(
                getattr(self, "_weakref_env_flags", {}).get(expr.func.ident, False)
            )
        return False

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
