"""String method lowering helpers for L1CodeGen."""
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
    IntType,
    StrLit,
    StrType,
)
from . import marshal


_I1 = ir.IntType(1)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()

_STR_METHOD_NATIVE = frozenset(
    {
        "upper",
        "lower",
        "capitalize",
        "swapcase",
        "title",
        "casefold",
        "strip",
        "lstrip",
        "rstrip",
        "split",
        "rsplit",
        "partition",
        "rpartition",
        "translate",
        "removeprefix",
        "removesuffix",
        "rjust",
        "ljust",
        "center",
        "zfill",
        "expandtabs",
        "join",
        "replace",
        "find",
        "count",
        "encode",
        "startswith",
        "endswith",
        "splitlines",
        "isdigit",
        "isalpha",
        "isspace",
        "isalnum",
        "isupper",
        "islower",
        "index",
        "rindex",
    }
)


def _str_method_arg(host, e: Expr) -> ir.Value:
    v = host._emit_expr(e)
    return marshal.marshal_to_object(
        host.builder,
        host.module,
        host.runtime,
        v,
        e.ty,
    )


def _str_i32_to_i1(host, v: ir.Value, nm: str) -> ir.Value:
    return host.builder.icmp_signed(
        "!=",
        v,
        ir.Constant(_I32, 0),
        name=host._fresh(nm),
    )


