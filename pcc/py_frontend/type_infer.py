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

from .codegen.host_contract import (
    L1_CODEGEN_HOST_ATTRS,
    L1_CODEGEN_HOST_METHODS,
    PROBE_POLICY_CONTEXTUAL_MIXIN,
    per_module_probe_policy,
)
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
    ByteArrayType,
    BytesLit,
    BytesType,
    Call,
    ClassDef,
    ClassType,
    ComplexLit,
    ComplexType,
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
    MemoryViewType,
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
TYPE_COMPLEX: ComplexType = ComplexType(name="complex")
TYPE_BOOL: BoolType = BoolType(name="bool")
TYPE_NONE: NoneType = NoneType(name="None")
TYPE_STR: StrType = StrType(name="str")
TYPE_BYTES: BytesType = BytesType(name="bytes")
TYPE_BYTEARRAY: ByteArrayType = ByteArrayType(name="bytearray")
TYPE_MEMORYVIEW: MemoryViewType = MemoryViewType(name="memoryview")
TYPE_DYN: DynType = DynType(name="dyn")

_CLASS_LOWERING_HOST_METHODS = (
    "_find_method_def",
    "_load_bases_array",
    "declare_class",
    "declare_extern_class",
    "emit_class_attr_load",
    "emit_class_attr_store",
    "emit_instantiate",
    "emit_isinstance",
    "emit_methods",
    "emit_module_init",
    "emit_self_attr_load",
    "emit_self_attr_store",
    "emit_super_lookup",
    "lookup_class_attr",
    "lookup_field_index",
)


def _make_list_type(elem: Type) -> ListType:
    return ListType(name="list", elem=elem)


def _make_dict_type(key: Type, value: Type) -> DictType:
    return DictType(name="dict", key=key, value=value)


def _make_tuple_type(name: str, elems: tuple[Type, ...]) -> TupleType:
    return TupleType(name=name, elems=elems)


def _annotation_or_none(node):
    try:
        return node.annotation
    except AttributeError:
        return None


def _tuple_elem_type(ty: TupleType) -> Type:
    if ty.elems:
        acc = ty.elems[0]
        for elem in ty.elems[1:]:
            acc = common_type(acc, elem)
        return acc
    return TYPE_DYN


def _list_type_elem(ty: Type) -> Optional[Type]:
    if isinstance(ty, ListType):
        return ty.elem
    try:
        name = ty.name
    except AttributeError:
        return None
    if name != "list":
        return None
    try:
        return ty.elem
    except AttributeError:
        return None


def _dict_type_parts(ty: Type) -> Optional[tuple[Type, Type]]:
    if isinstance(ty, DictType):
        return (ty.key, ty.value)
    try:
        name = ty.name
    except AttributeError:
        return None
    if name != "dict":
        return None
    try:
        key = ty.key
        value = ty.value
    except AttributeError:
        return None
    return (key, value)


def _tuple_from_iterable_type(ty: Type) -> TupleType:
    if isinstance(ty, TupleType):
        return ty
    list_elem = _list_type_elem(ty)
    if list_elem is not None:
        return TupleType(name="tuple_variadic", elems=(list_elem,))
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
    return FuncType(name="callable", params=params, ret=ret)


def _is_none_type(ty: Type) -> bool:
    if isinstance(ty, NoneType):
        return True
    return ty.name == "None" or ty.name == "NoneType"


def _make_class_type(
    name: str,
    module: str,
    fields: tuple[tuple[str, Type], ...],
    bases: tuple[ClassType, ...],
    properties: tuple[tuple[str, Type], ...] = (),
    valueclass: bool = False,
) -> ClassType:
    return ClassType(
        name,
        module,
        fields,
        bases,
        properties,
        valueclass,
    )


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
    "type": FuncType(
        name="callable",
        params=(TYPE_DYN,),
        ret=ClassType(
            "type", "", (("__name__", TYPE_STR),), ()
        ),
    ),
    "set": FuncType(
        name="callable",
        params=(TYPE_DYN,),
        ret=DynType(name="set"),
    ),
    "frozenset": FuncType(
        name="callable",
        params=(TYPE_DYN,),
        ret=DynType(name="set"),
    ),
    "chr": FuncType(name="callable", params=(TYPE_INT,), ret=TYPE_STR),
    "abs": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_DYN),
    "min": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_DYN),
    "max": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_DYN),
    "sum": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_DYN),
    "__await__": FuncType(name="callable", params=(TYPE_DYN,), ret=TYPE_DYN),
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
    "stat_kind": TYPE_INT,
    "stat_mtime": TYPE_FLOAT,
    "target_sys_platform": TYPE_DYN,
    "target_platform_machine": TYPE_DYN,
    "call_ptr1": TYPE_DYN,
    "call_ptr2": TYPE_DYN,
}


# ---------------------------------------------------------------------------
# Scope plumbing
# ---------------------------------------------------------------------------


class _Scope:
    """Lexical scope chain for type lookup.

    Scopes are walked in order: local → enclosing params → module
    globals → builtins (builtins live as a fallback in ``_lookup``).
    """

    __slots__ = ("bindings", "parent")

    def __init__(self, parent: Optional["_Scope"] = None) -> None:
        self.bindings: dict[str, Type] = {}
        if parent:
            self.parent: Optional[_Scope] = parent
        else:
            self.parent = None

    def _find_local(self, name: str) -> int:
        if self.bindings.get(name) is not None:
            return 0
        return -1

    def define(self, name: str, ty: Type) -> None:
        self.bindings[name] = ty

    def update(self, name: str, ty: Type) -> None:
        """Update or insert; used for assignment re-typing."""
        self.define(name, ty)

    def lookup_local(self, name: str) -> Optional[Type]:
        return self.bindings.get(name)

    def lookup(self, name: str) -> Optional[Type]:
        scope: Optional[_Scope] = self
        while scope:
            found = scope.bindings.get(name)
            if found is not None:
                return found
            scope = scope.parent
        return None


# ---------------------------------------------------------------------------
# Inference context
# ---------------------------------------------------------------------------


class _InferCtx:
    """Shared state while inferring one module."""

    module: Module
    module_name: str
    globals: _Scope
    func_types: dict[str, FuncType]
    external_exports: dict
    derived_class_map: dict
    contextual_host_params: dict
    dataclasses_replace_aliases: set[str]
    class_types: dict[str, ClassType]
    _l1_codegen_host_type: Optional[ClassType]

    def __init__(
        self,
        module: Module,
        external_exports: Optional[dict] = None,
        derived_class_map: Optional[dict] = None,
        contextual_host_params: Optional[dict] = None,
    ) -> None:
        self.module = module
        try:
            self.module_name = module.name or ""
        except AttributeError:
            self.module_name = ""
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
        # Multi-file compile: ``{base_class_name: (derived_module,
        # derived_class_name)}``. When a class C in this module is the
        # sole base of some derived class D anywhere in the closure,
        # methods on C are inferred with ``self_ty=D`` so cross-module
        # mixin patterns (``class NativeXxxMixin: def m(self):
        # self.builder. ...``) resolve fields against D's full schema
        # instead of C's empty one. Built once in the multi-file
        # pipeline and shared across every module's _InferCtx.
        self.derived_class_map = derived_class_map or {}
        self.contextual_host_params = contextual_host_params or {}
        # ``from dataclasses import replace as ...`` is common across
        # the frontend passes. Track the local aliases explicitly so
        # call-result inference can preserve the first argument's type
        # instead of collapsing the whole expression to DynType.
        self.dataclasses_replace_aliases: set[str] = set()
        self.class_types: dict[str, ClassType] = {}
        self._l1_codegen_host_type: Optional[ClassType] = None

    # -- helpers -----------------------------------------------------------

    def register_class_type(self, local_name: str, ty: ClassType) -> None:
        """Register a schema-bearing class type under local and stable names."""
        self.class_types[local_name] = ty
        self.class_types[ty.name] = ty
        ty_module = _class_type_module(ty)
        if ty_module:
            self.class_types[f"{ty_module}.{ty.name}"] = ty

    def l1_codegen_host_type(self) -> ClassType:
        """Synthetic type for helper functions that receive L1CodeGen host.

        This is intentionally opt-in via ``contextual_host_params``. It is
        the type-inference half of contextual host extraction: helpers can
        see ``host._fresh`` / ``host.builder`` as known fields instead of
        collapsing to ``DynType`` immediately. Codegen direct host calls are
        a separate step.
        """
        cached = self._l1_codegen_host_type
        if cached is not None:
            return cached

        class_lowering_ty = _make_class_type(
            "ClassLowering",
            "pcc.py_frontend.codegen.class_gen",
            (),
            (),
        )
        method_returns = {
            "_fresh": TYPE_STR,
            "_ir_scaffold_enabled": TYPE_BOOL,
            "_class_is_subclass": TYPE_BOOL,
        }
        attr_types = {
            "class_lowering": class_lowering_ty,
        }
        fields: list[tuple[str, Type]] = []
        for attr_name in L1_CODEGEN_HOST_ATTRS:
            fields.append((attr_name, attr_types.get(attr_name, TYPE_DYN)))
        for method_name in L1_CODEGEN_HOST_METHODS:
            fields.append(
                (
                    method_name,
                    _make_func_type(
                        (TYPE_DYN,), method_returns.get(method_name, TYPE_DYN)
                    ),
                )
            )
        host_ty = _make_class_type(
            "L1CodeGen",
            "pcc.py_frontend.codegen.layer1",
            tuple(fields),
            (),
        )
        self._l1_codegen_host_type = host_ty
        self.register_class_type("L1CodeGen", host_ty)
        return host_ty

    def resolve_annotation(self, ann: object) -> Type:
        """Normalise an ``annotation`` field into a ``Type``.

        Parser implementations may attach either a ``Type`` directly (if
        they already resolved the annotation) or an ``Expr`` describing
        the raw annotation AST.  We accept both.
        """
        if ann is None:
            return TYPE_DYN
        if isinstance(ann, Type):
            # During bootstrap, parser variants can sometimes emit the raw
            # ``Type`` base object for annotations that should normally be
            # ``IntType``/``ClassType``/etc. Treat the bare base as
            # equivalent to unknown annotation and keep the check permissive.
            if ann.__class__ is Type:
                return TYPE_DYN
            # Some bootstrap snapshots materialize a shadow ``Type`` class
            # under a different module and pass it through as an annotation
            # object with name ``Type``. That class name collides with the
            # semantic base type and would otherwise fail return-checking.
            if ann.__class__.__name__ == "Type" and ann.name == "Type":
                return TYPE_DYN
            resolved = self.resolve_type_refs(ann)
            # If the parser produced an unnamed type shim, treat it as
            # dynamic rather than a hard annotation failure.
            if isinstance(resolved, Type) and not resolved.name:
                return TYPE_DYN
            return resolved
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
            ty_module = _class_type_module(ty)
            ty_fields = _class_type_fields(ty)
            ty_bases = _class_type_bases(ty)
            if not ty_module and not ty_fields and not ty_bases:
                if (
                    ty.name == "L1CodeGen"
                    and _ctx_module_name(self) == "pcc.py_frontend.codegen.class_gen"
                ):
                    return self.l1_codegen_host_type()
                if ty.name == "list":
                    return _make_list_type(TYPE_DYN)
                if ty.name == "dict":
                    return _make_dict_type(TYPE_DYN, TYPE_DYN)
                if ty.name == "tuple":
                    return _make_tuple_type("tuple_variadic", (TYPE_DYN,))
            if ty_module:
                found = self.class_types.get(f"{ty_module}.{ty.name}")
                if found is not None:
                    return found
            found = self.class_types.get(ty.name)
            if found is not None:
                return found
            fields = tuple(
                (name, self.resolve_type_refs(field_ty)) for name, field_ty in ty_fields
            )
            bases = tuple(self.resolve_type_refs(base) for base in ty_bases)
            if fields == ty_fields and bases == ty_bases:
                return ty
            return _make_class_type(ty.name, ty_module, fields, bases)
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


