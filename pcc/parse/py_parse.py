"""P6C.3 — native Python parser skeleton.

Recursive-descent parser over the tokens produced by
:mod:`pcc.parse.py_lex`. Produces pcc's internal AST (the same
node types already used by :mod:`pcc.py_frontend.py_ast`).

Current scope is intentionally narrow — just enough to parse
``hello.py``-class programs end-to-end so the P6C.3 milestone has a
concrete proof. The full grammar (f-strings, match/case, decorators
with arguments, walrus, starred assignments) is staged in
successive iterations. CPython's own parser (``Parser/Python.asdl``
and ``Parser/parser.c``) is the reference.
"""
from __future__ import annotations

from dataclasses import dataclass

from .py_lex import (
    Lexer, Token,
    TK_DEDENT, TK_EOF, TK_INDENT, TK_KEYWORD, TK_NAME, TK_NEWLINE,
    TK_NUMBER, TK_OP, TK_STRING,
)


class ParseError(Exception):
    pass


@dataclass
class _Module:
    body: list


@dataclass
class _Pass:
    line: int


@dataclass
class _Return:
    value: "object | None"
    line: int


@dataclass
class _Expr:
    expr: object
    line: int


@dataclass
class _Num:
    value: "int | float"
    line: int


@dataclass
class _Str:
    value: str
    line: int


@dataclass
class _Name:
    ident: str
    line: int


@dataclass
class _BinOp:
    op: str
    lhs: object
    rhs: object
    line: int


@dataclass
class _Call:
    func: object
    args: list
    line: int


@dataclass
class _If:
    cond: object
    body: list
    else_body: list
    line: int


@dataclass
class _FuncDef:
    name: str
    params: list
    body: list
    line: int
    decorators: list = None
    returns: object = None


@dataclass
class _Assign:
    target: object
    value: object
    annotation: "object | None"
    line: int


@dataclass
class _AugAssign:
    target: object
    op: str           # ``+=`` / ``-=`` / ...
    value: object
    line: int


@dataclass
class _While:
    cond: object
    body: list
    else_body: list
    line: int


@dataclass
class _For:
    target: object
    iter: object
    body: list
    else_body: list
    line: int


@dataclass
class _Break:
    line: int


@dataclass
class _Continue:
    line: int


@dataclass
class _Import:
    names: list  # list of (module, as_name)
    line: int


@dataclass
class _ImportFrom:
    module: str
    names: list  # list of (attr, as_name)
    level: int
    line: int


@dataclass
class _ClassDef:
    name: str
    bases: list
    body: list
    decorators: list
    line: int


@dataclass
class _Raise:
    exc: "object | None"
    cause: "object | None"
    line: int


@dataclass
class _Try:
    body: list
    handlers: list  # list of (exc_type, as_name, body)
    else_body: list
    finally_body: list
    line: int


@dataclass
class _Attr:
    obj: object
    name: str
    line: int


@dataclass
class _Subscript:
    obj: object
    idx: object
    line: int


@dataclass
class _List:
    elems: list
    line: int


@dataclass
class _Tuple:
    elems: list
    line: int


@dataclass
class _UnaryOp:
    op: str
    operand: object
    line: int


@dataclass
class _BoolOp:
    op: str  # "and" / "or"
    values: list
    line: int


@dataclass
class _Compare:
    op: str
    lhs: object
    rhs: object
    line: int


@dataclass
class _Bool:
    value: bool
    line: int


@dataclass
class _None:
    line: int


@dataclass
class _Dict:
    keys: list
    values: list
    line: int


@dataclass
class _Set:
    elems: list
    line: int


@dataclass
class _Lambda:
    params: list   # same shape as _FuncDef.params
    body: object
    line: int


@dataclass
class _Del:
    targets: list
    line: int


@dataclass
class _Ternary:
    then_expr: object
    cond: object
    else_expr: object
    line: int


@dataclass
class _Comp:
    kind: str           # "list" / "set" / "dict" / "gen"
    elt: object         # or (key, value) tuple for dict
    generators: list    # list of (target, iter, [ifs])
    line: int


@dataclass
class _Yield:
    value: "object | None"
    is_from: bool
    line: int


@dataclass
class _Starred:
    value: object
    is_kw: bool    # ``*x`` vs ``**x``
    line: int


@dataclass
class _Global:
    names: list
    line: int


@dataclass
class _Nonlocal:
    names: list
    line: int


@dataclass
class _With:
    items: list   # list of (ctx_expr, as_name)
    body: list
    line: int


@dataclass
class _Assert:
    test: object
    msg: "object | None"
    line: int


@dataclass
class _FString:
    parts: list   # mix of str literals and embedded expr nodes
    line: int


