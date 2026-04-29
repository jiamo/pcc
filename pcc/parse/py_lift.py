"""P6C.3 — lift the native parser's ``_*`` nodes into ``py_ast``.

The native parser (:mod:`pcc.parse.py_parse`) yields a small set of
dataclasses (``_Module``, ``_FuncDef``, ``_Call`` etc.). The rest of
the Python frontend consumes :mod:`pcc.py_frontend.py_ast` nodes. This
module bridges the two.

All lifted :class:`py_ast.Expr` nodes default their ``ty`` field to
:class:`py_ast.DynType` — the type-inference pass fills in concrete
types later.

Coverage:
  - All statement nodes emitted by the parser.
  - Expression nodes: literals, names, binary / unary / compare /
    bool-op, call, attr, subscript (incl. slice), list / tuple / dict
    literals, ternary, lambda.
  - Best-effort for f-strings and comprehensions: f-strings lower to
    a string-concat call, comprehensions to a synthetic call to
    ``_list_comp`` / ``_dict_comp`` / ``_set_comp`` / ``_gen_comp`` —
    a shim the lowering stage rewrites to an explicit loop.
"""
from __future__ import annotations

from typing import Optional

from ..py_frontend import py_ast as pa
from . import py_parse as pp


_DYN = pa.DynType(name="dyn")


class LiftError(Exception):
    """Raised when a parser node has no py_ast equivalent yet."""


def _pow10f_lift(exp: int) -> float:
    out = float(int("1", 10))
    ten = float(int("10", 10))
    i = 0
    while i < exp:
        out = out * ten
        i += 1
    return out


def _parse_float_literal_lift(text: str) -> float:
    exp = 0
    mantissa = text
    lower = text.lower()
    e_idx = lower.find("e")
    if e_idx >= 0:
        mantissa = text[:e_idx]
        exp = int(text[e_idx + 1:], 10)
    dot_idx = mantissa.find(".")
    frac_len = 0
    digits = mantissa
    if dot_idx >= 0:
        frac_len = len(mantissa) - dot_idx - 1
        digits = mantissa[:dot_idx] + mantissa[dot_idx + 1:]
    if not digits:
        digits = "0"
    value = float(int(digits, 10))
    if frac_len > 0:
        value = value / _pow10f_lift(frac_len)
    if exp > 0:
        value = value * _pow10f_lift(exp)
    elif exp < 0:
        value = value / _pow10f_lift(-exp)
    return value


def lift_module(mod: pp._Module, filename: str, module_name: str) -> pa.Module:
    L = _Lifter(filename)
    body = tuple(L.lift_stmt(s) for s in mod.body)
    # Flatten any lists (chained-assignment fanout etc.).
    flat: list[pa.Stmt] = []
    for s in body:
        if isinstance(s, list):
            flat.extend(s)
        else:
            flat.append(s)
    docstring: Optional[str] = None
    if flat and isinstance(flat[0], pa.ExprStmt) and isinstance(flat[0].expr, pa.StrLit):
        docstring = flat[0].expr.value
    return pa.Module(name=module_name, body=tuple(flat), docstring=docstring)