def _name_ident(node: object) -> Optional[str]:
    """Return identifier from a Name node across AST snapshot variants."""
    ident = getattr(node, "ident", None)
    if ident is None:
        ident = getattr(node, "id", None)
    return ident


def _ctx_module_name(ctx: _InferCtx) -> str:
    try:
        return ctx.module_name or ""
    except AttributeError:
        pass
    try:
        return ctx.module.name or ""
    except AttributeError:
        return ""


def _infer_expr(ctx: _InferCtx, scope: _Scope, expr: Expr) -> Expr:
    # Literals -----------------------------------------------------------
    if isinstance(expr, IntLit):
        return _with_ty(expr, TYPE_INT)
    if isinstance(expr, FloatLit):
        return _with_ty(expr, TYPE_FLOAT)
    if isinstance(expr, ComplexLit):
        return _with_ty(expr, TYPE_COMPLEX)
    if isinstance(expr, BoolLit):
        return _with_ty(expr, TYPE_BOOL)
    if isinstance(expr, NoneLit):
        return _with_ty(expr, TYPE_NONE)
    if isinstance(expr, StrLit):
        return _with_ty(expr, TYPE_STR)
    if isinstance(expr, BytesLit):
        return _with_ty(expr, TYPE_BYTES)

    # Name lookup --------------------------------------------------------
    if isinstance(expr, Name):
        ident = _name_ident(expr)
        if ident is None:
            _raise_frontend_error(
                expr.span,
                "internal name node missing identifier",
                "upgrade the parser/frontend AST to use ident field",
            )
        scope_cur: Optional[_Scope] = scope
        while scope_cur:
            found = scope_cur.bindings.get(ident)
            if found is not None:
                return _with_ty(expr, found)
            scope_cur = scope_cur.parent
        builtin = _BUILTIN_TYPES.get(ident)
        if builtin is not None:
            return _with_ty(expr, builtin)
        return _with_ty(expr, TYPE_DYN)

    # Binary arithmetic --------------------------------------------------
    if isinstance(expr, BinOp):
        lhs = _infer_expr(ctx, scope, expr.lhs)
        rhs = _infer_expr(ctx, scope, expr.rhs)
        op = expr.op
        ty = _binop_result(op, lhs.ty, rhs.ty, expr.span)
        return replace(expr, lhs=lhs, rhs=rhs, ty=ty)

    # Unary --------------------------------------------------------------
    if isinstance(expr, UnaryOp):
        operand = _infer_expr(ctx, scope, expr.operand)
        op = expr.op
        if op == "not":
            ty: Type = TYPE_BOOL
        elif op == "~":
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
        op = expr.op
        if op in ("is", "is not") and (
            _is_valueclass_type(lhs.ty) or _is_valueclass_type(rhs.ty)
        ):
            _raise_frontend_error(
                expr.span,
                "identity comparison is not supported for valueclass payloads in strict mode",
                "compare valueclass fields with == or explicitly box before observing identity",
            )
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
        new_kwargs = tuple((k, _infer_expr(ctx, scope, v)) for (k, v) in expr.kwargs)
        # Comprehension sentinels: synthesise a concrete container type
        # so downstream ``for`` loops / subscripts see a real ListType /
        # DictType / SetType instead of plain DynType.
        if isinstance(callee, Name):
            sentinel = _name_ident(callee)
            if sentinel is None:
                _raise_frontend_error(
                    expr.span,
                    "call target missing identifier",
                    "upgrade the parser/frontend AST to use Name-style identifiers",
                )
            if sentinel in (
                "_list_comp",
                "__listcomp__",
                "_gen_comp",
                "__genexpr__",
            ):
                elt = new_args[0] if new_args else None
                elt_ty = ctx.resolve_type_refs(elt.ty) if elt is not None else TYPE_DYN
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=ListType(name="list", elem=elt_ty),
                )
            if sentinel in ("_set_comp", "__setcomp__"):
                # py_ast has no first-class SetType yet. Preserve the
                # container kind with DynType(name="set") so codegen does
                # not route set operators through integer bitwise lowering.
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=DynType(name="set"),
                )
            if sentinel in ("_dict_comp", "__dictcomp__"):
                # Native: first arg is TupleExpr(k, v). CPython-AST:
                # first two args are key/val exprs.
                if sentinel == "_dict_comp" and new_args:
                    kv = new_args[0]
                    if isinstance(kv, TupleExpr) and len(kv.elems) == 2:
                        k_ty = ctx.resolve_type_refs(kv.elems[0].ty)
                        v_ty = ctx.resolve_type_refs(kv.elems[1].ty)
                        return replace(
                            expr,
                            func=callee,
                            args=new_args,
                            kwargs=new_kwargs,
                            ty=DictType(name="dict", key=k_ty, value=v_ty),
                        )
                if sentinel == "__dictcomp__" and len(new_args) >= 2:
                    k_ty = ctx.resolve_type_refs(new_args[0].ty)
                    v_ty = ctx.resolve_type_refs(new_args[1].ty)
                    return replace(
                        expr,
                        func=callee,
                        args=new_args,
                        kwargs=new_kwargs,
                        ty=DictType(name="dict", key=k_ty, value=v_ty),
                    )
        # Known-return-type builtins: ``sum`` returns int, ``len`` returns
        # int, ``min``/``max``/``abs`` return the operand type family.
        if isinstance(callee, Name):
            bname = _name_ident(callee)
            if bname is None:
                _raise_frontend_error(
                    expr.span,
                    "call target missing identifier",
                    "upgrade the parser/frontend AST to use Name-style identifiers",
                )
            if bname in ctx.dataclasses_replace_aliases and len(new_args) == 1:
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=new_args[0].ty,
                )
            if bname == "sum":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=IntType(name="int"),
                )
            if bname == "divmod":
                dm_elem: Type = IntType(name="int")
                if any(isinstance(a.ty, FloatType) for a in new_args[:2]):
                    dm_elem = TYPE_FLOAT
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TupleType(
                        name="tuple",
                        elems=(dm_elem, dm_elem),
                    ),
                )
            if bname == "pow":
                ty: Type = IntType(name="int")
                if any(isinstance(a.ty, FloatType) for a in new_args[:2]):
                    ty = TYPE_FLOAT
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=ty,
                )
            if bname in ("iter", "next"):
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_DYN,
                )
            if bname == "__await__":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_DYN,
                )
            if bname == "type" and len(new_args) == 3:
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_DYN,
                )
            if bname == "int":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=IntType(name="int"),
                )
            if bname == "bool":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_BOOL,
                )
            if bname == "float":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_FLOAT,
                )
            if bname == "complex":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_COMPLEX,
                )
            if bname == "__pcc_format_spec":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_STR,
                )
            if bname in ("setattr", "delattr"):
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=NoneType(name="None"),
                )
            if bname == "str":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_STR,
                )
            if bname == "bytes":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_BYTES,
                )
            if bname == "bytearray":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_BYTEARRAY,
                )
            if bname == "memoryview":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_MEMORYVIEW,
                )
            if bname == "tuple":
                if not new_args:
                    ty = TupleType(name="tuple", elems=())
                else:
                    ty = _tuple_from_iterable_type(ctx.resolve_type_refs(new_args[0].ty))
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=ty,
                )
            if bname in ("sorted", "reversed"):
                elem_ty: Type = TYPE_DYN
                if new_args:
                    src_ty = ctx.resolve_type_refs(new_args[0].ty)
                    if isinstance(src_ty, ListType):
                        elem_ty = ctx.resolve_type_refs(src_ty.elem)
                    elif isinstance(src_ty, TupleType):
                        elem_ty = ctx.resolve_type_refs(_tuple_elem_type(src_ty))
                    elif isinstance(src_ty, DictType):
                        elem_ty = ctx.resolve_type_refs(src_ty.key)
                    elif isinstance(src_ty, StrType):
                        elem_ty = TYPE_STR
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=ListType(name="list", elem=elem_ty),
                )
            if bname == "chr":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_STR,
                )
            if bname in ("any", "all"):
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_BOOL,
                )
            if bname == "issubclass":
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_BOOL,
                )
            if bname == "abs":
                if new_args and isinstance(
                    new_args[0].ty,
                    (IntType, FloatType, BoolType),
                ):
                    return replace(
                        expr,
                        func=callee,
                        args=new_args,
                        kwargs=new_kwargs,
                        ty=new_args[0].ty,
                    )
            if bname in ("repr",):
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=TYPE_STR,
                )
            if bname in ("hash", "id"):
                if bname == "id" and new_args and _is_valueclass_type(new_args[0].ty):
                    _raise_frontend_error(
                        expr.span,
                        "id() is not supported for valueclass payloads in strict mode",
                        "explicitly box the value before observing identity",
                    )
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=IntType(name="int"),
                )
            if bname in ("min", "max") and new_args:
                # Single-arg iterable form: result is the iterable's
                # element type. Multi-arg form: common type of args.
                if len(new_args) == 1:
                    a0_ty = ctx.resolve_type_refs(new_args[0].ty)
                    if isinstance(a0_ty, ListType):
                        acc = ctx.resolve_type_refs(a0_ty.elem)
                    elif isinstance(a0_ty, TupleType) and a0_ty.elems:
                        acc = ctx.resolve_type_refs(a0_ty.elems[0])
                        for e in a0_ty.elems[1:]:
                            acc = common_type(acc, ctx.resolve_type_refs(e))
                    else:
                        acc = IntType(name="int")
                else:
                    acc = ctx.resolve_type_refs(new_args[0].ty)
                    for a in new_args[1:]:
                        acc = common_type(acc, ctx.resolve_type_refs(a.ty))
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
                    ty=acc,
                )

        # Method-call result inference for known typed-container methods
        # so chained calls stay on the pcc-native fast paths without
        # needing an annotation hint at every site.
        if isinstance(callee, Attr):
            recv_ty = ctx.resolve_type_refs(callee.obj.ty)
            method = callee.name
            inferred = _container_method_return_type(recv_ty, method)
            if inferred is not None:
                return replace(
                    expr,
                    func=callee,
                    args=new_args,
                    kwargs=new_kwargs,
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
        obj_ty = ctx.resolve_type_refs(obj.ty)
        # Module-aliased class reference: ``alias.ClassName`` where the
        # ``import alias`` statement registered the module's exports.
        # Returning the ClassType here lets the call-result inference
        # treat ``alias.ClassName(args)`` as a constructor and type the
        # result as a ClassType instance. Without this, stdlib-walked
        # modules (``import pathlib``) bottom out at DynType.
        if isinstance(expr.obj, Name):
            obj_ident = _name_ident(expr.obj)
            if obj_ident is not None:
                qualified = f"{obj_ident}.{expr.name}"
                qty = ctx.class_types.get(qualified)
                if isinstance(qty, ClassType):
                    return replace(expr, obj=obj, ty=qty)
        # Bucket 1: when the receiver is a known class type with
        # field declarations, look up the field's declared type.
        # Walks the MRO (bases) so inherited fields resolve too.
        if isinstance(obj_ty, ClassType):
            # @property declarations take precedence over the generic
            # DynType fallback so downstream typed-method dispatch
            # (e.g. ``c.name.rfind('.')`` where ``name`` is a str-typed
            # property) routes through the native runtime instead of
            # ``py_cpy_getattr``. See
            # docs/investigations/pcc-py-type-infer-property-return-type.md.
            attr_ty = _lookup_class_attr_type(obj_ty, expr.name)
            if attr_ty is not None:
                return replace(expr, obj=obj, ty=ctx.resolve_type_refs(attr_ty))
        if isinstance(obj_ty, ComplexType) and expr.name in ("real", "imag"):
            return replace(expr, obj=obj, ty=TYPE_FLOAT)
        return replace(expr, obj=obj, ty=TYPE_DYN)

    if isinstance(expr, Subscript):
        obj = _infer_expr(ctx, scope, expr.obj)
        idx = _infer_expr(ctx, scope, expr.idx)
        obj_ty = ctx.resolve_type_refs(obj.ty)
        # ``xs[lo:hi]`` — slicing returns a new container of the same
        # kind: list → list[elem], str → str, tuple → tuple (element
        # types preserved but arity unknown, use ``tuple_variadic``).
        if isinstance(idx, Slice):
            if isinstance(obj_ty, ListType):
                ty = obj_ty
            elif isinstance(obj_ty, StrType):
                ty = TYPE_STR
            elif isinstance(obj_ty, (BytesType, ByteArrayType, MemoryViewType)):
                ty = TYPE_BYTES
            elif isinstance(obj_ty, TupleType):
                if obj_ty.elems:
                    ty = TupleType(
                        name="tuple_variadic",
                        elems=(ctx.resolve_type_refs(_tuple_elem_type(obj_ty)),),
                    )
                else:
                    ty = TupleType(name="tuple", elems=())
            else:
                ty = TYPE_DYN
            return replace(expr, obj=obj, idx=idx, ty=ty)
        if isinstance(obj_ty, ListType):
            ty = ctx.resolve_type_refs(obj_ty.elem)
        elif isinstance(obj_ty, TupleType) and obj_ty.elems:
            # Phase 1: if all element types agree, use that; else dyn.
            first = ctx.resolve_type_refs(obj_ty.elems[0])
            ty = (
                first
                if all(type_eq(first, ctx.resolve_type_refs(e)) for e in obj_ty.elems)
                else TYPE_DYN
            )
        elif isinstance(obj_ty, DictType):
            ty = ctx.resolve_type_refs(obj_ty.value)
        elif isinstance(obj_ty, StrType):
            ty = TYPE_STR
        elif isinstance(obj_ty, (BytesType, ByteArrayType, MemoryViewType)):
            ty = TYPE_INT
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
            acc = ctx.resolve_type_refs(new_elems[0].ty)
            for el in new_elems[1:]:
                acc = common_type(acc, ctx.resolve_type_refs(el.ty))
            list_ty = ListType(name="list", elem=acc)
        return replace(expr, elems=new_elems, ty=list_ty)

    if isinstance(expr, TupleExpr):
        new_elems = tuple(_infer_expr(ctx, scope, e) for e in expr.elems)
        tup_ty = TupleType(
            name="tuple",
            elems=tuple(ctx.resolve_type_refs(e.ty) for e in new_elems),
        )
        return replace(expr, elems=new_elems, ty=tup_ty)

    if isinstance(expr, DictExpr):
        new_pairs = tuple(
            (_infer_expr(ctx, scope, k), _infer_expr(ctx, scope, v))
            for (k, v) in expr.pairs
        )
        if not new_pairs:
            dict_ty: Type = DictType(name="dict", key=TYPE_DYN, value=TYPE_DYN)
        else:
            key_ty = ctx.resolve_type_refs(new_pairs[0][0].ty)
            val_ty = ctx.resolve_type_refs(new_pairs[0][1].ty)
            for k, v in new_pairs[1:]:
                key_ty = common_type(key_ty, ctx.resolve_type_refs(k.ty))
                val_ty = common_type(val_ty, ctx.resolve_type_refs(v.ty))
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
        param_types = tuple(ctx.resolve_annotation(p.annotation) for p in expr.params)
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
        if isinstance(a, ComplexType) or isinstance(b, ComplexType):
            return TYPE_COMPLEX
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
                _raise_frontend_error(
                    span,
                    f"unsupported operand type(s) for {op}: 'str' and numeric",
                    "use str() or explicit conversion",
                )
        if isinstance(b, StrType) and is_numeric(a):
            if op not in ("*", "%"):
                _raise_frontend_error(
                    span,
                    f"unsupported operand type(s) for {op}: numeric and 'str'",
                    "use str() or explicit conversion",
                )

    # True division always returns float for numeric operands.
    if isinstance(a, ComplexType) or isinstance(b, ComplexType):
        return TYPE_COMPLEX
    if op == "/" and is_numeric(a) and is_numeric(b):
        return TYPE_FLOAT

    if (
        op in ("&", "|", "-")
        and isinstance(a, DynType)
        and isinstance(b, DynType)
        and a.name in ("set", "frozenset")
        and b.name in ("set", "frozenset")
    ):
        return DynType(name="set")

    # Bitwise / shift on int-like operands stays int.
    if op in ("&", "|", "^", "<<", ">>"):
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
        callee_ident = _name_ident(callee)
        if callee_ident is None:
            return TYPE_DYN
        ft = ctx.func_types.get(callee_ident)
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
            target_ident = _name_ident(stmt.target)
            if target_ident is not None:
                scope.update(target_ident, new_ty)
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
        elem_ty = ctx.resolve_type_refs(
            _element_type_of(ctx.resolve_type_refs(iter_e.ty))
        )
        target = _infer_expr(ctx, scope, stmt.target)
        if isinstance(stmt.target, Name):
            target_ident = _name_ident(stmt.target)
            if target_ident is not None:
                scope.update(target_ident, elem_ty)
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
        cause = _infer_expr(ctx, scope, stmt.cause) if stmt.cause is not None else None
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
                    as_var_ident = _name_ident(as_var)
                    if as_var_ident is not None:
                        scope.update(as_var_ident, TYPE_DYN)
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
                and _annotation_or_none(s) is not None
                and isinstance(s.value, NoneLit)
                and len(s.targets) == 1
                and isinstance(s.targets[0], Name)
            ):
                new_body.append(s)
                continue
            if isinstance(s, FuncDef):
                self_ty = ctx.class_types.get(stmt.name)
                # Mixin self_ty propagation: if the current class is a
                # base of exactly one derived class anywhere in the
                # multi-file closure, type-infer the mixin's method
                # bodies with ``self_ty=derived_class`` so cross-module
                # ``self.X`` resolves against the derived class's full
                # field schema. The mixin's IR still lives in the mixin
                # module — only the type used for resolution changes.
                derived_entry = ctx.derived_class_map.get(stmt.name)
                if derived_entry is not None:
                    derived_mod, derived_name = derived_entry
                    derived_ty = ctx.class_types.get(f"{derived_mod}.{derived_name}")
                    if derived_ty is None:
                        derived_ty = ctx.class_types.get(derived_name)
                    if derived_ty is not None:
                        self_ty = derived_ty
                if (
                    self_ty is not None
                    and per_module_probe_policy(_ctx_module_name(ctx))
                    == PROBE_POLICY_CONTEXTUAL_MIXIN
                    and self_ty.name != "L1CodeGen"
                ):
                    self_ty = ctx.l1_codegen_host_type()
                new_body.append(_infer_funcdef(ctx, class_scope, s, self_ty=self_ty))
                continue
            new_body.append(_infer_stmt(ctx, class_scope, s))
        return replace(stmt, body=tuple(new_body))

    # Import/Global/Nonlocal/Pass/Break/Continue — mostly pass through.
    if isinstance(stmt, Import):
        for mod_name, as_name in stmt.names:
            if mod_name == "math":
                local_name = as_name or mod_name.split(".", 1)[0]
                scope.update(local_name, DynType(name="module:math"))
                continue
            # Cross-module class registration: when ``mod_name`` was
            # supplied through the multi-file pre-pass (or pulled in by
            # the recursive_stdlib walker), eagerly bind each exported
            # class under the qualified key ``<alias>.<ClassName>`` so
            # ``alias.Class(args)`` types as an instance constructor.
            # Without this, ``import pathlib; pathlib.PurePath(...)``
            # bottoms out at ``DynType`` and downstream property
            # accesses (``p.name``) fall back to ``py_obj_getattr``,
            # silently returning the property descriptor instead of
            # invoking the getter. See investigation
            # pcc-py-type-infer-property-return-type.md.
            module_exports = ctx.external_exports.get(mod_name)
            if not module_exports:
                continue
            local_name = as_name or mod_name.split(".", 1)[0]
            memo: dict[tuple[str, str], ClassType] = {}
            for info in module_exports.values():
                if not isinstance(info, dict) or info.get("kind") != "class":
                    continue
                cls_ty = _class_type_from_export(
                    ctx,
                    mod_name,
                    info,
                    module_exports,
                    memo,
                )
                ctx.class_types[f"{local_name}.{cls_ty.name}"] = cls_ty
        return stmt

    # For ImportFrom against a registered native sibling module we
    # bind the imported names in the current scope to the remote
    # function / class type so downstream call-site and attribute
    # inference picks the concrete type rather than DynType.
    if isinstance(stmt, ImportFrom):
        resolved = _resolve_relative_module(
            _import_from_module_or_empty(stmt),
            _import_from_level_or_zero(stmt),
            _ctx_module_name(ctx),
            stmt.span.file,
        )
        if resolved == "dataclasses":
            for attr_name, as_name in stmt.names:
                if attr_name != "replace":
                    continue
                local_name = as_name or attr_name
                ft = FuncType(
                    name="callable",
                    params=(TYPE_DYN,),
                    ret=TYPE_DYN,
                )
                ctx.dataclasses_replace_aliases.add(local_name)
                scope.update(local_name, ft)
                ctx.func_types[local_name] = ft
        if resolved == "math":
            for attr_name, as_name in stmt.names:
                if attr_name not in ("floor", "sqrt"):
                    continue
                local_name = as_name or attr_name
                ret_ty: Type = TYPE_INT if attr_name == "floor" else TYPE_FLOAT
                ft = FuncType(
                    name="callable",
                    params=(TYPE_DYN,),
                    ret=ret_ty,
                )
                scope.update(local_name, ft)
                ctx.func_types[local_name] = ft
        if resolved == "asyncio":
            for attr_name, as_name in stmt.names:
                if attr_name not in ("run", "sleep"):
                    continue
                local_name = as_name or attr_name
                ft = FuncType(
                    name="callable",
                    params=(TYPE_DYN,),
                    ret=TYPE_DYN,
                )
                scope.update(local_name, ft)
                ctx.func_types[local_name] = ft
        if resolved == "pcc.unsafe":
            for attr_name, as_name in stmt.names:
                ret_ty = _UNSAFE_INTRINSIC_RETURN_TYPES.get(attr_name)
                if ret_ty is None:
                    continue
                local_name = as_name or attr_name
                ft = FuncType(
                    name="callable",
                    params=(TYPE_DYN,),
                    ret=ret_ty,
                )
                scope.update(local_name, ft)
                ctx.func_types[local_name] = ft
        _bind_ir_compat_module_alias(ctx, scope, resolved, stmt.names)
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
    ctx: _InferCtx,
    expr: Expr,
) -> Optional[Type]:
    """Resolve ``isinstance``'s second argument when it is one type.

    Tuple forms describe a union. The frontend has no union type yet, so
    we deliberately leave those unnarrowed instead of guessing.
    """
    if isinstance(expr, Name):
        expr_ident = _name_ident(expr)
        if expr_ident is not None and expr_ident.startswith("_") and (
            _ctx_module_name(ctx).startswith("pcc.py_frontend.")
        ):
            py_ast_ty = ctx.class_types.get(expr_ident[1:])
            if (
                isinstance(py_ast_ty, ClassType)
                and _class_type_module(py_ast_ty) == "pcc.py_frontend.py_ast"
            ):
                return py_ast_ty
        if expr_ident == "tuple":
            return TupleType(name="tuple_variadic", elems=(TYPE_DYN,))
        if expr_ident == "list":
            return ListType(name="list", elem=TYPE_DYN)
        if expr_ident == "dict":
            return DictType(name="dict", key=TYPE_DYN, value=TYPE_DYN)
        if expr_ident == "str":
            return TYPE_STR
        if expr_ident == "int":
            return TYPE_INT
        if expr_ident == "bool":
            return TYPE_BOOL
        if expr_ident == "float":
            return TYPE_FLOAT
        if expr_ident is not None and ctx.external_exports:
            module_exports = ctx.external_exports.get("pcc.py_frontend.py_ast")
            if module_exports is not None:
                ref_info = module_exports.get(expr_ident)
                if isinstance(ref_info, dict) and ref_info.get("kind") == "class":
                    return _class_type_from_export(
                        ctx,
                        "pcc.py_frontend.py_ast",
                        ref_info,
                        module_exports,
                        {},
                    )
        ty = ctx.resolve_type_refs(expr.ty)
        if isinstance(ty, ClassType):
            return ty
        found = ctx.class_types.get(expr_ident) if expr_ident is not None else None
        if found is not None:
            return found
    return None


