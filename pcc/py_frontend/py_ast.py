"""pcc_py AST node definitions (frozen v0.1).

See Section 2 of docs/plans/python-frontend-interfaces.md for the
authoritative contract. These dataclasses are ``frozen=True`` — no
mutation after construction. Type inference runs as a separate pass
that constructs fresh nodes.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass(frozen=True)
class SourceSpan:
    """Line/column range for diagnostics."""
    file: str
    line: int
    col: int
    end_line: int
    end_col: int


# -- Types -------------------------------------------------------------------

@dataclass(frozen=True)
class Type:
    """Base; every type has a name."""
    name: str


@dataclass(frozen=True)
class IntType(Type):    # name = "int"
    width: int = 64     # tagged default; 32 for explicit i32, etc.
    signed: bool = True


@dataclass(frozen=True)
class FloatType(Type):  # name = "float"
    width: int = 64


@dataclass(frozen=True)
class BoolType(Type):   pass   # name = "bool"


@dataclass(frozen=True)
class NoneType(Type):   pass   # name = "None"


@dataclass(frozen=True)
class StrType(Type):    pass   # name = "str"


@dataclass(frozen=True)
class ListType(Type):
    elem: Type


@dataclass(frozen=True)
class DictType(Type):
    key: Type
    value: Type


@dataclass(frozen=True)
class TupleType(Type):
    elems: tuple[Type, ...]


@dataclass(frozen=True)
class FuncType(Type):
    params: tuple[Type, ...]
    ret: Type


@dataclass(frozen=True)
class ClassType(Type):
    module: str
    fields: tuple[tuple[str, Type], ...] = ()
    bases: tuple["ClassType", ...] = ()


@dataclass(frozen=True)
class DynType(Type):    pass   # name = "dyn"; fallback when untyped


# -- Expressions -------------------------------------------------------------

@dataclass(frozen=True)
class Expr:
    span: SourceSpan
    ty: Type


@dataclass(frozen=True)
class IntLit(Expr):      value: int


@dataclass(frozen=True)
class FloatLit(Expr):    value: float


@dataclass(frozen=True)
class BoolLit(Expr):     value: bool


@dataclass(frozen=True)
class NoneLit(Expr):     pass


@dataclass(frozen=True)
class StrLit(Expr):      value: str


@dataclass(frozen=True)
class Name(Expr):        ident: str


@dataclass(frozen=True)
class BinOp(Expr):
    op: str              # "+", "-", "*", "/", "//", "%", "**",
                         # "&", "|", "^", "<<", ">>"
    lhs: Expr
    rhs: Expr


@dataclass(frozen=True)
class UnaryOp(Expr):
    op: str              # "-", "+", "~", "not"
    operand: Expr


@dataclass(frozen=True)
class Compare(Expr):
    op: str              # "==", "!=", "<", "<=", ">", ">=", "is", "is not", "in", "not in"
    lhs: Expr
    rhs: Expr


@dataclass(frozen=True)
class BoolExpr(Expr):
    op: str              # "and", "or"
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Call(Expr):
    func: Expr
    args: tuple[Expr, ...]
    kwargs: tuple[tuple[str, Expr], ...] = ()


@dataclass(frozen=True)
class Attr(Expr):
    obj: Expr
    name: str


@dataclass(frozen=True)
class Subscript(Expr):
    obj: Expr
    idx: Expr


@dataclass(frozen=True)
class Slice(Expr):
    lo: Optional[Expr]
    hi: Optional[Expr]
    step: Optional[Expr]


@dataclass(frozen=True)
class ListExpr(Expr):    elems: tuple[Expr, ...]


@dataclass(frozen=True)
class DictExpr(Expr):    pairs: tuple[tuple[Expr, Expr], ...]


@dataclass(frozen=True)
class TupleExpr(Expr):   elems: tuple[Expr, ...]


@dataclass(frozen=True)
class IfExpr(Expr):
    cond: Expr
    then_e: Expr
    else_e: Expr


@dataclass(frozen=True)
class Lambda(Expr):
    params: tuple["Arg", ...]
    body: Expr


# -- Statements --------------------------------------------------------------

@dataclass(frozen=True)
class Stmt:
    span: SourceSpan


@dataclass(frozen=True)
class Assign(Stmt):
    targets: tuple[Expr, ...]   # Name/Attr/Subscript
    value: Expr
    annotation: Optional[Type] = None


@dataclass(frozen=True)
class AugAssign(Stmt):
    target: Expr
    op: str                      # "+=", etc.
    value: Expr


@dataclass(frozen=True)
class ExprStmt(Stmt):
    expr: Expr


@dataclass(frozen=True)
class If(Stmt):
    cond: Expr
    body: tuple[Stmt, ...]
    else_body: tuple[Stmt, ...] = ()


@dataclass(frozen=True)
class While(Stmt):
    cond: Expr
    body: tuple[Stmt, ...]
    else_body: tuple[Stmt, ...] = ()


@dataclass(frozen=True)
class For(Stmt):
    target: Expr
    iter: Expr
    body: tuple[Stmt, ...]
    else_body: tuple[Stmt, ...] = ()


@dataclass(frozen=True)
class Return(Stmt):
    value: Optional[Expr]


@dataclass(frozen=True)
class Pass(Stmt):    pass


@dataclass(frozen=True)
class Break(Stmt):   pass


@dataclass(frozen=True)
class Continue(Stmt): pass


@dataclass(frozen=True)
class Raise(Stmt):
    exc: Optional[Expr]
    cause: Optional[Expr]


@dataclass(frozen=True)
class Try(Stmt):
    body: tuple[Stmt, ...]
    handlers: tuple["ExceptHandler", ...]
    else_body: tuple[Stmt, ...]
    finally_body: tuple[Stmt, ...]


@dataclass(frozen=True)
class ExceptHandler:
    exc_type: Optional[Expr]
    name: Optional[str]
    body: tuple[Stmt, ...]
    span: SourceSpan


@dataclass(frozen=True)
class With(Stmt):
    items: tuple[tuple[Expr, Optional[Expr]], ...]  # (ctx, as_var?)
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class Import(Stmt):
    names: tuple[tuple[str, Optional[str]], ...]  # (module, asname?)


@dataclass(frozen=True)
class ImportFrom(Stmt):
    module: str
    names: tuple[tuple[str, Optional[str]], ...]
    level: int = 0


@dataclass(frozen=True)
class Global(Stmt):
    names: tuple[str, ...]


@dataclass(frozen=True)
class Nonlocal(Stmt):
    names: tuple[str, ...]


@dataclass(frozen=True)
class Delete(Stmt):
    targets: tuple[Expr, ...]


# -- Top-level & declarations -----------------------------------------------

@dataclass(frozen=True)
class Arg:
    name: str
    annotation: Optional[Type]
    default: Optional[Expr]
    kind: str  # "pos", "kw_only", "pos_only", "*args", "**kwargs"
    has_default: bool = False


@dataclass(frozen=True)
class FuncDef(Stmt):
    name: str
    args: tuple[Arg, ...]
    return_ty: Optional[Type]
    body: tuple[Stmt, ...]
    decorators: tuple[Expr, ...] = ()
    is_method: bool = False
    is_async: bool = False


@dataclass(frozen=True)
class ClassDef(Stmt):
    name: str
    bases: tuple[Expr, ...]
    keywords: tuple[tuple[str, Expr], ...]   # for metaclass=
    body: tuple[Stmt, ...]
    decorators: tuple[Expr, ...] = ()


@dataclass(frozen=True)
class Module:
    name: str
    body: tuple[Stmt, ...]
    docstring: Optional[str] = None
