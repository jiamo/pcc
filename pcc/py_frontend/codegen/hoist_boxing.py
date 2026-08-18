"""Closure-cell boxing transforms for nested-function hoisting.

These helpers operate only on typed AST nodes.  The hoist pass supplies its
free-name analyzer and the per-codegen boxed-parameter table explicitly, so
this module does not depend on the broad Layer1 codegen object.
"""

from __future__ import annotations

from dataclasses import replace as _replace

from ..py_ast import (
    Assign,
    AugAssign,
    Call,
    DynType,
    ExprStmt,
    For,
    FuncDef,
    If,
    IntLit,
    IntType,
    ListExpr,
    ListType,
    Name,
    NoneLit,
    NoneType,
    Return,
    Subscript,
    Try,
    While,
    With,
)
from .hoist_analysis import (
    _dataclass_field_names,
    _dataclass_field_value,
    append_name_once,
    clone_funcdef,
    name_in,
)


_DYN = DynType(name="dyn")


def _box_expr(expr, boxed):
    """Rewrite reads of boxed names through their one-element cell list."""
    int_ty = IntType(name="int")

    def go(node):
        if node is None:
            return node
        if isinstance(node, Name) and node.ident in boxed:
            return Subscript(
                span=node.span,
                ty=_DYN,
                obj=_replace(node, ty=_DYN),
                idx=IntLit(span=node.span, ty=int_ty, value=0),
            )
        if isinstance(node, Call):
            new_args = []
            for arg in node.args:
                new_args.append(go(arg))
            new_kwargs = []
            for key, value in node.kwargs:
                new_kwargs.append((key, go(value)))
            return _replace(
                node,
                func=go(node.func),
                args=tuple(new_args),
                kwargs=tuple(new_kwargs),
            )
        fields = _dataclass_field_names(node)
        if not fields:
            return node
        new_fields = {}
        for slot in fields:
            value = _dataclass_field_value(node, slot, None)
            if slot == "span":
                continue
            if isinstance(value, tuple):
                items = []
                for item in value:
                    items.append(go(item))
                new_fields[slot] = tuple(items)
            else:
                new_fields[slot] = (
                    go(value) if _dataclass_field_names(value) else value
                )
        if new_fields:
            return _replace(node, **new_fields)
        return node

    return go(expr)


def _box_stmts(stmts, boxed):
    """Rewrite reads and writes of boxed names through their cell list."""
    int_ty = IntType(name="int")

    def make_sub(name_ident, span, ty):
        return Subscript(
            span=span,
            ty=ty,
            obj=Name(span=span, ty=_DYN, ident=name_ident),
            idx=IntLit(span=span, ty=int_ty, value=0),
        )

    def box_target(target):
        if isinstance(target, Name) and target.ident in boxed:
            return make_sub(target.ident, target.span, target.ty)
        return _box_expr(target, boxed)

    out = []
    for stmt in stmts:
        if isinstance(stmt, Assign):
            new_value = _box_expr(stmt.value, boxed)
            new_targets = []
            for target in stmt.targets:
                new_targets.append(box_target(target))
            out.append(
                _replace(
                    stmt,
                    targets=tuple(new_targets),
                    value=new_value,
                )
            )
            continue
        if isinstance(stmt, AugAssign):
            new_value = _box_expr(stmt.value, boxed)
            out.append(
                _replace(
                    stmt,
                    target=box_target(stmt.target),
                    value=new_value,
                )
            )
            continue
        if isinstance(stmt, If):
            out.append(
                _replace(
                    stmt,
                    cond=_box_expr(stmt.cond, boxed),
                    body=_box_stmts(stmt.body, boxed),
                    else_body=_box_stmts(stmt.else_body, boxed),
                )
            )
            continue
        if isinstance(stmt, While):
            out.append(
                _replace(
                    stmt,
                    cond=_box_expr(stmt.cond, boxed),
                    body=_box_stmts(stmt.body, boxed),
                    else_body=_box_stmts(stmt.else_body, boxed),
                )
            )
            continue
        if isinstance(stmt, For):
            out.append(
                _replace(
                    stmt,
                    target=box_target(stmt.target),
                    iter=_box_expr(stmt.iter, boxed),
                    body=_box_stmts(stmt.body, boxed),
                    else_body=_box_stmts(stmt.else_body, boxed),
                )
            )
            continue
        if isinstance(stmt, Try):
            new_handlers = []
            for handler in stmt.handlers:
                new_handlers.append(
                    _replace(
                        handler,
                        body=_box_stmts(
                            _dataclass_field_value(handler, "body", ()),
                            boxed,
                        ),
                    )
                )
            out.append(
                _replace(
                    stmt,
                    body=_box_stmts(stmt.body, boxed),
                    else_body=_box_stmts(stmt.else_body, boxed),
                    finally_body=_box_stmts(stmt.finally_body, boxed),
                    handlers=tuple(new_handlers),
                )
            )
            continue
        if isinstance(stmt, With):
            out.append(_replace(stmt, body=_box_stmts(stmt.body, boxed)))
            continue
        if isinstance(stmt, ExprStmt):
            out.append(_replace(stmt, expr=_box_expr(stmt.expr, boxed)))
            continue
        if isinstance(stmt, Return):
            if stmt.value is None:
                out.append(stmt)
            else:
                out.append(_replace(stmt, value=_box_expr(stmt.value, boxed)))
            continue
        if isinstance(stmt, FuncDef):
            out.append(
                clone_funcdef(
                    stmt,
                    stmt.name,
                    stmt.args,
                    stmt.return_ty,
                    _box_stmts(stmt.body, boxed),
                )
            )
            continue
        out.append(stmt)
    return tuple(out)


def box_outer_body(
    body,
    owner_name,
    param_names,
    boxed_names,
    closure_boxed_params,
):
    """Apply pcc's list-cell closure representation to one outer body."""
    filtered = []
    for name in boxed_names:
        if name != "__class__":
            append_name_once(filtered, name)
    boxed = tuple(filtered)
    if not boxed:
        return body

    boxed_param_names = []
    for name in boxed:
        if name_in(param_names, name):
            boxed_param_names.append(name)
    boxed_params = tuple(boxed_param_names)
    if boxed_params:
        closure_boxed_params[owner_name] = boxed_params

    rewritten = _box_stmts(body, boxed)
    span = body[0].span if body else None
    sentinels = []
    for name in sorted(boxed):
        if name_in(param_names, name):
            continue
        sentinels.append(
            Assign(
                span=span,
                targets=(Name(span=span, ty=_DYN, ident=name),),
                value=ListExpr(
                    span=span,
                    ty=ListType(name="list", elem=DynType(name="dyn")),
                    elems=(NoneLit(span=span, ty=NoneType(name="None")),),
                ),
                annotation=None,
            )
        )
    return tuple(sentinels) + rewritten