def _narrow_scope_for_cond(ctx: _InferCtx, scope: _Scope, cond: Expr) -> _Scope:
    if isinstance(cond, BoolExpr) and cond.op == "and":
        narrowed_left = _narrow_scope_for_cond(ctx, scope, cond.left)
        return _narrow_scope_for_cond(ctx, narrowed_left, cond.right)
    return _narrow_scope_for_isinstance(ctx, scope, cond)


def _narrow_scope_for_isinstance(
    ctx: _InferCtx,
    scope: _Scope,
    cond: Expr,
) -> _Scope:
    if not (
        isinstance(cond, Call)
        and isinstance(cond.func, Name)
        and _name_ident(cond.func) == "isinstance"
        and len(cond.args) == 2
        and not cond.kwargs
        and isinstance(cond.args[0], Name)
    ):
        return scope
    candidate = _type_from_isinstance_arg(ctx, cond.args[1])
    if candidate is None:
        return scope
    var_name = _name_ident(cond.args[0])
    if var_name is None:
        return scope
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


def _import_from_module_or_empty(stmt) -> str:
    try:
        module = stmt.module
    except AttributeError:
        return ""
    return module or ""


def _import_from_level_or_zero(stmt) -> int:
    try:
        level = stmt.level
    except AttributeError:
        return 0
    return level or 0