class _Lifter:
    def __init__(self, filename: str) -> None:
        self.filename = filename

    # ------------------------------------------------------------------ spans

    def _span(self, line: int) -> pa.SourceSpan:
        return pa.SourceSpan(
            file=self.filename, line=line, col=1,
            end_line=line, end_col=1,
        )

    # ---------------------------------------------------------------- statements

    def lift_stmt(self, s) -> pa.Stmt:
        # Explicit dispatch — matches on concrete class identity. Beats
        # getattr(self, f"_s_{type(s).__name__[1:]}") for self-host:
        # scripts/audit_selfhost.py flags dynamic-attr patterns.
        t = type(s)
        if t is pp._Pass:
            return self._s_Pass(s)
        if t is pp._Break:
            return self._s_Break(s)
        if t is pp._Continue:
            return self._s_Continue(s)
        if t is pp._Return:
            return self._s_Return(s)
        if t is pp._Expr:
            return self._s_Expr(s)
        if t is pp._Assign:
            return self._s_Assign(s)
        if t is pp._AugAssign:
            return self._s_AugAssign(s)
        if t is pp._If:
            return self._s_If(s)
        if t is pp._While:
            return self._s_While(s)
        if t is pp._For:
            return self._s_For(s)
        if t is pp._FuncDef:
            return self._s_FuncDef(s)
        if t is pp._ClassDef:
            return self._s_ClassDef(s)
        if t is pp._Import:
            return self._s_Import(s)
        if t is pp._ImportFrom:
            return self._s_ImportFrom(s)
        if t is pp._Raise:
            return self._s_Raise(s)
        if t is pp._Try:
            return self._s_Try(s)
        if t is pp._With:
            return self._s_With(s)
        if t is pp._Global:
            return self._s_Global(s)
        if t is pp._Nonlocal:
            return self._s_Nonlocal(s)
        if t is pp._Del:
            return self._s_Del(s)
        if t is pp._Assert:
            return self._s_Assert(s)
        raise LiftError(f"no stmt lifter for {t.__name__}")

    def _s_Pass(self, s: pp._Pass) -> pa.Pass:
        return pa.Pass(span=self._span(s.line))

    def _s_Break(self, s: pp._Break) -> pa.Break:
        return pa.Break(span=self._span(s.line))

    def _s_Continue(self, s: pp._Continue) -> pa.Continue:
        return pa.Continue(span=self._span(s.line))

    def _s_Return(self, s: pp._Return) -> pa.Return:
        val = self.lift_expr(s.value) if s.value is not None else None
        return pa.Return(span=self._span(s.line), value=val)

    def _s_Expr(self, s: pp._Expr) -> pa.ExprStmt:
        return pa.ExprStmt(span=self._span(s.line), expr=self.lift_expr(s.expr))

    def _s_Assign(self, s: pp._Assign) -> pa.Stmt:
        # ``annotation`` field holds either:
        #   - an AST node (``x: int = 1`` — original annotation)
        #   - ``"walrus"`` (inline ``:=``)
        #   - ``None`` (plain assign)
        target = self.lift_expr(s.target)
        value = self.lift_expr(s.value)
        ann_type: Optional[pa.Type] = None
        if s.annotation is not None and s.annotation != "walrus":
            ann_type = _lift_type(s.annotation)
        return pa.Assign(
            span=self._span(s.line), targets=(target,), value=value,
            annotation=ann_type,
        )

    def _s_AugAssign(self, s: pp._AugAssign) -> pa.AugAssign:
        return pa.AugAssign(
            span=self._span(s.line), target=self.lift_expr(s.target),
            op=s.op, value=self.lift_expr(s.value),
        )

    def _s_If(self, s: pp._If) -> pa.If:
        return pa.If(
            span=self._span(s.line), cond=self.lift_expr(s.cond),
            body=tuple(self.lift_stmt(b) for b in s.body),
            else_body=tuple(self.lift_stmt(b) for b in s.else_body),
        )

    def _s_While(self, s: pp._While) -> pa.While:
        return pa.While(
            span=self._span(s.line), cond=self.lift_expr(s.cond),
            body=tuple(self.lift_stmt(b) for b in s.body),
            else_body=tuple(self.lift_stmt(b) for b in s.else_body),
        )

    def _s_For(self, s: pp._For) -> pa.For:
        return pa.For(
            span=self._span(s.line), target=self.lift_expr(s.target),
            iter=self.lift_expr(s.iter),
            body=tuple(self.lift_stmt(b) for b in s.body),
            else_body=tuple(self.lift_stmt(b) for b in s.else_body),
        )

    def _s_FuncDef(self, s: pp._FuncDef) -> pa.FuncDef:
        args = tuple(self._lift_arg(p) for p in s.params)
        decos = tuple(self.lift_expr(d) for d in (s.decorators or ()))
        ret_ty = _lift_type(s.returns) if s.returns is not None else None
        return pa.FuncDef(
            span=self._span(s.line), name=s.name, args=args,
            return_ty=ret_ty,
            body=tuple(self.lift_stmt(b) for b in s.body),
            decorators=decos,
        )

    def _lift_arg(self, param) -> pa.Arg:
        if len(param) == 2:
            kind, name = param
            ann = default = None
        else:
            kind, name, ann, default = param
        kmap = {
            "pos": "pos",
            "*args": "*args",
            "**kwargs": "**kwargs",
            "pos-only-sep": "pos_only",
            "kwonly-sep": "kw_only",
        }
        default_expr = self.lift_expr(default) if default is not None else None
        return pa.Arg(
            name=name,
            annotation=_lift_type(ann) if ann is not None else None,
            default=default_expr,
            kind=kmap.get(kind, "pos"),
            has_default=default is not None,
        )

    def _s_ClassDef(self, s: pp._ClassDef) -> pa.ClassDef:
        bases: list[pa.Expr] = []
        keywords: list[tuple[str, pa.Expr]] = []
        for b in s.bases:
            if isinstance(b, pp._Assign) and b.annotation is None:
                # ``name=value`` keyword — we encoded kwargs as _Assign.
                keywords.append(
                    (b.target.ident if isinstance(b.target, pp._Name) else "",
                      self.lift_expr(b.value))
                )
            else:
                bases.append(self.lift_expr(b))
        decos = tuple(self.lift_expr(d) for d in s.decorators)
        return pa.ClassDef(
            span=self._span(s.line), name=s.name, bases=tuple(bases),
            keywords=tuple(keywords),
            body=tuple(self.lift_stmt(b) for b in s.body),
            decorators=decos,
        )

    def _s_Import(self, s: pp._Import) -> pa.Import:
        names = tuple((m, a) for (m, a) in s.names)
        return pa.Import(span=self._span(s.line), names=names)

    def _s_ImportFrom(self, s: pp._ImportFrom) -> pa.ImportFrom:
        names = tuple((m, a) for (m, a) in s.names)
        return pa.ImportFrom(
            span=self._span(s.line), module=s.module, names=names,
            level=s.level,
        )

    def _s_Raise(self, s: pp._Raise) -> pa.Raise:
        exc = self.lift_expr(s.exc) if s.exc is not None else None
        cause = self.lift_expr(s.cause) if s.cause is not None else None
        return pa.Raise(span=self._span(s.line), exc=exc, cause=cause)

    def _s_Try(self, s: pp._Try) -> pa.Try:
        handlers = []
        for (exc_ty, name, body) in s.handlers:
            lifted_ty = self.lift_expr(exc_ty) if exc_ty is not None else None
            handlers.append(pa.ExceptHandler(
                exc_type=lifted_ty, name=name,
                body=tuple(self.lift_stmt(b) for b in body),
                span=self._span(s.line),
            ))
        return pa.Try(
            span=self._span(s.line),
            body=tuple(self.lift_stmt(b) for b in s.body),
            handlers=tuple(handlers),
            else_body=tuple(self.lift_stmt(b) for b in s.else_body),
            finally_body=tuple(self.lift_stmt(b) for b in s.finally_body),
        )

    def _s_With(self, s: pp._With) -> pa.With:
        items = tuple(
            (self.lift_expr(ctx),
              pa.Name(span=self._span(s.line), ty=_DYN, ident=name) if name else None)
            for (ctx, name) in s.items
        )
        return pa.With(
            span=self._span(s.line), items=items,
            body=tuple(self.lift_stmt(b) for b in s.body),
        )

    def _s_Global(self, s: pp._Global) -> pa.Global:
        return pa.Global(span=self._span(s.line), names=tuple(s.names))

    def _s_Nonlocal(self, s: pp._Nonlocal) -> pa.Nonlocal:
        return pa.Nonlocal(span=self._span(s.line), names=tuple(s.names))

    def _s_Del(self, s: pp._Del) -> pa.Delete:
        return pa.Delete(
            span=self._span(s.line),
            targets=tuple(self.lift_expr(t) for t in s.targets),
        )

    def _s_Assert(self, s: pp._Assert):
        # pcc AST has no Assert — desugar to ``if not test: raise AssertionError(msg)``.
        span = self._span(s.line)
        not_test = pa.UnaryOp(
            span=span, ty=_DYN, op="not", operand=self.lift_expr(s.test),
        )
        msg = self.lift_expr(s.msg) if s.msg is not None else None
        args = (msg,) if msg is not None else ()
        exc = pa.Call(
            span=span, ty=_DYN,
            func=pa.Name(span=span, ty=_DYN, ident="AssertionError"),
            args=args,
        )
        raise_stmt = pa.Raise(span=span, exc=exc, cause=None)
        return pa.If(span=span, cond=not_test, body=(raise_stmt,), else_body=())

    # ---------------------------------------------------------------- expressions

    def lift_expr(self, e) -> pa.Expr:
        t = type(e)
        if t is pp._Num:
            return self._e_Num(e)
        if t is pp._Str:
            return self._e_Str(e)
        if t is pp._FString:
            return self._e_FString(e)
        if t is pp._Bool:
            return self._e_Bool(e)
        if t is pp._None:
            return self._e_None(e)
        if t is pp._Name:
            return self._e_Name(e)
        if t is pp._BinOp:
            return self._e_BinOp(e)
        if t is pp._UnaryOp:
            return self._e_UnaryOp(e)
        if t is pp._Compare:
            return self._e_Compare(e)
        if t is pp._BoolOp:
            return self._e_BoolOp(e)
        if t is pp._Call:
            return self._e_Call(e)
        if t is pp._Attr:
            return self._e_Attr(e)
        if t is pp._Subscript:
            return self._e_Subscript(e)
        if t is pp._List:
            return self._e_List(e)
        if t is pp._Tuple:
            return self._e_Tuple(e)
        if t is pp._Dict:
            return self._e_Dict(e)
        if t is pp._Set:
            return self._e_Set(e)
        if t is pp._Ternary:
            return self._e_Ternary(e)
        if t is pp._Lambda:
            return self._e_Lambda(e)
        if t is pp._Comp:
            return self._e_Comp(e)
        if t is pp._Yield:
            return self._e_Yield(e)
        if t is pp._Starred:
            return self._e_Starred(e)
        if t is pp._Assign:
            return self._e_Assign(e)
        raise LiftError(f"no expr lifter for {t.__name__}")

    def _e_Num(self, e: pp._Num) -> pa.Expr:
        span = self._span(e.line)
        if e.is_int:
            return pa.IntLit(
                span=span,
                ty=pa.IntType(name="int", width=64, signed=True),
                value=int(e.text, 0),
            )
        return pa.FloatLit(
            span=span,
            ty=pa.FloatType(name="float", width=64),
            value=_parse_float_literal_lift(e.text),
        )

    def _e_Str(self, e: pp._Str) -> pa.StrLit:
        cooked: list[str] = []
        for raw_text, is_raw in e.parts:
            cooked.append(raw_text if is_raw else _decode_escapes(raw_text))
        return pa.StrLit(
            span=self._span(e.line), ty=pa.StrType(name="str"),
            value="".join(cooked),
        )

    def _e_FString(self, e: pp._FString) -> pa.Expr:
        span = self._span(e.line)
        pieces: list[pa.Expr] = []
        for part in e.parts:
            if type(part) is pp._FStringText:
                text = part.text if part.is_raw else _decode_escapes(part.text)
                if text:
                    pieces.append(pa.StrLit(
                        span=span, ty=pa.StrType(name="str"), value=text,
                    ))
                continue
            inner = self.lift_expr(part)
            pieces.append(pa.Call(
                span=span, ty=pa.StrType(name="str"),
                func=pa.Name(span=span, ty=_DYN, ident="str"),
                args=(inner,), kwargs=(),
            ))
        if not pieces:
            return pa.StrLit(span=span, ty=pa.StrType(name="str"), value="")
        out = pieces[0]
        i = 1
        while i < len(pieces):
            out = pa.BinOp(
                span=span, ty=pa.StrType(name="str"),
                op="+", lhs=out, rhs=pieces[i],
            )
            i += 1
        return out

    def _e_Bool(self, e: pp._Bool) -> pa.BoolLit:
        return pa.BoolLit(
            span=self._span(e.line), ty=pa.BoolType(name="bool"), value=e.value,
        )

    def _e_None(self, e: pp._None) -> pa.NoneLit:
        return pa.NoneLit(span=self._span(e.line), ty=pa.NoneType(name="None"))

    def _e_Name(self, e: pp._Name) -> pa.Name:
        return pa.Name(span=self._span(e.line), ty=_DYN, ident=e.ident)

    def _e_BinOp(self, e: pp._BinOp) -> pa.BinOp:
        return pa.BinOp(
            span=self._span(e.line), ty=_DYN, op=e.op,
            lhs=self.lift_expr(e.lhs), rhs=self.lift_expr(e.rhs),
        )

    def _e_UnaryOp(self, e: pp._UnaryOp) -> pa.UnaryOp:
        return pa.UnaryOp(
            span=self._span(e.line), ty=_DYN, op=e.op,
            operand=self.lift_expr(e.operand),
        )

    def _e_Compare(self, e: pp._Compare) -> pa.Compare:
        return pa.Compare(
            span=self._span(e.line), ty=_DYN, op=e.op,
            lhs=self.lift_expr(e.lhs), rhs=self.lift_expr(e.rhs),
        )

    def _e_BoolOp(self, e: pp._BoolOp) -> pa.BoolExpr:
        # pcc AST models BoolExpr as binary — fold left for 3+ operands.
        vals = [self.lift_expr(v) for v in e.values]
        span = self._span(e.line)
        node = vals[0]
        for rhs in vals[1:]:
            node = pa.BoolExpr(
                span=span, ty=_DYN, op=e.op, left=node, right=rhs,
            )
        return node

    def _e_Call(self, e: pp._Call) -> pa.Call:
        args: list[pa.Expr] = []
        kwargs: list[tuple[str, pa.Expr]] = []
        for a in e.args:
            if isinstance(a, pp._Assign) and a.annotation is None:
                # ``name=value`` kwarg encoded as _Assign.
                name = a.target.ident if isinstance(a.target, pp._Name) else ""
                kwargs.append((name, self.lift_expr(a.value)))
            elif isinstance(a, pp._Starred):
                # Starred passes through as a Name("*<arg>") sentinel.
                inner = self.lift_expr(a.value)
                args.append(pa.Call(
                    span=self._span(e.line), ty=_DYN,
                    func=pa.Name(span=self._span(e.line), ty=_DYN,
                                  ident="*" if not a.is_kw else "**"),
                    args=(inner,),
                ))
            elif isinstance(a, pp._Comp):
                args.append(self._e_Comp(a))
            else:
                args.append(self.lift_expr(a))
        return pa.Call(
            span=self._span(e.line), ty=_DYN, func=self.lift_expr(e.func),
            args=tuple(args), kwargs=tuple(kwargs),
        )

    def _e_Attr(self, e: pp._Attr) -> pa.Attr:
        return pa.Attr(
            span=self._span(e.line), ty=_DYN,
            obj=self.lift_expr(e.obj), name=e.name,
        )

    def _e_Subscript(self, e: pp._Subscript) -> pa.Subscript:
        idx = self.lift_expr(e.idx)
        # Detect our slice sentinel.
        if (
            isinstance(idx, pa.Call)
            and isinstance(idx.func, pa.Name)
            and idx.func.ident == "_slice"
        ):
            lo, hi, step = idx.args
            lo = None if isinstance(lo, pa.NoneLit) else lo
            hi = None if isinstance(hi, pa.NoneLit) else hi
            step = None if isinstance(step, pa.NoneLit) else step
            idx = pa.Slice(
                span=self._span(e.line), ty=_DYN, lo=lo, hi=hi, step=step,
            )
        return pa.Subscript(
            span=self._span(e.line), ty=_DYN,
            obj=self.lift_expr(e.obj), idx=idx,
        )

    def _e_List(self, e: pp._List) -> pa.ListExpr:
        elems = tuple(self.lift_expr(x) for x in e.elems)
        return pa.ListExpr(span=self._span(e.line), ty=_DYN, elems=elems)

    def _e_Tuple(self, e: pp._Tuple) -> pa.TupleExpr:
        elems = tuple(self.lift_expr(x) for x in e.elems)
        return pa.TupleExpr(span=self._span(e.line), ty=_DYN, elems=elems)

    def _e_Dict(self, e: pp._Dict) -> pa.DictExpr:
        pairs = []
        for k, v in zip(e.keys, e.values):
            if k is None:
                # ``**mapping`` — encode as (Name("**"), mapping).
                pairs.append((
                    pa.Name(span=self._span(e.line), ty=_DYN, ident="**"),
                    self.lift_expr(v),
                ))
            else:
                pairs.append((self.lift_expr(k), self.lift_expr(v)))
        return pa.DictExpr(
            span=self._span(e.line), ty=_DYN, pairs=tuple(pairs),
        )

    def _e_Set(self, e: pp._Set) -> pa.Call:
        # py_ast has no SetExpr — lower to set([...]) so the downstream
        # pipeline sees a recognizable call.
        args = tuple(self.lift_expr(x) for x in e.elems)
        span = self._span(e.line)
        list_literal = pa.ListExpr(span=span, ty=_DYN, elems=args)
        return pa.Call(
            span=span, ty=_DYN,
            func=pa.Name(span=span, ty=_DYN, ident="set"),
            args=(list_literal,),
        )

    def _e_Ternary(self, e: pp._Ternary) -> pa.IfExpr:
        return pa.IfExpr(
            span=self._span(e.line), ty=_DYN,
            cond=self.lift_expr(e.cond),
            then_e=self.lift_expr(e.then_expr),
            else_e=self.lift_expr(e.else_expr),
        )

    def _e_Lambda(self, e: pp._Lambda) -> pa.Lambda:
        args = tuple(self._lift_arg(p) for p in e.params)
        return pa.Lambda(
            span=self._span(e.line), ty=_DYN, params=args,
            body=self.lift_expr(e.body),
        )

    def _e_Comp(self, e: pp._Comp) -> pa.Call:
        # No first-class comprehension node — lower to a sentinel Call
        # so the lowering stage can rewrite to an explicit loop.
        span = self._span(e.line)
        sym = {"list": "_list_comp", "dict": "_dict_comp",
               "set": "_set_comp", "gen": "_gen_comp"}[e.kind]
        if e.kind == "dict":
            k, v = e.elt
            elt = pa.TupleExpr(
                span=span, ty=_DYN,
                elems=(self.lift_expr(k), self.lift_expr(v)),
            )
        else:
            elt = self.lift_expr(e.elt)
        # Flatten generators into a tuple of (target, iter, (ifs,)) calls.
        gens = []
        for (target, it, ifs) in e.generators:
            gen_call = pa.Call(
                span=span, ty=_DYN,
                func=pa.Name(span=span, ty=_DYN, ident="_gen_clause"),
                args=(
                    self.lift_expr(target), self.lift_expr(it),
                    pa.TupleExpr(
                        span=span, ty=_DYN,
                        elems=tuple(self.lift_expr(i) for i in ifs),
                    ),
                ),
            )
            gens.append(gen_call)
        return pa.Call(
            span=span, ty=_DYN,
            func=pa.Name(span=span, ty=_DYN, ident=sym),
            args=(elt, *gens),
        )

    def _e_Yield(self, e: pp._Yield) -> pa.Call:
        # No yield node — sentinel call.
        span = self._span(e.line)
        sym = "_yield_from" if e.is_from else "_yield"
        args = (self.lift_expr(e.value),) if e.value is not None else ()
        return pa.Call(
            span=span, ty=_DYN,
            func=pa.Name(span=span, ty=_DYN, ident=sym),
            args=args,
        )

    def _e_Starred(self, e: pp._Starred) -> pa.Call:
        span = self._span(e.line)
        return pa.Call(
            span=span, ty=_DYN,
            func=pa.Name(span=span, ty=_DYN, ident="**" if e.is_kw else "*"),
            args=(self.lift_expr(e.value),),
        )

    def _e_Assign(self, e: pp._Assign) -> pa.Expr:
        # Walrus / inline assignment in expression position.
        span = self._span(e.line)
        return pa.Call(
            span=span, ty=_DYN,
            func=pa.Name(span=span, ty=_DYN, ident="_walrus"),
            args=(self.lift_expr(e.target), self.lift_expr(e.value)),
        )


