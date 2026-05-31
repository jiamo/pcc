"""Native ``json`` and ``re`` module lowering helpers."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, BinOp, BoolLit, Call, Expr, IntLit, Name, StrLit
from .import_lowering import (
    _dataclass_field_names,
    _dataclass_field_value,
)

_I64 = ir.IntType(64)
_RE_LITERAL_SPLIT_META = frozenset(".^$*+?{}[]\\|()")
_RE_CONSTS = {
    "I": 2,
    "IGNORECASE": 2,
    "M": 8,
    "MULTILINE": 8,
    "S": 16,
    "DOTALL": 16,
}
_RE_ALIAS_METHODS = frozenset(("match", "search", "findall"))


class NativeTextModulesLoweringMixin:
    def _native_re_findall_supported_pattern_text(self, pattern: str) -> bool:
        return pattern in (
            r"\b[a-z][\w$]*\b",
            r"\(.*?\)",
        )

    def _textwrap_literal_split_lines(self, text: str):
        lines = []
        start = 0
        i = 0
        n = len(text)
        while i < n:
            if text[i] == "\n":
                lines.append(text[start : i + 1])
                start = i + 1
            i += 1
        if start < n:
            lines.append(text[start:n])
        return lines

    def _textwrap_literal_line_body_and_end(self, line: str):
        n = len(line)
        if n >= 2 and line[n - 2 : n] == "\r\n":
            return line[: n - 2], "\r\n"
        if n >= 1 and (line[n - 1] == "\n" or line[n - 1] == "\r"):
            return line[: n - 1], line[n - 1 : n]
        return line, ""

    def _textwrap_literal_is_blank(self, text: str) -> bool:
        for ch in text:
            if ch != " " and ch != "\t":
                return False
        return True

    def _textwrap_literal_indent(self, text: str) -> str:
        i = 0
        n = len(text)
        while i < n and (text[i] == " " or text[i] == "\t"):
            i += 1
        return text[:i]

    def _textwrap_literal_common_prefix(self, a: str, b: str) -> str:
        n = len(a)
        if len(b) < n:
            n = len(b)
        i = 0
        while i < n and a[i] == b[i]:
            i += 1
        return a[:i]

    def _textwrap_dedent_literal_value(self, text: str) -> str:
        parts = []
        margin = None
        for line in self._textwrap_literal_split_lines(text):
            body, end = self._textwrap_literal_line_body_and_end(line)
            if self._textwrap_literal_is_blank(body):
                body = ""
            else:
                indent = self._textwrap_literal_indent(body)
                if margin is None:
                    margin = indent
                else:
                    margin = self._textwrap_literal_common_prefix(margin, indent)
            parts.append((body, end))
        if not margin:
            unchanged = []
            for item in parts:
                unchanged.append(item[0] + item[1])
            return "".join(unchanged)
        width = len(margin)
        out = []
        for item in parts:
            body = item[0]
            end = item[1]
            if body[:width] == margin:
                body = body[width:]
            out.append(body + end)
        return "".join(out)

    def _emit_native_textwrap_dedent_call(
        self,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if kwargs or len(args) != 1:
            return None
        arg = args[0]
        if not isinstance(arg, StrLit):
            return None
        return self._emit_str_literal(
            self._textwrap_dedent_literal_value(arg.value)
        )

    def _native_re_static_flags_value(self, expr: Expr | None) -> Optional[int]:
        if expr is None:
            return 0
        if isinstance(expr, IntLit):
            return int(expr.value)
        if (
            isinstance(expr, Attr)
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "re"
            and expr.name in _RE_CONSTS
        ):
            return _RE_CONSTS[expr.name]
        if isinstance(expr, BinOp) and expr.op == "|":
            lhs = self._native_re_static_flags_value(expr.lhs)
            rhs = self._native_re_static_flags_value(expr.rhs)
            if lhs is None or rhs is None:
                return None
            return lhs | rhs
        return None

    def _native_re_compile_alias_info(
        self,
        expr: Expr,
    ) -> Optional[tuple[str, int]]:
        if not isinstance(expr, Call) or expr.kwargs:
            return None
        func = expr.func
        if (
            not isinstance(func, Attr)
            or func.name != "compile"
            or not isinstance(func.obj, Name)
            or self._native_builtin_module_for_name(func.obj.ident) != "re"
        ):
            return None
        if len(expr.args) < 1 or len(expr.args) > 2:
            return None
        pattern = expr.args[0]
        if not isinstance(pattern, StrLit):
            return None
        flags = self._native_re_static_flags_value(
            expr.args[1] if len(expr.args) == 2 else None
        )
        if flags is None:
            return None
        return pattern.value, flags

    def _native_re_compile_alias_for_name(
        self,
        alias: str,
    ) -> Optional[tuple[str, int]]:
        current_func = getattr(self, "current_func_def", None)
        if current_func is not None:
            local_aliases = getattr(self, "_native_re_compile_local_aliases", {})
            key = (id(current_func), alias)
            if key in local_aliases:
                value = local_aliases[key]
                return value if value is not None else None
        return getattr(self, "_native_re_compile_aliases", {}).get(alias)

    def _native_re_compile_alias_uses_are_safe(
        self,
        alias: str,
        initial_stmt,
        scope_body=None,
    ) -> bool:
        def walk(obj, *, assign_target: bool = False) -> bool:
            if obj is None:
                return True
            if isinstance(obj, (str, int, bool, float, bytes)):
                return True
            if isinstance(obj, (tuple, list)):
                for item in obj:
                    if not walk(item, assign_target=assign_target):
                        return False
                return True
            if isinstance(obj, Name):
                return assign_target or obj.ident != alias
            if isinstance(obj, Attr):
                if isinstance(obj.obj, Name) and obj.obj.ident == alias:
                    return obj.name in _RE_ALIAS_METHODS
                return walk(obj.obj)
            if type(obj).__name__ == "Assign":
                if obj is not initial_stmt:
                    for target in getattr(obj, "targets", ()):
                        if isinstance(target, Name) and target.ident == alias:
                            return False
                        if not walk(target, assign_target=True):
                            return False
                return walk(getattr(obj, "value", None))
            if type(obj).__name__ == "AugAssign":
                target = getattr(obj, "target", None)
                if isinstance(target, Name) and target.ident == alias:
                    return False
                return walk(target, assign_target=True) and walk(
                    getattr(obj, "value", None)
                )
            for field_name in _dataclass_field_names(obj):
                if not walk(_dataclass_field_value(obj, field_name)):
                    return False
            return True

        if scope_body is None:
            scope_body = getattr(self.ast_module, "body", ())
        return walk(scope_body)

    def _native_re_class_compile_attr_string_value(
        self,
        class_name: str,
        attr_name: str,
        value_expr: Expr,
    ) -> Optional[str]:
        alias_info = self._native_re_compile_alias_info(value_expr)
        if alias_info is None:
            return None
        pattern, flags = alias_info
        if flags != 0:
            return None

        def is_target_attr(obj) -> bool:
            if not isinstance(obj, Attr) or obj.name != attr_name:
                return False
            if not isinstance(obj.obj, Name):
                return False
            return obj.obj.ident in (class_name, "self", "cls")

        def is_re_func(obj, names: tuple[str, ...]) -> bool:
            return (
                isinstance(obj, Attr)
                and obj.name in names
                and isinstance(obj.obj, Name)
                and self._native_builtin_module_for_name(obj.obj.ident) == "re"
            )

        def walk(obj) -> bool:
            if obj is None:
                return True
            if isinstance(obj, (str, int, bool, float, bytes)):
                return True
            if isinstance(obj, (tuple, list)):
                for item in obj:
                    if not walk(item):
                        return False
                return True
            if is_target_attr(obj):
                return False
            if isinstance(obj, Call):
                if (
                    is_re_func(obj.func, ("split", "findall"))
                    and len(obj.args) >= 1
                    and is_target_attr(obj.args[0])
                ):
                    for arg in obj.args[1:]:
                        if not walk(arg):
                            return False
                    for _key, value in obj.kwargs:
                        if not walk(value):
                            return False
                    return True
                if not walk(obj.func):
                    return False
                for arg in obj.args:
                    if not walk(arg):
                        return False
                for _key, value in obj.kwargs:
                    if not walk(value):
                        return False
                return True
            for field_name in _dataclass_field_names(obj):
                if not walk(_dataclass_field_value(obj, field_name)):
                    return False
            return True

        if not walk(getattr(self.ast_module, "body", ())):
            return None
        return pattern

    def _native_re_findall_supported_pattern(self, pattern: Expr) -> bool:
        if not isinstance(pattern, StrLit):
            return False
        return self._native_re_findall_supported_pattern_text(pattern.value)

    def _native_re_literal_split_pattern(self, pattern: Expr) -> bool:
        if not isinstance(pattern, StrLit):
            return False
        if pattern.value == "":
            return False
        for ch in pattern.value:
            if ch in _RE_LITERAL_SPLIT_META:
                return False
        return True

    def _emit_native_json_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "json"
            or len(expr.args) != 1
        ):
            return None
        # `json.dumps` supports `sort_keys` but we currently ignore it at
        # codegen time to stay on the native path (`py_json_dumps`).
        if expr.kwargs:
            if attr.name != "dumps" or len(expr.kwargs) != 1:
                return None
            key, value = expr.kwargs[0]
            if key != "sort_keys" or not isinstance(value, BoolLit):
                return None
        if attr.name == "loads":
            return self.builder.call(
                self.runtime["py_json_loads"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("json.loads"),
            )
        if attr.name == "dumps":
            return self.builder.call(
                self.runtime["py_json_dumps"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("json.dumps"),
            )
        return None

    def _emit_native_re_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if isinstance(attr.obj, Name):
            alias_info = self._native_re_compile_alias_for_name(attr.obj.ident)
            if alias_info is not None:
                return self._emit_native_re_compile_alias_method_call(
                    alias_info,
                    attr.name,
                    expr.args,
                    expr.kwargs,
                )
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "re"
        ):
            return None
        if attr.name == "escape" and not expr.kwargs and len(expr.args) == 1:
            arg = self._emit_as_object(expr.args[0])
            return self.builder.call(
                self.runtime["py_re_escape"],
                [arg],
                name=self._fresh("re.escape"),
            )
        if attr.name == "findall":
            return self._emit_native_re_findall_call(expr.args, expr.kwargs)
        if attr.name == "split":
            return self._emit_native_re_split_call(expr.args, expr.kwargs)
        if attr.name not in ("match", "search"):
            return None
        return self._emit_native_re_value_call(
            "re." + attr.name,
            expr.args,
            expr.kwargs,
        )

    def _emit_native_re_value_call(
        self,
        kind: str,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if kind not in ("re.match", "re.search"):
            return None
        if kwargs or len(args) < 2 or len(args) > 3:
            return None
        if len(args) == 3:
            helper = (
                "py_re_match_flags"
                if kind == "re.match"
                else "py_re_search_flags"
            )
            return self.builder.call(
                self.runtime[helper],
                [
                    self._emit_as_object(args[0]),
                    self._emit_as_object(args[1]),
                    self._emit_expr_as_i64(args[2]),
                ],
                name=self._fresh(kind),
            )
        helper = "py_re_match" if kind == "re.match" else "py_re_search"
        return self.builder.call(
            self.runtime[helper],
            [self._emit_as_object(args[0]), self._emit_as_object(args[1])],
            name=self._fresh(kind),
        )

    def _emit_native_re_findall_call(
        self,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if kwargs or len(args) < 2 or len(args) > 3:
            return None
        if not self._native_re_findall_supported_pattern(args[0]):
            return None
        flags = (
            ir.Constant(_I64, 0)
            if len(args) == 2
            else self._emit_expr_as_i64(args[2])
        )
        return self.builder.call(
            self.runtime["py_re_findall_flags"],
            [
                self._emit_as_object(args[0]),
                self._emit_as_object(args[1]),
                flags,
            ],
            name=self._fresh("re.findall"),
        )

    def _emit_native_re_split_call(
        self,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if len(args) < 2 or len(args) > 4:
            return None
        if not self._native_re_literal_split_pattern(args[0]):
            return None

        maxsplit_expr: Expr | None = None
        flags_expr: Expr | None = None
        if len(args) >= 3:
            maxsplit_expr = args[2]
        if len(args) >= 4:
            flags_expr = args[3]
        for key, value in kwargs:
            if key == "maxsplit":
                if maxsplit_expr is not None:
                    return None
                maxsplit_expr = value
            elif key == "flags":
                if flags_expr is not None:
                    return None
                flags_expr = value
            else:
                return None

        if flags_expr is not None:
            if not isinstance(flags_expr, IntLit) or int(flags_expr.value) != 0:
                return None

        if maxsplit_expr is None:
            return self.builder.call(
                self.runtime["py_str_split"],
                [self._emit_as_object(args[1]), self._emit_as_object(args[0])],
                name=self._fresh("re.split.literal"),
            )
        if not isinstance(maxsplit_expr, IntLit):
            return None
        maxsplit_value = int(maxsplit_expr.value)
        if maxsplit_value <= 0:
            return self.builder.call(
                self.runtime["py_str_split"],
                [self._emit_as_object(args[1]), self._emit_as_object(args[0])],
                name=self._fresh("re.split.literal"),
            )
        return self.builder.call(
            self.runtime["py_str_split_maxsplit"],
            [
                self._emit_as_object(args[1]),
                self._emit_as_object(args[0]),
                ir.Constant(_I64, maxsplit_value),
            ],
            name=self._fresh("re.split.literal.maxsplit"),
        )

    def _emit_native_re_compile_alias_method_call(
        self,
        alias_info: tuple[str, int],
        method_name: str,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if kwargs or method_name not in _RE_ALIAS_METHODS or len(args) != 1:
            return None
        pattern, flags = alias_info
        if method_name == "findall" and not self._native_re_findall_supported_pattern_text(
            pattern
        ):
            return None
        helper = {
            "match": "py_re_match_flags",
            "search": "py_re_search_flags",
            "findall": "py_re_findall_flags",
        }[method_name]
        return self.builder.call(
            self.runtime[helper],
            [
                self._emit_str_literal(pattern),
                self._emit_as_object(args[0]),
                ir.Constant(_I64, flags),
            ],
            name=self._fresh(f"re.compile.alias.{method_name}"),
        )

    def _emit_native_re_compile_method_attr(
        self,
        expr: Attr,
    ) -> Optional[ir.Value]:
        if expr.name not in ("match", "search", "findall"):
            return None
        if isinstance(expr.obj, Name):
            alias_info = self._native_re_compile_alias_for_name(expr.obj.ident)
            if alias_info is not None:
                pattern, flags = alias_info
                if (
                    expr.name == "findall"
                    and not self._native_re_findall_supported_pattern_text(pattern)
                ):
                    return None
                method_kind = {"match": 0, "search": 1, "findall": 2}[expr.name]
                return self.builder.call(
                    self.runtime["py_re_compile_method"],
                    [
                        self._emit_str_literal(pattern),
                        ir.Constant(_I64, flags),
                        ir.Constant(_I64, method_kind),
                    ],
                    name=self._fresh(f"re.compile.alias.{expr.name}"),
                )
        call = expr.obj
        if not isinstance(call, Call) or call.kwargs:
            return None
        func = call.func
        if (
            not isinstance(func, Attr)
            or func.name != "compile"
            or not isinstance(func.obj, Name)
            or self._native_builtin_module_for_name(func.obj.ident) != "re"
        ):
            return None
        if len(call.args) < 1 or len(call.args) > 2:
            return None
        if expr.name == "findall" and not self._native_re_findall_supported_pattern(
            call.args[0]
        ):
            return None
        flags = (
            ir.Constant(_I64, 0)
            if len(call.args) == 1
            else self._emit_expr_as_i64(call.args[1])
        )
        method_kind = {"match": 0, "search": 1, "findall": 2}[expr.name]
        return self.builder.call(
            self.runtime["py_re_compile_method"],
            [
                self._emit_as_object(call.args[0]),
                flags,
                ir.Constant(_I64, method_kind),
            ],
            name=self._fresh(f"re.compile.{expr.name}"),
        )


__all__ = ["NativeTextModulesLoweringMixin"]