def _class_type_name(ty) -> str:
    try:
        return ty.name
    except AttributeError:
        return ""


def _class_type_module(ty) -> str:
    try:
        module = ty.module
    except AttributeError:
        return ""
    return module or ""


def _class_type_fields(ty):
    try:
        fields = ty.fields
    except AttributeError:
        return ()
    return fields or ()


def _class_type_bases(ty):
    try:
        bases = ty.bases
    except AttributeError:
        return ()
    return bases or ()


def _class_key_seen(
    seen_modules: list[str],
    seen_names: list[str],
    module: str,
    name: str,
) -> bool:
    i = 0
    while i < len(seen_names):
        if seen_names[i] == name and seen_modules[i] == module:
            return True
        i += 1
    return False


def _class_mro_list(cls_ty: ClassType) -> list[ClassType]:
    """Return ``cls_ty`` and its bases breadth-first, guarding cycles.

    This is intentionally list-based rather than a generator plus ``set``:
    self-hosted pcc runs this in a very hot type-inference path, and the
    generator/set shape allocates heavily under the pcc-Python runtime.
    """
    seen_modules: list[str] = []
    seen_names: list[str] = []
    queue: list[ClassType] = [cls_ty]
    out: list[ClassType] = []
    idx = 0
    while idx < len(queue):
        cur = queue[idx]
        idx += 1
        cur_name = _class_type_name(cur)
        cur_module = _class_type_module(cur)
        if _class_key_seen(seen_modules, seen_names, cur_module, cur_name):
            continue
        seen_modules.append(cur_module)
        seen_names.append(cur_name)
        out.append(cur)
        bases = _class_type_bases(cur)
        i = 0
        while i < len(bases):
            queue.append(bases[i])
            i += 1
    return out


def _iter_class_mro(cls_ty: ClassType):
    for cur in _class_mro_list(cls_ty):
        yield cur


def _lookup_class_attr_type(cls_ty: ClassType, attr_name: str) -> Optional[Type]:
    mro = _class_mro_list(cls_ty)
    i = 0
    while i < len(mro):
        cur = mro[i]
        for pname, pty in getattr(cur, "properties", ()):
            if pname == attr_name:
                return pty
        i += 1

    i = 0
    while i < len(mro):
        cur = mro[i]
        cur_name = _class_type_name(cur)
        cur_module = _class_type_module(cur)
        if (
            cur_name == "ClassLowering"
            and cur_module == "pcc.py_frontend.codegen.class_gen"
        ):
            if attr_name == "classes":
                return _make_dict_type(TYPE_STR, TYPE_DYN)
            if attr_name in _CLASS_LOWERING_HOST_METHODS:
                return _make_func_type((TYPE_DYN,), TYPE_DYN)
        for fname, fty in _class_type_fields(cur):
            if fname == attr_name:
                return fty
        i += 1
    return None


def _lookup_class_field(cls_ty: ClassType, field_name: str) -> Optional[Type]:
    mro = _class_mro_list(cls_ty)
    i = 0
    while i < len(mro):
        cur = mro[i]
        cur_name = _class_type_name(cur)
        cur_module = _class_type_module(cur)
        if (
            cur_name == "ClassLowering"
            and cur_module == "pcc.py_frontend.codegen.class_gen"
        ):
            if field_name == "classes":
                return _make_dict_type(TYPE_STR, TYPE_DYN)
            if field_name in _CLASS_LOWERING_HOST_METHODS:
                return _make_func_type((TYPE_DYN,), TYPE_DYN)
        for fname, fty in _class_type_fields(cur):
            if fname == field_name:
                return fty
        i += 1
    return None


def _lookup_class_property(cls_ty: ClassType, prop_name: str) -> Optional[Type]:
    """MRO walk for ``@property`` declarations. Mirrors
    ``_lookup_class_field`` but searches ``ClassType.properties``."""
    mro = _class_mro_list(cls_ty)
    i = 0
    while i < len(mro):
        cur = mro[i]
        for pname, pty in getattr(cur, "properties", ()):
            if pname == prop_name:
                return pty
        i += 1
    return None


def _class_bases_from_def(ctx: _InferCtx, stmt: ClassDef) -> tuple[ClassType, ...]:
    bases: list[ClassType] = []
    for base_expr in stmt.bases:
        if not isinstance(base_expr, Name):
            continue
        base_ident = _name_ident(base_expr)
        if base_ident is None:
            continue
        if base_ident == "object":
            continue
        base_ty = ctx.class_types.get(base_ident)
        if isinstance(base_ty, ClassType):
            bases.append(base_ty)
        else:
            bases.append(
                _make_class_type(
                    base_ident,
                    _ctx_module_name(ctx),
                    (),
                    (),
                )
            )
    return tuple(bases)


def _append_field(
    fields: list[tuple[str, Type]],
    name: str,
    field_ty: Type,
) -> None:
    for i, (existing, _old_ty) in enumerate(fields):
        if existing == name:
            fields[i] = (name, field_ty)
            return
    fields.append((name, field_ty))


def _is_property_decorator(dec) -> bool:
    """True if ``dec`` is the ``@property`` decorator.

    Accepts the bare ``Name("property")`` form and the qualified
    ``Attr(Name("builtins"), "property")`` form. ``@<name>.setter`` /
    ``.deleter`` are intentionally NOT recognised — Gap 2 is read-only
    property support; setter/deleter are out of scope until needed by
    pcc's self-host surface.
    """
    if isinstance(dec, Name):
        return _name_ident(dec) == "property"
    if isinstance(dec, Attr):
        return (
            dec.name == "property"
            and isinstance(dec.obj, Name)
            and _name_ident(dec.obj) == "builtins"
        )
    return False


def _simple_decorator_name(dec) -> Optional[str]:
    if isinstance(dec, Name):
        return _name_ident(dec)
    if isinstance(dec, Attr) and isinstance(dec.obj, Name):
        obj_ident = _name_ident(dec.obj)
        if obj_ident is None:
            return None
        return f"{obj_ident}.{dec.name}"
    if isinstance(dec, Call):
        return _simple_decorator_name(dec.func)
    return None


def _class_has_valueclass_decorator(stmt: ClassDef) -> bool:
    for dec in stmt.decorators:
        if _simple_decorator_name(dec) in ("valueclass", "pcc.valueclass"):
            return True
    return False


def _raise_frontend_error(
    span: Optional[SourceSpan],
    message: str,
    hint: str,
) -> None:
    raise PyFrontendError(span, message, hint)


def _is_valueclass_type(ty: Type) -> bool:
    if isinstance(ty, ClassType):
        return ty.valueclass
    return False