class StringMethodLoweringMixin:
    def _extract_splitlines_keepends(self, expr: Call):
        """Return the ``keepends`` constant bool for a
        ``splitlines(True)`` / ``splitlines(keepends=…)`` call, or ``None``
        if the caller passed neither (the bare ``splitlines()`` form)."""
        # Positional: splitlines(True) / splitlines(0).
        if expr.args:
            v = expr.args[0]
            if isinstance(v, BoolLit):
                return bool(v.value)
            if isinstance(v, IntLit):
                return bool(v.value)
            # Non-constant — treat as True (preserves line endings) to be safe.
            return True
        for key, v in expr.kwargs or ():
            if key == "keepends":
                if isinstance(v, BoolLit):
                    return bool(v.value)
                if isinstance(v, IntLit):
                    return bool(v.value)
                # Non-constant keepends — treat as ``True`` to be
                # safe; produced output preserves line endings.
                return True
        return None
    def _maybe_emit_str_method_via_dyn(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        """DynType receiver whose method name matches one of the
        pcc-native str helpers — dispatch through the same runtime
        entries used by the StrType fast path. If the runtime value
        isn't actually a str, the helper crashes cleanly, matching
        Python's AttributeError behaviour in the spirit of 'no
        libpython'."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name not in _STR_METHOD_NATIVE:
            return None
        # The only kwarg we recognise on a str method today is
        # ``splitlines(keepends=…)``. Everything else is routed via
        # the caller's fallback.
        if expr.kwargs and not (
            attr.name == "splitlines" and self._kwargs_are_only_keepends(expr.kwargs)
        ):
            return None
        # Re-use the StrType fast path by recovering the StrType
        # marshal for the receiver. The dyn value is already a
        # PyObject*; marshal_to_object is a no-op when it already
        # is.
        # Build an expr clone whose obj.ty is StrType so the
        # existing helper's type checks line up. Because ``expr`` is
        # a frozen dataclass we go directly to the dispatch using
        # the same implementation inlined here.
        name = attr.name
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            recv = self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"],
                [recv],
                name=self._fresh(f"cpy.str.{name}.recv"),
            )
        # Guard against a native-scalar Dyn payload (i1 from a short-
        # circuit ``or``, i64 from an unboxed attribute read, etc.).
        # Box to PyObject* before passing to the py_str_* helpers
        # which all expect a pointer operand.
        if not isinstance(recv.type, ir.PointerType):
            recv = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                recv,
                attr.obj.ty,
            )

        if (
            name
            in (
                "upper",
                "lower",
                "capitalize",
                "swapcase",
                "title",
                "casefold",
                "strip",
                "lstrip",
                "rstrip",
            )
            and not expr.args
        ):
            fn = {
                "upper": "py_str_upper",
                "lower": "py_str_lower",
                "capitalize": "py_str_capitalize",
                "swapcase": "py_str_swapcase",
                "title": "py_str_title",
                "casefold": "py_str_casefold",
                "strip": "py_str_strip",
                "lstrip": "py_str_lstrip",
                "rstrip": "py_str_rstrip",
            }[name]
            return self.builder.call(
                self.runtime[fn],
                [recv],
                name=self._fresh(f"dyn.str.{name}"),
            )
        if name in ("strip", "lstrip", "rstrip") and len(expr.args) == 1:
            fn = {
                "strip": "py_str_strip_chars",
                "lstrip": "py_str_lstrip_chars",
                "rstrip": "py_str_rstrip_chars",
            }[name]
            return self.builder.call(
                self.runtime[fn],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh(f"dyn.str.{name}.chars"),
            )
        if name == "count" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_count"],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh("dyn.str.count"),
            )
        if (
            name
            in (
                "isdigit",
                "isalpha",
                "isspace",
                "isalnum",
                "isupper",
                "islower",
            )
            and not expr.args
        ):
            fn = {
                "isdigit": "py_str_isdigit",
                "isalpha": "py_str_isalpha",
                "isspace": "py_str_isspace",
                "isalnum": "py_str_isalnum",
                "isupper": "py_str_isupper",
                "islower": "py_str_islower",
            }[name]
            i32v = self.builder.call(
                self.runtime[fn],
                [recv],
                name=self._fresh(f"dyn.str.{name}"),
            )
            return self.builder.icmp_signed(
                "!=",
                i32v,
                ir.Constant(_I32, 0),
                name=self._fresh(f"dyn.str.{name}.i1"),
            )
        if (
            name == "encode"
            and len(expr.args) == 1
            and isinstance(expr.args[0], StrLit)
            and expr.args[0].value in ("latin-1", "latin1")
        ):
            return self.builder.call(
                self.runtime["py_str_latin1_encode"],
                [recv],
                name=self._fresh("dyn.str.encode.latin1"),
            )
        if name == "encode" and (
            len(expr.args) == 0
            or (
                len(expr.args) == 1
                and isinstance(expr.args[0], StrLit)
                and expr.args[0].value in ("utf-8", "utf8", "UTF-8", "UTF8")
            )
        ):
            return self.builder.call(
                self.runtime["py_str_utf8_encode"],
                [recv],
                name=self._fresh("dyn.str.encode.utf8"),
            )
        if name == "splitlines" and len(expr.args) <= 1:
            keepends = self._extract_splitlines_keepends(expr)
            if keepends is None:
                return self.builder.call(
                    self.runtime["py_str_splitlines"],
                    [recv],
                    name=self._fresh("dyn.str.splitlines"),
                )
            return self.builder.call(
                self.runtime["py_str_splitlines_keepends"],
                [recv, ir.Constant(_I32, 1 if keepends else 0)],
                name=self._fresh("dyn.str.splitlines.keepends"),
            )
        if name == "split" and len(expr.args) <= 2:
            # ``split()`` with no args splits on whitespace — pass
            # NULL PyObject* to the runtime sep arg, which switches
            # py_str_split to the whitespace path.
            if expr.args:
                sep = _str_method_arg(self, expr.args[0])
            else:
                sep = ir.Constant(_CSTR, None)
            if len(expr.args) == 2:
                maxsplit = self._emit_expr_as_i64(expr.args[1])
                return self.builder.call(
                    self.runtime["py_str_split_maxsplit"],
                    [recv, sep, maxsplit],
                    name=self._fresh("dyn.str.split.maxsplit"),
                )
            return self.builder.call(
                self.runtime["py_str_split"],
                [recv, sep],
                name=self._fresh("dyn.str.split"),
            )
        if name == "rsplit" and 1 <= len(expr.args) <= 2:
            sep = _str_method_arg(self, expr.args[0])
            if len(expr.args) == 2:
                maxsplit = self._emit_expr_as_i64(expr.args[1])
                return self.builder.call(
                    self.runtime["py_str_rsplit_maxsplit"],
                    [recv, sep, maxsplit],
                    name=self._fresh("dyn.str.rsplit"),
                )
            return self.builder.call(
                self.runtime["py_str_split"],
                [recv, sep],
                name=self._fresh("dyn.str.rsplit.nolimit"),
            )
        if name in ("removeprefix", "removesuffix") and len(expr.args) == 1:
            arg = _str_method_arg(self, expr.args[0])
            fn = (
                "py_str_removeprefix"
                if name == "removeprefix"
                else "py_str_removesuffix"
            )
            return self.builder.call(
                self.runtime[fn],
                [recv, arg],
                name=self._fresh("dyn.str." + name),
            )
        if name == "partition" and len(expr.args) == 1:
            sep = _str_method_arg(self, expr.args[0])
            return self.builder.call(
                self.runtime["py_str_partition"],
                [recv, sep],
                name=self._fresh("dyn.str.partition"),
            )
        if name == "rpartition" and len(expr.args) == 1:
            sep = _str_method_arg(self, expr.args[0])
            return self.builder.call(
                self.runtime["py_str_rpartition"],
                [recv, sep],
                name=self._fresh("dyn.str.rpartition"),
            )
        if name == "translate" and len(expr.args) == 1:
            table = self._emit_as_object(expr.args[0])
            return self.builder.call(
                self.runtime["py_str_translate"],
                [recv, table],
                name=self._fresh("dyn.str.translate"),
            )
        if name in ("rjust", "ljust", "center") and 1 <= len(expr.args) <= 2:
            width = self._emit_expr_as_i64(expr.args[0])
            if len(expr.args) == 2:
                fill = _str_method_arg(self, expr.args[1])
            else:
                fill = ir.Constant(_CSTR, None)
            justfn = {
                "rjust": "py_str_rjust",
                "ljust": "py_str_ljust",
                "center": "py_str_center",
            }[name]
            return self.builder.call(
                self.runtime[justfn],
                [recv, width, fill],
                name=self._fresh("dyn.str." + name),
            )
        if name == "zfill" and len(expr.args) == 1:
            width = self._emit_expr_as_i64(expr.args[0])
            return self.builder.call(
                self.runtime["py_str_zfill"],
                [recv, width],
                name=self._fresh("dyn.str.zfill"),
            )
        if name == "expandtabs" and len(expr.args) <= 1 and not expr.kwargs:
            if expr.args:
                tabsize = self._emit_expr_as_i64(expr.args[0])
            else:
                tabsize = ir.Constant(_I64, 8)
            return self.builder.call(
                self.runtime["py_str_expandtabs"],
                [recv, tabsize],
                name=self._fresh("dyn.str.expandtabs"),
            )
        if name == "join" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_join"],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh("dyn.str.join"),
            )
        if name == "replace" and len(expr.args) == 2:
            return self.builder.call(
                self.runtime["py_str_replace"],
                [recv, _str_method_arg(self, expr.args[0]), _str_method_arg(self, expr.args[1])],
                name=self._fresh("dyn.str.replace"),
            )
        if name == "replace" and len(expr.args) == 3:
            maxreplace = self._emit_expr_as_i64(expr.args[2])
            return self.builder.call(
                self.runtime["py_str_replace_count"],
                [
                    recv,
                    _str_method_arg(self, expr.args[0]),
                    _str_method_arg(self, expr.args[1]),
                    maxreplace,
                ],
                name=self._fresh("dyn.str.replace.count"),
            )
        if name == "find" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_find"],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh("dyn.str.find"),
            )
        if name in ("index", "rindex") and len(expr.args) == 1:
            idx_fn = "py_str_index_of" if name == "index" else "py_str_rindex_of"
            res = self.builder.call(
                self.runtime[idx_fn],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh(f"dyn.str.{name}"),
            )
            # index/rindex raise ValueError when the substring is absent;
            # emit the post-call error check so a surrounding try/except can
            # catch it (mirrors subscript_lowering).
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return res
        if name == "rfind" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_rfind"],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh("dyn.str.rfind"),
            )
        if name in ("startswith", "endswith") and len(expr.args) == 1:
            fn = {"startswith": "py_str_startswith", "endswith": "py_str_endswith"}[
                name
            ]
            i32v = self.builder.call(
                self.runtime[fn],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh(f"dyn.str.{name}"),
            )
            return self.builder.icmp_signed(
                "!=",
                i32v,
                ir.Constant(_I32, 0),
                name=self._fresh(f"dyn.str.{name}.i1"),
            )
        return None
    def _maybe_emit_str_method(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        """Dispatch selected ``str`` methods via the pcc str runtime."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs and not (
            (attr.name == "splitlines" and self._kwargs_are_only_keepends(expr.kwargs))
            or attr.name == "format"
        ):
            return None
        name = attr.name
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            recv = self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"],
                [recv],
                name=self._fresh(f"cpy.str.{name}.recv"),
            )
        # Receiver may be a non-pointer when it came from an ``or``
        # / ``and`` phi that ended at i1 / i64. Box to PyObject* so
        # the py_str_* runtime sees a proper pcc string.
        if not isinstance(recv.type, ir.PointerType):
            recv = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                recv,
                attr.obj.ty,
            )

        if name == "format":
            return self._maybe_emit_literal_str_format(expr)
        if name == "format_map":
            return self._maybe_emit_literal_str_format_map(expr)
        if (
            name
            in (
                "upper",
                "lower",
                "capitalize",
                "swapcase",
                "title",
                "casefold",
                "strip",
                "lstrip",
                "rstrip",
            )
            and not expr.args
        ):
            fn = {
                "upper": "py_str_upper",
                "lower": "py_str_lower",
                "capitalize": "py_str_capitalize",
                "swapcase": "py_str_swapcase",
                "title": "py_str_title",
                "casefold": "py_str_casefold",
                "strip": "py_str_strip",
                "lstrip": "py_str_lstrip",
                "rstrip": "py_str_rstrip",
            }[name]
            return self.builder.call(
                self.runtime[fn],
                [recv],
                name=self._fresh(f"str.{name}"),
            )
        if name in ("strip", "lstrip", "rstrip") and len(expr.args) == 1:
            fn = {
                "strip": "py_str_strip_chars",
                "lstrip": "py_str_lstrip_chars",
                "rstrip": "py_str_rstrip_chars",
            }[name]
            return self.builder.call(
                self.runtime[fn],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh(f"str.{name}.chars"),
            )
        if name == "count" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_count"],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh("str.count"),
            )
        if (
            name
            in (
                "isdigit",
                "isalpha",
                "isspace",
                "isalnum",
                "isupper",
                "islower",
            )
            and not expr.args
        ):
            fn = {
                "isdigit": "py_str_isdigit",
                "isalpha": "py_str_isalpha",
                "isspace": "py_str_isspace",
                "isalnum": "py_str_isalnum",
                "isupper": "py_str_isupper",
                "islower": "py_str_islower",
            }[name]
            i32v = self.builder.call(
                self.runtime[fn],
                [recv],
                name=self._fresh(f"str.{name}"),
            )
            return self.builder.icmp_signed(
                "!=",
                i32v,
                ir.Constant(_I32, 0),
                name=self._fresh(f"str.{name}.i1"),
            )
        if (
            name == "encode"
            and len(expr.args) == 1
            and isinstance(expr.args[0], StrLit)
            and expr.args[0].value in ("latin-1", "latin1")
        ):
            return self.builder.call(
                self.runtime["py_str_latin1_encode"],
                [recv],
                name=self._fresh("str.encode.latin1"),
            )
        if name == "encode" and (
            len(expr.args) == 0
            or (
                len(expr.args) == 1
                and isinstance(expr.args[0], StrLit)
                and expr.args[0].value in ("utf-8", "utf8", "UTF-8", "UTF8")
            )
        ):
            return self.builder.call(
                self.runtime["py_str_utf8_encode"],
                [recv],
                name=self._fresh("str.encode.utf8"),
            )
        if name == "splitlines" and len(expr.args) <= 1:
            keepends = self._extract_splitlines_keepends(expr)
            if keepends is None:
                return self.builder.call(
                    self.runtime["py_str_splitlines"],
                    [recv],
                    name=self._fresh("str.splitlines"),
                )
            return self.builder.call(
                self.runtime["py_str_splitlines_keepends"],
                [recv, ir.Constant(_I32, 1 if keepends else 0)],
                name=self._fresh("str.splitlines.keepends"),
            )
        if name == "split":
            if len(expr.args) > 2:
                return None
            if expr.args:
                sep = _str_method_arg(self, expr.args[0])
            else:
                sep = ir.Constant(_CSTR, None)
            if len(expr.args) == 2:
                maxsplit = self._emit_expr_as_i64(expr.args[1])
                return self.builder.call(
                    self.runtime["py_str_split_maxsplit"],
                    [recv, sep, maxsplit],
                    name=self._fresh("str.split.maxsplit"),
                )
            return self.builder.call(
                self.runtime["py_str_split"],
                [recv, sep],
                name=self._fresh("str.split"),
            )
        if name == "rsplit" and 1 <= len(expr.args) <= 2:
            sep = _str_method_arg(self, expr.args[0])
            if len(expr.args) == 2:
                maxsplit = self._emit_expr_as_i64(expr.args[1])
                return self.builder.call(
                    self.runtime["py_str_rsplit_maxsplit"],
                    [recv, sep, maxsplit],
                    name=self._fresh("str.rsplit"),
                )
            # rsplit(sep) without a limit yields the same parts as split(sep).
            return self.builder.call(
                self.runtime["py_str_split"],
                [recv, sep],
                name=self._fresh("str.rsplit.nolimit"),
            )
        if name in ("removeprefix", "removesuffix") and len(expr.args) == 1:
            arg = _str_method_arg(self, expr.args[0])
            fn = (
                "py_str_removeprefix"
                if name == "removeprefix"
                else "py_str_removesuffix"
            )
            return self.builder.call(
                self.runtime[fn],
                [recv, arg],
                name=self._fresh("str." + name),
            )
        if name == "partition" and len(expr.args) == 1:
            sep = _str_method_arg(self, expr.args[0])
            return self.builder.call(
                self.runtime["py_str_partition"],
                [recv, sep],
                name=self._fresh("str.partition"),
            )
        if name == "rpartition" and len(expr.args) == 1:
            sep = _str_method_arg(self, expr.args[0])
            return self.builder.call(
                self.runtime["py_str_rpartition"],
                [recv, sep],
                name=self._fresh("str.rpartition"),
            )
        if name == "translate" and len(expr.args) == 1:
            table = self._emit_as_object(expr.args[0])
            return self.builder.call(
                self.runtime["py_str_translate"],
                [recv, table],
                name=self._fresh("str.translate"),
            )
        if name in ("rjust", "ljust", "center") and 1 <= len(expr.args) <= 2:
            width = self._emit_expr_as_i64(expr.args[0])
            if len(expr.args) == 2:
                fill = _str_method_arg(self, expr.args[1])
            else:
                fill = ir.Constant(_CSTR, None)
            justfn = {
                "rjust": "py_str_rjust",
                "ljust": "py_str_ljust",
                "center": "py_str_center",
            }[name]
            return self.builder.call(
                self.runtime[justfn],
                [recv, width, fill],
                name=self._fresh("str." + name),
            )
        if name == "zfill" and len(expr.args) == 1:
            width = self._emit_expr_as_i64(expr.args[0])
            return self.builder.call(
                self.runtime["py_str_zfill"],
                [recv, width],
                name=self._fresh("str.zfill"),
            )
        if name == "expandtabs" and len(expr.args) <= 1 and not expr.kwargs:
            if expr.args:
                tabsize = self._emit_expr_as_i64(expr.args[0])
            else:
                tabsize = ir.Constant(_I64, 8)
            return self.builder.call(
                self.runtime["py_str_expandtabs"],
                [recv, tabsize],
                name=self._fresh("str.expandtabs"),
            )
        if name == "join":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_str_join"],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh("str.join"),
            )
        if name == "replace":
            if len(expr.args) == 2:
                return self.builder.call(
                    self.runtime["py_str_replace"],
                    [recv, _str_method_arg(self, expr.args[0]), _str_method_arg(self, expr.args[1])],
                    name=self._fresh("str.replace"),
                )
            if len(expr.args) == 3:
                maxreplace = self._emit_expr_as_i64(expr.args[2])
                return self.builder.call(
                    self.runtime["py_str_replace_count"],
                    [
                        recv,
                        _str_method_arg(self, expr.args[0]),
                        _str_method_arg(self, expr.args[1]),
                        maxreplace,
                    ],
                    name=self._fresh("str.replace.count"),
                )
            return None
        if name == "find":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_str_find"],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh("str.find"),
            )
        if name in ("index", "rindex"):
            if len(expr.args) != 1:
                return None
            idx_fn = "py_str_index_of" if name == "index" else "py_str_rindex_of"
            res = self.builder.call(
                self.runtime[idx_fn],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh(f"str.{name}"),
            )
            # ValueError on absent substring -> emit err check so try/except
            # can catch it (mirrors subscript_lowering).
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return res
        if name == "rfind":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_str_rfind"],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh("str.rfind"),
            )
        if name in ("startswith", "endswith"):
            if len(expr.args) != 1:
                return None
            fn = {"startswith": "py_str_startswith", "endswith": "py_str_endswith"}[
                name
            ]
            i32v = self.builder.call(
                self.runtime[fn],
                [recv, _str_method_arg(self, expr.args[0])],
                name=self._fresh(f"str.{name}"),
            )
            return _str_i32_to_i1(self, i32v, f"str.{name}.i1")
        return None
