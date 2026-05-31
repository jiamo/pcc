"""Format lowering helpers for Layer-1 Python codegen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Assign,
    Attr,
    AugAssign,
    Call,
    ClassDef,
    DictType,
    Expr,
    For,
    FuncDef,
    If,
    Name,
    StrLit,
    StrType,
    Try,
    While,
    With,
)
from . import marshal


_I64 = ir.IntType(64)


class FormatLoweringMixin:
    def _parse_auto_format_literal(
        self,
        fmt: str,
        argc: int,
    ) -> Optional[list[str | tuple[str]]]:
        parts: list = []
        buf: list[str] = []
        placeholders = 0
        saw_auto = False
        saw_manual = False
        i = 0
        while i < len(fmt):
            ch = fmt[i]
            if ch == "{":
                if i + 1 < len(fmt) and fmt[i + 1] == "{":
                    buf.append("{")
                    i += 2
                    continue
                end = -1
                j = i + 1
                while j < len(fmt):
                    if fmt[j] == "}":
                        end = j
                        break
                    j += 1
                if end < 0:
                    return None
                field = fmt[i + 1:end]
                if ":" in field:
                    pieces = field.split(":", 1)
                    ref_part = pieces[0]
                    spec = pieces[1]
                    if "{" in spec or "}" in spec:
                        return None
                else:
                    ref_part = field
                    spec = ""
                if ref_part == "":
                    kind = "auto"
                    saw_auto = True
                elif ref_part.isdigit():
                    kind = "index"
                    saw_manual = True
                else:
                    kind = "name"
                    saw_manual = True
                if buf:
                    parts.append("".join(buf))
                    buf = []
                parts.append((spec, kind, ref_part))
                placeholders += 1
                i = end + 1
                continue
            if ch == "}":
                if i + 1 < len(fmt) and fmt[i + 1] == "}":
                    buf.append("}")
                    i += 2
                    continue
                return None
            buf.append(ch)
            i += 1
        if buf:
            parts.append("".join(buf))
        if saw_auto and saw_manual:
            return None              # CPython forbids mixing auto and manual
        if saw_auto and placeholders != argc:
            return None
        return parts

    def _emit_format_arg_as_str(self, arg: Expr, spec: str = "") -> ir.Value:
        obj = self._emit_as_object(arg)
        spec_obj = self._emit_str_literal(spec)
        formatted = self.builder.call(
            self.runtime["py_obj_format"],
            [obj, spec_obj],
            name=self._fresh("format.field"),
        )
        self._emit_post_call_err_check(getattr(arg, "span", None))
        return formatted

    def _emit_obj_format_call(self, value_expr: Expr, spec_expr: Expr) -> ir.Value:
        value_obj = self._emit_as_object(value_expr)
        spec_obj = self._emit_as_object(spec_expr)
        result = self.builder.call(
            self.runtime["py_obj_format"],
            [value_obj, spec_obj],
            name=self._fresh("format.obj"),
        )
        self._emit_post_call_err_check(getattr(value_expr, "span", None))
        return result

    def _emit_obj_format_literal_spec(self, value_expr: Expr, spec: str) -> ir.Value:
        value_obj = self._emit_as_object(value_expr)
        spec_obj = self._emit_str_literal(spec)
        return self.builder.call(
            self.runtime["py_obj_format"],
            [value_obj, spec_obj],
            name=self._fresh("format.obj"),
        )

    def _resolve_str_literal_value(self, expr: Expr) -> Optional[str]:
        """Return the static string value backing ``expr`` when it can be
        resolved to a single literal.

        - ``StrLit``: the literal directly.
        - ``Name``: tries the current function body first; if the name
          isn't bound there at all, falls back to the module's top-level
          body (descending into top-level If/Try/With/While/For without
          stepping into nested FuncDef / ClassDef). In either scope,
          requires exactly one ``Name = <StrLit>`` Assign and no other
          rebind (AugAssign, For-target, except-handler name, With-as,
          or non-StrLit Assign).

        Returning None means the caller must fall back to the libpython
        path. This helper exists so that ``fmt = "{x}"; fmt.format(x=...)``
        and the module-level ``_MSG = "..."; comprehension {... _MSG.format(...) ...}``
        idioms (common in CPython packages such as numpy at
        ``numpy/__init__.py:637-663``) take the native format fast path.
        """
        if isinstance(expr, StrLit):
            return expr.value
        if not isinstance(expr, Name):
            return None
        target = expr.ident
        fd = getattr(self, "current_func_def", None)
        if fd is not None and self._body_binds_name(fd.body, target):
            return self._scan_body_for_str_literal(fd.body, target)
        ast_module = getattr(self, "ast_module", None)
        if ast_module is None:
            return None
        return self._scan_body_for_str_literal(
            getattr(ast_module, "body", ()),
            target,
        )

    def _scan_body_for_str_literal(self, body, target_name: str) -> Optional[str]:
        found = None
        rebind_count = 0
        for stmt in self._iter_function_str_lit_bindings(body, target_name):
            rebind_count += 1
            if rebind_count > 1:
                return None
            found = stmt
        return found

    def _body_binds_name(self, body, target_name: str) -> bool:
        """Return True iff ``target_name`` is the target of ANY binding
        construct in ``body`` (Assign, AugAssign, For-target, except-
        handler name, With-as). Used to decide whether to shadow the
        module-level resolution: if a function rebinds the name locally,
        we must NOT fall back to the module-level value.
        """
        pending = list(body)
        while pending:
            s = pending.pop()
            if isinstance(s, (FuncDef, ClassDef)):
                continue
            if isinstance(s, Assign):
                for t in s.targets:
                    if isinstance(t, Name) and t.ident == target_name:
                        return True
                continue
            if isinstance(s, AugAssign) and isinstance(s.target, Name):
                if s.target.ident == target_name:
                    return True
                continue
            if isinstance(s, If):
                pending.extend(s.body)
                pending.extend(s.else_body)
                continue
            if isinstance(s, While):
                pending.extend(s.body)
                pending.extend(s.else_body)
                continue
            if isinstance(s, For):
                if isinstance(s.target, Name) and s.target.ident == target_name:
                    return True
                pending.extend(s.body)
                pending.extend(s.else_body)
                continue
            if isinstance(s, Try):
                pending.extend(s.body)
                pending.extend(s.else_body)
                pending.extend(s.finally_body)
                for h in s.handlers:
                    if h.name == target_name:
                        return True
                    pending.extend(h.body)
                continue
            if isinstance(s, With):
                for ctx_pair in s.items:
                    as_var = ctx_pair[1]
                    if isinstance(as_var, Name) and as_var.ident == target_name:
                        return True
                pending.extend(s.body)
                continue
        return False

    def _iter_function_str_lit_bindings(self, body, target_name: str):
        """Yield the StrLit value for every ``Assign(targets=[Name(target_name)])``
        whose value is a ``StrLit``; yield a sentinel ``None`` and stop
        iteration when ``target_name`` is rebound by any other shape so
        the caller can treat the binding as not-a-pure-literal.

        Walks the transitive body (descending into If/For/While/Try/With
        bodies) but does NOT descend into nested FuncDef / ClassDef
        scopes — those have their own name bindings.
        """
        pending = list(body)
        while pending:
            s = pending.pop()
            if isinstance(s, (FuncDef, ClassDef)):
                continue
            if isinstance(s, Assign):
                if len(s.targets) == 1 and isinstance(s.targets[0], Name):
                    if s.targets[0].ident == target_name:
                        if isinstance(s.value, StrLit):
                            yield s.value.value
                        else:
                            yield None
                            return
                continue
            if isinstance(s, AugAssign) and isinstance(s.target, Name):
                if s.target.ident == target_name:
                    yield None
                    return
                continue
            if isinstance(s, If):
                pending.extend(s.body)
                pending.extend(s.else_body)
                continue
            if isinstance(s, While):
                pending.extend(s.body)
                pending.extend(s.else_body)
                continue
            if isinstance(s, For):
                if isinstance(s.target, Name) and s.target.ident == target_name:
                    yield None
                    return
                pending.extend(s.body)
                pending.extend(s.else_body)
                continue
            if isinstance(s, Try):
                pending.extend(s.body)
                pending.extend(s.else_body)
                pending.extend(s.finally_body)
                for h in s.handlers:
                    if h.name == target_name:
                        yield None
                        return
                    pending.extend(h.body)
                continue
            if isinstance(s, With):
                for ctx_pair in s.items:
                    as_var = ctx_pair[1]
                    if isinstance(as_var, Name) and as_var.ident == target_name:
                        yield None
                        return
                pending.extend(s.body)
                continue

    def _maybe_emit_literal_str_format(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name != "format":
            return None
        fmt_str = self._resolve_str_literal_value(attr.obj)
        if fmt_str is None:
            return None
        parts = self._parse_auto_format_literal(fmt_str, len(expr.args))
        if parts is None:
            return None
        kw_map = {}
        for kw in expr.kwargs:
            kname = kw[0]
            if kname is None or kname == "":
                return None
            if kname.startswith("*"):
                return None            # **splat: not handled here
            kw_map[kname] = kw[1]
        out: Optional[ir.Value] = None
        auto_idx = 0
        for part in parts:
            if isinstance(part, tuple):
                spec = part[0]
                kind = part[1]
                ref = part[2]
                if kind == "index":
                    idx = int(ref)
                    if idx < 0 or idx >= len(expr.args):
                        return None
                    argexpr = expr.args[idx]
                elif kind == "name":
                    if ref not in kw_map:
                        return None
                    argexpr = kw_map[ref]
                else:
                    if auto_idx >= len(expr.args):
                        return None
                    argexpr = expr.args[auto_idx]
                    auto_idx += 1
                piece = self._emit_format_arg_as_str(argexpr, spec)
            else:
                piece = self._emit_str_literal(part)
            if out is None:
                out = piece
            else:
                out = self.builder.call(
                    self.runtime["py_str_concat"],
                    [out, piece],
                    name=self._fresh("str.format.concat"),
                )
        if out is None:
            return self._emit_str_literal("")
        return out

    def _maybe_emit_literal_str_format_map(self, expr: Call) -> Optional[ir.Value]:
        """Lower ``"...{name}...".format_map(mapping)`` natively.

        Same compile-time field parse as ``.format()``, but each named field is
        resolved at runtime against the single mapping argument via the generic
        ``py_obj_getitem`` (works for dict and any mapping, and raises
        ``KeyError`` on a missing key exactly like CPython ``format_map``).

        Bounded scope (matches the common idiom): the format string must be a
        resolvable literal and every replacement field must be a ``{name}``
        named field. Auto ``{}`` and indexed ``{0}`` fields, ``**`` splats, or a
        non-literal format string bail to ``None`` so the caller can fall back.
        """
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name != "format_map":
            return None
        if len(expr.args) != 1 or expr.kwargs:
            return None
        fmt_str = self._resolve_str_literal_value(attr.obj)
        if fmt_str is None:
            return None
        # argc=0 rejects auto ``{}`` fields at parse time; format_map keys are
        # explicit, never positional auto-numbered.
        parts = self._parse_auto_format_literal(fmt_str, 0)
        if parts is None:
            return None
        for part in parts:
            if isinstance(part, tuple) and part[1] != "name":
                return None            # only named fields handled natively
        map_arg = expr.args[0]
        map_obj = self._emit_as_object(map_arg)
        span = getattr(map_arg, "span", None)
        # A real dict resolves missing keys to a catchable KeyError via
        # py_dict_getitem (CPython format_map semantics). Any other mapping
        # goes through the generic py_obj_getitem.
        getitem_fn = (
            "py_dict_getitem"
            if isinstance(getattr(map_arg, "ty", None), DictType)
            else "py_obj_getitem"
        )
        out: Optional[ir.Value] = None
        for part in parts:
            if isinstance(part, tuple):
                spec = part[0]
                ref = part[2]
                key_obj = self._emit_str_literal(ref)
                val = self.builder.call(
                    self.runtime[getitem_fn],
                    [map_obj, key_obj],
                    name=self._fresh("format_map.get"),
                )
                self._emit_post_call_err_check(span)
                spec_obj = self._emit_str_literal(spec)
                piece = self.builder.call(
                    self.runtime["py_obj_format"],
                    [val, spec_obj],
                    name=self._fresh("format_map.field"),
                )
                self._emit_post_call_err_check(span)
            else:
                piece = self._emit_str_literal(part)
            if out is None:
                out = piece
            else:
                out = self.builder.call(
                    self.runtime["py_str_concat"],
                    [out, piece],
                    name=self._fresh("format_map.concat"),
                )
        if out is None:
            return self._emit_str_literal("")
        return out

    def _emit_format_spec_builtin(self, expr: Call) -> Optional[ir.Value]:
        if len(expr.args) != 2 or expr.kwargs:
            return None
        value_expr, spec_expr = expr.args
        if not isinstance(spec_expr, StrLit):
            return None
        spec = spec_expr.value
        if spec.endswith("x"):
            body = spec[:-1]
            zero_pad = body.startswith("0") and len(body) > 0
            width_text = body[1:] if zero_pad else body
            if width_text == "" or width_text.isdigit():
                width = int(width_text) if width_text else 0
                value_obj = self._emit_as_object(value_expr)
                return self.builder.call(
                    self.runtime["py_int_format_hex"],
                    [
                        value_obj,
                        ir.Constant(_I64, width),
                        ir.Constant(_I64, 1 if zero_pad else 0),
                    ],
                    name=self._fresh("format.hex"),
                )
        if spec == "," or spec == "_" or spec.endswith("d"):
            body = spec[:-1] if spec.endswith("d") else spec
            comma = 0
            # ``comma`` carries the grouping separator *byte* (0 = none), so
            # py_int_format_decimal inserts the right char: ',' (44) or '_'
            # (95). Passing a bare 1 here would make the runtime emit chr(1).
            if body.endswith(","):
                comma = 44
                body = body[:-1]
            elif body.endswith("_"):
                comma = 95
                body = body[:-1]
            zero_pad = body.startswith("0") and len(body) > 0
            width_text = body[1:] if zero_pad else body
            if width_text == "" or width_text.isdigit():
                width = int(width_text) if width_text else 0
                value_obj = self._emit_as_object(value_expr)
                return self.builder.call(
                    self.runtime["py_int_format_decimal"],
                    [
                        value_obj,
                        ir.Constant(_I64, width),
                        ir.Constant(_I64, 1 if zero_pad else 0),
                        ir.Constant(_I64, comma),
                    ],
                    name=self._fresh("format.decimal"),
                )
        if (
            len(spec) >= 3
            and spec[0] == "."
            and spec[-1] == "f"
            and spec[1:-1].isdigit()
        ):
            precision = int(spec[1:-1])
            value_obj = self._emit_as_object(value_expr)
            return self.builder.call(
                self.runtime["py_float_format_fixed"],
                [value_obj, ir.Constant(_I64, precision)],
                name=self._fresh("format.fixed"),
            )
        result = self._emit_obj_format_literal_spec(value_expr, spec)
        self._emit_post_call_err_check(getattr(value_expr, "span", None))
        return result