def _valueclass_type_refs(
    ty: Type,
    valueclass_names: set[str],
    module_name: str,
) -> set[str]:
    refs: set[str] = set()
    if isinstance(ty, ClassType):
        ty_name = _class_type_name(ty)
        ty_module = _class_type_module(ty)
        same_module = ty_module == "" or ty_module == module_name
        if same_module and ty_name in valueclass_names:
            refs.add(ty_name)
        for _field_name, field_ty in _class_type_fields(ty):
            refs.update(_valueclass_type_refs(field_ty, valueclass_names, module_name))
        return refs
    if isinstance(ty, ListType):
        return _valueclass_type_refs(ty.elem, valueclass_names, module_name)
    if isinstance(ty, DictType):
        refs.update(_valueclass_type_refs(ty.key, valueclass_names, module_name))
        refs.update(_valueclass_type_refs(ty.value, valueclass_names, module_name))
        return refs
    if isinstance(ty, TupleType):
        for elem_ty in ty.elems:
            refs.update(_valueclass_type_refs(elem_ty, valueclass_names, module_name))
    return refs


def _validate_valueclass_recursion(ctx: _InferCtx, module: Module) -> None:
    valueclass_defs: dict[str, ClassDef] = {}
    for stmt in module.body:
        if isinstance(stmt, ClassDef) and _class_has_valueclass_decorator(stmt):
            valueclass_defs[stmt.name] = stmt
    if not valueclass_defs:
        return

    module_name = module.name or ""
    valueclass_names = set(valueclass_defs)
    graph: dict[str, set[str]] = {}
    for name, stmt in valueclass_defs.items():
        refs: set[str] = set()
        for _field_name, field_ty in _class_fields_from_def(ctx, stmt):
            refs.update(_valueclass_type_refs(field_ty, valueclass_names, module_name))
        graph[name] = refs

    visiting: set[str] = set()
    visited: set[str] = set()

    for name in valueclass_defs:
        stack = [(name, (), False)]
        while stack:
            cur_name, path, expanded = stack.pop()
            if expanded:
                visiting.discard(cur_name)
                visited.add(cur_name)
                continue
            if cur_name in visiting:
                _raise_frontend_error(
                    valueclass_defs[cur_name].span,
                    "recursive valueclass payload is not supported",
                    "break the cycle with a normal identity class or box the recursive edge explicitly",
                )
            if cur_name in visited:
                continue
            visiting.add(cur_name)
            stack.append((cur_name, path, True))
            refs = tuple(graph.get(cur_name, ()))
            ref_i = len(refs) - 1
            while ref_i >= 0:
                ref = refs[ref_i]
                stack.append((ref, path + (cur_name,), False))
                ref_i -= 1


def _slots_contains_identity_slot(expr: Expr) -> Optional[str]:
    if isinstance(expr, StrLit):
        if expr.value == "__dict__" or expr.value == "__weakref__":
            return expr.value
        return None
    if isinstance(expr, TupleExpr):
        for item in expr.elems:
            slot = _slots_contains_identity_slot(item)
            if slot is not None:
                return slot
    return None


def _validate_valueclass_shape(ctx: _InferCtx, stmt: ClassDef) -> None:
    """Reject source shapes the current boxed V0 valueclass subset cannot honor."""
    for base in stmt.bases:
        if not (isinstance(base, Name) and _name_ident(base) == "object"):
            _raise_frontend_error(
                base.span,
                f"valueclass {stmt.name!r} cannot subclass another class in the current V0 subset",
                "remove the base class or use a normal identity class",
            )

    for body_stmt in stmt.body:
        if isinstance(body_stmt, FuncDef):
            if body_stmt.name == "__del__":
                _raise_frontend_error(
                    body_stmt.span,
                    f"valueclass {stmt.name!r} cannot define __del__",
                    "valueclass instances are identity-free; move finalization to an owning identity object",
                )
            continue

        if isinstance(body_stmt, Assign):
            for target in body_stmt.targets:
                if not isinstance(target, Name):
                    continue
                target_ident = _name_ident(target)
                if target_ident is None:
                    continue
                if target_ident == "__dict__" or target_ident == "__weakref__":
                    _raise_frontend_error(
                        target.span,
                        f"valueclass {stmt.name!r} cannot declare {target_ident}",
                        "valueclass instances do not support instance dictionaries or weakrefs in the current V0 subset",
                    )
                if target_ident == "__slots__":
                    slot = _slots_contains_identity_slot(body_stmt.value)
                    if slot is not None:
                        _raise_frontend_error(
                            body_stmt.span,
                            f"valueclass {stmt.name!r} cannot include {slot} in __slots__",
                            "valueclass instances do not support instance dictionaries or weakrefs in the current V0 subset",
                        )
                    continue
                if _annotation_or_none(body_stmt) is None:
                    _raise_frontend_error(
                        target.span,
                        f"valueclass field {stmt.name}.{target_ident} needs an explicit type annotation",
                        "declare the field as 'name: Type' or make the class a normal identity class",
                    )

    for body_stmt in stmt.body:
        if isinstance(body_stmt, FuncDef):
            if body_stmt.name != "__init__":
                continue
            arg_types: dict[str, Type] = {}
            for arg in body_stmt.args:
                if arg.name == "" or arg.name == "self" or arg.name == "cls":
                    continue
                arg_types[arg.name] = ctx.resolve_annotation(_annotation_or_none(arg))
            for init_stmt in body_stmt.body:
                if isinstance(init_stmt, Assign):
                    explicit_ty: Optional[Type] = None
                    init_annotation = _annotation_or_none(init_stmt)
                    if init_annotation is not None:
                        explicit_ty = ctx.resolve_annotation(init_annotation)
                    for target in init_stmt.targets:
                        if isinstance(target, Attr):
                            target_obj = target.obj
                            if not isinstance(target_obj, Name):
                                continue
                            if _name_ident(target_obj) != "self":
                                continue
                            field_ty = explicit_ty
                            if field_ty is None and isinstance(init_stmt.value, Name):
                                field_ty = arg_types.get(_name_ident(init_stmt.value))
                            if field_ty is None or isinstance(field_ty, DynType):
                                _raise_frontend_error(
                                    target.span,
                                    f"valueclass field {stmt.name}.{target.name} needs a typed initializer",
                                    "annotate the __init__ parameter or the self-field assignment",
                                )


def _class_properties_from_def(
    ctx: _InferCtx, stmt: ClassDef
) -> tuple[tuple[str, Type], ...]:
    """Collect ``@property`` declarations on a class body.

    Returns ``(name, return_ty)`` pairs. ``return_ty`` is taken from
    the getter's declared return annotation; missing annotation falls
    back to ``DynType``. See
    ``docs/investigations/pcc-py-type-infer-property-return-type.md``.
    """
    out: list[tuple[str, Type]] = []
    for body_stmt in stmt.body:
        if not isinstance(body_stmt, FuncDef):
            continue
        decorators = getattr(body_stmt, "decorators", ()) or ()
        if not any(_is_property_decorator(d) for d in decorators):
            continue
        ret_ty = ctx.resolve_annotation(body_stmt.return_ty)
        out.append((body_stmt.name, ret_ty))
    return tuple(out)


def _class_fields_from_def(
    ctx: _InferCtx, stmt: ClassDef
) -> tuple[tuple[str, Type], ...]:
    fields: list[tuple[str, Type]] = []
    for body_stmt in stmt.body:
        if isinstance(body_stmt, Assign):
            body_annotation = _annotation_or_none(body_stmt)
            if body_annotation is None:
                continue
            field_ty = ctx.resolve_annotation(body_annotation)
            for target in body_stmt.targets:
                if isinstance(target, Name):
                    target_ident = _name_ident(target)
                    if target_ident is not None:
                        _append_field(fields, target_ident, field_ty)
        elif isinstance(body_stmt, FuncDef) and body_stmt.name == "__init__":
            arg_types = {
                arg.name: ctx.resolve_annotation(_annotation_or_none(arg))
                for arg in body_stmt.args
                if arg.name not in ("", "self", "cls")
            }
            for init_stmt in body_stmt.body:
                if not isinstance(init_stmt, Assign):
                    continue
                explicit_ty: Optional[Type] = None
                init_annotation = _annotation_or_none(init_stmt)
                if init_annotation is not None:
                    explicit_ty = ctx.resolve_annotation(init_annotation)
                for target in init_stmt.targets:
                    if (
                        not isinstance(target, Attr)
                        or not isinstance(target.obj, Name)
                        or _name_ident(target.obj) != "self"
                    ):
                        continue
                    field_ty = explicit_ty
                    if field_ty is None and isinstance(init_stmt.value, Name):
                        field_ty = arg_types.get(_name_ident(init_stmt.value))
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

    base_names = _py_ast_static_base_names_for_export(module_name, class_name)
    if not base_names:
        base_names = tuple(info.get("base_names", ()))
    bases: list[ClassType] = []
    for base_name in base_names:
        base_info = module_exports.get(base_name)
        if isinstance(base_info, dict) and base_info.get("kind") == "class":
            bases.append(
                _class_type_from_export(
                    ctx,
                    module_name,
                    base_info,
                    module_exports,
                    memo,
                )
            )
        else:
            bases.append(_make_class_type(base_name, module_name, (), ()))

    static_fields = _py_ast_static_fields_for_export(module_name, class_name)
    if not static_fields:
        static_fields = _llvm_ir_static_fields_for_export(module_name, class_name)
    if static_fields:
        field_type_map = {
            fname: _resolve_export_type_refs(
                ctx,
                module_name,
                module_exports,
                memo,
                field_ty,
            )
            for fname, field_ty in static_fields
        }
    else:
        field_type_map = {
            fname: _resolve_export_type_refs(
                ctx,
                module_name,
                module_exports,
                memo,
                _annotation_to_type(decode_type(field_ty)),
            )
            for fname, field_ty in info.get("field_types", ())
        }
    field_names = tuple(info.get("field_names", ()))
    if field_names:
        fields = tuple(
            (fname, field_type_map.get(fname, TYPE_DYN)) for fname in field_names
        )
    else:
        fields = tuple(field_type_map.items())
    cls_ty = _make_class_type(
        class_name,
        module_name,
        fields,
        tuple(bases),
    )
    memo[key] = cls_ty
    ctx.register_class_type(class_name, cls_ty)
    return cls_ty


def _py_ast_ref(name: str) -> ClassType:
    return _make_class_type(name, "pcc.py_frontend.py_ast", (), ())


def _py_ast_tuple_of(ty: Type) -> TupleType:
    return _make_tuple_type("tuple", (ty,))


