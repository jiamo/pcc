"""Type hierarchy utilities for the pcc Python frontend.

Utility helpers over the ``Type`` dataclass hierarchy defined in
``pcc.py_frontend.py_ast``.  This module intentionally does *not* redefine
the ``Type`` dataclasses — it only imports them and provides helpers such
as annotation parsing, equality, numeric promotion, and a handful of
module-level singleton constants.

See ``docs/plans/python-frontend-interfaces.md`` section 2 for the frozen
AST/Type definitions and section 8 for the error-reporting convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .py_ast import (
    BoolType,
    ClassType,
    DictType,
    DynType,
    Expr,
    FloatType,
    FuncType,
    IntType,
    ListType,
    ListExpr,
    Name,
    NoneLit,
    NoneType,
    SourceSpan,
    StrLit,
    StrType,
    Subscript,
    TupleExpr,
    TupleType,
    Type,
)

# ---------------------------------------------------------------------------
# Module-level singleton type constants.
#
# These are the canonical "Phase 1 default" instances of each primitive
# type.  Equality is structural (dataclass __eq__), so comparing against
# these constants is safe even if other parts of the compiler construct
# their own instances with the same fields.
# ---------------------------------------------------------------------------

TYPE_INT: IntType = IntType(name="int", width=64, signed=True)
TYPE_FLOAT: FloatType = FloatType(name="float", width=64)
TYPE_BOOL: BoolType = BoolType(name="bool")
TYPE_NONE: NoneType = NoneType(name="None")
TYPE_STR: StrType = StrType(name="str")
TYPE_DYN: DynType = DynType(name="dyn")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@dataclass
class PyFrontendError(Exception):
    """Base class for user-visible compile failures in the Python frontend.

    Matches the convention documented in
    ``docs/plans/python-frontend-interfaces.md`` section 8.
    """

    span: Optional[SourceSpan]
    message: str
    hint: Optional[str] = None

    def __post_init__(self) -> None:
        # Let ``Exception`` carry a printable message for convenience when
        # the error propagates untouched.
        super().__init__(self.format())

    def format(self) -> str:
        """Render a short diagnostic string.

        The fancier "source excerpt + caret" form lives in the driver; this
        method is sufficient for logging and tests.
        """
        if self.span is not None:
            loc = f"{self.span.file}:{self.span.line}:{self.span.col}"
        else:
            loc = "<unknown>"
        out = f"{loc}: error: {self.message}"
        if self.hint:
            out += f"\nhint: {self.hint}"
        return out


# ---------------------------------------------------------------------------
# Annotation parsing
# ---------------------------------------------------------------------------

# Map of plain Python builtin-name annotations to their Phase 1 types.
_BUILTIN_NAMED_TYPES: dict[str, Type] = {
    "int": TYPE_INT,
    "float": TYPE_FLOAT,
    "bool": TYPE_BOOL,
    "str": TYPE_STR,
    "None": TYPE_NONE,
    "NoneType": TYPE_NONE,
    "object": TYPE_DYN,
    "Any": TYPE_DYN,
    "set": DynType(name="set"),
    "frozenset": DynType(name="set"),
}


def _name_ident(expr: Expr) -> Optional[str]:
    """Return the identifier if ``expr`` is a bare ``Name``, else ``None``."""
    if isinstance(expr, Name):
        return expr.ident
    return None


def parse_annotation(expr: Expr) -> Type:
    """Convert a Python annotation AST node into a ``Type`` instance.

    Supports:

    * Bare names such as ``int``, ``float``, ``bool``, ``str``, ``None``.
    * Subscripted generics ``list[T]``, ``dict[K, V]``, ``tuple[T1, T2, ...]``.
    * ``Optional[T]`` → treated as ``T`` for Phase 1 (nullability lives
      outside the type system until Phase 2).
    * Anything unrecognised falls back to ``DynType``.
    """

    # Bare name annotation: int, float, bool, str, None, MyClass, ...
    ident = _name_ident(expr)
    if ident is not None:
        if ident in _BUILTIN_NAMED_TYPES:
            return _BUILTIN_NAMED_TYPES[ident]
        # User-defined class name — we do not have the class body yet in
        # Phase 1, so record it as a ClassType with no fields/bases.  The
        # inference pass can refine later once class defs are collected.
        return ClassType(name=ident, module="", fields=(), bases=())

    if isinstance(expr, StrLit) and expr.value:
        if expr.value in _BUILTIN_NAMED_TYPES:
            return _BUILTIN_NAMED_TYPES[expr.value]
        return ClassType(name=expr.value, module="", fields=(), bases=())

    # A NoneLit used as an annotation (``-> None`` parses that way in some
    # frontends; robustness only).
    if isinstance(expr, NoneLit):
        return TYPE_NONE

    # Subscripted generic: list[int], dict[str, int], tuple[int, str], ...
    if isinstance(expr, Subscript):
        head = _name_ident(expr.obj)
        idx = expr.idx

        # Collect the index expressions (Subscript holds a single Expr; for
        # multi-arg generics the parser may lower this as a TupleExpr).
        if isinstance(idx, TupleExpr):
            idx_exprs = tuple(idx.elems)
        else:
            idx_exprs = (idx,)

        if head == "list" or head == "List":
            if len(idx_exprs) == 1:
                return ListType(name="list", elem=parse_annotation(idx_exprs[0]))
            return ListType(name="list", elem=TYPE_DYN)

        if head == "dict" or head == "Dict":
            if len(idx_exprs) == 2:
                return DictType(
                    name="dict",
                    key=parse_annotation(idx_exprs[0]),
                    value=parse_annotation(idx_exprs[1]),
                )
            return DictType(name="dict", key=TYPE_DYN, value=TYPE_DYN)

        if head == "tuple" or head == "Tuple":
            return TupleType(
                name="tuple",
                elems=tuple(parse_annotation(e) for e in idx_exprs),
            )

        if head == "Optional":
            if len(idx_exprs) == 1:
                # Phase 1: drop the optionality, keep the payload type.
                return parse_annotation(idx_exprs[0])
            return TYPE_DYN

        if head == "Callable":
            # Callable[[A, B], R] — best-effort.
            if len(idx_exprs) == 2:
                params_expr, ret_expr = idx_exprs
                if isinstance(params_expr, ListExpr):
                    params = tuple(
                        parse_annotation(p) for p in params_expr.elems
                    )
                else:
                    params = ()
                return FuncType(
                    name="callable",
                    params=params,
                    ret=parse_annotation(ret_expr),
                )
            return TYPE_DYN

        # Unknown generic — fall through to dynamic.
        return TYPE_DYN

    # Attribute annotations like ``typing.List[int]`` are not unwrapped
    # for Phase 1; treat as dynamic.
    return TYPE_DYN


# ---------------------------------------------------------------------------
# Type predicates and comparisons
# ---------------------------------------------------------------------------


def type_eq(a: Type, b: Type) -> bool:
    """Structural equality on ``Type`` instances.

    Keep this explicit instead of relying on dataclass ``__eq__``. The
    self-hosted runtime does not yet provide CPython-complete generated
    dunder equality for frozen dataclasses, and type inference must not
    depend on that dynamic path.
    """
    if a is b:
        return True
    primitive_name = (
        a.name == "int"
        or a.name == "float"
        or a.name == "bool"
        or a.name == "str"
        or a.name == "None"
        or a.name == "dyn"
    )
    if a.name == b.name and primitive_name:
        return True
    if isinstance(a, IntType) or isinstance(b, IntType):
        if not (isinstance(a, IntType) and isinstance(b, IntType)):
            return False
        return a.name == b.name
    if isinstance(a, FloatType) or isinstance(b, FloatType):
        if not (isinstance(a, FloatType) and isinstance(b, FloatType)):
            return False
        return a.name == b.name
    if isinstance(a, BoolType) or isinstance(b, BoolType):
        return (
            isinstance(a, BoolType)
            and isinstance(b, BoolType)
            and a.name == b.name
        )
    if isinstance(a, NoneType) or isinstance(b, NoneType):
        return (
            isinstance(a, NoneType)
            and isinstance(b, NoneType)
            and a.name == b.name
        )
    if isinstance(a, StrType) or isinstance(b, StrType):
        return (
            isinstance(a, StrType)
            and isinstance(b, StrType)
            and a.name == b.name
        )
    if isinstance(a, DynType) or isinstance(b, DynType):
        return (
            isinstance(a, DynType)
            and isinstance(b, DynType)
            and a.name == b.name
        )
    if isinstance(a, ListType) or isinstance(b, ListType):
        if not (isinstance(a, ListType) and isinstance(b, ListType)):
            return False
        return a.name == b.name and type_eq(a.elem, b.elem)
    if isinstance(a, DictType) or isinstance(b, DictType):
        if not (isinstance(a, DictType) and isinstance(b, DictType)):
            return False
        return (
            a.name == b.name
            and type_eq(a.key, b.key)
            and type_eq(a.value, b.value)
        )
    if isinstance(a, TupleType) or isinstance(b, TupleType):
        if not (isinstance(a, TupleType) and isinstance(b, TupleType)):
            return False
        if a.name != b.name or len(a.elems) != len(b.elems):
            return False
        i = 0
        while i < len(a.elems):
            if not type_eq(a.elems[i], b.elems[i]):
                return False
            i += 1
        return True
    if isinstance(a, FuncType) or isinstance(b, FuncType):
        if not (isinstance(a, FuncType) and isinstance(b, FuncType)):
            return False
        if a.name != b.name or len(a.params) != len(b.params):
            return False
        i = 0
        while i < len(a.params):
            if not type_eq(a.params[i], b.params[i]):
                return False
            i += 1
        return type_eq(a.ret, b.ret)
    if isinstance(a, ClassType) or isinstance(b, ClassType):
        if not (isinstance(a, ClassType) and isinstance(b, ClassType)):
            return False
        if (
            a.name != b.name
            or a.module != b.module
            or len(a.fields) != len(b.fields)
            or len(a.bases) != len(b.bases)
        ):
            return False
        i = 0
        while i < len(a.fields):
            a_name, a_ty = a.fields[i]
            b_name, b_ty = b.fields[i]
            if a_name != b_name or not type_eq(a_ty, b_ty):
                return False
            i += 1
        i = 0
        while i < len(a.bases):
            if not type_eq(a.bases[i], b.bases[i]):
                return False
            i += 1
        return True
    return a.name == b.name


def is_numeric(t: Type) -> bool:
    """True if ``t`` participates in arithmetic promotion.

    ``bool`` counts as numeric because Python treats booleans as a
    subclass of ``int``.  ``DynType`` is *not* numeric — dynamic operands
    force a dynamic result.
    """
    return isinstance(t, (IntType, FloatType, BoolType))


def _is_int_like(t: Type) -> bool:
    return isinstance(t, (IntType, BoolType))


def common_type(a: Type, b: Type) -> Type:
    """Return the arithmetic-promotion result type for ``a`` and ``b``.

    Phase 1 rules:

    * ``int`` + ``int``    → ``int`` (width = max(a.width, b.width), signed if either is signed).
    * ``bool`` + ``bool``  → ``int`` (Python semantics: ``True + True == 2``).
    * ``int`` + ``float``  → ``float``.
    * ``float`` + ``float``→ ``float`` (wider width wins).
    * ``str`` + ``str``    → ``str``.
    * otherwise            → ``DynType``.
    """

    # Float wins over anything int-like.
    if isinstance(a, FloatType) and isinstance(b, FloatType):
        return FloatType(name="float", width=max(a.width, b.width))
    if isinstance(a, FloatType) and _is_int_like(b):
        return a
    if isinstance(b, FloatType) and _is_int_like(a):
        return b

    # Two int-likes.
    if _is_int_like(a) and _is_int_like(b):
        aw = getattr(a, "width", 64)
        bw = getattr(b, "width", 64)
        asg = getattr(a, "signed", True)
        bsg = getattr(b, "signed", True)
        return IntType(
            name="int",
            width=max(aw, bw),
            signed=asg or bsg,
        )

    # Strings.
    if isinstance(a, StrType) and isinstance(b, StrType):
        return TYPE_STR

    # Same nominal type (e.g. two NoneTypes, or identical class types).
    if type_eq(a, b):
        return a

    return TYPE_DYN


__all__ = [
    "TYPE_INT",
    "TYPE_FLOAT",
    "TYPE_BOOL",
    "TYPE_NONE",
    "TYPE_STR",
    "TYPE_DYN",
    "PyFrontendError",
    "parse_annotation",
    "type_eq",
    "is_numeric",
    "common_type",
]
