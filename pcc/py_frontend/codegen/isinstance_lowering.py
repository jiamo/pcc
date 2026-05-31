"""``isinstance`` / ``issubclass`` helper bodies for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    BoolType,
    Call,
    DictType,
    DynType,
    Expr,
    FloatType,
    IntType,
    ListType,
    Name,
    NoneLit,
    NoneType,
    StrType,
    TupleExpr,
    TupleType,
)
from .errors import L1CodegenError

_I1 = ir.IntType(1)
_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()
_BUILTIN_TYPE_MATCHERS = {
    "str": StrType,
    "int": IntType,
    "float": FloatType,
    "bool": BoolType,
    "list": ListType,
    "dict": DictType,
    "tuple": TupleType,
    "NoneType": NoneType,
}
_BUILTIN_TYPE_TAGS = {
    "NoneType": 0,
    "bool": 1,
    "int": 2,
    "float": 3,
    "str": 4,
    "list": 5,
    "dict": 6,
    "tuple": 7,
}


def compile_time_isinstance_impl(
    host,
    obj_expr: Expr,
    class_ident: str,
) -> Optional[ir.Value]:
    """Resolve ``isinstance(x, BuiltinType)`` at compile time when the
    operand's static type is known."""
    if class_ident not in _BUILTIN_TYPE_MATCHERS:
        return None
    matcher = _BUILTIN_TYPE_MATCHERS[class_ident]
    ty = obj_expr.ty
    if isinstance(ty, DynType):
        return None
    return ir.Constant(_I1, 1 if isinstance(ty, matcher) else 0)


def emit_builtin_runtime_isinstance_impl(
    host,
    obj_expr: Expr,
    class_ident: str,
    obj_val: Optional[ir.Value] = None,
) -> Optional[ir.Value]:
    if class_ident not in _BUILTIN_TYPE_TAGS:
        return None
    tag = _BUILTIN_TYPE_TAGS[class_ident]
    if obj_val is None:
        obj_val = host._emit_as_object(obj_expr)
    actual = host.builder.call(
        host.runtime["py_obj_type_tag"],
        [obj_val],
        name=host._fresh("obj.type_tag"),
    )
    return host.builder.icmp_signed(
        "==",
        actual,
        ir.Constant(_I64, tag),
        name=host._fresh("builtin.isinstance"),
    )


def ir_scaffold_class_symbol_impl(host, expr: Expr) -> Optional[str]:
    """Return the ``pcc.llvm_capi.ir`` class name for ``ir.X``."""
    if not host._ir_scaffold_enabled():
        return None
    if not isinstance(expr, Attr):
        return None
    symbol = host._ir_module_symbol_target(expr)
    if symbol is None:
        return None
    return symbol


def emit_ir_scaffold_isinstance_impl(
    host,
    obj_val: ir.Value,
    class_name: str,
) -> ir.Value:
    g_name = ".class.pcc_llvm_capi_ir." + class_name
    existing = host.module.globals.get(g_name)
    if existing is None:
        gv = ir.GlobalVariable(host.module, _CSTR, name=g_name)
        gv.linkage = "external"
    else:
        gv = existing
    cls_ptr = host.builder.load(
        gv,
        name=host._fresh("ir.cls." + class_name),
    )
    res_i64 = host.builder.call(
        host.runtime["py_isinstance"],
        [obj_val, cls_ptr],
        name=host._fresh("ir.isinstance." + class_name),
    )
    return host.builder.icmp_signed(
        "!=",
        res_i64,
        ir.Constant(_I64, 0),
        name=host._fresh("ir.isinstance.i1"),
    )


def maybe_emit_issubclass_builtin_impl(host, expr: Call) -> Optional[ir.Value]:
    lhs, rhs = expr.args
    if not isinstance(lhs, Name) or not isinstance(rhs, Name):
        return None
    sub_name = host._resolve_class_alias(lhs.ident)
    sup_name = host._resolve_class_alias(rhs.ident)
    if not hasattr(host, "class_lowering"):
        return None
    if sub_name not in host.class_lowering.classes:
        return None
    if sup_name != "object" and sup_name not in host.class_lowering.classes:
        return None
    return ir.Constant(
        _I1,
        1 if host._class_is_subclass(sub_name, sup_name) else 0,
    )