def _py_ast_static_fields_for_export(
    module_name: str,
    class_name: str,
) -> tuple[tuple[str, Type], ...]:
    if module_name != "pcc.py_frontend.py_ast":
        return ()
    span = _py_ast_ref("SourceSpan")
    ty = _py_ast_ref("Type")
    expr = _py_ast_ref("Expr")
    stmt = _py_ast_ref("Stmt")
    arg = _py_ast_ref("Arg")
    if class_name == "SourceSpan":
        return (
            ("file", TYPE_STR),
            ("line", TYPE_INT),
            ("col", TYPE_INT),
            ("end_line", TYPE_INT),
            ("end_col", TYPE_INT),
        )
    if class_name == "Type":
        return (("name", TYPE_STR),)
    if class_name == "IntType":
        return (("name", TYPE_STR), ("width", TYPE_INT), ("signed", TYPE_BOOL))
    if class_name == "FloatType":
        return (("name", TYPE_STR), ("width", TYPE_INT))
    if class_name in (
        "ComplexType",
        "BoolType",
        "NoneType",
        "StrType",
        "BytesType",
        "ByteArrayType",
        "MemoryViewType",
        "DynType",
    ):
        return (("name", TYPE_STR),)
    if class_name == "ListType":
        return (("name", TYPE_STR), ("elem", ty))
    if class_name == "DictType":
        return (("name", TYPE_STR), ("key", ty), ("value", ty))
    if class_name == "TupleType":
        return (("name", TYPE_STR), ("elems", _py_ast_tuple_of(ty)))
    if class_name == "FuncType":
        return (
            ("name", TYPE_STR),
            ("params", _py_ast_tuple_of(ty)),
            ("ret", ty),
        )
    if class_name == "ClassType":
        return (
            ("name", TYPE_STR),
            ("module", TYPE_STR),
            ("fields", _py_ast_tuple_of(_make_tuple_type("tuple", (TYPE_STR, ty)))),
            ("bases", _py_ast_tuple_of(_py_ast_ref("ClassType"))),
            (
                "properties",
                _py_ast_tuple_of(_make_tuple_type("tuple", (TYPE_STR, ty))),
            ),
            ("valueclass", TYPE_BOOL),
        )
    if class_name == "ValueClassType":
        return (
            ("name", TYPE_STR),
            ("module", TYPE_STR),
            ("fields", _py_ast_tuple_of(_make_tuple_type("tuple", (TYPE_STR, ty)))),
            ("bases", _py_ast_tuple_of(_py_ast_ref("ClassType"))),
            (
                "properties",
                _py_ast_tuple_of(_make_tuple_type("tuple", (TYPE_STR, ty))),
            ),
            ("valueclass", TYPE_BOOL),
            ("flattened", TYPE_BOOL),
            ("nullable_fields", TYPE_BOOL),
        )
    if class_name == "Expr":
        return (("span", span), ("ty", ty))
    if class_name == "Stmt":
        return (("span", span),)
    if class_name == "Name":
        return (("span", span), ("ty", ty), ("ident", TYPE_STR))
    if class_name == "IntLit":
        return (("span", span), ("ty", ty), ("value", TYPE_INT))
    if class_name == "FloatLit":
        return (("span", span), ("ty", ty), ("value", TYPE_FLOAT))
    if class_name == "ComplexLit":
        return (
            ("span", span),
            ("ty", ty),
            ("real", TYPE_FLOAT),
            ("imag", TYPE_FLOAT),
        )
    if class_name == "BoolLit":
        return (("span", span), ("ty", ty), ("value", TYPE_BOOL))
    if class_name == "NoneLit":
        return (("span", span), ("ty", ty))
    if class_name == "StrLit":
        return (("span", span), ("ty", ty), ("value", TYPE_STR))
    if class_name == "BytesLit":
        return (("span", span), ("ty", ty), ("value", TYPE_BYTES))
    if class_name == "BinOp" or class_name == "Compare":
        return (
            ("span", span),
            ("ty", ty),
            ("op", TYPE_STR),
            ("lhs", expr),
            ("rhs", expr),
        )
    if class_name == "UnaryOp":
        return (
            ("span", span),
            ("ty", ty),
            ("op", TYPE_STR),
            ("operand", expr),
        )
    if class_name == "BoolExpr":
        return (
            ("span", span),
            ("ty", ty),
            ("op", TYPE_STR),
            ("left", expr),
            ("right", expr),
        )
    if class_name == "Subscript":
        return (("span", span), ("ty", ty), ("obj", expr), ("idx", expr))
    if class_name == "Slice":
        return (
            ("span", span),
            ("ty", ty),
            ("lo", expr),
            ("hi", expr),
            ("step", expr),
        )
    if class_name == "DictExpr":
        return (
            ("span", span),
            ("ty", ty),
            ("pairs", _py_ast_tuple_of(_make_tuple_type("tuple", (expr, expr)))),
        )
    if class_name == "IfExpr":
        return (
            ("span", span),
            ("ty", ty),
            ("cond", expr),
            ("then_e", expr),
            ("else_e", expr),
        )
    if class_name == "Lambda":
        return (
            ("span", span),
            ("ty", ty),
            ("params", _py_ast_tuple_of(arg)),
            ("body", expr),
        )
    if class_name == "Arg":
        return (
            ("name", TYPE_STR),
            ("annotation", ty),
            ("default", expr),
            ("kind", TYPE_STR),
            ("has_default", TYPE_BOOL),
        )
    if class_name == "FuncDef":
        return (
            ("span", span),
            ("name", TYPE_STR),
            ("args", _py_ast_tuple_of(arg)),
            ("return_ty", ty),
            ("body", _py_ast_tuple_of(stmt)),
            ("decorators", _py_ast_tuple_of(expr)),
            ("is_method", TYPE_BOOL),
            ("is_async", TYPE_BOOL),
        )
    if class_name == "ClassDef":
        return (
            ("span", span),
            ("name", TYPE_STR),
            ("bases", _py_ast_tuple_of(expr)),
            ("keywords", _py_ast_tuple_of(_make_tuple_type("tuple", (TYPE_STR, expr)))),
            ("body", _py_ast_tuple_of(stmt)),
            ("decorators", _py_ast_tuple_of(expr)),
        )
    if class_name == "Assign":
        return (
            ("span", span),
            ("targets", _py_ast_tuple_of(expr)),
            ("value", expr),
            ("annotation", ty),
        )
    if class_name == "AugAssign":
        return (
            ("span", span),
            ("target", expr),
            ("op", TYPE_STR),
            ("value", expr),
        )
    if class_name == "For":
        return (
            ("span", span),
            ("target", expr),
            ("iter", expr),
            ("body", _py_ast_tuple_of(stmt)),
            ("else_body", _py_ast_tuple_of(stmt)),
            ("is_async", TYPE_BOOL),
        )
    if class_name == "Return":
        return (("span", span), ("value", expr))
    if class_name in ("Pass", "Break", "Continue"):
        return (("span", span),)
    if class_name == "ExprStmt":
        return (("span", span), ("expr", expr))
    if class_name == "If" or class_name == "While":
        return (
            ("span", span),
            ("cond", expr),
            ("body", _py_ast_tuple_of(stmt)),
            ("else_body", _py_ast_tuple_of(stmt)),
        )
    if class_name == "Raise":
        return (("span", span), ("exc", expr), ("cause", expr))
    if class_name == "Try":
        return (
            ("span", span),
            ("body", _py_ast_tuple_of(stmt)),
            ("handlers", _py_ast_tuple_of(_py_ast_ref("ExceptHandler"))),
            ("else_body", _py_ast_tuple_of(stmt)),
            ("finally_body", _py_ast_tuple_of(stmt)),
        )
    if class_name == "ExceptHandler":
        return (
            ("exc_type", expr),
            ("name", TYPE_STR),
            ("body", _py_ast_tuple_of(stmt)),
            ("span", span),
        )
    if class_name == "With":
        return (
            ("span", span),
            ("items", _py_ast_tuple_of(_make_tuple_type("tuple", (expr, expr)))),
            ("body", _py_ast_tuple_of(stmt)),
            ("is_async", TYPE_BOOL),
        )
    if class_name == "Import":
        return (
            ("span", span),
            ("names", _py_ast_tuple_of(_make_tuple_type("tuple", (TYPE_STR, TYPE_STR)))),
        )
    if class_name == "ImportFrom":
        return (
            ("span", span),
            ("module", TYPE_STR),
            ("names", _py_ast_tuple_of(_make_tuple_type("tuple", (TYPE_STR, TYPE_STR)))),
            ("level", TYPE_INT),
        )
    if class_name == "Global" or class_name == "Nonlocal":
        return (("span", span), ("names", _py_ast_tuple_of(TYPE_STR)))
    if class_name == "Delete":
        return (("span", span), ("targets", _py_ast_tuple_of(expr)))
    if class_name == "Call":
        return (
            ("span", span),
            ("ty", ty),
            ("func", expr),
            ("args", _py_ast_tuple_of(expr)),
            ("kwargs", _py_ast_tuple_of(_make_tuple_type("tuple", (TYPE_STR, expr)))),
        )
    if class_name == "Attr":
        return (("span", span), ("ty", ty), ("obj", expr), ("name", TYPE_STR))
    if class_name == "TupleExpr" or class_name == "ListExpr":
        return (("span", span), ("ty", ty), ("elems", _py_ast_tuple_of(expr)))
    if class_name == "Module":
        return (
            ("name", TYPE_STR),
            ("body", _py_ast_tuple_of(stmt)),
            ("docstring", TYPE_STR),
        )
    return ()


def _py_ast_static_base_names_for_export(
    module_name: str,
    class_name: str,
) -> tuple[str, ...]:
    if module_name != "pcc.py_frontend.py_ast":
        return ()
    if class_name in (
        "IntType",
        "FloatType",
        "ComplexType",
        "BoolType",
        "NoneType",
        "StrType",
        "BytesType",
        "ByteArrayType",
        "MemoryViewType",
        "ListType",
        "DictType",
        "TupleType",
        "FuncType",
        "ClassType",
        "DynType",
    ):
        return ("Type",)
    if class_name == "ValueClassType":
        return ("ClassType",)
    if class_name in (
        "IntLit",
        "FloatLit",
        "ComplexLit",
        "BoolLit",
        "NoneLit",
        "StrLit",
        "BytesLit",
        "Name",
        "BinOp",
        "UnaryOp",
        "Compare",
        "BoolExpr",
        "Call",
        "Attr",
        "Subscript",
        "Slice",
        "ListExpr",
        "DictExpr",
        "TupleExpr",
        "IfExpr",
        "Lambda",
    ):
        return ("Expr",)
    if class_name in (
        "Assign",
        "AugAssign",
        "ExprStmt",
        "If",
        "While",
        "For",
        "Return",
        "Pass",
        "Break",
        "Continue",
        "Raise",
        "Try",
        "With",
        "Import",
        "ImportFrom",
        "Global",
        "Nonlocal",
        "Delete",
        "FuncDef",
        "ClassDef",
    ):
        return ("Stmt",)
    return ()


def _llvm_ir_ref(class_name: str) -> ClassType:
    return _make_class_type(class_name, "pcc.llvm_capi.ir", (), ())


