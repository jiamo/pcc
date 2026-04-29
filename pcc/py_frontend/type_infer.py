"""Annotation-driven type inference for the pcc Python frontend.

This pass walks a parsed :class:`pcc.py_frontend.py_ast.Module`, fills in
the ``ty`` field on every expression, and rewrites ``Arg``/``Assign``/
``FuncDef`` nodes whose annotations have been resolved from surface
``Expr`` form into first-class :class:`Type` instances.

The AST nodes are ``frozen=True`` dataclasses, so this pass never
mutates in place; instead it produces fresh nodes via
:func:`dataclasses.replace`.

Phase 1 scope (see ``docs/plans/python-frontend-plan.md`` section
"Phase 1"):

* Every ``FuncDef`` argument with an ``annotation`` gets that type;
  missing annotations default to ``DynType``.
* Return type comes from ``return_ty`` when present; otherwise ``DynType``.
* Local assignments use ``Assign.annotation`` when provided, else the
  inferred RHS type.
* Literals map to their native types.
* ``Name`` lookup walks the local scope, then params, then module
  globals, then builtins.
* Arithmetic ``BinOp`` uses :func:`pcc.py_frontend.types.common_type`.
  ``str + str`` stays ``str``; everything else that is not numeric or
  string falls back to ``DynType`` for Phase 1.
* ``Compare`` and ``BoolExpr`` always produce ``BoolType``.
* ``Call`` returns the callee's annotated return type when the callee
  is a known :class:`FuncDef`; otherwise ``DynType``.
* ``ListExpr`` uses the common type of its elements; empty list →
  ``list[dyn]``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from . import py_ast
from .export_meta import decode_type
from .py_ast import (
    Arg,
    Assign,
    AugAssign,
    Attr,
    BinOp,
    BoolExpr,
    BoolLit,
    BoolType,
    Break,
    Call,
    ClassDef,
    ClassType,
    Compare,
    Continue,
    Delete,
    DictExpr,
    DictType,
    DynType,
    ExceptHandler,
    Expr,
    ExprStmt,
    FloatLit,
    FloatType,
    For,
    FuncDef,
    FuncType,
    Global,
    If,
    IfExpr,
    Import,
    ImportFrom,
    IntLit,
    IntType,
    Lambda,
    ListExpr,
    ListType,
    Module,
    Name,
    NoneLit,
    NoneType,
    Nonlocal,
    Pass,
    Raise,
    Return,
    Slice,
    SourceSpan,
    Stmt,
    StrLit,
    StrType,
    Subscript,
    TupleExpr,
    TupleType,
    Try,
    Type,
    UnaryOp,
    While,
    With,
)
from .types import (
    PyFrontendError,
    common_type,
    is_numeric,
    parse_annotation,
    type_eq,
)


TYPE_INT: IntType = IntType(name="int", width=64, signed=True)
TYPE_FLOAT: FloatType = FloatType(name="float", width=64)
TYPE_BOOL: BoolType = BoolType(name="bool")
TYPE_NONE: NoneType = NoneType(name="None")
TYPE_STR: StrType = StrType(name="str")
TYPE_DYN: DynType = DynType(name="dyn")


def _make_list_type(elem: Type) -> ListType:
    return ListType("list", elem)


def _make_dict_type(key: Type, value: Type) -> DictType:
    return DictType("dict", key, value)


def _make_tuple_type(name: str, elems: tuple[Type, ...]) -> TupleType:
    return TupleType(name, elems)


def _tuple_elem_type(ty: TupleType) -> Type:
    if ty.elems:
        acc = ty.elems[0]
        for elem in ty.elems[1:]:
            acc = common_type(acc, elem)
        return acc
    return TYPE_DYN


def _tuple_from_iterable_type(ty: Type) -> TupleType:
    if isinstance(ty, TupleType):
        return ty
    if isinstance(ty, ListType):
        return TupleType(name="tuple_variadic", elems=(ty.elem,))
    if isinstance(ty, StrType):
        return TupleType(name="tuple_variadic", elems=(TYPE_STR,))
    return TupleType(name="tuple_variadic", elems=(TYPE_DYN,))


def _tuple_concat_type(a: TupleType, b: TupleType) -> TupleType:
    if not a.elems and not b.elems:
        return TupleType(name="tuple", elems=())
    return TupleType(
        name="tuple_variadic",
        elems=(common_type(_tuple_elem_type(a), _tuple_elem_type(b)),),
    )


def _make_func_type(params: tuple[Type, ...], ret: Type) -> FuncType:
    return FuncType("callable", params, ret)


def _make_class_type(
    name: str,
    module: str,
    fields: tuple[tuple[str, Type], ...],
    bases: tuple[ClassType, ...],
) -> ClassType:
    return ClassType(name, module, fields, bases)


# ---------------------------------------------------------------------------
# Builtin symbol table
#
# Phase 1 only recognises a tiny slice of builtins.  Each entry maps an
# identifier to the ``Type`` you get when the name appears in a value
# position (for callables that is a ``FuncType``).
# ---------------------------------------------------------------------------

_BUILTIN_TYPES: dict[str, Type] = {
    "True": TYPE_BOOL,
    "False": TYPE_BOOL,
    "None": TYPE_NONE,
    # Common builtin callables — Phase 1 treats them as dynamic so the
    # driver can route them through the runtime library.  Future phases
    # refine to concrete ``FuncType`` entries.
    "print": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_NONE),
    "len": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_INT),
    "range": FuncType(name="callable", params=(TYPE_INT,), ret=TYPE_DYN),
    "int": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_INT),
    "float": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_FLOAT),
    "str": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_STR),
    "bool": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_BOOL),
    "set": FuncType(
        name="callable", params=(TYPE_DYN,), ret=DynType(name="set"),
    ),
    "frozenset": FuncType(
        name="callable", params=(TYPE_DYN,), ret=DynType(name="set"),
    ),
    "chr": FuncType(name="callable", params=(TYPE_INT,), ret=TYPE_STR),
    "abs": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_DYN),
    "min": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_DYN),
    "max": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_DYN),
    "sum": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_DYN),
}

_UNSAFE_INTRINSIC_RETURN_TYPES: dict[str, Type] = {
    "malloc": TYPE_DYN,
    "cstr": TYPE_DYN,
    "global_addr": TYPE_DYN,
    "global_load_ptr": TYPE_DYN,
    "global_store_ptr": TYPE_NONE,
    "define_global_i8": TYPE_NONE,
    "define_global_i32": TYPE_NONE,
    "define_global_header": TYPE_NONE,
    "define_global_ptr_null": TYPE_NONE,
    "define_global_ptr_to_global": TYPE_NONE,
    "define_global_cstr": TYPE_NONE,
    "define_global_ptr_array": TYPE_NONE,
    "define_global_null_ptr_array": TYPE_NONE,
    "define_global_i32_array": TYPE_NONE,
    "calloc": TYPE_DYN,
    "realloc": TYPE_DYN,
    "free": TYPE_NONE,
    "ptr_add": TYPE_DYN,
    "null": TYPE_DYN,
    "ptr_eq": TYPE_BOOL,
    "ptr_is_null": TYPE_BOOL,
    "is_tagged_int": TYPE_BOOL,
    "tag_int": TYPE_DYN,
    "untag_int": TYPE_INT,
    "load_i64": TYPE_INT,
    "load_i32": TYPE_INT,
    "load_i8": TYPE_INT,
    "load_ptr": TYPE_DYN,
    "load_f64": TYPE_FLOAT,
    "store_i64": TYPE_NONE,
    "store_i32": TYPE_NONE,
    "store_i8": TYPE_NONE,
    "store_ptr": TYPE_NONE,
    "store_f64": TYPE_NONE,
    "memset": TYPE_DYN,
    "memcpy": TYPE_DYN,
    "memmove": TYPE_DYN,
    "write": TYPE_INT,
    "strlen": TYPE_INT,
    "getenv": TYPE_DYN,
    "setenv": TYPE_INT,
    "unsetenv": TYPE_INT,
    "access": TYPE_INT,
}


# ---------------------------------------------------------------------------
# Scope plumbing
# ---------------------------------------------------------------------------


class _Scope:
    """Lexical scope chain for type lookup.

    Scopes are walked in order: local → enclosing params → module
    globals → builtins (builtins live as a fallback in ``_lookup``).
    """

    __slots__ = ("names", "types", "parent")

    def __init__(self, parent: Optional["_Scope"] = None) -> None:
        self.names: list[str] = []
        self.types: list[Type] = []
        self.parent: Optional[_Scope] = parent

    def _find_local(self, name: str) -> int:
        i = 0
        while i < len(self.names):
            if self.names[i] == name:
                return i
            i = i + 1
        return -1

    def define(self, name: str, ty: Type) -> None:
        idx = self._find_local(name)
        if idx >= 0:
            self.types[idx] = ty
            return
        self.names.append(name)
        self.types.append(ty)

    def update(self, name: str, ty: Type) -> None:
        """Update or insert; used for assignment re-typing."""
        self.define(name, ty)

    def lookup_local(self, name: str) -> Optional[Type]:
        idx = self._find_local(name)
        if idx >= 0:
            return self.types[idx]
        return None

    def lookup(self, name: str) -> Optional[Type]:
        scope: Optional[_Scope] = self
        while scope is not None:
            found = scope.lookup_local(name)
            if found is not None:
                return found
            scope = scope.parent
        return None


# ---------------------------------------------------------------------------
# Inference context
# ---------------------------------------------------------------------------


class _InferCtx:
    """Shared state while inferring one module."""

    def __init__(
        self,
        module: Module,
        external_exports: Optional[dict] = None,
    ) -> None:
        self.module = module
        # Module-level globals (functions, top-level vars).
        self.globals: _Scope = _Scope(parent=None)
        # Map from function name to its (possibly refined) ``FuncType``.
        self.func_types: dict[str, FuncType] = {}
        # Multi-file compile: ``{dotted_mod: {name: export_info}}``
        # where ``export_info`` matches the pipeline pre-pass shape
        # (kind, param_types, return_ty, class metadata). Consulted by
        # ImportFrom handling so cross-module function/class types
        # flow into this module's scope at inference time rather than
        # collapsing to DynType.
        self.external_exports = external_exports or {}
        # ``from dataclasses import replace as ...`` is common across
        # the frontend passes. Track the local aliases explicitly so
        # call-result inference can preserve the first argument's type
        # instead of collapsing the whole expression to DynType.
        self.dataclasses_replace_aliases: set[str] = set()
        self.class_types: dict[str, ClassType] = {}

    # -- helpers -----------------------------------------------------------

    def register_class_type(self, local_name: str, ty: ClassType) -> None:
        """Register a schema-bearing class type under local and stable names."""
        self.class_types[local_name] = ty
        self.class_types[ty.name] = ty
        if ty.module:
            self.class_types[f"{ty.module}.{ty.name}"] = ty

    def resolve_annotation(self, ann: object) -> Type:
        """Normalise an ``annotation`` field into a ``Type``.

        Parser implementations may attach either a ``Type`` directly (if
        they already resolved the annotation) or an ``Expr`` describing
        the raw annotation AST.  We accept both.
        """
        if ann is None:
            return TYPE_DYN
        if isinstance(ann, Type):
            return self.resolve_type_refs(ann)
        if isinstance(ann, Expr):
            return self.resolve_type_refs(parse_annotation(ann))
        # Unknown annotation payload — be defensive, don't crash.
        return TYPE_DYN

    def resolve_type_refs(self, ty: Type) -> Type:
        """Resolve ``ClassType`` refs inside annotations.

        Both parsers can preserve an unknown annotation name as
        ``ClassType(name, fields=())`` before inference has seen the
        corresponding class body. Once the module class table exists,
        replace those shells with the schema-bearing class type and
        recurse through container annotations.
        """
        if isinstance(ty, ClassType):
            if (
                not ty.module
                and not ty.fields
                and not ty.bases
            ):
                if ty.name == "list":
                    return _make_list_type(TYPE_DYN)
                if ty.name == "dict":
                    return _make_dict_type(TYPE_DYN, TYPE_DYN)
                if ty.name == "tuple":
                    return _make_tuple_type("tuple_variadic", (TYPE_DYN,))
            if ty.module:
                found = self.class_types.get(f"{ty.module}.{ty.name}")
                if found is not None:
                    return found
            found = self.class_types.get(ty.name)
            if found is not None:
                return found
            fields = tuple(
                (name, self.resolve_type_refs(field_ty))
                for name, field_ty in ty.fields
            )
            bases = tuple(self.resolve_type_refs(base) for base in ty.bases)
            if fields == ty.fields and bases == ty.bases:
                return ty
            return _make_class_type(ty.name, ty.module, fields, bases)
        if isinstance(ty, ListType):
            elem = self.resolve_type_refs(ty.elem)
            if elem == ty.elem:
                return ty
            return _make_list_type(elem)
        if isinstance(ty, DictType):
            key = self.resolve_type_refs(ty.key)
            value = self.resolve_type_refs(ty.value)
            if key == ty.key and value == ty.value:
                return ty
            return _make_dict_type(key, value)
        if isinstance(ty, TupleType):
            elems = tuple(self.resolve_type_refs(e) for e in ty.elems)
            if elems == ty.elems:
                return ty
            return _make_tuple_type(ty.name, elems)
        if isinstance(ty, FuncType):
            params = tuple(self.resolve_type_refs(p) for p in ty.params)
            ret = self.resolve_type_refs(ty.ret)
            if params == ty.params and ret == ty.ret:
                return ty
            return _make_func_type(params, ret)
        return ty

    def lookup_name(self, scope: _Scope, ident: str) -> Type:
        """Resolve a bare name, falling through to builtins."""
        found = scope.lookup(ident)
        if found is not None:
            return found
        # Module globals are reachable via the scope chain, so we only
        # need the builtin table here.
        builtin = _BUILTIN_TYPES.get(ident)
        if builtin is not None:
            return builtin
        return TYPE_DYN


# ---------------------------------------------------------------------------
# Expression inference
#
# Every helper returns a *new* expression node whose ``ty`` field has
# been filled in (or replaced with a more precise type).
# ---------------------------------------------------------------------------


def _with_ty(node: Expr, ty: Type) -> Expr:
    """Return ``node`` with its ``ty`` field replaced by ``ty``."""
    return replace(node, ty=ty)


def _infer_expr(ctx: _InferCtx, scope: _Scope, expr: Expr) -> Expr:
    # Literals -----------------------------------------------------------
    if isinstance(expr, IntLit):
        return _with_ty(expr, TYPE_INT)
    if isinstance(expr, FloatLit):
        return _with_ty(expr, TYPE_FLOAT)
    if isinstance(expr, BoolLit):
        return _with_ty(expr, TYPE_BOOL)
    if isinstance(expr, NoneLit):
        return _with_ty(expr, TYPE_NONE)
    if isinstance(expr, StrLit):
        return _with_ty(expr, TYPE_STR)

    # Name lookup --------------------------------------------------------
    if isinstance(expr, Name):
        ty = ctx.lookup_name(scope, expr.ident)
        return _with_ty(expr, ty)

    # Binary arithmetic --------------------------------------------------
    if isinstance(expr, BinOp):
        lhs = _infer_expr(ctx, scope, expr.lhs)
        rhs = _infer_expr(ctx, scope, expr.rhs)
        ty = _binop_result(expr.op, lhs.ty, rhs.ty, expr.span)
        return replace(expr, lhs=lhs, rhs=rhs, ty=ty)

    # Unary --------------------------------------------------------------
    if isinstance(expr, UnaryOp):
        operand = _infer_expr(ctx, scope, expr.operand)
        if expr.op == "not":
            ty: Type = TYPE_BOOL
        elif expr.op == "~":
            ty = operand.ty if isinstance(operand.ty, (IntType, BoolType)) else TYPE_DYN
            if isinstance(operand.ty, BoolType):
                ty = TYPE_INT
        else:  # "+" / "-"
            if is_numeric(operand.ty):
                # Promote bool to int under unary +/-.
                ty = TYPE_INT if isinstance(operand.ty, BoolType) else operand.ty
            else:
                ty = TYPE_DYN
        return replace(expr, operand=operand, ty=ty)

    # Comparisons + boolean ops --------------------------------------------
    if isinstance(expr, Compare):
        lhs = _infer_expr(ctx, scope, expr.lhs)
        rhs = _infer_expr(ctx, scope, expr.rhs)
        return replace(expr, lhs=lhs, rhs=rhs, ty=TYPE_BOOL)
    if isinstance(expr, BoolExpr):
        left = _infer_expr(ctx, scope, expr.left)
        right = _infer_expr(ctx, scope, expr.right)
        # Python's ``a or b`` / ``a and b`` return one of the operand
        # values (not a coerced bool), so the expression's type is the
        # common type of the two branches. ``common_type`` keeps the
        # BoolType fall-through for numeric operands while widening to
        # Str/List/Dict/... for object operands — which is what
        # ``self.name or "<anon>"`` idioms need.
        result_ty = common_type(left.ty, right.ty)
        # When both arms are numeric/bool the old invariant still holds
        # (operand values do compare as booleans), so short-circuit to
        # BoolType for back-compat with existing codegen expectations.
        if isinstance(left.ty, BoolType) and isinstance(right.ty, BoolType):
            result_ty = TYPE_BOOL
        return replace(expr, left=left, right=right, ty=result_ty)

    # Calls --------------------------------------------------------------
    if isinstance(expr, Call):
        callee = _infer_expr(ctx, scope, expr.func)
        new_args = tuple(_infer_expr(ctx, scope, a) for a in expr.args)
        new_kwargs = tuple(
            (k, _infer_expr(ctx, scope, v)) for (k, v) in expr.kwargs
        )
        # Comprehension sentinels: synthesise a concrete container type
        # so downstream ``for`` loops / subscripts see a real ListType /
        # DictType / SetType instead of plain DynType.
        if isinstance(callee, Name):
            sentinel = callee.ident
            if sentinel in (
                "_list_comp", "__listcomp__",
                "_gen_comp", "__genexpr__",
            ):
                elt = new_args[0] if new_args else None
                elt_ty = elt.ty if elt is not None else TYPE_DYN
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=ListType(name="list", elem=elt_ty),
                )
            if sentinel in ("_set_comp", "__setcomp__"):
                # py_ast has no first-class SetType yet. Preserve the
                # container kind with DynType(name="set") so codegen does
                # not route set operators through integer bitwise lowering.
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=DynType(name="set"),
                )
            if sentinel in ("_dict_comp", "__dictcomp__"):
                # Native: first arg is TupleExpr(k, v). CPython-AST:
                # first two args are key/val exprs.
                if sentinel == "_dict_comp" and new_args:
                    kv = new_args[0]
                    if isinstance(kv, TupleExpr) and len(kv.elems) == 2:
                        k_ty, v_ty = kv.elems[0].ty, kv.elems[1].ty
                        return replace(
                            expr, func=callee, args=new_args,
                            kwargs=new_kwargs,
                            ty=DictType(name="dict", key=k_ty, value=v_ty),
                        )
                if sentinel == "__dictcomp__" and len(new_args) >= 2:
                    k_ty = new_args[0].ty
                    v_ty = new_args[1].ty
                    return replace(
                        expr, func=callee, args=new_args,
                        kwargs=new_kwargs,
                        ty=DictType(name="dict", key=k_ty, value=v_ty),
                    )
        # Known-return-type builtins: ``sum`` returns int, ``len`` returns
        # int, ``min``/``max``/``abs`` return the operand type family.
        if isinstance(callee, Name):
            bname = callee.ident
            if (
                bname in ctx.dataclasses_replace_aliases
                and len(new_args) == 1
            ):
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=new_args[0].ty,
                )
            if bname == "sum":
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=IntType(name="int"),
                )
            if bname == "int":
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=IntType(name="int"),
                )
            if bname == "bool":
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=TYPE_BOOL,
                )
            if bname == "float":
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=TYPE_FLOAT,
                )
            if bname == "str":
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=TYPE_STR,
                )
            if bname == "tuple":
                if not new_args:
                    ty = TupleType(name="tuple", elems=())
                else:
                    ty = _tuple_from_iterable_type(new_args[0].ty)
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=ty,
                )
            if bname == "chr":
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=TYPE_STR,
                )
            if bname in ("any", "all"):
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=TYPE_BOOL,
                )
            if bname == "abs":
                if new_args and isinstance(
                    new_args[0].ty, (IntType, FloatType, BoolType),
                ):
                    return replace(
                        expr, func=callee, args=new_args,
                        kwargs=new_kwargs, ty=new_args[0].ty,
                    )
            if bname in ("repr",):
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=TYPE_STR,
                )
            if bname in ("hash", "id"):
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=IntType(name="int"),
                )
            if bname in ("min", "max") and new_args:
                # Single-arg iterable form: result is the iterable's
                # element type. Multi-arg form: common type of args.
                if len(new_args) == 1:
                    a0_ty = new_args[0].ty
                    if isinstance(a0_ty, ListType):
                        acc = a0_ty.elem
                    elif isinstance(a0_ty, TupleType) and a0_ty.elems:
                        acc = a0_ty.elems[0]
                        for e in a0_ty.elems[1:]:
                            acc = common_type(acc, e)
                    else:
                        acc = IntType(name="int")
                else:
                    acc = new_args[0].ty
                    for a in new_args[1:]:
                        acc = common_type(acc, a.ty)
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=acc,
                )

        # Method-call result inference for known typed-container methods
        # so chained calls stay on the pcc-native fast paths without
        # needing an annotation hint at every site.
        if isinstance(callee, Attr):
            recv_ty = callee.obj.ty
            method = callee.name
            inferred = _container_method_return_type(recv_ty, method)
            if inferred is not None:
                return replace(
                    expr, func=callee, args=new_args, kwargs=new_kwargs,
                    ty=inferred,
                )
        ret_ty = _call_result_type(ctx, callee)
        return replace(
            expr,
            func=callee,
            args=new_args,
            kwargs=new_kwargs,
            ty=ret_ty,
        )

    # Attribute / subscript / slice (Phase 1: opaque → dyn) --------------
    if isinstance(expr, Attr):
        obj = _infer_expr(ctx, scope, expr.obj)
        # Bucket 1: when the receiver is a known class type with
        # field declarations, look up the field's declared type.
        # Walks the MRO (bases) so inherited fields resolve too.
        if isinstance(obj.ty, ClassType):
            field_ty = _lookup_class_field(obj.ty, expr.name)
            if field_ty is not None:
                return replace(expr, obj=obj, ty=field_ty)
        return replace(expr, obj=obj, ty=TYPE_DYN)

    if isinstance(expr, Subscript):
        obj = _infer_expr(ctx, scope, expr.obj)
        idx = _infer_expr(ctx, scope, expr.idx)
        # ``xs[lo:hi]`` — slicing returns a new container of the same
        # kind: list → list[elem], str → str, tuple → tuple (element
        # types preserved but arity unknown, use ``tuple_variadic``).
        if isinstance(idx, Slice):
            if isinstance(obj.ty, ListType):
                ty = obj.ty
            elif isinstance(obj.ty, StrType):
                ty = TYPE_STR
            elif isinstance(obj.ty, TupleType):
                if obj.ty.elems:
                    ty = TupleType(
                        name="tuple_variadic",
                        elems=(_tuple_elem_type(obj.ty),),
                    )
                else:
                    ty = TupleType(name="tuple", elems=())
            else:
                ty = TYPE_DYN
            return replace(expr, obj=obj, idx=idx, ty=ty)
        if isinstance(obj.ty, ListType):
            ty = obj.ty.elem
        elif isinstance(obj.ty, TupleType) and obj.ty.elems:
            # Phase 1: if all element types agree, use that; else dyn.
            first = obj.ty.elems[0]
            ty = first if all(type_eq(first, e) for e in obj.ty.elems) else TYPE_DYN
        elif isinstance(obj.ty, DictType):
            ty = obj.ty.value
        elif isinstance(obj.ty, StrType):
            ty = TYPE_STR
        else:
            ty = TYPE_DYN
        return replace(expr, obj=obj, idx=idx, ty=ty)

    if isinstance(expr, Slice):
        lo = _infer_expr(ctx, scope, expr.lo) if expr.lo is not None else None
        hi = _infer_expr(ctx, scope, expr.hi) if expr.hi is not None else None
        step = _infer_expr(ctx, scope, expr.step) if expr.step is not None else None
        return replace(expr, lo=lo, hi=hi, step=step, ty=TYPE_DYN)

    # Container literals ------------------------------------------------
    if isinstance(expr, ListExpr):
        new_elems = tuple(_infer_expr(ctx, scope, e) for e in expr.elems)
        if not new_elems:
            list_ty: Type = ListType(name="list", elem=TYPE_DYN)
        else:
            acc = new_elems[0].ty
            for el in new_elems[1:]:
                acc = common_type(acc, el.ty)
            list_ty = ListType(name="list", elem=acc)
        return replace(expr, elems=new_elems, ty=list_ty)

    if isinstance(expr, TupleExpr):
        new_elems = tuple(_infer_expr(ctx, scope, e) for e in expr.elems)
        tup_ty = TupleType(name="tuple", elems=tuple(e.ty for e in new_elems))
        return replace(expr, elems=new_elems, ty=tup_ty)

    if isinstance(expr, DictExpr):
        new_pairs = tuple(
            (_infer_expr(ctx, scope, k), _infer_expr(ctx, scope, v))
            for (k, v) in expr.pairs
        )
        if not new_pairs:
            dict_ty: Type = DictType(name="dict", key=TYPE_DYN, value=TYPE_DYN)
        else:
            key_ty = new_pairs[0][0].ty
            val_ty = new_pairs[0][1].ty
            for k, v in new_pairs[1:]:
                key_ty = common_type(key_ty, k.ty)
                val_ty = common_type(val_ty, v.ty)
            dict_ty = DictType(name="dict", key=key_ty, value=val_ty)
        return replace(expr, pairs=new_pairs, ty=dict_ty)

    # Ternary ``a if c else b`` ----------------------------------------
    if isinstance(expr, IfExpr):
        cond = _infer_expr(ctx, scope, expr.cond)
        then_e = _infer_expr(ctx, scope, expr.then_e)
        else_e = _infer_expr(ctx, scope, expr.else_e)
        ty = common_type(then_e.ty, else_e.ty)
        return replace(expr, cond=cond, then_e=then_e, else_e=else_e, ty=ty)

    # Lambda — Phase 1 leaves the body untyped; return a dyn FuncType.
    if isinstance(expr, Lambda):
        # Resolve annotations on the lambda params (usually absent).
        param_types = tuple(
            ctx.resolve_annotation(p.annotation) for p in expr.params
        )
        lam_ty = FuncType(name="callable", params=param_types, ret=TYPE_DYN)
        return _with_ty(expr, lam_ty)

    # Unknown expression node — leave as dyn.  This keeps the pass total.
    return _with_ty(expr, TYPE_DYN)


def _binop_result(op: str, a: Type, b: Type, span: SourceSpan) -> Type:
    """Type-of for ``a op b``.

    Phase 1 focuses on numeric + string.  Bitwise ops on ints stay int;
    division (``/``) promotes to float; everything else follows
    :func:`common_type`.
    """
    # String concatenation / repetition.
    if op == "+":
        if isinstance(a, StrType) and isinstance(b, StrType):
            return TYPE_STR
        if isinstance(a, TupleType) and isinstance(b, TupleType):
            return _tuple_concat_type(a, b)
        if isinstance(a, ListType) and isinstance(b, ListType):
            return ListType(name="list", elem=common_type(a.elem, b.elem))
    if op == "%":
        if isinstance(a, StrType):
            return TYPE_STR
    if op == "*":
        # str * int or int * str → str
        if isinstance(a, StrType) and isinstance(b, (IntType, BoolType)):
            return TYPE_STR
        if isinstance(b, StrType) and isinstance(a, (IntType, BoolType)):
            return TYPE_STR

    # Reject obvious mismatches early with a friendly error.
    if op in ("+", "-", "*", "/", "//", "%", "**"):
        if isinstance(a, StrType) and is_numeric(b):
            if op not in ("*", "%"):
                raise PyFrontendError(
                    span=span,
                    message=f"unsupported operand type(s) for {op}: 'str' and numeric",
                    hint="use str() or explicit conversion",
                )
        if isinstance(b, StrType) and is_numeric(a):
            if op not in ("*", "%"):
                raise PyFrontendError(
                    span=span,
                    message=f"unsupported operand type(s) for {op}: numeric and 'str'",
                    hint="use str() or explicit conversion",
                )

    # True division always returns float for numeric operands.
    if op == "/" and is_numeric(a) and is_numeric(b):
        return TYPE_FLOAT

    # Bitwise / shift on int-like operands stays int.
    if op in ("&", "|", "^", "<<", ">>"):
        if (
            op == "|"
            and isinstance(a, DynType)
            and isinstance(b, DynType)
            and a.name in ("set", "frozenset")
            and b.name in ("set", "frozenset")
        ):
            return DynType(name="set")
        if isinstance(a, (IntType, BoolType)) and isinstance(b, (IntType, BoolType)):
            # Bool <<>> anything else returns int (Python promotes).
            return TYPE_INT
        return TYPE_DYN

    # Power: int ** int → int; anything touching float → float.
    if op == "**":
        if isinstance(a, FloatType) or isinstance(b, FloatType):
            return TYPE_FLOAT
        if is_numeric(a) and is_numeric(b):
            return TYPE_INT

    # Default arithmetic promotion.
    if is_numeric(a) and is_numeric(b):
        return common_type(a, b)

    return TYPE_DYN


def _call_result_type(ctx: _InferCtx, callee: Expr) -> Type:
    """Best-effort return type for a ``Call`` whose callee has been typed."""
    # Direct by name: look up user-defined function.
    if isinstance(callee, Name):
        ft = ctx.func_types.get(callee.ident)
        if ft is not None:
            return ft.ret
        # Fall through to the callee's own type (may be a FuncType from
        # builtins or a user definition captured via a local binding).
    if isinstance(callee.ty, FuncType):
        return callee.ty.ret
    if isinstance(callee.ty, ClassType):
        return callee.ty
    return TYPE_DYN


# ---------------------------------------------------------------------------
# Statement inference
# ---------------------------------------------------------------------------


def _infer_stmt(ctx: _InferCtx, scope: _Scope, stmt: Stmt) -> Stmt:
    if isinstance(stmt, FuncDef):
        return _infer_funcdef(ctx, scope, stmt)

    if isinstance(stmt, Assign):
        return _infer_assign(ctx, scope, stmt)

    if isinstance(stmt, AugAssign):
        target = _infer_expr(ctx, scope, stmt.target)
        value = _infer_expr(ctx, scope, stmt.value)
        # Re-bind the target's type to the promoted result so subsequent
        # statements see the refined type.
        if isinstance(stmt.target, Name):
            new_ty = _binop_result(stmt.op[:-1], target.ty, value.ty, stmt.span)
            scope.update(stmt.target.ident, new_ty)
        return replace(stmt, target=target, value=value)

    if isinstance(stmt, ExprStmt):
        expr = _infer_expr(ctx, scope, stmt.expr)
        return replace(stmt, expr=expr)

    if isinstance(stmt, Return):
        if stmt.value is None:
            return stmt
        value = _infer_expr(ctx, scope, stmt.value)
        return replace(stmt, value=value)

    if isinstance(stmt, If):
        cond = _infer_expr(ctx, scope, stmt.cond)
        body_scope = _narrow_scope_for_cond(ctx, scope, cond)
        body = tuple(_infer_stmt(ctx, body_scope, s) for s in stmt.body)
        else_body = tuple(_infer_stmt(ctx, scope, s) for s in stmt.else_body)
        return replace(stmt, cond=cond, body=body, else_body=else_body)

    if isinstance(stmt, While):
        cond = _infer_expr(ctx, scope, stmt.cond)
        body = tuple(_infer_stmt(ctx, scope, s) for s in stmt.body)
        else_body = tuple(_infer_stmt(ctx, scope, s) for s in stmt.else_body)
        return replace(stmt, cond=cond, body=body, else_body=else_body)

    if isinstance(stmt, For):
        iter_e = _infer_expr(ctx, scope, stmt.iter)
        # Phase 1: loop variable type = element type of the iterable if
        # we can discover it; else dyn.
        elem_ty = _element_type_of(iter_e.ty)
        target = _infer_expr(ctx, scope, stmt.target)
        if isinstance(stmt.target, Name):
            scope.update(stmt.target.ident, elem_ty)
            target = _with_ty(stmt.target, elem_ty)
        body = tuple(_infer_stmt(ctx, scope, s) for s in stmt.body)
        else_body = tuple(_infer_stmt(ctx, scope, s) for s in stmt.else_body)
        return replace(
            stmt,
            target=target,
            iter=iter_e,
            body=body,
            else_body=else_body,
        )

    if isinstance(stmt, Raise):
        exc = _infer_expr(ctx, scope, stmt.exc) if stmt.exc is not None else None
        cause = (
            _infer_expr(ctx, scope, stmt.cause) if stmt.cause is not None else None
        )
        return replace(stmt, exc=exc, cause=cause)

    if isinstance(stmt, Try):
        body = tuple(_infer_stmt(ctx, scope, s) for s in stmt.body)
        handlers = tuple(_infer_handler(ctx, scope, h) for h in stmt.handlers)
        else_body = tuple(_infer_stmt(ctx, scope, s) for s in stmt.else_body)
        finally_body = tuple(_infer_stmt(ctx, scope, s) for s in stmt.finally_body)
        return replace(
            stmt,
            body=body,
            handlers=handlers,
            else_body=else_body,
            finally_body=finally_body,
        )

    if isinstance(stmt, With):
        new_items = []
        for ctx_expr, as_var in stmt.items:
            new_ctx = _infer_expr(ctx, scope, ctx_expr)
            if as_var is not None:
                new_as = _infer_expr(ctx, scope, as_var)
                if isinstance(as_var, Name):
                    scope.update(as_var.ident, TYPE_DYN)
            else:
                new_as = None
            new_items.append((new_ctx, new_as))
        body = tuple(_infer_stmt(ctx, scope, s) for s in stmt.body)
        return replace(stmt, items=tuple(new_items), body=body)

    if isinstance(stmt, Delete):
        targets = tuple(_infer_expr(ctx, scope, t) for t in stmt.targets)
        return replace(stmt, targets=targets)

    if isinstance(stmt, ClassDef):
        # Phase 1 does not type the body of classes; leave the class
        # node alone but still walk the body so nested funcs get typed.
        # Class-level ``x: T`` (no value — NoneLit placeholder from the
        # AnnAssign lift) is an instance-field declaration, not a real
        # assignment, so don't run the usual compatibility check that
        # would otherwise reject ``None`` against the annotation.
        class_scope = _Scope(parent=scope)
        new_body: list = []
        for s in stmt.body:
            if (
                isinstance(s, Assign)
                and s.annotation is not None
                and isinstance(s.value, NoneLit)
                and len(s.targets) == 1
                and isinstance(s.targets[0], Name)
            ):
                new_body.append(s)
                continue
            if isinstance(s, FuncDef):
                self_ty = ctx.class_types.get(stmt.name)
                new_body.append(
                    _infer_funcdef(ctx, class_scope, s, self_ty=self_ty)
                )
                continue
            new_body.append(_infer_stmt(ctx, class_scope, s))
        return replace(stmt, body=tuple(new_body))

    # Import/Global/Nonlocal/Pass/Break/Continue — mostly pass through.
    # For ImportFrom against a registered native sibling module we
    # bind the imported names in the current scope to the remote
    # function / class type so downstream call-site and attribute
    # inference picks the concrete type rather than DynType.
    if isinstance(stmt, ImportFrom):
        resolved = _resolve_relative_module(
            stmt.module,
            stmt.level,
            ctx.module.name,
            stmt.span.file,
        )
        if resolved == "dataclasses":
            for attr_name, as_name in stmt.names:
                if attr_name != "replace":
                    continue
                local_name = as_name or attr_name
                ft = FuncType(
                    name="callable", params=(TYPE_DYN,), ret=TYPE_DYN,
                )
                ctx.dataclasses_replace_aliases.add(local_name)
                scope.update(local_name, ft)
                ctx.func_types[local_name] = ft
        if resolved == "pcc.unsafe":
            for attr_name, as_name in stmt.names:
                ret_ty = _UNSAFE_INTRINSIC_RETURN_TYPES.get(attr_name)
                if ret_ty is None:
                    continue
                local_name = as_name or attr_name
                ft = FuncType(
                    name="callable", params=(TYPE_DYN,), ret=ret_ty,
                )
                scope.update(local_name, ft)
                ctx.func_types[local_name] = ft
        if ctx.external_exports:
            _bind_external_import_exports(ctx, scope, resolved, stmt.names)
        return stmt
    if isinstance(stmt, (Import, Global, Nonlocal, Pass, Break, Continue)):
        return stmt

    # Anything unhandled: return unchanged rather than crash.
    return stmt


def _infer_handler(
    ctx: _InferCtx, scope: _Scope, handler: ExceptHandler
) -> ExceptHandler:
    exc_type = (
        _infer_expr(ctx, scope, handler.exc_type)
        if handler.exc_type is not None
        else None
    )
    if handler.name is not None:
        scope.update(handler.name, TYPE_DYN)
    body = tuple(_infer_stmt(ctx, scope, s) for s in handler.body)
    return replace(handler, exc_type=exc_type, body=body)


def _element_type_of(ty: Type) -> Type:
    if isinstance(ty, ListType):
        return ty.elem
    if isinstance(ty, DictType):
        return ty.key
    if isinstance(ty, TupleType):
        if ty.elems:
            first = ty.elems[0]
            if all(type_eq(first, e) for e in ty.elems):
                return first
        return TYPE_DYN
    if isinstance(ty, StrType):
        return TYPE_STR
    return TYPE_DYN


def _type_from_isinstance_arg(
    ctx: _InferCtx, expr: Expr,
) -> Optional[Type]:
    """Resolve ``isinstance``'s second argument when it is one type.

    Tuple forms describe a union. The frontend has no union type yet, so
    we deliberately leave those unnarrowed instead of guessing.
    """
    if isinstance(expr, Name):
        if expr.ident == "tuple":
            return TupleType(name="tuple_variadic", elems=(TYPE_DYN,))
        if expr.ident == "list":
            return ListType(name="list", elem=TYPE_DYN)
        if expr.ident == "dict":
            return DictType(name="dict", key=TYPE_DYN, value=TYPE_DYN)
        if expr.ident == "str":
            return TYPE_STR
        if expr.ident == "int":
            return TYPE_INT
        if expr.ident == "bool":
            return TYPE_BOOL
        if expr.ident == "float":
            return TYPE_FLOAT
        ty = ctx.resolve_type_refs(expr.ty)
        if isinstance(ty, ClassType):
            return ty
        found = ctx.class_types.get(expr.ident)
        if found is not None:
            return found
    return None


def _narrow_scope_for_cond(ctx: _InferCtx, scope: _Scope, cond: Expr) -> _Scope:
    if isinstance(cond, BoolExpr) and cond.op == "and":
        narrowed_left = _narrow_scope_for_cond(ctx, scope, cond.left)
        return _narrow_scope_for_cond(ctx, narrowed_left, cond.right)
    return _narrow_scope_for_isinstance(ctx, scope, cond)


def _narrow_scope_for_isinstance(
    ctx: _InferCtx, scope: _Scope, cond: Expr,
) -> _Scope:
    if not (
        isinstance(cond, Call)
        and isinstance(cond.func, Name)
        and cond.func.ident == "isinstance"
        and len(cond.args) == 2
        and not cond.kwargs
        and isinstance(cond.args[0], Name)
    ):
        return scope
    candidate = _type_from_isinstance_arg(ctx, cond.args[1])
    if candidate is None:
        return scope
    var_name = cond.args[0].ident
    current = scope.lookup(var_name)
    if current is None:
        return scope
    if isinstance(current, DynType):
        narrowed = _Scope(parent=scope)
        narrowed.update(var_name, candidate)
        return narrowed
    if not (
        isinstance(current, ClassType)
        and isinstance(candidate, ClassType)
        and _class_type_assignable(current, candidate)
    ):
        return scope
    narrowed = _Scope(parent=scope)
    narrowed.update(var_name, candidate)
    return narrowed


def _iter_class_mro(cls_ty: ClassType):
    """Yield ``cls_ty`` and its bases breadth-first, guarding cycles."""
    seen: set[tuple[str, str]] = set()
    queue: list[ClassType] = [cls_ty]
    while queue:
        cur = queue.pop(0)
        key = (cur.module, cur.name)
        if key in seen:
            continue
        seen.add(key)
        yield cur
        queue.extend(cur.bases)


def _lookup_class_field(cls_ty: ClassType, field_name: str) -> Optional[Type]:
    for cur in _iter_class_mro(cls_ty):
        for fname, fty in cur.fields:
            if fname == field_name:
                return fty
    return None


def _class_bases_from_def(ctx: _InferCtx, stmt: ClassDef) -> tuple[ClassType, ...]:
    bases: list[ClassType] = []
    for base_expr in stmt.bases:
        if not isinstance(base_expr, Name):
            continue
        if base_expr.ident == "object":
            continue
        base_ty = ctx.class_types.get(base_expr.ident)
        if isinstance(base_ty, ClassType):
            bases.append(base_ty)
        else:
            bases.append(
                _make_class_type(
                    base_expr.ident, ctx.module.name or "", (), (),
                )
            )
    return tuple(bases)


def _append_field(
    fields: list[tuple[str, Type]], name: str, field_ty: Type,
) -> None:
    for i, (existing, _old_ty) in enumerate(fields):
        if existing == name:
            fields[i] = (name, field_ty)
            return
    fields.append((name, field_ty))


def _class_fields_from_def(ctx: _InferCtx, stmt: ClassDef) -> tuple[tuple[str, Type], ...]:
    fields: list[tuple[str, Type]] = []
    for body_stmt in stmt.body:
        if isinstance(body_stmt, Assign):
            if body_stmt.annotation is None:
                continue
            field_ty = ctx.resolve_annotation(body_stmt.annotation)
            for target in body_stmt.targets:
                if isinstance(target, Name):
                    _append_field(fields, target.ident, field_ty)
        elif isinstance(body_stmt, FuncDef) and body_stmt.name == "__init__":
            arg_types = {
                arg.name: ctx.resolve_annotation(arg.annotation)
                for arg in body_stmt.args
                if arg.name not in ("", "self", "cls")
            }
            for init_stmt in body_stmt.body:
                if not isinstance(init_stmt, Assign):
                    continue
                explicit_ty: Optional[Type] = None
                if init_stmt.annotation is not None:
                    explicit_ty = ctx.resolve_annotation(init_stmt.annotation)
                for target in init_stmt.targets:
                    if (
                        not isinstance(target, Attr)
                        or not isinstance(target.obj, Name)
                        or target.obj.ident != "self"
                    ):
                        continue
                    field_ty = explicit_ty
                    if field_ty is None and isinstance(init_stmt.value, Name):
                        field_ty = arg_types.get(init_stmt.value.ident)
                    if field_ty is None:
                        continue
                    _append_field(fields, target.name, field_ty)
    return tuple(fields)


def _class_type_from_export(
    ctx: _InferCtx,
    module_name: str,
    info: dict,
    module_exports: dict,
    memo: dict[tuple[str, str], ClassType],
) -> ClassType:
    class_name = info["class_name"]
    key = (module_name, class_name)
    cached = memo.get(key)
    if cached is not None:
        return cached

    placeholder = _make_class_type(class_name, module_name, (), ())
    memo[key] = placeholder

    bases: list[ClassType] = []
    for base_name in info.get("base_names", ()):
        base_info = module_exports.get(base_name)
        if isinstance(base_info, dict) and base_info.get("kind") == "class":
            bases.append(
                _class_type_from_export(
                    ctx, module_name, base_info, module_exports, memo,
                )
            )
        else:
            bases.append(
                _make_class_type(base_name, module_name, (), ())
            )

    fields = tuple(
        (
            fname,
            ctx.resolve_type_refs(_annotation_to_type(decode_type(field_ty))),
        )
        for fname, field_ty in info.get("field_types", ())
    )
    cls_ty = _make_class_type(
        class_name, module_name, fields, tuple(bases),
    )
    memo[key] = cls_ty
    ctx.register_class_type(class_name, cls_ty)
    return cls_ty


def _bind_external_import_exports(
    ctx: _InferCtx,
    scope: _Scope,
    resolved_module: str,
    names: tuple[tuple[str, Optional[str]], ...],
) -> None:
    module_exports = ctx.external_exports.get(resolved_module)
    if module_exports is None:
        return

    memo: dict[tuple[str, str], ClassType] = {}
    for info in module_exports.values():
        if isinstance(info, dict) and info.get("kind") == "class":
            _class_type_from_export(
                ctx, resolved_module, info, module_exports, memo,
            )

    for attr_name, as_name in names:
        local_name = as_name or attr_name
        info = module_exports.get(attr_name)
        if info is None:
            continue
        if info["kind"] == "function":
            param_tys = tuple(
                ctx.resolve_type_refs(_annotation_to_type(decode_type(t)))
                for t in info["param_types"]
            )
            ret_ty = ctx.resolve_type_refs(
                _annotation_to_type(decode_type(info["return_ty"]))
            )
            ft = _make_func_type(param_tys, ret_ty)
            scope.update(local_name, ft)
            ctx.func_types[local_name] = ft
        elif info["kind"] == "class":
            cls_ty = _class_type_from_export(
                ctx, resolved_module, info, module_exports, memo,
            )
            scope.update(local_name, cls_ty)
            ctx.register_class_type(local_name, cls_ty)


def _preload_unique_external_classes(ctx: _InferCtx) -> None:
    by_name: dict[str, list[tuple[str, dict, dict]]] = {}
    for module_name, module_exports in ctx.external_exports.items():
        for info in module_exports.values():
            if not isinstance(info, dict) or info.get("kind") != "class":
                continue
            by_name.setdefault(info["class_name"], []).append(
                (module_name, info, module_exports)
            )
    memo: dict[tuple[str, str], ClassType] = {}
    for class_name, entries in by_name.items():
        if len(entries) != 1:
            continue
        module_name, info, module_exports = entries[0]
        cls_ty = _class_type_from_export(
            ctx, module_name, info, module_exports, memo,
        )
        ctx.register_class_type(class_name, cls_ty)


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def _infer_assign(ctx: _InferCtx, scope: _Scope, stmt: Assign) -> Assign:
    value = _infer_expr(ctx, scope, stmt.value)
    ann_ty = ctx.resolve_annotation(stmt.annotation)
    if isinstance(ann_ty, DynType):
        bind_ty = value.ty
    else:
        _check_assign_compatible(ann_ty, value.ty, stmt.span)
        bind_ty = ann_ty

    new_targets = []
    for tgt in stmt.targets:
        if isinstance(tgt, Name):
            scope.update(tgt.ident, bind_ty)
            new_targets.append(_with_ty(tgt, bind_ty))
        else:
            new_targets.append(_infer_expr(ctx, scope, tgt))
    # Preserve the resolved annotation as a ``Type`` in the node so the
    # codegen layer doesn't have to re-parse it.
    new_annotation = ann_ty if not isinstance(ann_ty, DynType) else stmt.annotation
    return replace(
        stmt,
        targets=tuple(new_targets),
        value=value,
        annotation=new_annotation,
    )


def _check_assign_compatible(ann: Type, rhs: Type, span: SourceSpan) -> None:
    """Raise ``PyFrontendError`` if ``rhs`` is incompatible with ``ann``."""
    if isinstance(ann, DynType) or isinstance(rhs, DynType):
        return
    if type_eq(ann, rhs):
        return
    # Allow implicit int→float promotion at assignment.
    if isinstance(ann, FloatType) and isinstance(rhs, (IntType, BoolType)):
        return
    # Allow bool→int.
    if isinstance(ann, IntType) and isinstance(rhs, BoolType):
        return
    # Allow None to any object-ish annotation (``Optional[list]`` /
    # ``Optional[dict]`` / ``Optional[MyClass]`` parses as the inner
    # type; None-at-init is a standard idiom).
    from .py_ast import NoneType as _NoneType
    if isinstance(rhs, _NoneType) and not isinstance(
        ann, (IntType, FloatType, BoolType)
    ):
        return
    # Container element subsumption with DynType.
    if _is_assignable(ann, rhs):
        return
    raise PyFrontendError(
        span=span,
        message=f"cannot assign value of type {rhs.name!r} to variable annotated as {ann.name!r}",
        hint="add an explicit cast or relax the annotation",
    )


# ---------------------------------------------------------------------------
# FuncDef
# ---------------------------------------------------------------------------


def _infer_funcdef(
    ctx: _InferCtx,
    scope: _Scope,
    fn: FuncDef,
    *,
    self_ty: Optional[ClassType] = None,
) -> FuncDef:
    # Resolve argument annotations up-front.
    new_args: list[Arg] = []
    param_scope = _Scope(parent=scope)
    for index, a in enumerate(fn.args):
        ty = ctx.resolve_annotation(a.annotation)
        if (
            self_ty is not None
            and index == 0
            and a.name in ("self", "cls")
            and a.annotation is None
        ):
            ty = self_ty
        new_args.append(
            replace(
                a,
                annotation=ty,
                default=(
                    _infer_expr(ctx, param_scope, a.default)
                    if a.default is not None
                    else None
                ),
            )
        )
        param_scope.define(a.name, ty)

    ret_ty = ctx.resolve_annotation(fn.return_ty)

    # Record the function's full type in the module-level table *before*
    # walking the body so recursive calls see it.
    ft = FuncType(
        name="callable",
        params=tuple(a.annotation if isinstance(a.annotation, Type) else TYPE_DYN for a in new_args),
        ret=ret_ty,
    )
    ctx.func_types[fn.name] = ft
    # Also expose the function by name so lookups inside the body find
    # the binding.  Outer scope (module or enclosing def) keeps a copy
    # through ``scope.update`` below.
    param_scope.define(fn.name, ft)
    scope.update(fn.name, ft)

    new_body = tuple(_infer_stmt(ctx, param_scope, s) for s in fn.body)

    # Type-check ``return`` against ``ret_ty``.
    if not isinstance(ret_ty, DynType):
        _check_returns(new_body, ret_ty)

    return replace(
        fn,
        args=tuple(new_args),
        return_ty=ret_ty,
        body=new_body,
    )


def _container_method_return_type(
    recv_ty: Type, method: str,
) -> Optional[Type]:
    """Known return types for the typed-container fast-path methods.

    Keeps ``method`` result typed so that chained calls like
    ``s.strip().upper()`` stay on the pcc-native dispatch path rather
    than falling through to CPython — which would break the libpython
    free-standing guarantee.
    """
    if isinstance(recv_ty, StrType):
        if method == "count":
            return IntType(name="int")
        if method in ("isdigit", "isalpha", "isspace", "isalnum"):
            return BoolType(name="bool")
        if method in (
            "upper", "lower", "strip", "lstrip", "rstrip",
            "replace", "join", "split", "splitlines",
        ):
            if method in ("split", "splitlines"):
                return ListType(name="list", elem=StrType(name="str"))
            if method == "join":
                return StrType(name="str")
            return StrType(name="str")
        if method in ("startswith", "endswith"):
            return BoolType(name="bool")
        if method == "find":
            return IntType(name="int")
    if isinstance(recv_ty, ListType):
        if method == "pop":
            return recv_ty.elem
        if method == "index":
            return IntType(name="int")
        if method in ("append", "extend", "insert", "remove"):
            return NoneType(name="None")
    if isinstance(recv_ty, DictType):
        if method == "get":
            return recv_ty.value
        if method == "keys":
            return ListType(name="list", elem=recv_ty.key)
        if method == "values":
            return ListType(name="list", elem=recv_ty.value)
        if method == "items":
            return ListType(
                name="list",
                elem=TupleType(
                    name="tuple", elems=(recv_ty.key, recv_ty.value),
                ),
            )
    return None


def _is_assignable(declared: Type, got: Type) -> bool:
    """Return True if ``got`` is assignable to a slot declared ``declared``.

    Beyond ``type_eq``, this permits ``DynType`` to flow into any
    declared slot (forward subsumption, mirroring the callsite checks
    elsewhere) and recursively descends into ``TupleType`` / ``ListType``
    / ``DictType`` so that e.g. ``tuple[dyn, bool]`` satisfies
    ``tuple[str, bool]`` — which happens whenever a local was typed
    dynamically by a side assignment but the annotation is concrete.
    """
    if isinstance(got, DynType) or isinstance(declared, DynType):
        return True
    if type_eq(declared, got):
        return True
    if isinstance(got, NoneType) and not isinstance(
        declared, (IntType, FloatType, BoolType)
    ):
        return True
    if isinstance(declared, ClassType):
        if _runtime_type_object_assignable(declared, got):
            return True
        if _builtin_container_class_assignable(declared, got):
            return True
        if isinstance(got, ClassType):
            return _class_type_assignable(declared, got)
    if isinstance(declared, TupleType) and isinstance(got, TupleType):
        # ``tuple[T, ...]`` — variadic declared tuple matches any
        # tuple whose every element is assignable to ``T``.
        if declared.name == "tuple_variadic" and declared.elems:
            elem_ty = declared.elems[0]
            return all(_is_assignable(elem_ty, g) for g in got.elems)
        # A variadic-got flowing into a fixed-arity declared form is
        # treated conservatively: assignable when the got's element
        # type subsumes every declared slot.
        if got.name == "tuple_variadic" and got.elems:
            got_elem = got.elems[0]
            return all(_is_assignable(d, got_elem) for d in declared.elems)
        if len(declared.elems) != len(got.elems):
            return False
        i = 0
        while i < len(declared.elems):
            if not _is_assignable(declared.elems[i], got.elems[i]):
                return False
            i += 1
        return True
    if isinstance(declared, ListType) and isinstance(got, ListType):
        return _is_assignable(declared.elem, got.elem)
    if isinstance(declared, DictType) and isinstance(got, DictType):
        return (
            _is_assignable(declared.key, got.key)
            and _is_assignable(declared.value, got.value)
        )
    return False


def _runtime_type_object_assignable(declared: ClassType, got: Type) -> bool:
    """Compatibility for pcc's meta-level Type objects.

    The frontend type lattice uses ``IntType`` / ``NoneType`` instances
    both to describe runtime Python values and as the objects returned by
    helpers such as ``parse_annotation() -> Type``. When annotations like
    ``Type`` / ``NoneType`` are preserved as ``ClassType`` refs, those
    existing meta values must remain assignable.
    """
    if declared.name == "Type" and isinstance(got, Type):
        return True
    if declared.name == "IntType" and isinstance(got, IntType):
        return True
    if declared.name == "FloatType" and isinstance(got, FloatType):
        return True
    if declared.name == "BoolType" and isinstance(got, BoolType):
        return True
    if declared.name == "NoneType" and isinstance(got, NoneType):
        return True
    if declared.name == "StrType" and isinstance(got, StrType):
        return True
    if declared.name == "ListType" and isinstance(got, ListType):
        return True
    if declared.name == "DictType" and isinstance(got, DictType):
        return True
    if declared.name == "TupleType" and isinstance(got, TupleType):
        return True
    if declared.name == "FuncType" and isinstance(got, FuncType):
        return True
    if declared.name == "ClassType" and isinstance(got, ClassType):
        return True
    if declared.name == "DynType" and isinstance(got, DynType):
        return True
    return False


def _builtin_container_class_assignable(declared: ClassType, got: Type) -> bool:
    if declared.name == "list" and isinstance(got, ListType):
        return True
    if declared.name == "dict" and isinstance(got, DictType):
        return True
    if declared.name == "tuple" and isinstance(got, TupleType):
        return True
    return False


def _class_type_is_unresolved_shell(ty: ClassType) -> bool:
    return not ty.module and not ty.fields and not ty.bases


def _class_type_assignable(declared: ClassType, got: ClassType) -> bool:
    if declared.name == got.name and (
        declared.module == got.module or not declared.module or not got.module
    ):
        return True
    # An annotation imported from a module whose schema is not available
    # (the per-module self-compile probe runs without external_exports)
    # must behave like the old DynType path. Preserve strict subclass
    # checks only once at least one side carries real schema/module data.
    if (
        _class_type_is_unresolved_shell(declared)
        or _class_type_is_unresolved_shell(got)
    ):
        return True
    for base in got.bases:
        if _class_type_assignable(declared, base):
            return True
    return False


def _check_returns(body: tuple[Stmt, ...], ret_ty: Type) -> None:
    for s in body:
        if isinstance(s, Return):
            if s.value is None:
                if not isinstance(ret_ty, NoneType):
                    raise PyFrontendError(
                        span=s.span,
                        message=f"function annotated to return {ret_ty.name!r} but returns no value",
                        hint=f"return a value of type {ret_ty.name!r}",
                    )
            else:
                vty = s.value.ty
                if isinstance(vty, DynType) or isinstance(ret_ty, DynType):
                    continue
                if type_eq(ret_ty, vty):
                    continue
                if isinstance(ret_ty, FloatType) and isinstance(vty, (IntType, BoolType)):
                    continue
                if isinstance(ret_ty, IntType) and isinstance(vty, BoolType):
                    continue
                if _is_assignable(ret_ty, vty):
                    continue
                raise PyFrontendError(
                    span=s.span,
                    message=f"return type mismatch: expected {ret_ty.name!r}, got {vty.name!r}",
                    hint="change the annotation or convert the value",
                )
        elif isinstance(s, If):
            _check_returns(s.body, ret_ty)
            _check_returns(s.else_body, ret_ty)
        elif isinstance(s, While):
            _check_returns(s.body, ret_ty)
            _check_returns(s.else_body, ret_ty)
        elif isinstance(s, For):
            _check_returns(s.body, ret_ty)
            _check_returns(s.else_body, ret_ty)
        elif isinstance(s, Try):
            _check_returns(s.body, ret_ty)
            for h in s.handlers:
                _check_returns(h.body, ret_ty)
            _check_returns(s.else_body, ret_ty)
            _check_returns(s.finally_body, ret_ty)
        elif isinstance(s, With):
            _check_returns(s.body, ret_ty)


# ---------------------------------------------------------------------------
# Module entrypoint
# ---------------------------------------------------------------------------


def _prepopulate_module_scope(ctx: _InferCtx, module: Module) -> None:
    """Seed module scope with imports, class schemas, and function signatures.

    This lets forward references and mutual recursion work: every ``def``
    at module scope is registered with its annotated (or ``dyn``) signature
    first, and every local class name is registered as a schema-bearing
    ``ClassType`` before function bodies resolve annotations.
    """
    if ctx.external_exports:
        _preload_unique_external_classes(ctx)

    for stmt in module.body:
        if not isinstance(stmt, ImportFrom) or not ctx.external_exports:
            continue
        resolved = _resolve_relative_module(
            stmt.module,
            stmt.level,
            ctx.module.name,
            stmt.span.file,
        )
        _bind_external_import_exports(ctx, ctx.globals, resolved, stmt.names)

    for stmt in module.body:
        if isinstance(stmt, ClassDef):
            cls_ty = _make_class_type(stmt.name, module.name or "", (), ())
            ctx.register_class_type(stmt.name, cls_ty)
            ctx.globals.define(stmt.name, cls_ty)

    for stmt in module.body:
        if isinstance(stmt, ClassDef):
            bases = _class_bases_from_def(ctx, stmt)
            fields = _class_fields_from_def(ctx, stmt)
            cls_ty = _make_class_type(stmt.name, module.name or "", fields, bases)
            ctx.register_class_type(stmt.name, cls_ty)
            ctx.globals.define(stmt.name, cls_ty)

    for stmt in module.body:
        if isinstance(stmt, FuncDef):
            params = tuple(ctx.resolve_annotation(a.annotation) for a in stmt.args)
            ret = ctx.resolve_annotation(stmt.return_ty)
            ft = _make_func_type(params, ret)
            ctx.func_types[stmt.name] = ft
            ctx.globals.define(stmt.name, ft)


def _resolve_relative_module(
    module: Optional[str], level: int, current: Optional[str],
    current_file: Optional[str] = None,
) -> str:
    """Mirror of ``layer1._resolve_relative_import``. Needed at
    inference time so cross-module exports lookup uses the
    absolute dotted name."""
    level = level or 0
    if level == 0:
        return module or ""
    cur = current or ""
    parts = cur.split(".") if cur else []
    current_file = (current_file or "").replace("\\", "/")
    is_package_init = (
        current_file == "__init__.py"
        or current_file.endswith("/__init__.py")
    )
    package_parts = parts if is_package_init else parts[:-1]
    up = level - 1
    if up > len(package_parts):
        return module or ""
    base_parts = package_parts[: len(package_parts) - up]
    if module:
        return ".".join(base_parts + [module])
    return ".".join(base_parts)


def _annotation_to_type(value) -> Type:
    """Normalize an annotation field (already-resolved Type or raw
    Expr) into a Type. Mirrors ``_InferCtx.resolve_annotation``."""
    if value is None:
        return TYPE_DYN
    if isinstance(value, Type):
        return value
    if isinstance(value, Expr):
        return parse_annotation(value)
    return TYPE_DYN


def infer_module(m: Module, *, external_exports=None) -> Module:
    """Run type inference over an entire module and return a new ``Module``.

    The returned module has every expression's ``ty`` filled in with the
    best type Phase 1 could determine (``DynType`` where we cannot).
    Annotations on ``Arg``/``Assign``/``FuncDef`` that were parsed as
    surface ``Expr`` nodes are replaced with resolved ``Type`` instances.

    ``external_exports`` is the multi-file compile pre-pass table
    ``{dotted_module: {name: export_info}}`` — when supplied,
    ``from .sibling import fn`` bindings are typed from the
    sibling's exported ``FuncType``/``ClassType`` instead of
    falling through to ``DynType`` at call sites.
    """

    ctx = _InferCtx(m, external_exports=external_exports)
    _prepopulate_module_scope(ctx, m)
    new_body = tuple(_infer_stmt(ctx, ctx.globals, s) for s in m.body)
    return replace(m, body=new_body)


__all__ = ["infer_module", "PyFrontendError"]