def class_is_subclass_impl(host, sub_name: str, sup_name: str) -> bool:
    if sub_name == sup_name or sup_name == "object":
        return True
    visited: set[str] = set()
    queue = [sub_name]
    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        info = host.class_lowering.classes.get(name)
        if info is None:
            continue
        for base_expr in info.bases_ast:
            if not isinstance(base_expr, Name):
                continue
            base_name = host._resolve_class_alias(base_expr.ident)
            if base_name == sup_name:
                return True
            if base_name != "object":
                queue.append(base_name)
    return False


def emit_isinstance_call_impl(
    host,
    expr: Call,
) -> ir.Value:
    if len(expr.args) != 2:
        raise L1CodegenError("isinstance expects exactly two arguments")
    class_arg = expr.args[1]

    def class_name_from_expr(e: Expr) -> tuple[Optional[str], Optional[str]]:
        ir_symbol = host._ir_scaffold_class_symbol(e)
        if isinstance(e, Name):
            return e.ident, None
        if isinstance(e, Attr):
            if isinstance(e.obj, Name):
                host._ensure_native_module_alias_class_export(
                    e.obj.ident,
                    e.name,
                )
            return e.name, ir_symbol
        if (
            isinstance(e, Call)
            and isinstance(e.func, Name)
            and e.func.ident == "type"
            and len(e.args) == 1
            and not e.kwargs
            and isinstance(e.args[0], NoneLit)
        ):
            return "NoneType", None
        return None, None

    def is_dynamic_type_call(e: Expr) -> bool:
        return (
            isinstance(e, Call)
            and isinstance(e.func, Name)
            and e.func.ident == "type"
            and len(e.args) == 1
            and not e.kwargs
        )

    def emit_dynamic_classinfo_isinstance(
        obj_val: ir.Value,
        classinfo_expr: Expr,
    ) -> ir.Value:
        cls_val = host._emit_expr(classinfo_expr)
        raw = host.builder.call(
            host.runtime["py_obj_isinstance"],
            [obj_val, cls_val],
            name=host._fresh("obj.isinstance"),
        )
        return host.builder.icmp_signed(
            "!=",
            raw,
            ir.Constant(_I64, 0),
            name=host._fresh("obj.isinstance.i1"),
        )

    # Tuple form ``isinstance(x, (A, B, C))`` -> OR of per-class checks.
    if isinstance(class_arg, TupleExpr):
        if not class_arg.elems:
            host._emit_as_object(expr.args[0])
            return ir.Constant(_I1, 0)
        names: list[Optional[str]] = []
        ir_class_names: list[Optional[str]] = []
        dynamic_classinfos: list[Optional[Expr]] = []
        for e in class_arg.elems:
            name, ir_symbol = class_name_from_expr(e)
            if name is None:
                if not is_dynamic_type_call(e):
                    raise NotImplementedError(
                        "isinstance tuple form requires bare class names "
                        "module.name chains, type(None), or type(expr); got "
                        f"{type(e).__name__}"
                    )
                dynamic_classinfos.append(e)
            else:
                dynamic_classinfos.append(None)
            names.append(name)
            ir_class_names.append(ir_symbol)
        acc: Optional[ir.Value] = None
        obj_val: Optional[ir.Value] = None
        for idx, nm in enumerate(names):
            dynamic_classinfo = dynamic_classinfos[idx]
            if dynamic_classinfo is not None:
                if obj_val is None:
                    obj_val = host._emit_as_object(expr.args[0])
                ct = emit_dynamic_classinfo_isinstance(
                    obj_val,
                    dynamic_classinfo,
                )
                acc = (
                    ct
                    if acc is None
                    else host.builder.or_(
                        acc,
                        ct,
                        name=host._fresh("isinstance_or"),
                    )
                )
                continue
            assert nm is not None
            nm = host._resolve_class_alias(nm)
            ir_symbol = ir_class_names[idx]
            if ir_symbol is not None:
                if obj_val is None:
                    obj_val = host._emit_as_object(expr.args[0])
                ct = host._emit_ir_scaffold_isinstance(
                    obj_val,
                    ir_symbol,
                )
            else:
                ct = host._compile_time_isinstance(expr.args[0], nm)
            if ct is None:
                if nm in _BUILTIN_TYPE_TAGS:
                    if obj_val is None:
                        obj_val = host._emit_as_object(expr.args[0])
                    ct = host._emit_builtin_runtime_isinstance(
                        expr.args[0],
                        nm,
                        obj_val,
                    )
                elif nm in host.class_lowering.classes:
                    if obj_val is None:
                        obj_val = host._emit_as_object(expr.args[0])
                    ct = host.class_lowering.emit_isinstance(obj_val, nm)
                else:
                    ct = ir.Constant(_I1, 0)
            acc = (
                ct
                if acc is None
                else host.builder.or_(
                    acc,
                    ct,
                    name=host._fresh("isinstance_or"),
                )
            )
        assert acc is not None
        return acc

    # ``mod.Class`` second-arg: use tail token as the class name.
    cls_ident, ir_symbol = class_name_from_expr(class_arg)
    if cls_ident is None:
        if is_dynamic_type_call(class_arg):
            obj_val = host._emit_as_object(expr.args[0])
            return emit_dynamic_classinfo_isinstance(obj_val, class_arg)
        raise NotImplementedError(
            "isinstance second argument must be a bare class name, "
            "a tuple of bare class names, a module.attr chain, type(None), "
            "or type(expr)"
        )
    cls_ident = host._resolve_class_alias(cls_ident)
    protocol_check = host._maybe_emit_protocol_isinstance(
        expr.args[0],
        cls_ident,
    )
    if protocol_check is not None:
        return protocol_check
    ct = host._compile_time_isinstance(expr.args[0], cls_ident)
    if ct is not None:
        return ct
    ct = host._emit_builtin_runtime_isinstance(expr.args[0], cls_ident)
    if ct is not None:
        return ct
    if ir_symbol is not None:
        obj_val = host._emit_as_object(expr.args[0])
        return host._emit_ir_scaffold_isinstance(obj_val, ir_symbol)
    if cls_ident not in host.class_lowering.classes:
        if isinstance(class_arg, Name) and (
            class_arg.ident in host.env
            or class_arg.ident in getattr(host, "_module_globals", {})
        ):
            obj_val = host._emit_as_object(expr.args[0])
            cls_val = host._emit_expr(class_arg)
            raw = host.builder.call(
                host.runtime["py_obj_isinstance"],
                [obj_val, cls_val],
                name=host._fresh("obj.isinstance"),
            )
            return host.builder.icmp_signed(
                "!=",
                raw,
                ir.Constant(_I64, 0),
                name=host._fresh("obj.isinstance.i1"),
            )
        host._emit_as_object(expr.args[0])
        return ir.Constant(_I1, 0)
    obj_val = host._emit_as_object(expr.args[0])
    return host.class_lowering.emit_isinstance(obj_val, cls_ident)