def _llvm_ir_static_fields_for_export(
    module_name: str,
    class_name: str,
) -> tuple[tuple[str, Type], ...]:
    if module_name != "pcc.llvm_capi.ir":
        return ()

    ty = _llvm_ir_ref("Type")
    value = _llvm_ir_ref("Value")
    module = _llvm_ir_ref("Module")
    function_type = _llvm_ir_ref("FunctionType")
    function = _llvm_ir_ref("Function")
    global_variable = _llvm_ir_ref("GlobalVariable")
    argument = _llvm_ir_ref("Argument")
    block = _llvm_ir_ref("Block")
    instruction = _llvm_ir_ref("InstructionRecord")
    function_attrs = _llvm_ir_ref("FunctionAttributes")

    value_fields = (
        ("type", ty),
        ("_ref", TYPE_STR),
        ("_instr", TYPE_STR),
        ("_flags", _make_list_type(TYPE_STR)),
        ("_is_unsigned", TYPE_BOOL),
        ("_pcc_unsigned_pointee", TYPE_BOOL),
        ("_pcc_unsigned_return", TYPE_BOOL),
    )
    if class_name == "Value":
        return value_fields
    if class_name == "Argument":
        return (
            ("type", ty),
            ("index", TYPE_INT),
            ("_name", TYPE_STR),
            ("_ref", TYPE_STR),
        )
    if class_name == "InstructionRecord":
        return (("text", TYPE_STR), ("opname", TYPE_STR), ("block", block))
    if class_name == "Block":
        return (
            ("parent", function),
            ("function", function),
            ("name", TYPE_STR),
            ("_instrs", _make_list_type(instruction)),
            ("_text_lines", _make_list_type(TYPE_STR)),
            ("_terminated", TYPE_BOOL),
        )
    if class_name == "Function":
        return value_fields + (
            ("module", module),
            ("ftype", function_type),
            ("function_type", function_type),
            ("name", TYPE_STR),
            ("blocks", _make_list_type(block)),
            ("args", _make_tuple_type("tuple_variadic", (argument,))),
            ("_name_counter", TYPE_INT),
            ("_block_counter", TYPE_INT),
            ("_name_registry", _make_dict_type(TYPE_STR, TYPE_INT)),
            ("linkage", TYPE_STR),
            ("attributes", function_attrs),
            ("calling_convention", TYPE_STR),
        )
    if class_name == "GlobalVariable":
        return value_fields + (
            ("value_type", ty),
            ("name", TYPE_STR),
            ("linkage", TYPE_STR),
            ("global_constant", TYPE_BOOL),
            ("initializer", TYPE_DYN),
            ("addrspace", TYPE_INT),
            ("section", TYPE_STR),
            ("align", TYPE_INT),
            ("unnamed_addr", TYPE_STR),
        )
    if class_name == "Module":
        return (
            ("name", TYPE_STR),
            ("triple", TYPE_STR),
            ("data_layout", TYPE_STR),
            ("_functions", _make_list_type(function)),
            ("_globals", _make_list_type(global_variable)),
            ("globals", _make_dict_type(TYPE_STR, TYPE_DYN)),
            ("_named_metadata", _make_dict_type(TYPE_STR, TYPE_DYN)),
            ("context", TYPE_DYN),
            ("_name_counters", _make_dict_type(TYPE_STR, TYPE_INT)),
        )
    if class_name == "IRBuilder":
        return (
            ("_block", block),
            ("_pos", TYPE_INT),
            ("_fn", function),
            ("block", block),
            ("function", function),
        )
    if class_name == "FunctionType":
        return (("return_type", ty), ("args", _make_tuple_type("tuple_variadic", (ty,))), ("var_arg", TYPE_BOOL))
    if class_name == "PointerType":
        return (("pointee", ty), ("addrspace", TYPE_INT))
    if class_name == "IntType":
        return (("width", TYPE_INT),)
    return ()


def _resolve_export_type_refs(
    ctx: _InferCtx,
    module_name: str,
    module_exports: dict,
    memo: dict[tuple[str, str], ClassType],
    ty: Type,
) -> Type:
    ty = ctx.resolve_type_refs(ty)
    if isinstance(ty, ClassType):
        ty_module = _class_type_module(ty)
        ty_fields = _class_type_fields(ty)
        ty_bases = _class_type_bases(ty)
        if (not ty_module or ty_module == module_name) and not ty_fields and not ty_bases:
            ref_info = module_exports.get(ty.name)
            if isinstance(ref_info, dict) and ref_info.get("kind") == "class":
                return _class_type_from_export(
                    ctx,
                    module_name,
                    ref_info,
                    module_exports,
                    memo,
                )
        return ty
    if isinstance(ty, ListType):
        elem = _resolve_export_type_refs(ctx, module_name, module_exports, memo, ty.elem)
        if elem == ty.elem:
            return ty
        return _make_list_type(elem)
    if isinstance(ty, DictType):
        key = _resolve_export_type_refs(ctx, module_name, module_exports, memo, ty.key)
        value = _resolve_export_type_refs(
            ctx,
            module_name,
            module_exports,
            memo,
            ty.value,
        )
        if key == ty.key and value == ty.value:
            return ty
        return _make_dict_type(key, value)
    if isinstance(ty, TupleType):
        elems = tuple(
            _resolve_export_type_refs(ctx, module_name, module_exports, memo, elem)
            for elem in ty.elems
        )
        if elems == ty.elems:
            return ty
        return _make_tuple_type(ty.name, elems)
    if isinstance(ty, FuncType):
        params = tuple(
            _resolve_export_type_refs(ctx, module_name, module_exports, memo, param)
            for param in ty.params
        )
        ret = _resolve_export_type_refs(ctx, module_name, module_exports, memo, ty.ret)
        if params == ty.params and ret == ty.ret:
            return ty
        return _make_func_type(params, ret)
    return ty


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
                ctx,
                resolved_module,
                info,
                module_exports,
                memo,
            )

    for attr_name, as_name in names:
        local_name = as_name or attr_name
        info = module_exports.get(attr_name)
        if info is None:
            continue
        if info["kind"] == "function":
            param_tys = tuple(
                _resolve_export_type_refs(
                    ctx,
                    resolved_module,
                    module_exports,
                    memo,
                    _annotation_to_type(decode_type(t)),
                )
                for t in info["param_types"]
            )
            ret_ty = _resolve_export_type_refs(
                ctx,
                resolved_module,
                module_exports,
                memo,
                _annotation_to_type(decode_type(info["return_ty"])),
            )
            ft = _make_func_type(param_tys, ret_ty)
            scope.update(local_name, ft)
            ctx.func_types[local_name] = ft
        elif info["kind"] == "class":
            cls_ty = _class_type_from_export(
                ctx,
                resolved_module,
                info,
                module_exports,
                memo,
            )
            scope.update(local_name, cls_ty)
            ctx.register_class_type(local_name, cls_ty)
        elif info["kind"] == "module_global":
            value_ty = _resolve_export_type_refs(
                ctx,
                resolved_module,
                module_exports,
                memo,
                _annotation_to_type(decode_type(info.get("value_ty"))),
            )
            scope.update(local_name, value_ty)
        elif info["kind"] == "constant":
            value_kind = info.get("value_kind")
            if value_kind == "str":
                scope.update(local_name, TYPE_STR)
            elif value_kind == "int":
                scope.update(local_name, TYPE_INT)
            elif value_kind == "bool":
                scope.update(local_name, TYPE_BOOL)
            elif value_kind == "none":
                scope.update(local_name, TYPE_NONE)