# Map a lifted type-expression AST node to a pcc ``Type``. Best effort;
# unknown shapes fall back to ``DynType``.
_TYPE_NAME_MAP = {
    "int": pa.IntType(name="int", width=64, signed=True),
    "i8": pa.IntType(name="int", width=8),
    "i16": pa.IntType(name="int", width=16),
    "i32": pa.IntType(name="int", width=32),
    "i64": pa.IntType(name="int", width=64),
    "float": pa.FloatType(name="float", width=64),
    "bool": pa.BoolType(name="bool"),
    "str": pa.StrType(name="str"),
    "None": pa.NoneType(name="None"),
    "object": _DYN,
    "Any": _DYN,
    "set": _DYN,
    "frozenset": _DYN,
}


def _class_type(name: str) -> pa.ClassType:
    return pa.ClassType(name=name, module="", fields=(), bases=())


def _lift_type(node) -> pa.Type:
    if node is None:
        return _DYN
    if isinstance(node, pp._Name):
        ty = _TYPE_NAME_MAP.get(node.ident)
        if ty is not None:
            return ty
        return _class_type(node.ident)
    if isinstance(node, pp._None):
        return pa.NoneType(name="None")
    if isinstance(node, pp._Attr):
        # e.g. ``pcc.IntType`` — resolve the tail token only.
        ty = _TYPE_NAME_MAP.get(node.name)
        if ty is not None:
            return ty
        return _DYN
    if isinstance(node, pp._Subscript):
        base = node.obj
        if isinstance(base, pp._Name):
            if base.ident == "list":
                return pa.ListType(name="list", elem=_lift_type(node.idx))
            if base.ident == "tuple":
                inner = node.idx
                # ``tuple[T, ...]`` — homogeneous variadic tuple.
                # Phase-1 models this as a ``TupleType`` with an
                # empty ``elems`` and a separate marker so type
                # comparisons treat it as compatible with any
                # TupleType whose elements all subtype ``T``. To
                # stay within the existing dataclass, encode the
                # element type as a length-1 ``elems`` tuple and
                # tag the result via a side channel; the checker
                # consults it via ``_is_variadic_tuple``.
                if (
                    isinstance(inner, pp._Tuple)
                    and len(inner.elems) == 2
                    and isinstance(inner.elems[1], pp._Name)
                    and inner.elems[1].ident == "Ellipsis"
                ):
                    return pa.TupleType(
                        name="tuple_variadic",
                        elems=(_lift_type(inner.elems[0]),),
                    )
                if isinstance(inner, pp._Tuple):
                    return pa.TupleType(
                        name="tuple",
                        elems=tuple(_lift_type(x) for x in inner.elems),
                    )
                return pa.TupleType(name="tuple", elems=(_lift_type(inner),))
            if base.ident == "dict":
                inner = node.idx
                if isinstance(inner, pp._Tuple) and len(inner.elems) == 2:
                    return pa.DictType(
                        name="dict",
                        key=_lift_type(inner.elems[0]),
                        value=_lift_type(inner.elems[1]),
                    )
            if base.ident in ("set", "frozenset"):
                return pa.DynType(name="set")
        return _DYN
    if isinstance(node, pp._BinOp) and node.op == "|":
        # PEP 604 union — pcc has no Union type, fall back to Dyn.
        return _DYN
    return _DYN


# Python-style escape processing. We only handle the escapes the parser
# tokens might contain — ``\n``, ``\t``, ``\\``, ``\'``, ``\"``, ``\xNN``,
# ``\uNNNN``. The parser strips quotes; escapes remain literal.
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
             "'": "'", '"': '"', "0": "\0", "a": "\a", "b": "\b",
             "f": "\f", "v": "\v"}


def _decode_escapes(raw: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c != "\\" or i + 1 >= len(raw):
            out.append(c)
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
            continue
        if nxt == "x" and i + 3 < len(raw):
            out.append(chr(int(raw[i + 2:i + 4], 16)))
            i += 4
            continue
        if nxt == "u" and i + 5 < len(raw):
            out.append(chr(int(raw[i + 2:i + 6], 16)))
            i += 6
            continue
        # Unknown escape: keep literal (Python does the same with a warning).
        out.append(c)
        i += 1
    return "".join(out)


def parse_and_lift(src: str, filename: str, module_name: str) -> pa.Module:
    """Parse ``src`` with the native parser, lift to py_ast.Module."""
    mod = pp.parse(src, filename=filename)
    return lift_module(mod, filename, module_name)