class Parser:
    """Hand-written recursive-descent parser over the py_lex token
    stream. Returns a narrow AST (the ``_*`` dataclasses above) that
    a companion lowering step (not yet written) will convert into
    :mod:`pcc.py_frontend.py_ast` nodes.
    """

    def __init__(self, src: str, filename: str = "<input>") -> None:
        self.filename = filename
        self.tokens: list[Token] = list(Lexer(src, filename))
        self.pos = 0

    # --------------------------------------------------- token helpers

    def _peek(self, off: int = 0) -> Token:
        return self.tokens[min(self.pos + off, len(self.tokens) - 1)]

    def _advance(self) -> Token:
        t = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return t

    def _accept(self, kind: str, text: str | None = None) -> Token | None:
        t = self._peek()
        if t.kind != kind:
            return None
        if text is not None and t.text != text:
            return None
        self._advance()
        return t

    def _expect(self, kind: str, text: str | None = None) -> Token:
        # Treat EOF as an implicit NEWLINE — files without a trailing
        # newline are common enough to tolerate.
        if kind == TK_NEWLINE:
            p = self._peek()
            if p.kind == TK_EOF:
                return p
            # ``a; b`` — semicolon is a soft statement terminator.
            if p.kind == TK_OP and p.text == ";":
                return self._advance()
        t = self._accept(kind, text)
        if t is None:
            got = self._peek()
            exp = text or kind
            raise ParseError(
                f"{self.filename}:{got.line}:{got.col}: "
                f"expected {exp!r}, got {got.kind} {got.text!r}"
            )
        return t

    def _skip_newlines(self) -> None:
        while self._accept(TK_NEWLINE) is not None:
            pass

    # --------------------------------------------------- entry

    def parse_module(self) -> _Module:
        stmts: list = []
        while self._peek().kind != TK_EOF:
            self._skip_newlines()
            if self._peek().kind == TK_EOF:
                break
            stmts.append(self._parse_stmt())
        return _Module(body=stmts)

    # --------------------------------------------------- statements

    def _parse_stmt(self):
        t = self._peek()
        if t.kind == TK_KEYWORD:
            if t.text == "def":
                return self._parse_funcdef()
            if t.text == "return":
                return self._parse_return()
            if t.text == "if":
                return self._parse_if()
            if t.text == "pass":
                self._advance()
                self._expect(TK_NEWLINE)
                return _Pass(line=t.line)
            if t.text == "while":
                return self._parse_while()
            if t.text == "for":
                return self._parse_for()
            if t.text == "break":
                self._advance()
                self._expect(TK_NEWLINE)
                return _Break(line=t.line)
            if t.text == "continue":
                self._advance()
                self._expect(TK_NEWLINE)
                return _Continue(line=t.line)
            if t.text == "import":
                return self._parse_import()
            if t.text == "from":
                return self._parse_import_from()
            if t.text == "class":
                return self._parse_classdef(())
            if t.text == "raise":
                return self._parse_raise()
            if t.text == "try":
                return self._parse_try()
            if t.text == "del":
                return self._parse_del()
            if t.text == "global":
                return self._parse_global_or_nonlocal("global")
            if t.text == "nonlocal":
                return self._parse_global_or_nonlocal("nonlocal")
            if t.text == "with":
                return self._parse_with()
            if t.text == "assert":
                return self._parse_assert()
        if t.kind == TK_OP and t.text == "@":
            # Decorator chain — followed by either ``def`` or ``class``.
            return self._parse_decorated()
        return self._parse_simple_stmt()

    def _parse_del(self) -> _Del:
        kw = self._expect(TK_KEYWORD, "del")
        targets = [self._parse_expr()]
        while self._accept(TK_OP, ","):
            targets.append(self._parse_expr())
        self._expect(TK_NEWLINE)
        return _Del(targets=targets, line=kw.line)

    def _parse_global_or_nonlocal(self, which: str):
        kw = self._advance()
        names = [self._expect(TK_NAME).text]
        while self._accept(TK_OP, ","):
            names.append(self._expect(TK_NAME).text)
        self._expect(TK_NEWLINE)
        cls = _Global if which == "global" else _Nonlocal
        return cls(names=names, line=kw.line)

    def _parse_with(self) -> _With:
        kw = self._expect(TK_KEYWORD, "with")
        items = [self._parse_with_item()]
        while self._accept(TK_OP, ","):
            items.append(self._parse_with_item())
        self._expect(TK_OP, ":")
        body = self._parse_block()
        return _With(items=items, body=body, line=kw.line)

    def _parse_with_item(self) -> tuple:
        ctx = self._parse_expr()
        as_name = None
        if self._accept(TK_KEYWORD, "as"):
            as_name = self._expect(TK_NAME).text
        return (ctx, as_name)

    def _parse_assert(self) -> _Assert:
        kw = self._expect(TK_KEYWORD, "assert")
        test = self._parse_expr()
        msg = None
        if self._accept(TK_OP, ","):
            msg = self._parse_expr()
        self._expect(TK_NEWLINE)
        return _Assert(test=test, msg=msg, line=kw.line)

    def _parse_simple_stmt(self):
        """Expression statement OR ``name (: ann)? = value`` assign OR
        ``target op= value`` augmented assign. Also handles tuple
        unpacking on the LHS: ``a, b = pair``."""
        start = self.pos
        lhs = self._parse_expr()
        # Tuple LHS: ``a, b = ...`` — fold the comma list into a _Tuple
        # so the assign target is a single node.
        if self._peek().kind == TK_OP and self._peek().text == ",":
            elems = [lhs]
            while self._accept(TK_OP, ","):
                p = self._peek()
                if p.kind == TK_NEWLINE:
                    break
                if p.kind == TK_OP and p.text in ("=", ":"):
                    break
                elems.append(self._parse_expr())
            lhs = _Tuple(elems=elems, line=self._peek().line)
        t = self._peek()
        if t.kind == TK_OP and t.text == ":":
            # Annotation. Consume ``: type`` and optional ``= value``.
            self._advance()
            ann_node = self._parse_type_expr()
            if self._accept(TK_OP, "="):
                value = self._parse_expr()
            else:
                value = _None(line=t.line)
            self._expect(TK_NEWLINE)
            return _Assign(
                target=lhs, value=value, annotation=ann_node, line=t.line,
            )
        if t.kind == TK_OP and t.text == "=":
            self._advance()
            value = self._parse_expr()
            # Support ``a, b = 1, 2``: tuple RHS without parens.
            if self._peek().kind == TK_OP and self._peek().text == ",":
                elems = [value]
                while self._accept(TK_OP, ","):
                    if self._peek().kind == TK_NEWLINE:
                        break
                    elems.append(self._parse_expr())
                value = _Tuple(elems=elems, line=t.line)
            # Chained assignment ``a = b = c`` is parsed as repeated
            # ``= rhs`` — fold extras into nested assigns.
            while self._peek().kind == TK_OP and self._peek().text == "=":
                self._advance()
                nxt = self._parse_expr()
                if self._peek().kind == TK_OP and self._peek().text == ",":
                    elems = [nxt]
                    while self._accept(TK_OP, ","):
                        if self._peek().kind == TK_NEWLINE:
                            break
                        elems.append(self._parse_expr())
                    nxt = _Tuple(elems=elems, line=t.line)
                # Rewrite ``a = b = c`` as ``a = (b := c)`` — model
                # with a nested _Assign on the RHS.
                value = _Assign(
                    target=value, value=nxt, annotation=None, line=t.line,
                )
            self._expect(TK_NEWLINE)
            return _Assign(target=lhs, value=value, annotation=None,
                            line=t.line)
        if t.kind == TK_OP and t.text in (
            "+=", "-=", "*=", "/=", "//=", "%=", "**=",
            "&=", "|=", "^=", "<<=", ">>=",
        ):
            op = self._advance().text
            value = self._parse_expr()
            self._expect(TK_NEWLINE)
            return _AugAssign(target=lhs, op=op, value=value, line=t.line)
        # Plain expression statement.
        self._expect(TK_NEWLINE)
        return _Expr(expr=lhs, line=t.line)

    def _parse_while(self) -> _While:
        kw = self._expect(TK_KEYWORD, "while")
        cond = self._parse_expr()
        self._expect(TK_OP, ":")
        body = self._parse_block()
        else_body: list = []
        if self._peek().kind == TK_KEYWORD and self._peek().text == "else":
            self._advance()
            self._expect(TK_OP, ":")
            else_body = self._parse_block()
        return _While(cond=cond, body=body, else_body=else_body,
                       line=kw.line)

    def _parse_for(self) -> _For:
        kw = self._expect(TK_KEYWORD, "for")
        target = self._parse_for_target()
        self._expect(TK_KEYWORD, "in")
        it = self._parse_expr()
        # ``for a in b, c, d:`` — implicit tuple iterable.
        if self._peek().kind == TK_OP and self._peek().text == ",":
            elems = [it]
            while self._accept(TK_OP, ","):
                if self._peek().text == ":":
                    break
                elems.append(self._parse_expr())
            it = _Tuple(elems=elems, line=kw.line)
        self._expect(TK_OP, ":")
        body = self._parse_block()
        else_body: list = []
        if self._peek().kind == TK_KEYWORD and self._peek().text == "else":
            self._advance()
            self._expect(TK_OP, ":")
            else_body = self._parse_block()
        return _For(target=target, iter=it, body=body, else_body=else_body,
                     line=kw.line)

    def _parse_for_target(self):
        """Loop variable: NAME, ``*name``, tuple ``(a, b)``, or comma-list ``a, b``."""
        first = self._parse_for_target_atom()
        if self._peek().kind == TK_OP and self._peek().text == ",":
            elems = [first]
            while self._accept(TK_OP, ","):
                if (
                    self._peek().kind == TK_KEYWORD
                    and self._peek().text == "in"
                ):
                    break
                if self._peek().kind == TK_OP and self._peek().text in (
                    ")", "]",
                ):
                    break
                elems.append(self._parse_for_target_atom())
            return _Tuple(elems=elems, line=first.line if hasattr(first, "line") else 0)
        return first

    def _parse_for_target_atom(self):
        t = self._peek()
        if t.kind == TK_OP and t.text == "(":
            self._advance()
            inner = self._parse_for_target()
            self._expect(TK_OP, ")")
            return inner
        if t.kind == TK_OP and t.text == "[":
            self._advance()
            inner = self._parse_for_target()
            self._expect(TK_OP, "]")
            return inner
        if t.kind == TK_OP and t.text == "*":
            self._advance()
            name = self._expect(TK_NAME)
            return _Starred(
                value=_Name(name.text, name.line), is_kw=False, line=t.line,
            )
        name = self._expect(TK_NAME)
        node = _Name(name.text, name.line)
        # Allow dotted attr or subscript (rare but legal): ``for obj.x in ...``.
        while True:
            p = self._peek()
            if p.kind == TK_OP and p.text == ".":
                self._advance()
                attr = self._expect(TK_NAME).text
                node = _Attr(obj=node, name=attr, line=p.line)
            elif p.kind == TK_OP and p.text == "[":
                self._advance()
                idx = self._parse_subscript()
                self._expect(TK_OP, "]")
                node = _Subscript(obj=node, idx=idx, line=p.line)
            else:
                break
        return node

    def _parse_import(self) -> _Import:
        kw = self._expect(TK_KEYWORD, "import")
        names: list = []
        names.append(self._parse_dotted_name_as_pair())
        while self._accept(TK_OP, ","):
            names.append(self._parse_dotted_name_as_pair())
        self._expect(TK_NEWLINE)
        return _Import(names=names, line=kw.line)

    def _parse_import_from(self) -> _ImportFrom:
        kw = self._expect(TK_KEYWORD, "from")
        level = 0
        while self._accept(TK_OP, "."):
            level += 1
        if level and self._peek().kind == TK_KEYWORD:
            # Pure ``from . import x`` — no dotted-name portion.
            module = ""
        else:
            module = self._parse_dotted_name()
        self._expect(TK_KEYWORD, "import")
        names: list = []
        if self._accept(TK_OP, "("):
            names.append(self._parse_import_name_item())
            while self._accept(TK_OP, ","):
                if self._peek().text == ")":
                    break
                names.append(self._parse_import_name_item())
            self._expect(TK_OP, ")")
        else:
            names.append(self._parse_import_name_item())
            while self._accept(TK_OP, ","):
                names.append(self._parse_import_name_item())
        self._expect(TK_NEWLINE)
        return _ImportFrom(module=module, names=names, level=level,
                             line=kw.line)

    def _parse_dotted_name_as_pair(self) -> tuple:
        mod = self._parse_dotted_name()
        as_name = None
        if self._accept(TK_KEYWORD, "as"):
            as_name = self._expect(TK_NAME).text
        return (mod, as_name)

    def _parse_import_name_item(self) -> tuple:
        # ``from x import *`` — star import.
        if self._peek().kind == TK_OP and self._peek().text == "*":
            self._advance()
            return ("*", None)
        name = self._expect(TK_NAME).text
        as_name = None
        if self._accept(TK_KEYWORD, "as"):
            as_name = self._expect(TK_NAME).text
        return (name, as_name)

    def _parse_dotted_name(self) -> str:
        parts = [self._expect(TK_NAME).text]
        while self._accept(TK_OP, "."):
            parts.append(self._expect(TK_NAME).text)
        return ".".join(parts)

    def _parse_raise(self) -> _Raise:
        kw = self._expect(TK_KEYWORD, "raise")
        exc = None
        cause = None
        if self._peek().kind != TK_NEWLINE:
            exc = self._parse_expr()
            if self._accept(TK_KEYWORD, "from"):
                cause = self._parse_expr()
        self._expect(TK_NEWLINE)
        return _Raise(exc=exc, cause=cause, line=kw.line)

    def _parse_try(self) -> _Try:
        kw = self._expect(TK_KEYWORD, "try")
        self._expect(TK_OP, ":")
        body = self._parse_block()
        handlers: list = []
        else_body: list = []
        finally_body: list = []
        while self._peek().kind == TK_KEYWORD and self._peek().text == "except":
            self._advance()
            exc_type = None
            as_name = None
            if self._peek().kind != TK_OP or self._peek().text != ":":
                exc_type = self._parse_expr()
                if self._accept(TK_KEYWORD, "as"):
                    as_name = self._expect(TK_NAME).text
            self._expect(TK_OP, ":")
            handlers.append((exc_type, as_name, self._parse_block()))
        if self._peek().kind == TK_KEYWORD and self._peek().text == "else":
            self._advance()
            self._expect(TK_OP, ":")
            else_body = self._parse_block()
        if self._peek().kind == TK_KEYWORD and self._peek().text == "finally":
            self._advance()
            self._expect(TK_OP, ":")
            finally_body = self._parse_block()
        return _Try(body=body, handlers=handlers, else_body=else_body,
                     finally_body=finally_body, line=kw.line)

    def _parse_decorated(self):
        decorators: list = []
        while self._accept(TK_OP, "@"):
            decorators.append(self._parse_expr())
            self._expect(TK_NEWLINE)
        t = self._peek()
        if t.kind == TK_KEYWORD and t.text == "def":
            fn = self._parse_funcdef()
            fn.decorators = list(decorators)
            return fn
        if t.kind == TK_KEYWORD and t.text == "class":
            return self._parse_classdef(tuple(decorators))
        raise ParseError(
            f"{self.filename}:{t.line}: expected def or class after "
            "decorators"
        )

    def _parse_classdef(self, decorators: tuple) -> _ClassDef:
        kw = self._expect(TK_KEYWORD, "class")
        name = self._expect(TK_NAME).text
        bases: list = []
        if self._accept(TK_OP, "("):
            if self._peek().text != ")":
                bases.append(self._parse_call_arg())
                while self._accept(TK_OP, ","):
                    if self._peek().text == ")":
                        break
                    bases.append(self._parse_call_arg())
            self._expect(TK_OP, ")")
        self._expect(TK_OP, ":")
        body = self._parse_block()
        return _ClassDef(name=name, bases=bases, body=body,
                          decorators=list(decorators), line=kw.line)

    def _parse_funcdef(self) -> _FuncDef:
        kw = self._expect(TK_KEYWORD, "def")
        name = self._expect(TK_NAME).text
        self._expect(TK_OP, "(")
        # Each element is a tuple (kind, name) where kind ∈
        # {"pos", "*args", "**kwargs"}. We drop type / default info at
        # the skeleton layer; the AST lift will pick them up once we
        # wire it in.
        params: list[tuple[str, str]] = []
        if self._peek().text != ")":
            params.append(self._parse_funcdef_param())
            while self._accept(TK_OP, ","):
                if self._peek().text == ")":
                    break
                params.append(self._parse_funcdef_param())
        self._expect(TK_OP, ")")
        # Optional ``-> type`` return annotation.
        returns = None
        if self._accept(TK_OP, "->"):
            returns = self._parse_type_expr()
        self._expect(TK_OP, ":")
        body = self._parse_block()
        return _FuncDef(
            name=name, params=params, body=body, line=kw.line,
            returns=returns,
        )

    def _parse_funcdef_param(self, in_lambda: bool = False):
        """Parse one parameter. Returns a 4-tuple
        ``(kind, name, annotation, default)`` — ``annotation`` is the
        AST node for the ``: T`` part (or ``None``), ``default`` the
        value expression after ``=`` (or ``None``)."""
        t = self._peek()
        if t.kind == TK_OP and t.text == "*":
            self._advance()
            if self._peek().kind == TK_OP and self._peek().text in (",", ":"):
                return ("kwonly-sep", "", None, None)
            pname = self._expect(TK_NAME).text
            ann = None
            if not in_lambda and self._accept(TK_OP, ":"):
                ann = self._parse_type_expr()
            return ("*args", pname, ann, None)
        if t.kind == TK_OP and t.text == "**":
            self._advance()
            pname = self._expect(TK_NAME).text
            ann = None
            if not in_lambda and self._accept(TK_OP, ":"):
                ann = self._parse_type_expr()
            return ("**kwargs", pname, ann, None)
        if t.kind == TK_OP and t.text == "/":
            self._advance()
            return ("pos-only-sep", "", None, None)
        pname = self._expect(TK_NAME).text
        ann = None
        default = None
        if not in_lambda and self._accept(TK_OP, ":"):
            ann = self._parse_type_expr()
        if self._accept(TK_OP, "="):
            default = self._parse_expr()
        return ("pos", pname, ann, default)

    def _parse_type_expr(self):
        """Parse a type expression and return it as an expression AST
        node. Accepts Names, singletons (``None`` / ``True`` / ``False``),
        chained ``a.b``, string forward refs, tuple shorthand, ``[X]``
        subscripts, and PEP 604 union ``A | B``."""
        lhs = self._parse_type_atom()
        while self._accept(TK_OP, "|"):
            rhs = self._parse_type_atom()
            lhs = _BinOp(
                op="|", lhs=lhs, rhs=rhs,
                line=getattr(lhs, "line", self._peek().line),
            )
        return lhs

    def _parse_type_atom(self):
        t = self._peek()
        if t.kind == TK_OP and t.text == "...":
            # Ellipsis — ``Callable[..., T]`` / ``tuple[T, ...]``.
            self._advance()
            node = _Name(ident="Ellipsis", line=t.line)
            return node
        if t.kind == TK_KEYWORD and t.text in ("None", "True", "False"):
            self._advance()
            if t.text == "None":
                node = _None(line=t.line)
            else:
                node = _Bool(value=(t.text == "True"), line=t.line)
        elif t.kind == TK_OP and t.text == "(":
            self._advance()
            elems: list = []
            if not self._accept(TK_OP, ")"):
                elems.append(self._parse_type_expr())
                while self._accept(TK_OP, ","):
                    if self._peek().text == ")":
                        break
                    elems.append(self._parse_type_expr())
                self._expect(TK_OP, ")")
            node = _Tuple(elems=elems, line=t.line)
        elif t.kind == TK_NAME:
            self._advance()
            node = _Name(ident=t.text, line=t.line)
            while self._accept(TK_OP, "."):
                attr_tok = self._expect(TK_NAME)
                node = _Attr(obj=node, name=attr_tok.text, line=attr_tok.line)
        elif t.kind == TK_STRING:
            self._advance()
            body = self._string_body(t.text)
            node = _Str(value=body, line=t.line)
        elif t.kind == TK_OP and t.text == "*":
            self._advance()
            inner = self._parse_type_atom()
            node = _Starred(value=inner, is_kw=False, line=t.line)
        else:
            return _None(line=t.line)
        if self._accept(TK_OP, "["):
            elems = []
            if not self._accept(TK_OP, "]"):
                elems.append(self._parse_type_subscript_elem())
                while self._accept(TK_OP, ","):
                    if self._peek().text == "]":
                        break
                    elems.append(self._parse_type_subscript_elem())
                self._expect(TK_OP, "]")
            idx = elems[0] if len(elems) == 1 else _Tuple(elems=elems, line=t.line)
            node = _Subscript(obj=node, idx=idx, line=t.line)
        return node

    def _parse_type_subscript_elem(self):
        """One subscript item — may be a nested list ``[A, B]`` for
        ``Callable[[A, B], R]`` signatures."""
        if self._peek().kind == TK_OP and self._peek().text == "[":
            start = self._advance()
            elems = []
            if not self._accept(TK_OP, "]"):
                elems.append(self._parse_type_expr())
                while self._accept(TK_OP, ","):
                    if self._peek().text == "]":
                        break
                    elems.append(self._parse_type_expr())
                self._expect(TK_OP, "]")
            return _List(elems=elems, line=start.line)
        return self._parse_type_expr()

    def _parse_return(self) -> _Return:
        kw = self._expect(TK_KEYWORD, "return")
        if self._peek().kind == TK_NEWLINE:
            self._advance()
            return _Return(value=None, line=kw.line)
        value = self._parse_expr()
        # Implicit tuple: ``return a, b``.
        if self._peek().kind == TK_OP and self._peek().text == ",":
            elems = [value]
            while self._accept(TK_OP, ","):
                if self._peek().kind == TK_NEWLINE:
                    break
                elems.append(self._parse_expr())
            value = _Tuple(elems=elems, line=kw.line)
        self._expect(TK_NEWLINE)
        return _Return(value=value, line=kw.line)

    def _parse_if(self) -> _If:
        kw = self._expect(TK_KEYWORD, "if")
        cond = self._parse_expr()
        self._expect(TK_OP, ":")
        body = self._parse_block()
        else_body: list = []
        if self._peek().kind == TK_KEYWORD and self._peek().text == "elif":
            else_body = [self._parse_if_elif()]
        elif self._peek().kind == TK_KEYWORD and self._peek().text == "else":
            self._advance()
            self._expect(TK_OP, ":")
            else_body = self._parse_block()
        return _If(cond=cond, body=body, else_body=else_body, line=kw.line)

    def _parse_if_elif(self) -> _If:
        # Same shape as ``_parse_if`` but for the ``elif`` keyword.
        kw = self._expect(TK_KEYWORD, "elif")
        cond = self._parse_expr()
        self._expect(TK_OP, ":")
        body = self._parse_block()
        else_body: list = []
        if self._peek().kind == TK_KEYWORD and self._peek().text == "elif":
            else_body = [self._parse_if_elif()]
        elif self._peek().kind == TK_KEYWORD and self._peek().text == "else":
            self._advance()
            self._expect(TK_OP, ":")
            else_body = self._parse_block()
        return _If(cond=cond, body=body, else_body=else_body, line=kw.line)

    def _parse_block(self) -> list:
        # Single-line suite: ``if x: return 1`` — body follows on the
        # same line instead of indenting. CPython supports this for
        # most compound statements.
        if self._peek().kind != TK_NEWLINE:
            stmts: list = []
            stmts.append(self._parse_stmt())
            while self._peek().kind == TK_OP and self._peek().text == ";":
                self._advance()
                if self._peek().kind == TK_NEWLINE:
                    break
                stmts.append(self._parse_stmt())
            return stmts
        self._expect(TK_NEWLINE)
        # Allow blank / comment-only lines between the colon and the
        # indented block — the lexer emits a NEWLINE for each.
        self._skip_newlines()
        self._expect(TK_INDENT)
        stmts = []
        while self._peek().kind != TK_DEDENT:
            self._skip_newlines()
            if self._peek().kind == TK_DEDENT:
                break
            stmts.append(self._parse_stmt())
        self._expect(TK_DEDENT)
        return stmts

    def _parse_expr_stmt(self) -> _Expr:
        e = self._parse_expr()
        line = self._peek().line
        self._expect(TK_NEWLINE)
        return _Expr(expr=e, line=line)

    # --------------------------------------------------- expressions
    #
    # Precedence ladder (narrow but covers pcc source):
    #   or       -> or_term (or or_term)*
    #   or_term  -> and_term (and and_term)*
    #   and_term -> not_term
    #   not_term -> 'not' not_term | compare
    #   compare  -> add ((<|>|<=|>=|==|!=|is|in|'not' 'in') add)*
    #   add      -> mul ((+|-) mul)*
    #   mul      -> unary ((*|/|//|%) unary)*
    #   unary    -> (+|-|~) unary | power
    #   power    -> atom trailer*        trailer = (args) | [idx] | .name
    #   atom     -> NUMBER | STRING | NAME | True | False | None |
    #               '(' expr ')' | '[' items ']' | '(' items ',' ')'

    def _parse_expr(self):
        # Top-level expression accepts ``lambda``, ``yield``, ternary
        # ``a if b else c`` and walrus ``name := expr``. Ternary has the
        # lowest precedence above ``or``; lambda / yield / walrus are
        # parallel entry points.
        t = self._peek()
        if t.kind == TK_KEYWORD and t.text == "lambda":
            return self._parse_lambda()
        if t.kind == TK_KEYWORD and t.text == "yield":
            return self._parse_yield_expr()
        # Walrus: ``name := expr``. Only when the next-next token is
        # exactly ``:=``.
        if (
            t.kind == TK_NAME
            and self._peek(1).kind == TK_OP
            and self._peek(1).text == ":="
        ):
            target_tok = self._advance()
            self._advance()  # ':='
            val = self._parse_expr()
            # Model as an _Assign so the lowering stage can see it
            # as both an expression and a binding.
            return _Assign(
                target=_Name(target_tok.text, target_tok.line),
                value=val, annotation="walrus", line=target_tok.line,
            )
        lhs = self._parse_or()
        if self._peek().kind == TK_KEYWORD and self._peek().text == "if":
            self._advance()
            cond = self._parse_or()
            self._expect(TK_KEYWORD, "else")
            else_expr = self._parse_expr()
            return _Ternary(
                then_expr=lhs, cond=cond, else_expr=else_expr,
                line=self._peek().line,
            )
        return lhs

    def _parse_lambda(self):
        kw = self._expect(TK_KEYWORD, "lambda")
        params: list = []
        if self._peek().text != ":":
            params.append(self._parse_funcdef_param(in_lambda=True))
            while self._accept(TK_OP, ","):
                params.append(self._parse_funcdef_param(in_lambda=True))
        self._expect(TK_OP, ":")
        body = self._parse_expr()
        return _Lambda(params=params, body=body, line=kw.line)

    def _parse_yield_expr(self):
        kw = self._expect(TK_KEYWORD, "yield")
        if self._accept(TK_KEYWORD, "from"):
            val = self._parse_expr()
            return _Yield(value=val, is_from=True, line=kw.line)
        # Empty ``yield`` allowed as a standalone expression.
        t = self._peek()
        if t.kind == TK_NEWLINE or (t.kind == TK_OP and t.text in (")", "]", "}", ",", ":")):
            return _Yield(value=None, is_from=False, line=kw.line)
        val = self._parse_expr()
        return _Yield(value=val, is_from=False, line=kw.line)

    def _parse_or(self):
        lhs = self._parse_and()
        while self._peek().kind == TK_KEYWORD and self._peek().text == "or":
            self._advance()
            rhs = self._parse_and()
            lhs = _BoolOp(op="or", values=[lhs, rhs], line=self._peek().line)
        return lhs

    def _parse_and(self):
        lhs = self._parse_not()
        while self._peek().kind == TK_KEYWORD and self._peek().text == "and":
            self._advance()
            rhs = self._parse_not()
            lhs = _BoolOp(op="and", values=[lhs, rhs], line=self._peek().line)
        return lhs

    def _parse_not(self):
        if self._peek().kind == TK_KEYWORD and self._peek().text == "not":
            kw = self._advance()
            inner = self._parse_not()
            return _UnaryOp(op="not", operand=inner, line=kw.line)
        return self._parse_compare()

    def _parse_compare(self):
        lhs = self._parse_bitor()
        while True:
            t = self._peek()
            if t.kind == TK_OP and t.text in (
                "<", ">", "<=", ">=", "==", "!=",
            ):
                op = self._advance().text
            elif t.kind == TK_KEYWORD and t.text == "is":
                self._advance()
                if self._accept(TK_KEYWORD, "not"):
                    op = "is not"
                else:
                    op = "is"
            elif t.kind == TK_KEYWORD and t.text == "in":
                self._advance()
                op = "in"
            elif (
                t.kind == TK_KEYWORD and t.text == "not"
                and self._peek(1).kind == TK_KEYWORD
                and self._peek(1).text == "in"
            ):
                self._advance()  # not
                self._advance()  # in
                op = "not in"
            else:
                break
            rhs = self._parse_bitor()
            lhs = _Compare(op=op, lhs=lhs, rhs=rhs, line=self._peek().line)
        return lhs

    def _parse_bitor(self):
        lhs = self._parse_bitxor()
        while self._peek().kind == TK_OP and self._peek().text == "|":
            self._advance()
            rhs = self._parse_bitxor()
            lhs = _BinOp(op="|", lhs=lhs, rhs=rhs, line=self._peek().line)
        return lhs

    def _parse_bitxor(self):
        lhs = self._parse_bitand()
        while self._peek().kind == TK_OP and self._peek().text == "^":
            self._advance()
            rhs = self._parse_bitand()
            lhs = _BinOp(op="^", lhs=lhs, rhs=rhs, line=self._peek().line)
        return lhs

    def _parse_bitand(self):
        lhs = self._parse_shift()
        while self._peek().kind == TK_OP and self._peek().text == "&":
            self._advance()
            rhs = self._parse_shift()
            lhs = _BinOp(op="&", lhs=lhs, rhs=rhs, line=self._peek().line)
        return lhs

    def _parse_shift(self):
        lhs = self._parse_add()
        while self._peek().kind == TK_OP and self._peek().text in ("<<", ">>"):
            op = self._advance().text
            rhs = self._parse_add()
            lhs = _BinOp(op=op, lhs=lhs, rhs=rhs, line=self._peek().line)
        return lhs

    def _parse_add(self):
        lhs = self._parse_mul()
        while self._peek().kind == TK_OP and self._peek().text in ("+", "-"):
            op = self._advance().text
            rhs = self._parse_mul()
            lhs = _BinOp(op=op, lhs=lhs, rhs=rhs, line=self._peek().line)
        return lhs

    def _parse_mul(self):
        lhs = self._parse_unary()
        while self._peek().kind == TK_OP and self._peek().text in (
            "*", "/", "//", "%", "@",
        ):
            op = self._advance().text
            rhs = self._parse_unary()
            lhs = _BinOp(op=op, lhs=lhs, rhs=rhs, line=self._peek().line)
        return lhs

    def _parse_unary(self):
        t = self._peek()
        if t.kind == TK_OP and t.text in ("-", "+", "~"):
            self._advance()
            inner = self._parse_unary()
            return _UnaryOp(op=t.text, operand=inner, line=t.line)
        return self._parse_power()

    def _parse_power(self):
        lhs = self._parse_atom_trailer()
        if self._peek().kind == TK_OP and self._peek().text == "**":
            self._advance()
            rhs = self._parse_unary()  # right-associative
            return _BinOp(op="**", lhs=lhs, rhs=rhs, line=self._peek().line)
        return lhs

    @staticmethod
    def _string_body(raw: str) -> str:
        """Strip quotes and the optional prefix (``b``/``f``/``r``/``u``
        and 2-char combinations) from a STRING token's raw text.

        Returns the string body *with escapes unprocessed* — the
        lowering stage owns escape handling because it needs to know
        whether the prefix said ``r`` (raw, no processing) or not."""
        i = 0
        while i < len(raw) and raw[i] not in ("'", '"'):
            i += 1
        prefix = raw[:i]
        rest = raw[i:]
        if rest[:3] in ('"""', "'''"):
            return rest[3:-3]
        return rest[1:-1]

    def _parse_subscript(self):
        """``expr``, ``a:b``, ``a:b:c`` or tuple of subscripts."""
        first = self._parse_subscript_elem()
        if self._peek().kind == TK_OP and self._peek().text == ",":
            elems = [first]
            while self._accept(TK_OP, ","):
                if self._peek().text == "]":
                    break
                elems.append(self._parse_subscript_elem())
            return _Tuple(elems=elems, line=self._peek().line)
        return first

    def _parse_subscript_elem(self):
        """Single subscript: expression or ``start:stop[:step]`` slice."""
        if self._peek().kind == TK_OP and self._peek().text == ":":
            lo = None
        else:
            lo = self._parse_expr()
        if not (self._peek().kind == TK_OP and self._peek().text == ":"):
            return lo
        self._advance()  # ':'
        if (
            self._peek().kind == TK_OP
            and self._peek().text in (":", "]", ",")
        ):
            hi = None
        else:
            hi = self._parse_expr()
        step = None
        if self._accept(TK_OP, ":"):
            if self._peek().kind == TK_OP and self._peek().text in ("]", ","):
                step = None
            else:
                step = self._parse_expr()
        # Model a slice as a call to the sentinel ``_slice`` builtin
        # so existing AST visitors treat it like any other expression.
        line = self._peek().line
        args = [
            lo if lo is not None else _None(line),
            hi if hi is not None else _None(line),
            step if step is not None else _None(line),
        ]
        return _Call(func=_Name("_slice", line), args=args, line=line)

    def _parse_call_arg(self):
        """Call argument: expression, ``*expr``, ``**expr``, or
        ``name=expr`` kwarg. Lifts to an _Assign / _Starred where
        appropriate so the lowering stage can distinguish shapes."""
        t = self._peek()
        if t.kind == TK_OP and t.text == "*" and self._peek(1).text != "*":
            self._advance()
            val = self._parse_expr()
            return _Starred(value=val, is_kw=False, line=t.line)
        if t.kind == TK_OP and t.text == "**":
            self._advance()
            val = self._parse_expr()
            return _Starred(value=val, is_kw=True, line=t.line)
        # ``name=expr`` kwarg. We need one-token lookahead past the
        # name — but since we parse the whole expression first, we
        # detect the kwarg shape by looking at ``NAME '='`` ahead.
        if (
            t.kind == TK_NAME
            and self._peek(1).kind == TK_OP
            and self._peek(1).text == "="
        ):
            name_tok = self._advance()
            self._advance()  # '='
            val = self._parse_expr()
            return _Assign(
                target=_Name(name_tok.text, name_tok.line),
                value=val, annotation=None, line=name_tok.line,
            )
        return self._parse_expr()

    def _parse_atom_trailer(self):
        node = self._parse_atom()
        while True:
            t = self._peek()
            if t.kind == TK_OP and t.text == "(":
                self._advance()
                args: list = []
                if self._peek().text != ")":
                    args.append(self._parse_call_arg())
                    # ``f(x for y in z)`` — generator comprehension as
                    # the sole argument. The grammar allows a bare
                    # ``for`` right after the first expression.
                    if (
                        self._peek().kind == TK_KEYWORD
                        and self._peek().text == "for"
                    ):
                        gens = self._parse_comp_for()
                        args[-1] = _Comp(
                            kind="gen", elt=args[-1], generators=gens,
                            line=t.line,
                        )
                    else:
                        while self._accept(TK_OP, ","):
                            if self._peek().text == ")":
                                break
                            args.append(self._parse_call_arg())
                self._expect(TK_OP, ")")
                node = _Call(func=node, args=args, line=t.line)
            elif t.kind == TK_OP and t.text == "[":
                self._advance()
                idx = self._parse_subscript()
                self._expect(TK_OP, "]")
                node = _Subscript(obj=node, idx=idx, line=t.line)
            elif t.kind == TK_OP and t.text == ".":
                self._advance()
                attr = self._expect(TK_NAME).text
                node = _Attr(obj=node, name=attr, line=t.line)
            else:
                break
        return node

    def _parse_atom(self):
        t = self._peek()
        if t.kind == TK_NUMBER:
            self._advance()
            clean = t.text.replace("_", "")
            if "." in clean or "e" in clean.lower():
                return _Num(float(clean), t.line)
            return _Num(int(clean, 0), t.line)
        if t.kind == TK_STRING:
            self._advance()
            body = self._string_body(t.text)
            # Implicit concatenation: ``"foo" "bar"`` / ``"foo" r"bar"``.
            while self._peek().kind == TK_STRING:
                nxt = self._advance()
                body += self._string_body(nxt.text)
            return _Str(body, t.line)
        if t.kind == TK_KEYWORD:
            if t.text == "True":
                self._advance()
                return _Bool(True, t.line)
            if t.text == "False":
                self._advance()
                return _Bool(False, t.line)
            if t.text == "None":
                self._advance()
                return _None(t.line)
        if t.kind == TK_OP and t.text == "...":
            self._advance()
            # Model Ellipsis as a distinguished _Name — lowering can
            # treat it like any builtin singleton.
            return _Name("Ellipsis", t.line)
        if t.kind == TK_NAME:
            self._advance()
            return _Name(t.text, t.line)
        if t.kind == TK_OP and t.text == "(":
            self._advance()
            if self._accept(TK_OP, ")"):
                return _Tuple(elems=[], line=t.line)
            first = self._parse_list_item()
            # Generator expression: ``(x for y in z)``.
            if (
                self._peek().kind == TK_KEYWORD
                and self._peek().text == "for"
                and not isinstance(first, _Starred)
            ):
                gens = self._parse_comp_for()
                self._expect(TK_OP, ")")
                return _Comp(kind="gen", elt=first, generators=gens,
                              line=t.line)
            if self._accept(TK_OP, ","):
                elems = [first]
                if self._peek().text != ")":
                    elems.append(self._parse_list_item())
                    while self._accept(TK_OP, ","):
                        if self._peek().text == ")":
                            break
                        elems.append(self._parse_list_item())
                self._expect(TK_OP, ")")
                return _Tuple(elems=elems, line=t.line)
            self._expect(TK_OP, ")")
            return first
        if t.kind == TK_OP and t.text == "[":
            self._advance()
            if self._accept(TK_OP, "]"):
                return _List(elems=[], line=t.line)
            first = self._parse_list_item()
            # List comprehension: ``[expr for ...]``.
            if (
                self._peek().kind == TK_KEYWORD
                and self._peek().text == "for"
                and not isinstance(first, _Starred)
            ):
                gens = self._parse_comp_for()
                self._expect(TK_OP, "]")
                return _Comp(kind="list", elt=first, generators=gens,
                              line=t.line)
            elems: list = [first]
            while self._accept(TK_OP, ","):
                if self._peek().text == "]":
                    break
                elems.append(self._parse_list_item())
            self._expect(TK_OP, "]")
            return _List(elems=elems, line=t.line)
        if t.kind == TK_OP and t.text == "{":
            self._advance()
            if self._accept(TK_OP, "}"):
                # Empty ``{}`` is a dict literal, not a set.
                return _Dict(keys=[], values=[], line=t.line)
            # ``{*x, ...}`` → set with unpacking. Detect before the
            # generic expression parse because ``*`` isn't valid at
            # _parse_expr's top.
            if self._peek().kind == TK_OP and self._peek().text == "*" and self._peek(1).text != "*":
                first = self._parse_list_item()
                elems = [first]
                while self._accept(TK_OP, ","):
                    if self._peek().text == "}":
                        break
                    elems.append(self._parse_list_item())
                self._expect(TK_OP, "}")
                return _Set(elems=elems, line=t.line)
            # ``{**m}`` → dict. Otherwise parse first expression and
            # look at the next token to decide dict vs set vs comp.
            if self._peek().kind == TK_OP and self._peek().text == "**":
                self._advance()
                val = self._parse_expr()
                keys: list = [None]
                values: list = [val]
                while self._accept(TK_OP, ","):
                    if self._peek().text == "}":
                        break
                    if self._accept(TK_OP, "**"):
                        keys.append(None)
                        values.append(self._parse_expr())
                    else:
                        k = self._parse_expr()
                        self._expect(TK_OP, ":")
                        keys.append(k)
                        values.append(self._parse_expr())
                self._expect(TK_OP, "}")
                return _Dict(keys=keys, values=values, line=t.line)
            first = self._parse_expr()
            if self._accept(TK_OP, ":"):
                # Dict literal or dict comprehension.
                v = self._parse_expr()
                if (
                    self._peek().kind == TK_KEYWORD
                    and self._peek().text == "for"
                ):
                    gens = self._parse_comp_for()
                    self._expect(TK_OP, "}")
                    return _Comp(kind="dict", elt=(first, v),
                                  generators=gens, line=t.line)
                keys = [first]
                values = [v]
                while self._accept(TK_OP, ","):
                    if self._peek().text == "}":
                        break
                    if self._accept(TK_OP, "**"):
                        keys.append(None)
                        values.append(self._parse_expr())
                        continue
                    k = self._parse_expr()
                    self._expect(TK_OP, ":")
                    keys.append(k)
                    values.append(self._parse_expr())
                self._expect(TK_OP, "}")
                return _Dict(keys=keys, values=values, line=t.line)
            if (
                self._peek().kind == TK_KEYWORD
                and self._peek().text == "for"
            ):
                gens = self._parse_comp_for()
                self._expect(TK_OP, "}")
                return _Comp(kind="set", elt=first, generators=gens,
                              line=t.line)
            # Plain set literal.
            elems = [first]
            while self._accept(TK_OP, ","):
                if self._peek().text == "}":
                    break
                elems.append(self._parse_list_item())
            self._expect(TK_OP, "}")
            return _Set(elems=elems, line=t.line)
        raise ParseError(
            f"{self.filename}:{t.line}:{t.col}: unexpected token {t!r}"
        )

    def _parse_list_item(self):
        """Element inside ``[...]`` / ``{...}`` / tuple — accepts a
        plain expression or ``*expr`` unpacking (PEP 448)."""
        t = self._peek()
        if t.kind == TK_OP and t.text == "*" and self._peek(1).text != "*":
            self._advance()
            val = self._parse_expr()
            return _Starred(value=val, is_kw=False, line=t.line)
        return self._parse_expr()

    def _parse_comp_for(self) -> list:
        """Parse one or more ``for <target> in <iter> [if <cond>]`` clauses."""
        gens: list = []
        while (
            self._peek().kind == TK_KEYWORD and self._peek().text == "for"
        ):
            self._advance()
            target = self._parse_for_target()
            self._expect(TK_KEYWORD, "in")
            # Disjunction rather than full expr to avoid swallowing
            # the trailing ``if``/``for``.
            it = self._parse_or()
            ifs: list = []
            while (
                self._peek().kind == TK_KEYWORD
                and self._peek().text == "if"
            ):
                self._advance()
                ifs.append(self._parse_or())
            gens.append((target, it, ifs))
        return gens


def parse(src: str, filename: str = "<input>") -> _Module:
    return Parser(src, filename).parse_module()