class IsinstanceLoweringMixin:
    # Keep these methods host-owned on L1CodeGen.  The self-hosted stage
    # compiler still relies on the concrete L1CodeGen method table for
    # compiler-internal AST dispatch such as ``isinstance(stmt, Return)``.
    # The bodies above keep the helper logic explicit for contextual
    # per-module probing while this mixin keeps layer1.py small.

    def _compile_time_isinstance(
        self,
        obj_expr: Expr,
        class_ident: str,
    ) -> Optional[ir.Value]:
        return compile_time_isinstance_impl(
            self,
            obj_expr,
            class_ident,
        )

    def _emit_builtin_runtime_isinstance(
        self,
        obj_expr: Expr,
        class_ident: str,
        obj_val: Optional[ir.Value] = None,
    ) -> Optional[ir.Value]:
        return emit_builtin_runtime_isinstance_impl(
            self,
            obj_expr,
            class_ident,
            obj_val,
        )

    def _ir_scaffold_class_symbol(self, expr: Expr) -> Optional[str]:
        return ir_scaffold_class_symbol_impl(self, expr)

    def _emit_ir_scaffold_isinstance(
        self,
        obj_val: ir.Value,
        class_name: str,
    ) -> ir.Value:
        return emit_ir_scaffold_isinstance_impl(self, obj_val, class_name)

    def _maybe_emit_issubclass_builtin(self, expr: Call) -> Optional[ir.Value]:
        return maybe_emit_issubclass_builtin_impl(self, expr)

    def _class_is_subclass(self, sub_name: str, sup_name: str) -> bool:
        return class_is_subclass_impl(self, sub_name, sup_name)

    def _emit_isinstance_call(self, expr: Call) -> ir.Value:
        return emit_isinstance_call_impl(self, expr)