def _bind_ir_compat_module_alias(
    ctx: _InferCtx,
    scope: _Scope,
    resolved_module: str,
    names: tuple[tuple[str, Optional[str]], ...],
) -> None:
    """Bind ``from pcc.llvm_capi.compat import ir`` to real IR exports.

    The source spelling is a compile-time compatibility facade, but ON-mode
    closed-world builds link the concrete ``pcc.llvm_capi.ir`` provider.
    Type inference must mirror that replacement so annotations such as
    ``ir.IRBuilder`` resolve to the exported class schema instead of a shell
    ``ClassType(module="ir")``.
    """
    if resolved_module != "pcc.llvm_capi.compat":
        return
    if not ctx.external_exports:
        return
    module_exports = ctx.external_exports.get("pcc.llvm_capi.ir")
    if not module_exports:
        return
    memo: dict[tuple[str, str], ClassType] = {}
    for attr_name, as_name in names:
        if attr_name != "ir":
            continue
        local_name = as_name or attr_name
        for info in module_exports.values():
            if not isinstance(info, dict) or info.get("kind") != "class":
                continue
            cls_ty = _class_type_from_export(
                ctx,
                "pcc.llvm_capi.ir",
                info,
                module_exports,
                memo,
            )
            ctx.class_types[f"{local_name}.{cls_ty.name}"] = cls_ty
        scope.update(local_name, DynType(name="module:pcc.llvm_capi.ir"))


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
            ctx,
            module_name,
            info,
            module_exports,
            memo,
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
            tgt_ident = _name_ident(tgt)
            if tgt_ident is not None:
                scope.update(tgt_ident, bind_ty)
            new_targets.append(_with_ty(tgt, bind_ty))
        elif (
            isinstance(tgt, TupleExpr)
            and isinstance(bind_ty, TupleType)
            and len(bind_ty.elems) == len(tgt.elems)
            and all(isinstance(e, Name) for e in tgt.elems)
        ):
            # Tuple unpack ``a, b = <tuple>``: propagate each tuple element
            # type to its sub-target Name so the unpacked variable is typed
            # like a direct assignment. Without this, a float element bound to
            # an untyped sub-target was stored/read with a mismatched type
            # (only ints/strs happened to work by default; floats gave <null>).
            # Restricted to flat all-Name targets; nested/starred/subscript/
            # attr targets and arity mismatches fall back to plain inference.
            sub_targets = []
            for sub, elem_ty in zip(tgt.elems, bind_ty.elems):
                sub_ident = _name_ident(sub)
                if sub_ident is not None:
                    scope.update(sub_ident, elem_ty)
                sub_targets.append(_with_ty(sub, elem_ty))
            new_targets.append(replace(tgt, elems=tuple(sub_targets), ty=bind_ty))
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
    # Allow None on any non-numeric annotation. ``Optional[T]``
    # unwraps to ``T`` at parse time only for non-primitive ``T``
    # (see ``pcc/parse/py_lift.py``). Numeric ``Optional[int]`` /
    # ``Optional[float]`` / ``Optional[bool]`` stay as DynType because
    # pcc has no nullable representation for unboxed numerics, so the
    # exclusion below preserves a real correctness check rather than
    # a documentation gap.
    if _is_none_type(rhs) and not isinstance(ann, (IntType, FloatType, BoolType)):
        return
    # Container element subsumption with DynType.
    if _is_assignable(ann, rhs):
        return
    _raise_frontend_error(
        span,
        f"cannot assign value of type {rhs.name!r} to variable annotated as {ann.name!r}",
        "add an explicit cast or relax the annotation",
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
    host_param_names = ctx.contextual_host_params.get(fn.name, ())
    for index, a in enumerate(fn.args):
        ty = ctx.resolve_annotation(a.annotation)
        if (
            self_ty is not None
            and index == 0
            and a.name in ("self", "cls")
            and a.annotation is None
        ):
            ty = self_ty
            self_ty_name = _class_type_name(self_ty)
            self_ty_module = _class_type_module(self_ty)
            if isinstance(self_ty, ClassType) and (
                (
                    self_ty_name == "L1CodeGen"
                    and self_ty_module == "pcc.py_frontend.codegen.layer1"
                )
                or (
                    self_ty_name == "L1CodeGenMixinStack"
                    and self_ty_module == "pcc.py_frontend.codegen.layer1_mixins"
                )
                or (
                    self_ty_name == "L1CodeGenEntrypointMixin"
                    and self_ty_module
                    == "pcc.py_frontend.codegen.layer1_entrypoints"
                )
            ):
                ty = ctx.l1_codegen_host_type()
        if a.annotation is None and a.name in host_param_names:
            ty = ctx.l1_codegen_host_type()
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
    func_ret_ty = DynType(name="coroutine") if fn.is_async else ret_ty

    # Record the function's full type in the module-level table *before*
    # walking the body so recursive calls see it.
    ft = FuncType(
        name="callable",
        params=tuple(
            a.annotation if isinstance(a.annotation, Type) else TYPE_DYN
            for a in new_args
        ),
        ret=func_ret_ty,
    )
    ctx.func_types[fn.name] = ft
    # Also expose the function by name so lookups inside the body find
    # the binding.  Outer scope (module or enclosing def) keeps a copy
    # through ``scope.update`` below.
    param_scope.define(fn.name, ft)
    scope.update(fn.name, ft)

    new_body_items: list[Stmt] = []
    for body_stmt in fn.body:
        new_body_items.append(_infer_stmt(ctx, param_scope, body_stmt))
    new_body = tuple(new_body_items)

    # Type-check ``return`` against ``ret_ty``.
    if not fn.is_async and not isinstance(ret_ty, DynType):
        _check_returns(new_body, ret_ty)

    return FuncDef(
        span=fn.span,
        name=fn.name,
        args=tuple(new_args),
        return_ty=ret_ty,
        body=new_body,
        decorators=fn.decorators,
        is_method=fn.is_method,
        is_async=fn.is_async,
    )


def _container_method_return_type(
    recv_ty: Type,
    method: str,
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
        if method == "encode":
            return BytesType(name="bytes")
        if method in (
            "upper",
            "lower",
            "strip",
            "lstrip",
            "rstrip",
            "replace",
            "join",
            "split",
            "splitlines",
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
    if isinstance(recv_ty, (BytesType, ByteArrayType)):
        if method == "decode":
            return StrType(name="str")
    if isinstance(recv_ty, DynType) and recv_ty.name == "module:math":
        if method == "floor":
            return TYPE_INT
        if method == "sqrt":
            return TYPE_FLOAT
    if isinstance(recv_ty, ListType):
        if method == "pop":
            return recv_ty.elem
        if method == "index":
            return IntType(name="int")
        if method in ("append", "extend", "insert", "remove", "sort"):
            return NoneType(name="None")
    if isinstance(recv_ty, DictType):
        if method in ("get", "pop", "setdefault"):
            return recv_ty.value
        if method == "keys":
            return ListType(name="list", elem=recv_ty.key)
        if method == "values":
            return ListType(name="list", elem=recv_ty.value)
        if method == "items":
            return ListType(
                name="list",
                elem=TupleType(
                    name="tuple",
                    elems=(recv_ty.key, recv_ty.value),
                ),
            )
    if isinstance(recv_ty, DynType) and recv_ty.name in ("set", "frozenset"):
        if method in ("issubset", "issuperset"):
            return BoolType(name="bool")
    return None


def _tuple_type_parts(ty: Type) -> Optional[tuple[str, tuple[Type, ...]]]:
    if isinstance(ty, TupleType):
        return (ty.name, ty.elems)
    try:
        name = ty.name
    except AttributeError:
        return None
    if name not in ("tuple", "tuple_variadic"):
        return None
    try:
        elems = ty.elems
    except AttributeError:
        return None
    try:
        len(elems)
    except Exception:
        return None
    return (name, elems)


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
    if _is_none_type(got) and not isinstance(declared, (IntType, FloatType, BoolType)):
        return True
    if _builtin_container_name_assignable(declared, got):
        return True
    if isinstance(declared, ClassType):
        if _runtime_type_object_assignable(declared, got):
            return True
        if _builtin_container_class_assignable(declared, got):
            return True
        if isinstance(got, ClassType):
            return _class_type_assignable(declared, got)
    declared_tuple = _tuple_type_parts(declared)
    got_tuple = _tuple_type_parts(got)
    if declared_tuple is not None and got_tuple is not None:
        declared_name, declared_elems = declared_tuple
        got_name, got_elems = got_tuple
        # ``tuple[T, ...]`` — variadic declared tuple matches any
        # tuple whose every element is assignable to ``T``.
        if declared_name == "tuple_variadic" and declared_elems:
            elem_ty = declared_elems[0]
            return all(_is_assignable(elem_ty, g) for g in got_elems)
        # A variadic-got flowing into a fixed-arity declared form is
        # treated conservatively: assignable when the got's element
        # type subsumes every declared slot.
        if got_name == "tuple_variadic" and got_elems:
            got_elem = got_elems[0]
            return all(_is_assignable(d, got_elem) for d in declared_elems)
        if len(declared_elems) != len(got_elems):
            return False
        i = 0
        while i < len(declared_elems):
            if not _is_assignable(declared_elems[i], got_elems[i]):
                return False
            i += 1
        return True
    declared_list_elem = _list_type_elem(declared)
    got_list_elem = _list_type_elem(got)
    if declared_list_elem is not None and got_list_elem is not None:
        return _is_assignable(declared_list_elem, got_list_elem)
    declared_dict = _dict_type_parts(declared)
    got_dict = _dict_type_parts(got)
    if declared_dict is not None and got_dict is not None:
        declared_key, declared_value = declared_dict
        got_key, got_value = got_dict
        return _is_assignable(declared_key, got_key) and _is_assignable(
            declared_value, got_value
        )
    return False


def _builtin_container_name_assignable(declared: Type, got: Type) -> bool:
    name = getattr(declared, "name", "")
    if name == "list" and _list_type_elem(got) is not None:
        return True
    if name == "dict" and _dict_type_parts(got) is not None:
        return True
    if name == "tuple" and _tuple_type_parts(got) is not None:
        return True
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
    if declared.name == "NoneType" and _is_none_type(got):
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
    return (
        not _class_type_module(ty)
        and not _class_type_fields(ty)
        and not _class_type_bases(ty)
    )


def _class_type_assignable(declared: ClassType, got: ClassType) -> bool:
    declared_name = _class_type_name(declared)
    declared_module = _class_type_module(declared)
    got_name = _class_type_name(got)
    got_module = _class_type_module(got)
    if declared_name == got_name and (
        declared_module == got_module or not declared_module or not got_module
    ):
        return True
    if (
        declared_name == got_name
        and declared_module
        in (
            "pcc.py_frontend.py_ast",
            "pcc.py_frontend.types",
        )
        and got_module
        in (
            "pcc.py_frontend.py_ast",
            "pcc.py_frontend.types",
        )
    ):
        return True
    # An annotation imported from a module whose schema is not available
    # (the per-module self-compile probe runs without external_exports)
    # must behave like the old DynType path. Preserve strict subclass
    # checks only once at least one side carries real schema/module data.
    if _class_type_is_unresolved_shell(declared) or _class_type_is_unresolved_shell(
        got
    ):
        return True
    for base in _class_type_bases(got):
        if _class_type_assignable(declared, base):
            return True
    return False


def _check_returns(body: tuple[Stmt, ...], ret_ty: Type) -> None:
    for s in body:
        if isinstance(s, Return):
            if s.value is None:
                if not _is_none_type(ret_ty):
                    _raise_frontend_error(
                        s.span,
                        f"function annotated to return {ret_ty.name!r} but returns no value",
                        f"return a value of type {ret_ty.name!r}",
                    )
            else:
                vty = s.value.ty
                if isinstance(vty, DynType) or isinstance(ret_ty, DynType):
                    continue
                if type_eq(ret_ty, vty):
                    continue
                # ``Optional[T]`` is unwrapped to ``T`` at parse time
                # (see ``pcc/parse/py_lift.py``). A bare ``return None``
                # against any non-``NoneType`` annotation is treated as
                # the ``Optional[T]`` legitimate-None branch. This
                # preserves Python's documented ``Optional[T]`` ≡
                # ``T | None`` semantics without introducing a Union
                # type into Phase 1.
                if _is_none_type(vty):
                    continue
                if isinstance(ret_ty, FloatType) and isinstance(
                    vty, (IntType, BoolType)
                ):
                    continue
                if isinstance(ret_ty, IntType) and isinstance(vty, BoolType):
                    continue
                if _is_assignable(ret_ty, vty):
                    continue
                _raise_frontend_error(
                    s.span,
                    f"return type mismatch: expected {ret_ty.name!r}, got {vty.name!r}",
                    "change the annotation or convert the value",
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

    if ctx.external_exports:
        for stmt in module.body:
            if isinstance(stmt, ImportFrom):
                resolved = _resolve_relative_module(
                    _import_from_module_or_empty(stmt),
                    _import_from_level_or_zero(stmt),
                    _ctx_module_name(ctx),
                    stmt.span.file,
                )
                _bind_ir_compat_module_alias(ctx, ctx.globals, resolved, stmt.names)
                _bind_external_import_exports(ctx, ctx.globals, resolved, stmt.names)

    for stmt in module.body:
        if isinstance(stmt, ClassDef):
            cls_ty = _make_class_type(
                stmt.name,
                module.name or "",
                (),
                (),
                valueclass=_class_has_valueclass_decorator(stmt),
            )
            ctx.register_class_type(stmt.name, cls_ty)
            ctx.globals.define(stmt.name, cls_ty)

    _validate_valueclass_recursion(ctx, module)

    for stmt in module.body:
        if isinstance(stmt, ClassDef):
            if _class_has_valueclass_decorator(stmt):
                _validate_valueclass_shape(ctx, stmt)
            bases = _class_bases_from_def(ctx, stmt)
            fields = _class_fields_from_def(ctx, stmt)
            properties = _class_properties_from_def(ctx, stmt)
            cls_ty = _make_class_type(
                stmt.name,
                module.name or "",
                fields,
                bases,
                properties,
                valueclass=_class_has_valueclass_decorator(stmt),
            )
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
    module: Optional[str],
    level: int,
    current: Optional[str],
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
    is_package_init = current_file == "__init__.py" or current_file.endswith(
        "/__init__.py"
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


def infer_module(
    m: Module,
    *,
    external_exports=None,
    derived_class_map=None,
    contextual_host_params=None,
) -> Module:
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

    ``derived_class_map`` is the inverse base→derived table built by
    the multi-file pipeline: for every base class with a unique
    derived class in the closure, the value is ``(derived_module,
    derived_class_name)``. Mixin methods get type-inferred with
    ``self_ty=derived_class`` so cross-module ``self.X`` resolves
    against the derived class's full field schema. Single-file
    compiles pass ``None``.

    ``contextual_host_params`` is an opt-in helper extraction hook:
    ``{function_name: ("host", ...)}`` marks those unannotated params as
    the synthetic ``L1CodeGen`` host type so helper modules can type
    ``host._fresh`` / ``host.builder`` without immediately falling to
    ``DynType``.
    """

    ctx = _InferCtx(
        m,
        external_exports=external_exports,
        derived_class_map=derived_class_map,
        contextual_host_params=contextual_host_params,
    )
    _prepopulate_module_scope(ctx, m)
    new_body = []
    for stmt in tuple(m.body):
        typed_stmt = _infer_stmt(ctx, ctx.globals, stmt)
        new_body.append(typed_stmt)
    new_body = tuple(new_body)
    return replace(m, body=new_body)


__all__ = ["infer_module", "PyFrontendError"]
