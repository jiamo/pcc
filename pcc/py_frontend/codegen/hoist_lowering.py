"""Nested function hoisting helpers for L1CodeGen."""
from __future__ import annotations

import os
import sys
from dataclasses import replace as _replace

from ..py_ast import (
    Arg,
    Assign,
    Attr,
    AugAssign,
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
    Nonlocal,
    NoneLit,
    NoneType,
    Pass,
    Raise,
    Return,
    Slice,
    SourceSpan,
    Stmt,
    StrLit,
    StrType,
    Subscript,
    Try,
    TupleExpr,
    TupleType,
    Type,
    UnaryOp,
    While,
    With,
)

_Assign = Assign
_Attr = Attr
_AugAssign = AugAssign
_BinOp = BinOp
_BoolExpr = BoolExpr
_Break = Break
_Call = Call
_ClassDef = ClassDef
_Compare = Compare
_Continue = Continue
_Delete = Delete
_DictExpr = DictExpr
_DynType = DynType
_ExprStmt = ExprStmt
_ExprStmt2 = ExprStmt
_For = For
_FuncDef = FuncDef
_GL = Global
_Global = Global
_If = If
_IfExpr = IfExpr
_Import = Import
_ImportFrom = ImportFrom
_ImportFromStmt = ImportFrom
_ImportStmt = Import
_Lambda = Lambda
_ListExpr = ListExpr
_Name = Name
_NL = Nonlocal
_Nonlocal = Nonlocal
_Pass = Pass
_Raise = Raise
_Return = Return
_Slice = Slice
_Subscript = Subscript
_TopImport = Import
_TopImportFrom = ImportFrom
_Try = Try
_TupleExpr = TupleExpr
_UnaryOp = UnaryOp
_While = While
_With = With
_DYN = DynType(name="dyn")

from .hoist_analysis import (
    _PY_BUILTINS_NS,
    _dataclass_field_names,
    _dataclass_field_value,
    _import_names_from_stmt,
    _is_import_from_stmt,
    _is_import_stmt,
    append_name_once,
    body_augassigns_free_name,
    body_reads_self,
    body_returns_name,
    body_uses_name_as_value,
    clone_funcdef,
    copy_name_map,
    copy_names,
    extend_names_once,
    filter_capture_names,
    filter_self_capture_names,
    hoist_stat_inc,
    is_discard_capture_name,
    module_may_need_hoist_fast,
    name_in,
    update_name_map,
    write_hoist_profile,
)


def _hoist_decorator_name(dec):
    if isinstance(dec, _Name):
        return dec.ident
    if isinstance(dec, _Attr):
        base = _hoist_decorator_name(dec.obj)
        if base:
            return base + "." + dec.name
        return dec.name
    if isinstance(dec, _Call):
        return _hoist_decorator_name(dec.func)
    return None


def _hoist_method_kind(fd) -> str:
    kind = "instance"
    for dec in getattr(fd, "decorators", ()):
        name = _hoist_decorator_name(dec)
        if name == "staticmethod" or (
            name is not None and name.endswith(".staticmethod")
        ):
            return "static"
        if name == "classmethod" or (
            name is not None and name.endswith(".classmethod")
        ):
            kind = "classmethod"
    return kind


def _hoist_log(enabled: bool, mod_name: str, label: str) -> None:
    if not enabled:
        return
    sys.stderr.write("[pcc.hoist] " + mod_name + ":" + label + "\n")


def _hoist_cache_key(prefix: str, fd, names=(), extra: str = "") -> str:
    key = prefix + ":" + str(id(fd)) + ":" + extra
    for name in names:
        key = key + "|" + str(name)
    return key


def _hoist_cache_key4(prefix: str, fd, names_a, names_b, names_c) -> str:
    key = prefix + ":" + str(id(fd))
    for name in names_a:
        key = key + "|a:" + str(name)
    for name in names_b:
        key = key + "|b:" + str(name)
    for name in names_c:
        key = key + "|c:" + str(name)
    return key


class HoistLoweringMixin:
    def _hoist_nested_funcdefs(self) -> list:
        """Walk every top-level FuncDef / ClassDef method body and
        collect any nested FuncDef to the module's top-level body.

        The nested ``def`` is renamed ``__nested_<outer>_<name>`` and
        re-attached to ``self.ast_module.body``. No closure conversion
        — if the hoisted function reads an outer local, codegen
        surfaces the usual ``unbound name`` error at its first
        reference. Common use in pcc's own sources (regex callbacks,
        comparator helpers) doesn't capture anything, so the hoist
        alone buys a lot.

        Also rewrites same-scope ``Call(Name(<inner>), ...)`` sites
        in the outer body to route through the new hoisted symbol.
        Returns the list of hoisted FuncDef nodes (caller re-declares
        them via ``_declare_user_function`` during the normal scan).
        """
        debug_hoist = bool(os.environ.get("PCC_DEBUG_HOIST"))

        ast_module = self.ast_module
        mod_name = ast_module.name or "<module>"

        hoisted: list = []
        hoisted_capture_params = self._hoisted_capture_params
        hoisted_capture_params.clear()
        hoisted_class_capture_params = self._hoisted_class_capture_params
        hoisted_class_capture_params.clear()
        hoisted_enclosing_class = self._hoisted_enclosing_class
        hoisted_enclosing_class.clear()
        hoisted_enclosing_method_kind = self._hoisted_enclosing_method_kind
        hoisted_enclosing_method_kind.clear()
        closure_boxed_params = self._closure_boxed_params
        closure_boxed_params.clear()
        hoist_wrap_caps = self._hoist_wrap_caps
        hoist_wrap_caps.clear()
        generator_func_names = self._generator_func_names
        hoist_profile_path = os.environ.get("PCC_HOIST_PROFILE_PATH", "").strip()
        hoist_profile_enabled = bool(hoist_profile_path)
        hoist_stats = {
            "compute_free_names_calls": 0,
            "compute_free_names_cache_hits": 0,
            "called_sibling_names_calls": 0,
            "called_sibling_names_cache_hits": 0,
            "referenced_sibling_names_calls": 0,
            "referenced_sibling_names_cache_hits": 0,
            "sibling_effective_free_names_calls": 0,
            "sibling_effective_free_names_cache_hits": 0,
        }
        may_need_hoist = module_may_need_hoist_fast(ast_module.body)
        if not may_need_hoist:
            _hoist_log(debug_hoist, mod_name, "module skip")
            write_hoist_profile(
                hoist_profile_enabled,
                hoist_profile_path,
                hoist_stats,
            )
            return hoisted
        compute_free_names_cache = {}

        module_scope_names_base_items = []

        def add_module_scope_target_names(t):
            if isinstance(t, _Name):
                append_name_once(module_scope_names_base_items, t.ident)
            elif isinstance(t, _TupleExpr):
                for e in t.elems:
                    add_module_scope_target_names(e)

        for module_scope_stmt in ast_module.body:
            if isinstance(module_scope_stmt, (_FuncDef, _ClassDef)):
                append_name_once(module_scope_names_base_items, module_scope_stmt.name)
            elif _is_import_stmt(module_scope_stmt):
                if _is_import_from_stmt(module_scope_stmt):
                    for imported_name, as_name in _import_names_from_stmt(
                        module_scope_stmt,
                    ):
                        if imported_name == "*":
                            continue
                        append_name_once(
                            module_scope_names_base_items,
                            as_name or imported_name,
                        )
                elif isinstance(module_scope_stmt, (_TopImport, _ImportStmt)) or not hasattr(
                    module_scope_stmt,
                    "module",
                ):
                    for imported_mod_name, as_name in _import_names_from_stmt(
                        module_scope_stmt,
                    ):
                        bound = as_name or imported_mod_name.split(".", 1)[0]
                        if bound:
                            append_name_once(module_scope_names_base_items, bound)
            elif isinstance(module_scope_stmt, _Assign):
                for target in module_scope_stmt.targets:
                    add_module_scope_target_names(target)
        module_scope_names_base = tuple(module_scope_names_base_items)

        def existing_top_or_hoisted_names(include_classes=False):
            out = []
            for h in hoisted:
                append_name_once(out, h.name)
            for s in ast_module.body:
                if isinstance(s, _FuncDef):
                    append_name_once(out, s.name)
                elif include_classes and isinstance(s, _ClassDef):
                    append_name_once(out, s.name)
            return out

        def mutable_captures_in_fd(fd, excluded):
            """Return the set of free-var names that the nested ``fd``
            mutates via ``Name = v`` / ``name += v``. These are the
            candidates for list-box closure conversion — the outer
            scope and the hoisted inner both rewrite ``X`` references
            as ``X[0]`` subscripts, with a shared list allocation
            in outer scope."""
            free = compute_free_names(fd, excluded)
            mutated = []
            from ..py_ast import Nonlocal as _NL

            def collect_declared_nonlocals(stmts):
                for s in stmts:
                    if isinstance(s, _NL):
                        extend_names_once(mutated, s.names)
                        continue
                    if isinstance(s, _If):
                        collect_declared_nonlocals(s.body)
                        collect_declared_nonlocals(s.else_body)
                        continue
                    if isinstance(s, _For):
                        collect_declared_nonlocals(s.body)
                        collect_declared_nonlocals(s.else_body)
                        continue
                    if isinstance(s, _While):
                        collect_declared_nonlocals(s.body)
                        collect_declared_nonlocals(s.else_body)
                        continue
                    if isinstance(s, _With):
                        collect_declared_nonlocals(s.body)
                        continue
                    if isinstance(s, _Try):
                        collect_declared_nonlocals(s.body)
                        collect_declared_nonlocals(s.else_body)
                        collect_declared_nonlocals(s.finally_body)
                        for h in s.handlers:
                            collect_declared_nonlocals(h.body)

            collect_declared_nonlocals(fd.body)
            if not free and not mutated:
                return ()

            def walk(x):
                if isinstance(x, _Assign):
                    for t in x.targets:
                        if isinstance(t, _Name) and name_in(free, t.ident):
                            append_name_once(mutated, t.ident)
                    for slot in ("value",):
                        walk(_dataclass_field_value(x, slot))
                    return
                if isinstance(x, _AugAssign):
                    if isinstance(x.target, _Name) and name_in(free, x.target.ident):
                        append_name_once(mutated, x.target.ident)
                    walk(x.value)
                    return
                for slot in _dataclass_field_names(x):
                    v = _dataclass_field_value(x, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            walk(it)
                    else:
                        walk(v)

            for s in fd.body:
                walk(s)
            return tuple(mutated)

        def box_expr(expr, boxed):
            """Rewrite every ``Name(X)`` read in ``expr`` to
            ``Subscript(Name(X), IntLit(0))`` for X in ``boxed``.
            ``Call.func`` at the top of a Call is left alone — the
            name-to-box substitution only applies when the reference
            is at VALUE position (reading the box's current value);
            callable boxes are a separate concern."""
            _INT = IntType(name="int")

            def go(e):
                if e is None:
                    return e
                if isinstance(e, _Name) and e.ident in boxed:
                    return Subscript(
                        span=e.span,
                        ty=_DYN,
                        obj=_replace(e, ty=_DYN),
                        idx=IntLit(span=e.span, ty=_INT, value=0),
                    )
                if isinstance(e, _Call):
                    new_args_list = []
                    for a in e.args:
                        new_args_list.append(go(a))
                    new_kwargs_list = []
                    for k, v in e.kwargs:
                        new_kwargs_list.append((k, go(v)))
                    return _replace(
                        e,
                        func=go(e.func),
                        args=tuple(new_args_list),
                        kwargs=tuple(new_kwargs_list),
                    )
                fields = _dataclass_field_names(e)
                if not fields:
                    return e
                new_fields = {}
                for slot in fields:
                    v = _dataclass_field_value(e, slot, None)
                    if slot == "span":
                        continue
                    if isinstance(v, tuple):
                        items_list = []
                        for it in v:
                            items_list.append(go(it))
                        new_fields[slot] = tuple(items_list)
                    else:
                        new_fields[slot] = go(v) if _dataclass_field_names(v) else v
                return _replace(e, **new_fields) if new_fields else e

            return go(expr)

        def box_stmts(stmts, boxed):
            """Rewrite a list of statements so assignments to boxed
            names become subscript stores (``X = v`` → ``X[0] = v``)
            and reads become subscript loads (handled via
            ``box_expr``). AugAssign targets are similarly rewritten.
            Recurses into nested blocks (If / For / While / Try /
            With). Does NOT recurse into nested FuncDef bodies —
            that happens during the hoist pass with a fresh scope."""
            _INT = IntType(name="int")

            def make_sub(name_ident, span, ty):
                return Subscript(
                    span=span,
                    ty=ty,
                    obj=Name(span=span, ty=_DYN, ident=name_ident),
                    idx=IntLit(span=span, ty=_INT, value=0),
                )

            out = []
            for st in stmts:
                if isinstance(st, _Assign) and len(st.targets) == 1:
                    t = st.targets[0]
                    new_value = box_expr(st.value, boxed)
                    if isinstance(t, _Name) and t.ident in boxed:
                        out.append(
                            _replace(
                                st,
                                targets=(make_sub(t.ident, t.span, t.ty),),
                                value=new_value,
                            )
                        )
                        continue
                    out.append(_replace(st, value=new_value))
                    continue
                if isinstance(st, _AugAssign):
                    new_value = box_expr(st.value, boxed)
                    if isinstance(st.target, _Name) and st.target.ident in boxed:
                        out.append(
                            _replace(
                                st,
                                target=make_sub(
                                    st.target.ident, st.target.span, st.target.ty
                                ),
                                value=new_value,
                            )
                        )
                        continue
                    out.append(_replace(st, value=new_value))
                    continue
                if isinstance(st, _If):
                    out.append(
                        _replace(
                            st,
                            cond=box_expr(st.cond, boxed),
                            body=box_stmts(st.body, boxed),
                            else_body=box_stmts(st.else_body, boxed),
                        )
                    )
                    continue
                if isinstance(st, _While):
                    out.append(
                        _replace(
                            st,
                            cond=box_expr(st.cond, boxed),
                            body=box_stmts(st.body, boxed),
                            else_body=box_stmts(st.else_body, boxed),
                        )
                    )
                    continue
                if isinstance(st, _For):
                    out.append(
                        _replace(
                            st,
                            iter=box_expr(st.iter, boxed),
                            body=box_stmts(st.body, boxed),
                            else_body=box_stmts(st.else_body, boxed),
                        )
                    )
                    continue
                if isinstance(st, _Try):
                    new_handlers = []
                    for h in st.handlers:
                        new_handlers.append(
                            _replace(h, body=box_stmts(h.body, boxed))
                        )
                    out.append(
                        _replace(
                            st,
                            body=box_stmts(st.body, boxed),
                            else_body=box_stmts(st.else_body, boxed),
                            finally_body=box_stmts(st.finally_body, boxed),
                            handlers=tuple(new_handlers),
                        )
                    )
                    continue
                if isinstance(st, _With):
                    out.append(
                        _replace(
                            st,
                            body=box_stmts(st.body, boxed),
                        )
                    )
                    continue
                if isinstance(st, _ExprStmt):
                    out.append(_replace(st, expr=box_expr(st.expr, boxed)))
                    continue
                if isinstance(st, _Return):
                    if st.value is None:
                        out.append(st)
                    else:
                        out.append(_replace(st, value=box_expr(st.value, boxed)))
                    continue
                if isinstance(st, _FuncDef):
                    out.append(
                        clone_funcdef(
                            st,
                            st.name,
                            st.args,
                            st.return_ty,
                            box_stmts(st.body, boxed),
                        )
                    )
                    continue
                out.append(st)
            return tuple(out)

        def collect_all_mutable_captures(body):
            """Walk a function body and return a set of names that
            any nested FuncDef mutates as a free var. Used to decide
            which outer locals to box."""
            boxed = []
            for st in body:
                if isinstance(st, _FuncDef):
                    extend_names_once(boxed, mutable_captures_in_fd(st, ()))
                    # Recurse into deeply nested defs.
                    extend_names_once(boxed, collect_all_mutable_captures(st.body))
                    continue
                for slot in _dataclass_field_names(st):
                    v = _dataclass_field_value(st, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            if _dataclass_field_names(it):
                                if isinstance(it, _FuncDef):
                                    extend_names_once(
                                        boxed,
                                        mutable_captures_in_fd(it, ()),
                                    )
                                    extend_names_once(
                                        boxed,
                                        collect_all_mutable_captures(it.body),
                                    )
                                else:
                                    pass
            return tuple(boxed)

        def collect_first_class_closure_captures(body):
            """Return free vars captured by nested defs used as values.

            A closure object can be materialised before the outer scope
            finishes rebinding the captured name::

                x = 1
                def get(): return x
                f = get
                x = 2

            CPython stores ``x`` in a cell, so ``f()`` sees ``2``. The
            older pcc conversion captured the value at ``f = get`` time.
            Reuse the existing one-element-list box path for these
            value-position closures so the function object captures the
            shared box instead of a stale scalar/object payload.
            """
            boxed = []

            def walk_block(stmts):
                for st in stmts:
                    if isinstance(st, _FuncDef):
                        if body_uses_name_as_value(stmts, st.name):
                            extend_names_once(
                                boxed,
                                compute_free_names(st, ()),
                            )
                        walk_block(st.body)
                        continue
                    if isinstance(st, _If):
                        walk_block(st.body)
                        walk_block(st.else_body)
                        continue
                    if isinstance(st, _While):
                        walk_block(st.body)
                        walk_block(st.else_body)
                        continue
                    if isinstance(st, _For):
                        walk_block(st.body)
                        walk_block(st.else_body)
                        continue
                    if isinstance(st, _Try):
                        walk_block(st.body)
                        for h in st.handlers:
                            walk_block(h.body)
                        walk_block(st.else_body)
                        walk_block(st.finally_body)
                        continue
                    if isinstance(st, _With):
                        walk_block(st.body)

            walk_block(body)
            return tuple(boxed)

        def box_outer_body(body, owner_name, param_names):
            """Top-level entry for cell-like capture boxing.

            If any nested def mutates a free var, or if a nested def is
            used as a first-class value, prepend ``X = [None]`` sentinel
            assigns and rewrite all reads/writes of those names through
            ``X[0]``. The shared list is pcc's current native cell
            representation; it stays libpython-free and preserves the
            important rebinding semantics.
            """
            from ..py_ast import ListExpr as _List, NoneLit as _None

            boxed_names: list[str] = []
            extend_names_once(boxed_names, collect_all_mutable_captures(body))
            extend_names_once(
                boxed_names,
                collect_first_class_closure_captures(body),
            )
            filtered_boxed_names = []
            for name in boxed_names:
                # ``__class__`` is CPython's compiler-created defining-class
                # cell for methods. It is not a user-rebindable outer local,
                # so do not lower it through pcc's one-element-list cell box;
                # native closure conversion should capture the class object
                # itself.
                if name == "__class__":
                    continue
                append_name_once(filtered_boxed_names, name)
            boxed = tuple(filtered_boxed_names)
            if not boxed:
                return body
            boxed_param_names = []
            for name in boxed:
                if name_in(param_names, name):
                    boxed_param_names.append(name)
            boxed_params = tuple(boxed_param_names)
            if boxed_params:
                closure_boxed_params[owner_name] = boxed_params
            # First rewrite the body so every X read/write uses X[0].
            rewritten = box_stmts(body, boxed)
            # Prepend sentinel allocations so the name binds to a list
            # before any user read/write touches it.
            span = body[0].span if body else None
            sentinels = []
            for name in sorted(boxed):
                if name_in(param_names, name):
                    continue
                sentinel = Assign(
                    span=span,
                    targets=(Name(span=span, ty=_DYN, ident=name),),
                    value=ListExpr(
                        span=span,
                        ty=ListType(name="list", elem=DynType(name="dyn")),
                        elems=(NoneLit(span=span, ty=NoneType(name="None")),),
                    ),
                    annotation=None,
                )
                sentinels.append(sentinel)
            return tuple(sentinels) + rewritten

        def compute_free_names(fd, excluded, own_name=None):
            """Return the sorted tuple of Name idents that ``fd``'s
            body reads but aren't bound in its param list, a local
            assignment, a module-level symbol, a Python builtin, its
            own self-reference, or one of the ``excluded`` names.

            Callers that only want a bool can use
            ``bool(compute_free_names(...))``. Closure conversion
            uses the actual name set to append synthetic params."""
            cache_key = _hoist_cache_key("free", fd, excluded, own_name or "")
            cached = compute_free_names_cache.get(cache_key)
            if cached is not None:
                hoist_stat_inc(
                    hoist_profile_enabled,
                    hoist_stats,
                    "compute_free_names_cache_hits",
                )
                return cached
            hoist_stat_inc(
                hoist_profile_enabled,
                hoist_stats,
                "compute_free_names_calls",
            )
            param_names = []
            for a in fd.args:
                if a.name != "":
                    append_name_once(param_names, a.name)
            assigned_names = []
            module_names = copy_names(module_scope_names_base)
            extend_names_once(module_names, excluded)
            if own_name is not None:
                append_name_once(module_names, own_name)
            # ``fd.name`` is in scope for recursive self-calls.
            append_name_once(module_names, fd.name)

            from ..py_ast import TupleExpr as _TupleExpr

            def add_target_names(t):
                if isinstance(t, _Name):
                    append_name_once(assigned_names, t.ident)
                elif isinstance(t, _TupleExpr):
                    for e in t.elems:
                        add_target_names(e)

            # Names declared ``nonlocal`` / ``global`` in the body are
            # explicit outer-scope references, not local bindings —
            # even an ``X = v`` assignment to such a name writes to
            # the outer binding, not a local. Track them and exclude
            # from ``assigned_names``.
            nonlocal_names = []
            global_names = []
            from ..py_ast import (
                Global as _GL,
                Import as _ImportStmt,
                ImportFrom as _ImportFromStmt,
                Nonlocal as _NL,
            )

            def collect_nonlocal_global(stmts):
                for s in stmts:
                    if isinstance(s, _NL):
                        extend_names_once(nonlocal_names, s.names)
                    elif isinstance(s, _GL):
                        extend_names_once(global_names, s.names)
                    elif isinstance(s, _If):
                        collect_nonlocal_global(s.body)
                        collect_nonlocal_global(s.else_body)
                    elif isinstance(s, _For):
                        collect_nonlocal_global(s.body)
                    elif isinstance(s, _While):
                        collect_nonlocal_global(s.body)
                    elif isinstance(s, _With):
                        collect_nonlocal_global(s.body)
                    elif isinstance(s, _Try):
                        collect_nonlocal_global(s.body)
                        collect_nonlocal_global(s.else_body)
                        collect_nonlocal_global(s.finally_body)
                        for h in s.handlers:
                            collect_nonlocal_global(h.body)

            collect_nonlocal_global(fd.body)
            extend_names_once(module_names, global_names)

            def collect_assigned(stmts):
                for s in stmts:
                    if isinstance(s, _Assign):
                        for t in s.targets:
                            if isinstance(t, _Name) and name_in(
                                nonlocal_names, t.ident
                            ):
                                continue
                            add_target_names(t)
                    elif isinstance(s, _AugAssign):
                        # AugAssign requires the target to already be
                        # bound; it doesn't create a local on its own.
                        # Don't add to assigned_names so the free-var
                        # walker treats the read-modify-write as a
                        # capture if no pure Assign provides the
                        # binding first.
                        pass
                    elif isinstance(s, _For):
                        add_target_names(s.target)
                        collect_assigned(s.body)
                    elif isinstance(s, _If):
                        collect_assigned(s.body)
                        collect_assigned(s.else_body)
                    elif isinstance(s, _While):
                        collect_assigned(s.body)
                    elif isinstance(s, _With):
                        for _ctx_expr, as_var in s.items:
                            if as_var is not None:
                                add_target_names(as_var)
                        collect_assigned(s.body)
                    elif isinstance(s, _Try):
                        collect_assigned(s.body)
                        collect_assigned(s.else_body)
                        collect_assigned(s.finally_body)
                        for h in s.handlers:
                            if h.name:
                                append_name_once(assigned_names, h.name)
                            collect_assigned(h.body)
                    elif _is_import_stmt(s):
                        if _is_import_from_stmt(s):
                            for imported_name, as_name in _import_names_from_stmt(s):
                                if imported_name == "*":
                                    continue
                                append_name_once(
                                    assigned_names,
                                    as_name or imported_name,
                                )
                        else:
                            for mod_name, asname in _import_names_from_stmt(s):
                                bound = asname or mod_name.split(".", 1)[0]
                                if bound:
                                    append_name_once(assigned_names, bound)
                    elif isinstance(s, (_FuncDef, _ClassDef)):
                        # A nested ``def`` / ``class`` binds its name
                        # in the enclosing scope. Uses of that name in
                        # sibling statements are local references, not
                        # captures from an even-further-outer scope.
                        # Don't recurse into its body — that has its
                        # own local scope.
                        append_name_once(assigned_names, s.name)

            collect_assigned(fd.body)

            # Parser sentinel names the codegen special-cases at Call
            # position (comprehensions, walrus, yield). They never
            # refer to a user binding, so never count as a capture.
            sentinel_ns = (
                "__listcomp__",
                "_list_comp",
                "_gen_comp",
                "__genexpr__",
                "__setcomp__",
                "_set_comp",
                "__dictcomp__",
                "_dict_comp",
                "_walrus",
                "__walrus__",
                "_yield",
                "__yield__",
                "_yield_from",
                "__yield_from__",
                "_gen_clause",
                "__starred__",
                # dataclasses.replace aliases are lowered as a native
                # codegen helper, not a runtime callable capture.
                "replace",
                "_replace",
                # Treat bare ``_`` as a discard binding for closure
                # analysis. The pcc codebase uses it pervasively in
                # tuple-unpack / loop-target throwaway positions; if it
                # leaks into a propagated capture set, hoisted sibling
                # calls end up demanding an outer ``_`` binding that
                # doesn't semantically exist.
                "_",
                "*",
                "**",
            )
            local_scope = []
            extend_names_once(local_scope, param_names)
            extend_names_once(local_scope, assigned_names)
            extend_names_once(local_scope, module_names)
            extend_names_once(local_scope, sentinel_ns)
            builtins_ns = _PY_BUILTINS_NS
            free = []

            def _collect_target_names(t, acc):
                if isinstance(t, _Name):
                    append_name_once(acc, t.ident)
                    return
                for slot in _dataclass_field_names(t):
                    v = _dataclass_field_value(t, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            _collect_target_names(it, acc)

            def _call_ident(expr):
                return getattr(expr, "ident", None)

            def _is_call_node(expr):
                return isinstance(expr, _Call) or (
                    hasattr(expr, "func")
                    and hasattr(expr, "args")
                    and hasattr(expr, "kwargs")
                )

            def _has_gen_clause(node):
                if isinstance(node, _TupleExpr):
                    for it in node.elems:
                        if _has_gen_clause(it):
                            return True
                    return False
                if _is_call_node(node):
                    return _call_ident(node.func) == "_gen_clause"
                return False

            def _iter_comp_nodes(raw):
                if raw is None:
                    return tuple()
                if isinstance(raw, _TupleExpr):
                    raw = raw.elems
                if isinstance(raw, (list, tuple)):
                    out = []
                    for it in raw:
                        if isinstance(it, (list, tuple, _TupleExpr)):
                            for nested in _iter_comp_nodes(it):
                                out.append(nested)
                        else:
                            out.append(it)
                    return tuple(out)
                return (raw,)

            def _collect_gen_clauses(raw):
                if raw is None:
                    return tuple()
                out = []
                for node in _iter_comp_nodes(raw):
                    if (
                        _is_call_node(node)
                        and _call_ident(node.func) == "_gen_clause"
                    ):
                        out.append(node)
                        continue
                    if not _is_call_node(node):
                        continue
                    # Defensive path for malformed/foreign parser outputs where
                    # a synthetic `_gen_clause` tuple slips through as plain args.
                    if _call_ident(node.func) == "_gen_clause":
                        out.append(node)
                return tuple(out)

            def _decompose_gen_clause(clause):
                if _is_call_node(clause) and _call_ident(clause.func) == "_gen_clause":
                    target = clause.args[0] if len(clause.args) >= 1 else None
                    iter_expr = clause.args[1] if len(clause.args) >= 2 else None
                    ifs_expr = clause.args[2] if len(clause.args) >= 3 else None
                    return target, iter_expr, ifs_expr
                if _is_call_node(clause):
                    return None
                if isinstance(clause, _TupleExpr):
                    target = clause.elems[0] if len(clause.elems) >= 1 else None
                    iter_expr = clause.elems[1] if len(clause.elems) >= 2 else None
                    ifs_expr = clause.elems[2] if len(clause.elems) >= 3 else None
                    return target, iter_expr, ifs_expr
                if isinstance(clause, (tuple, list)) and len(clause) >= 1:
                    target = clause[0]
                    iter_expr = clause[1] if len(clause) >= 2 else None
                    ifs_expr = clause[2] if len(clause) >= 3 else None
                    return target, iter_expr, ifs_expr
                return None

            def walk(x, bound=None):
                if bound is None:
                    bound = ()
                if isinstance(x, (_FuncDef, _ClassDef)):
                    # Nested defs/classes introduce their own local
                    # scope. Their bodies must not contribute free vars
                    # to the enclosing function.
                    return
                if isinstance(x, tuple):
                    # Plain tuples show up in places like
                    # ``Call.kwargs = ((name, Expr), ...)``; recurse so
                    # kwarg values still participate in free-var
                    # analysis.
                    for it in x:
                        walk(it, bound)
                    return
                if isinstance(x, _Name):
                    if (
                        not name_in(local_scope, x.ident)
                        and not name_in(builtins_ns, x.ident)
                        and not name_in(bound, x.ident)
                    ):
                        append_name_once(free, x.ident)
                    return
                if _is_call_node(x):
                    # Use getattr-with-default rather than direct .ident
                    # access: pcc-py self-host's isinstance dispatch can
                    # return True against ``Name`` for a base ``Expr``
                    # instance that does NOT actually have ``ident``,
                    # causing AttributeError under ``self.builder.X``-
                    # style chains. Falling back to None lets the
                    # generic recursion handle the foreign-shape Call.
                    fname = _call_ident(x.func)
                    if fname is None:
                        for gen_arg in x.args[1:]:
                            if _has_gen_clause(gen_arg):
                                # Self-host may lose ident on calls that
                                # still carry explicit _gen_clause operands.
                                fname = "_list_comp"
                                break
                    if fname is None:
                        for slot in _dataclass_field_names(x):
                            v = _dataclass_field_value(x, slot, None)
                            if isinstance(v, tuple):
                                for it in v:
                                    walk(it, bound)
                            else:
                                walk(v, bound)
                        return
                    if (
                        fname
                        in (
                            "_list_comp",
                            "_set_comp",
                            "_gen_comp",
                            "__listcomp__",
                            "__setcomp__",
                            "__genexpr__",
                        )
                        and x.args
                    ):
                        gen_sources = (
                            x.args[1:] if not fname.startswith("__") else (x.args[-1],)
                        )
                        clauses = _collect_gen_clauses(gen_sources)
                        comp_bound = copy_names(bound)
                        for clause in clauses:
                            spec = _decompose_gen_clause(clause)
                            if spec is None:
                                continue
                            target, _, _ = spec
                            if target is not None:
                                _collect_target_names(target, comp_bound)
                        walk(x.args[0], comp_bound)
                        running_bound = copy_names(bound)
                        for clause in clauses:
                            spec = _decompose_gen_clause(clause)
                            if spec is None:
                                continue
                            target, iter_expr, ifs_expr = spec
                            if iter_expr is not None:
                                walk(iter_expr, running_bound)
                            if target is not None:
                                _collect_target_names(target, running_bound)
                            if ifs_expr is not None:
                                walk(ifs_expr, running_bound)
                        return
                    if fname in ("_dict_comp", "__dictcomp__") and x.args:
                        gen_sources = (
                            x.args[1:] if not fname.startswith("__") else (x.args[-1],)
                        )
                        clauses = _collect_gen_clauses(gen_sources)
                        comp_bound = copy_names(bound)
                        for clause in clauses:
                            spec = _decompose_gen_clause(clause)
                            if spec is None:
                                continue
                            target, _, _ = spec
                            if target is not None:
                                _collect_target_names(target, comp_bound)
                        walk(x.args[0], comp_bound)
                        if fname == "__dictcomp__":
                            if len(x.args) >= 2:
                                walk(x.args[1], comp_bound)
                        running_bound = copy_names(bound)
                        for clause in clauses:
                            spec = _decompose_gen_clause(clause)
                            if spec is None:
                                continue
                            target, iter_expr, ifs_expr = spec
                            if iter_expr is not None:
                                walk(iter_expr, running_bound)
                            if target is not None:
                                _collect_target_names(target, running_bound)
                            if ifs_expr is not None:
                                walk(ifs_expr, running_bound)
                        return
                    if fname == "_gen_clause" and x.args:
                        # _gen_clause(target, iter, (ifs,))
                        target = x.args[0]
                        new_bound = copy_names(bound)
                        _collect_target_names(target, new_bound)
                        for a in x.args[1:]:
                            walk(a, new_bound)
                        return
                for slot in _dataclass_field_names(x):
                    v = _dataclass_field_value(x, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            walk(it, bound)
                    else:
                        walk(v, bound)

            for s in fd.body:
                walk(s)
            extend_names_once(free, nonlocal_names)
            result = filter_capture_names(tuple(sorted(free)))
            compute_free_names_cache[cache_key] = result
            return result

        def body_reads_free_names(fd, excluded):
            return bool(compute_free_names(fd, excluded))

        def collect_scope_bindings(stmts):
            """Return names bound somewhere in the current lexical scope."""
            from ..py_ast import (
                Import as _ImportStmt,
                ImportFrom as _ImportFromStmt,
                TupleExpr as _TupleExpr,
            )

            bindings = []

            def add_target_names(t):
                if isinstance(t, _Name):
                    append_name_once(bindings, t.ident)
                elif isinstance(t, _TupleExpr):
                    for e in t.elems:
                        add_target_names(e)

            def walk(stmts):
                for s in stmts:
                    if isinstance(s, _Assign):
                        for t in s.targets:
                            add_target_names(t)
                    elif isinstance(s, _For):
                        add_target_names(s.target)
                        walk(s.body)
                        walk(s.else_body)
                    elif isinstance(s, _If):
                        walk(s.body)
                        walk(s.else_body)
                    elif isinstance(s, _While):
                        walk(s.body)
                        walk(s.else_body)
                    elif isinstance(s, _With):
                        for _, as_var in s.items:
                            if as_var is not None:
                                add_target_names(as_var)
                        walk(s.body)
                    elif isinstance(s, _Try):
                        walk(s.body)
                        walk(s.else_body)
                        walk(s.finally_body)
                        for h in s.handlers:
                            if h.name:
                                append_name_once(bindings, h.name)
                            walk(h.body)
                    elif isinstance(s, _ImportStmt):
                        for mod_name, asname in s.names:
                            bound = asname or mod_name.split(".", 1)[0]
                            if bound:
                                append_name_once(bindings, bound)
                    elif _is_import_from_stmt(s):
                        for imported_name, asname in _import_names_from_stmt(s):
                            if imported_name == "*":
                                continue
                            append_name_once(
                                bindings,
                                asname or imported_name,
                            )
                    elif isinstance(s, (_FuncDef, _ClassDef)):
                        append_name_once(bindings, s.name)

            walk(stmts)
            return tuple(bindings)

        def rewrite_body(
            stmts,
            rename_map,
            scope_names,
            enclosing_class_name=None,
            enclosing_method_kind=None,
        ):
            """Return a new body tuple with nested defs stripped out
            and inner-name Call sites rewritten through rename_map."""
            _hoist_log(debug_hoist, mod_name, "rewrite_body begin")
            locally_bound_names_cache = {}
            called_sibling_names_cache = {}
            referenced_sibling_names_cache = {}
            forwarded_value_capture_names_cache = {}
            sibling_effective_free_names_cache = {}
            # Pre-scan: pretend every sibling nested FuncDef already
            # hoisted so mutual-recursive siblings don't capture each
            # other as free vars. Each will get its real hoisted name
            # inserted when the main loop reaches it.
            prescan_map = copy_name_map(rename_map)
            for st in stmts:
                if isinstance(st, _FuncDef) and st.name not in prescan_map:
                    # Placeholder value — actual hoisted name assigned
                    # during the hoist branch below. Existence in the
                    # map is what ``compute_free_names``' excluded
                    # argument needs.
                    prescan_map[st.name] = (f"__nested_{st.name}", ())
            _hoist_log(debug_hoist, mod_name, "rewrite_body prescan done")
            sibling_names = []
            for st in stmts:
                if isinstance(st, _FuncDef):
                    append_name_once(sibling_names, st.name)

            def filter_sibling_capture_names(names, current_sibling_names):
                out = []
                for name in names:
                    if not name_in(current_sibling_names, name):
                        out.append(name)
                return tuple(out)

            def filter_renamed_capture_names(names):
                out = []
                for name in names:
                    discard = False
                    for key, mapped in rename_map.items():
                        mapped_name = mapped[0] if isinstance(mapped, tuple) else mapped
                        if name == key or name == mapped_name:
                            discard = True
                            break
                    if not discard:
                        out.append(name)
                return tuple(out)

            def locally_bound_names(fd):
                """Names that are already available inside ``fd`` without
                capturing them from the enclosing scope."""
                cache_key = _hoist_cache_key("local", fd)
                cached = locally_bound_names_cache.get(cache_key)
                if cached is not None:
                    return cached
                bound = []
                for a in fd.args:
                    if a.name != "":
                        append_name_once(bound, a.name)
                extend_names_once(bound, collect_scope_bindings(fd.body))
                result = tuple(bound)
                locally_bound_names_cache[cache_key] = result
                return result

            def called_sibling_names(fd, current_sibling_names):
                """Collect sibling nested defs called directly from
                ``fd``. Their own captures must be threaded through the
                caller once the call site is rewritten to the hoisted
                form with synthetic capture kwargs."""
                cache_key = _hoist_cache_key("called", fd)
                cached = called_sibling_names_cache.get(cache_key)
                if cached is not None:
                    hoist_stat_inc(
                        hoist_profile_enabled,
                        hoist_stats,
                        "called_sibling_names_cache_hits",
                    )
                    return cached
                hoist_stat_inc(
                    hoist_profile_enabled,
                    hoist_stats,
                    "called_sibling_names_calls",
                )
                out = []
                local_bound = locally_bound_names(fd)

                def walk(x):
                    if x is None:
                        return
                    if isinstance(x, tuple):
                        for it in x:
                            walk(it)
                        return
                    if isinstance(x, (_FuncDef, _ClassDef)):
                        return
                    if isinstance(x, _Call):
                        fname = getattr(x.func, "ident", None)
                        if (
                            isinstance(x.func, _Name)
                            and isinstance(fname, str)
                            and name_in(current_sibling_names, fname)
                            and not name_in(local_bound, fname)
                        ):
                            append_name_once(out, fname)
                        walk(x.func)
                        for arg in x.args:
                            walk(arg)
                        for _key, value in x.kwargs:
                            walk(value)
                        return
                    if isinstance(x, _Attr):
                        walk(x.obj)
                        return
                    if isinstance(x, _Subscript):
                        walk(x.obj)
                        walk(x.idx)
                        return
                    if isinstance(x, _Slice):
                        walk(x.lo)
                        walk(x.hi)
                        walk(x.step)
                        return
                    if isinstance(x, _BinOp):
                        walk(x.lhs)
                        walk(x.rhs)
                        return
                    if isinstance(x, _UnaryOp):
                        walk(x.operand)
                        return
                    if isinstance(x, _Compare):
                        walk(x.lhs)
                        walk(x.rhs)
                        return
                    if isinstance(x, _BoolExpr):
                        walk(x.left)
                        walk(x.right)
                        return
                    if isinstance(x, _IfExpr):
                        walk(x.cond)
                        walk(x.then_e)
                        walk(x.else_e)
                        return
                    if isinstance(x, (_ListExpr, _TupleExpr)):
                        for item in x.elems:
                            walk(item)
                        return
                    if isinstance(x, _DictExpr):
                        for key, value in x.pairs:
                            walk(key)
                            walk(value)
                        return
                    if isinstance(x, _Lambda):
                        walk(x.body)
                        return
                    if isinstance(x, _Assign):
                        for target in x.targets:
                            walk(target)
                        walk(x.value)
                        return
                    if isinstance(x, _AugAssign):
                        walk(x.target)
                        walk(x.value)
                        return
                    if isinstance(x, _ExprStmt):
                        walk(x.expr)
                        return
                    if isinstance(x, _Return):
                        walk(x.value)
                        return
                    if isinstance(x, _If):
                        walk(x.cond)
                        walk(x.body)
                        walk(x.else_body)
                        return
                    if isinstance(x, _While):
                        walk(x.cond)
                        walk(x.body)
                        walk(x.else_body)
                        return
                    if isinstance(x, _For):
                        walk(x.target)
                        walk(x.iter)
                        walk(x.body)
                        walk(x.else_body)
                        return
                    if isinstance(x, _Try):
                        walk(x.body)
                        for handler in x.handlers:
                            walk(handler.exc_type)
                            walk(handler.body)
                        walk(x.else_body)
                        walk(x.finally_body)
                        return
                    if isinstance(x, _With):
                        for ctx_expr, as_var in x.items:
                            walk(ctx_expr)
                            walk(as_var)
                        walk(x.body)
                        return
                    if isinstance(x, _Raise):
                        walk(x.exc)
                        walk(x.cause)
                        return
                    if isinstance(x, _Delete):
                        for target in x.targets:
                            walk(target)

                for s in fd.body:
                    walk(s)
                result = tuple(out)
                called_sibling_names_cache[cache_key] = result
                return result

            def referenced_sibling_names(fd, current_sibling_names):
                """Collect sibling nested defs referenced by ``fd`` in any
                expression position.

                ``called_sibling_names`` is intentionally narrow so call-site
                rewriting only fires for direct calls. Capture propagation has
                to be wider: a sibling used as a callback, stored in a local,
                or called inside an expression still needs its captures made
                available in the referencing function's environment.
                """
                cache_key = _hoist_cache_key("referenced", fd)
                cached = referenced_sibling_names_cache.get(cache_key)
                if cached is not None:
                    hoist_stat_inc(
                        hoist_profile_enabled,
                        hoist_stats,
                        "referenced_sibling_names_cache_hits",
                    )
                    return cached
                hoist_stat_inc(
                    hoist_profile_enabled,
                    hoist_stats,
                    "referenced_sibling_names_calls",
                )
                out = []

                def walk(x):
                    if x is None:
                        return
                    if isinstance(x, tuple):
                        for it in x:
                            walk(it)
                        return
                    if isinstance(x, (_FuncDef, _ClassDef)):
                        return
                    if isinstance(x, _Name) and name_in(current_sibling_names, x.ident):
                        if x.ident != fd.name:
                            append_name_once(out, x.ident)
                        return
                    if isinstance(x, _Call):
                        walk(x.func)
                        for arg in x.args:
                            walk(arg)
                        for _key, value in x.kwargs:
                            walk(value)
                        return
                    if isinstance(x, _Attr):
                        walk(x.obj)
                        return
                    if isinstance(x, _Subscript):
                        walk(x.obj)
                        walk(x.idx)
                        return
                    if isinstance(x, _Slice):
                        walk(x.lo)
                        walk(x.hi)
                        walk(x.step)
                        return
                    if isinstance(x, _BinOp):
                        walk(x.lhs)
                        walk(x.rhs)
                        return
                    if isinstance(x, _UnaryOp):
                        walk(x.operand)
                        return
                    if isinstance(x, _Compare):
                        walk(x.lhs)
                        walk(x.rhs)
                        return
                    if isinstance(x, _BoolExpr):
                        walk(x.left)
                        walk(x.right)
                        return
                    if isinstance(x, _IfExpr):
                        walk(x.cond)
                        walk(x.then_e)
                        walk(x.else_e)
                        return
                    if isinstance(x, (_ListExpr, _TupleExpr)):
                        for item in x.elems:
                            walk(item)
                        return
                    if isinstance(x, _DictExpr):
                        for key, value in x.pairs:
                            walk(key)
                            walk(value)
                        return
                    if isinstance(x, _Lambda):
                        walk(x.body)
                        return
                    if isinstance(x, _Assign):
                        for target in x.targets:
                            walk(target)
                        walk(x.value)
                        return
                    if isinstance(x, _AugAssign):
                        walk(x.target)
                        walk(x.value)
                        return
                    if isinstance(x, _ExprStmt):
                        walk(x.expr)
                        return
                    if isinstance(x, _Return):
                        walk(x.value)
                        return
                    if isinstance(x, _If):
                        walk(x.cond)
                        walk(x.body)
                        walk(x.else_body)
                        return
                    if isinstance(x, _While):
                        walk(x.cond)
                        walk(x.body)
                        walk(x.else_body)
                        return
                    if isinstance(x, _For):
                        walk(x.target)
                        walk(x.iter)
                        walk(x.body)
                        walk(x.else_body)
                        return
                    if isinstance(x, _Try):
                        walk(x.body)
                        for handler in x.handlers:
                            walk(handler.exc_type)
                            walk(handler.body)
                        walk(x.else_body)
                        walk(x.finally_body)
                        return
                    if isinstance(x, _With):
                        for ctx_expr, as_var in x.items:
                            walk(ctx_expr)
                            walk(as_var)
                        walk(x.body)
                        return
                    if isinstance(x, _Raise):
                        walk(x.exc)
                        walk(x.cause)
                        return
                    if isinstance(x, _Delete):
                        for target in x.targets:
                            walk(target)

                for s in fd.body:
                    walk(s)
                result = tuple(out)
                referenced_sibling_names_cache[cache_key] = result
                return result

            def forwarded_value_capture_names(
                fd,
                excluded_names,
                outer_scope_names,
                outer_local_bound,
            ):
                """Captures needed by nested defs used as first-class values.

                When ``fd`` returns or stores a nested def (instead of
                calling it directly), the outer hoisted wrapper must carry
                any outer-scope captures that nested def still needs. This
                is the ``make_body_for -> body`` shape in layer1's own
                comprehension helpers.
                """
                cache_key = _hoist_cache_key4(
                    "forwarded",
                    fd,
                    excluded_names,
                    outer_scope_names,
                    outer_local_bound,
                )
                cached = forwarded_value_capture_names_cache.get(cache_key)
                if cached is not None:
                    return cached
                out = []
                for inner_fd in fd.body:
                    if not isinstance(inner_fd, _FuncDef):
                        continue
                    inner_local_bound = locally_bound_names(inner_fd)
                    inner_free = compute_free_names(inner_fd, excluded_names)
                    inner_forwarded = forwarded_value_capture_names(
                        inner_fd,
                        excluded_names,
                        outer_scope_names,
                        inner_local_bound,
                    )
                    inner_needed = []
                    extend_names_once(inner_needed, inner_free)
                    for fv in inner_forwarded:
                        if not name_in(inner_local_bound, fv):
                            append_name_once(inner_needed, fv)
                    if not body_uses_name_as_value(fd.body, inner_fd.name):
                        continue
                    for fv in inner_needed:
                        if name_in(outer_scope_names, fv) and not name_in(
                            outer_local_bound, fv
                        ):
                            append_name_once(out, fv)
                result = tuple(out)
                forwarded_value_capture_names_cache[cache_key] = result
                return result

            excluded_prescan = []
            for k, v in prescan_map.items():
                append_name_once(excluded_prescan, k)
                if isinstance(v, tuple):
                    append_name_once(excluded_prescan, v[0])
                else:
                    append_name_once(excluded_prescan, v)

            def sibling_funcdef(name):
                for sibling_stmt in stmts:
                    if isinstance(sibling_stmt, _FuncDef) and sibling_stmt.name == name:
                        return sibling_stmt
                return None

            def sibling_effective_free_names(fd, seen_names):
                cache_key = _hoist_cache_key("effective", fd)
                cached = sibling_effective_free_names_cache.get(cache_key)
                if cached is not None:
                    hoist_stat_inc(
                        hoist_profile_enabled,
                        hoist_stats,
                        "sibling_effective_free_names_cache_hits",
                    )
                    return cached
                hoist_stat_inc(
                    hoist_profile_enabled,
                    hoist_stats,
                    "sibling_effective_free_names_calls",
                )
                out = []
                extend_names_once(out, compute_free_names(fd, excluded_prescan))
                local_bound = locally_bound_names(fd)
                extend_names_once(
                    out,
                    forwarded_value_capture_names(
                        fd,
                        excluded_prescan,
                        scope_names,
                        local_bound,
                    ),
                )
                result = tuple(sorted(out))
                sibling_effective_free_names_cache[cache_key] = result
                return result

            effective_free_names: dict[str, tuple] = {}
            _hoist_log(debug_hoist, mod_name, "rewrite_body effective begin")
            for st in stmts:
                if not isinstance(st, _FuncDef):
                    continue
                effective_free_names[st.name] = sibling_effective_free_names(
                    st,
                    (st.name,),
                )
            _hoist_log(debug_hoist, mod_name, "rewrite_body effective seed done")
            # Fixed-point propagation for sibling nested functions.
            # If one nested function calls or otherwise references
            # another sibling, the caller must carry every outer-scope
            # capture needed by the callee. Otherwise a later value
            # wrapper (e.g. ``sorted(xs, key=_pred_sort_key)`` where
            # ``_pred_sort_key`` calls ``_branch_arm_priority``) tries
            # to materialize the callee's capture in the caller body
            # and finds it unbound.
            changed_effective = True
            while changed_effective:
                changed_effective = False
                for st in stmts:
                    if not isinstance(st, _FuncDef):
                        continue
                    current = []
                    extend_names_once(
                        current,
                        effective_free_names.get(st.name, ()),
                    )
                    local_bound = locally_bound_names(st)
                    deps = []
                    extend_names_once(deps, called_sibling_names(st, sibling_names))
                    extend_names_once(deps, referenced_sibling_names(st, sibling_names))
                    for dep in deps:
                        dep_caps = effective_free_names.get(dep, ())
                        for fv in dep_caps:
                            if (
                                name_in(scope_names, fv)
                                and not name_in(local_bound, fv)
                                and not is_discard_capture_name(fv)
                            ):
                                append_name_once(current, fv)
                    current_tuple = tuple(sorted(current))
                    current_tuple = filter_capture_names(current_tuple)
                    current_tuple = filter_sibling_capture_names(
                        current_tuple,
                        sibling_names,
                    )
                    current_tuple = filter_renamed_capture_names(current_tuple)
                    if current_tuple != effective_free_names.get(st.name, ()):
                        effective_free_names[st.name] = current_tuple
                        changed_effective = True
            _hoist_log(debug_hoist, mod_name, "rewrite_body fixedpoint done")
            for sibling_name in sibling_names:
                mapped = prescan_map.get(sibling_name)
                if isinstance(mapped, tuple):
                    raw_capture_names = effective_free_names.get(sibling_name, ())
                    capture_names = filter_capture_names(raw_capture_names)
                    capture_names = filter_sibling_capture_names(
                        capture_names,
                        sibling_names,
                    )
                    capture_names = filter_renamed_capture_names(capture_names)
                    prescan_map[sibling_name] = (
                        mapped[0],
                        tuple(sorted(capture_names)),
                    )
            new_stmts = []
            stmt_debug_idx = 0
            for st in stmts:
                _hoist_log(debug_hoist, mod_name, 
                    "rewrite stmt "
                    + str(stmt_debug_idx)
                    + " "
                    + hoist_stmt_kind(st)
                )
                stmt_debug_idx += 1
                synthesized_lambda_func = False
                # ``name = lambda params: body`` — rewrite to a regular
                # nested ``def name(params): return body`` statement so
                # the lambda lifting falls out of the existing FuncDef
                # hoist (closure conversion + recursive-call rewrite
                # already apply). Matches Python's equivalence for
                # name-bound lambdas.
                lambda_target_ident = ""
                if (
                    isinstance(st, _Assign)
                    and isinstance(st.value, _Lambda)
                    and len(st.targets) == 1
                    and isinstance(st.targets[0], _Name)
                ):
                    lambda_target_ident = _dataclass_field_value(
                        st.targets[0],
                        "ident",
                        "",
                    )
                if (
                    lambda_target_ident
                    and isinstance(st, _Assign)
                    and isinstance(st.value, _Lambda)
                    and len(st.targets) == 1
                    and isinstance(st.targets[0], _Name)
                ):
                    lam = st.value
                    # Give each lambda param a DynType annotation when
                    # none was declared (lambdas usually omit them),
                    # matching the annotation gate in
                    # ``_declare_user_function``.
                    params_list = []
                    for a in lam.params:
                        param = a
                        if a.annotation is None:
                            param = _replace(a, annotation=_DYN)
                        params_list.append(param)
                    params = tuple(params_list)
                    return_stmt = Return(span=st.span, value=lam.body)
                    body_tuple = (return_stmt,)
                    fd_stmt = FuncDef(
                        span=st.span,
                        name=lambda_target_ident,
                        args=params,
                        return_ty=_DYN,
                        body=body_tuple,
                        is_async=False,
                        decorators=(),
                    )
                    lambda_free_names = tuple(
                        sorted(
                            compute_free_names(
                                fd_stmt,
                                excluded_prescan,
                            )
                        )
                    )
                    lambda_free_names = filter_capture_names(lambda_free_names)
                    lambda_free_names = filter_sibling_capture_names(
                        lambda_free_names,
                        sibling_names,
                    )
                    lambda_free_names = filter_renamed_capture_names(lambda_free_names)
                    if not lambda_free_names:
                        hoist_name = f"__nested_{fd_stmt.name}"
                        suffix = 0
                        existing = existing_top_or_hoisted_names(False)
                        final_name = hoist_name
                        while name_in(existing, final_name):
                            suffix += 1
                            final_name = f"{hoist_name}_{suffix}"
                        hoisted_capture_params[final_name] = ()
                        hoisted_fd = clone_funcdef(
                            fd_stmt,
                            final_name,
                            params,
                            _DYN,
                            fd_stmt.body,
                        )
                        hoisted.append(hoisted_fd)
                        rename_map[fd_stmt.name] = (final_name, ())
                        continue
                    st = fd_stmt
                    synthesized_lambda_func = True
                if not synthesized_lambda_func and isinstance(st, _ClassDef):
                    # Hoist nested ClassDef to module top level so
                    # ``_declare_user_function`` / ``emit_methods`` can
                    # process it via the standard class path. Nested
                    # instance methods sometimes read outer locals
                    # (e.g. ``ctx`` in a pass-local helper class); for
                    # those, rewrite bare capture reads to hidden
                    # instance attrs and attach the values at
                    # instantiation time. This is a one-shot closure
                    # approximation, but it unblocks the self-host
                    # survey on current nested-helper-class patterns.
                    existing = existing_top_or_hoisted_names(True)
                    hoist_name = st.name
                    suffix = 0
                    while name_in(existing, hoist_name):
                        suffix += 1
                        hoist_name = f"{st.name}_nest{suffix}"
                    class_free_names = []
                    new_class_body = []
                    for body_stmt in st.body:
                        if isinstance(body_stmt, _FuncDef):
                            recv_name = None
                            for a in body_stmt.args:
                                if a.name != "":
                                    recv_name = a.name
                                    break
                            method_free_names = []
                            for fv in compute_free_names(
                                body_stmt,
                                excluded_prescan,
                            ):
                                if name_in(
                                    scope_names, fv
                                ) and not is_discard_capture_name(fv):
                                    append_name_once(method_free_names, fv)
                            method_free = tuple(method_free_names)
                            if recv_name is not None and method_free:
                                cap_names = method_free

                                def rewrite_cap_node(x):
                                    if x is None:
                                        return x
                                    if isinstance(x, (_FuncDef, _ClassDef)):
                                        return x
                                    if isinstance(x, tuple):
                                        rewritten_items = []
                                        for it in x:
                                            rewritten_items.append(rewrite_cap_node(it))
                                        return tuple(rewritten_items)
                                    if isinstance(x, Name) and name_in(
                                        cap_names, x.ident
                                    ):
                                        return Attr(
                                            span=x.span,
                                            ty=_DYN,
                                            obj=Name(
                                                span=x.span,
                                                ty=_DYN,
                                                ident=recv_name,
                                            ),
                                            name=f"__pcc_cap_{x.ident}",
                                        )
                                    fields = tuple(_dataclass_field_names(x))
                                    if fields:
                                        replacements = {}
                                        for slot in fields:
                                            v = _dataclass_field_value(x, slot, None)
                                            new_v = rewrite_cap_node(v)
                                            if new_v != v:
                                                replacements[slot] = new_v
                                        if replacements:
                                            return _replace(x, **replacements)
                                    return x

                                rewritten_body_items = []
                                for s in body_stmt.body:
                                    rewritten_body_items.append(rewrite_cap_node(s))
                                body_stmt = _replace(
                                    body_stmt,
                                    body=tuple(rewritten_body_items),
                                )
                                extend_names_once(class_free_names, method_free)
                        new_class_body.append(body_stmt)
                    hoisted_cd = _replace(
                        st,
                        name=hoist_name,
                        body=tuple(new_class_body),
                    )
                    hoisted.append(hoisted_cd)
                    if class_free_names:
                        hoisted_class_capture_params[hoist_name] = tuple(
                            sorted(class_free_names)
                        )
                    if hoist_name != st.name:
                        rename_map[st.name] = hoist_name
                    continue
                if synthesized_lambda_func or isinstance(st, _FuncDef):
                    # Closure conversion threads ``self`` through as
                    # just another free-var capture when the nested
                    # def is inside a method body. The hoisted symbol
                    # lands at module scope with ``self`` as a
                    # trailing DynType param; ``self.<attr>`` reads
                    # then resolve via the DynType attribute path
                    # (``py_cpy_getattr`` / ``py_obj_getattr``) at
                    # runtime — slower than the compile-time class
                    # layout path but correct, and good enough to
                    # unblock solo-compile on the affected files.
                    # Skip hoisting when the nested name is used as a
                    # first-class value in a shape pcc cannot represent
                    # honestly. CPython API callbacks still need the
                    # pointer-shaped trampoline path; returned nested
                    # defs can now stay native as PY_TYPE_FUNC closure
                    # objects, including typed params/captures.
                    if body_uses_name_as_value(stmts, st.name):
                        # Check: 1 / 2 / 3 params — and every runtime
                        # param must either be unannotated / DynType or
                        # lower to a PyObject* at the IR level (the
                        # wrap trampoline hands CPython PyObject*s).
                        # Native-ABI types (int, float) would see bad
                        # data, so require pointer-shaped params.
                        _wrap_ok_annotations = (
                            _DynType,
                            StrType,
                            ListType,
                            DictType,
                            TupleType,
                            NoneType,
                        )
                        runtime_params = []
                        for a in st.args:
                            if a.name != "":
                                runtime_params.append(a)
                        simple_shape = 0 <= len(runtime_params) <= 3 and all(
                            a.annotation is None
                            or isinstance(
                                a.annotation,
                                _wrap_ok_annotations,
                            )
                            for a in runtime_params
                        )
                        excluded_pre = []
                        for k, v in prescan_map.items():
                            append_name_once(excluded_pre, k)
                            if isinstance(v, tuple):
                                append_name_once(excluded_pre, v[0])
                            else:
                                append_name_once(excluded_pre, v)
                        has_free = bool(
                            effective_free_names.get(
                                st.name,
                                compute_free_names(st, excluded_pre),
                            )
                        )
                        if not simple_shape and not body_returns_name(stmts, st.name):
                            new_stmts.append(st)
                            continue
                        if has_free:
                            # Track this nested def for adapter-wrap at
                            # value-position ``_emit_name``. The
                            # hoisted function carries captures as
                            # trailing kwarg params; the adapter
                            # synthesized at value position reads those
                            # captures from per-name internal globals
                            # (populated at wrap time in the outer
                            # scope) and calls the full-arity hoisted
                            # version.
                            # Actual capture list is computed further
                            # below once ``free_names`` is resolved;
                            # seed here with empty and patch later.
                            hoist_wrap_caps[st.name] = {
                                "original_arity": len(runtime_params),
                                "free_names": (),
                                "hoisted_name": None,
                            }
                    # Mutable-capture path: if the nested def mutates
                    # a free variable (``nonlocal X; X += 1`` pattern),
                    # the outer body has already been preprocessed by
                    # ``box_outer_body`` to box that name into a
                    # 1-element list. Every read/write of X in both
                    # outer and inner goes through ``X[0]`` subscript
                    # lookups, so closure-by-value is now correct —
                    # the list reference is shared.
                    # ``prescan_map`` already includes every sibling
                    # nested FuncDef's (original and hoisted) names —
                    # so mutual-recursive siblings don't capture each
                    # other as free vars. Also folds in outer-scope
                    # hoisted siblings that live in ``rename_map``.
                    excluded = []
                    for k, v in prescan_map.items():
                        append_name_once(excluded, k)
                        if isinstance(v, tuple):
                            append_name_once(excluded, v[0])
                        else:
                            append_name_once(excluded, v)
                    local_bound = locally_bound_names(st)
                    free_names = tuple(
                        sorted(
                            effective_free_names.get(
                                st.name,
                                compute_free_names(st, excluded),
                            )
                        )
                    )
                    free_names = filter_capture_names(free_names)
                    free_names = filter_sibling_capture_names(
                        free_names,
                        sibling_names,
                    )
                    free_names = filter_renamed_capture_names(free_names)
                    # Pick the hoisted symbol early so self-referential
                    # free-var detection works.
                    hoist_name = f"__nested_{st.name}"
                    suffix = 0
                    existing = existing_top_or_hoisted_names(False)
                    final_name = hoist_name
                    while name_in(existing, final_name):
                        suffix += 1
                        final_name = f"{hoist_name}_{suffix}"
                    free_names = filter_self_capture_names(
                        free_names,
                        st.name,
                        final_name,
                    )
                    while True:
                        # Closure conversion: prepend the free vars as
                        # extra trailing arguments with DynType annotation.
                        # Default to None so CPython-side kwarg fills
                        # aren't required, matching Python's no-default
                        # model for captured variables.
                        cap_args_list = []
                        for fv in free_names:
                            cap_args_list.append(
                                Arg(
                                    name=fv,
                                    annotation=_DYN,
                                    default=None,
                                    kind="pos",
                                    has_default=True,
                                )
                            )
                        cap_args = tuple(cap_args_list)
                        # Insert the captures BEFORE the first bare ``*``
                        # kw-only separator so the caller's trailing
                        # positional rewrite (appends captures after
                        # regular positionals) still maps correctly. A
                        # nested def like ``def f(a, b, *, k=1):`` stays
                        # ``def __nested_f(a, b, _cap1, _cap2, *, k=1)``
                        # rather than ending up with positional args
                        # after the kw-only separator, which
                        # ``_resolve_call_kwargs`` would reject.
                        orig_args = tuple(st.args)
                        split_idx = len(orig_args)
                        for i, a in enumerate(orig_args):
                            if a.name == "":
                                split_idx = i
                                break
                        new_args = (
                            orig_args[:split_idx] + cap_args + orig_args[split_idx:]
                        )
                        # Inside the inner body, recursive self-calls need
                        # to forward the same captured values. Seed the
                        # inner rename_map with the conversion entry so
                        # ``rewrite_expr`` rewrites the self-call's
                        # ``Call(Name(<inner_name>), args)`` to include
                        # the free vars as trailing positional args.
                        inner_map = copy_name_map(prescan_map)
                        update_name_map(inner_map, rename_map)
                        inner_map[st.name] = (final_name, free_names)
                        inner_scope = copy_names(scope_names)
                        for a in st.args:
                            if a.name != "":
                                append_name_once(inner_scope, a.name)
                        extend_names_once(
                            inner_scope,
                            collect_scope_bindings(st.body),
                        )
                        inner_body = rewrite_body(
                            st.body,
                            inner_map,
                            inner_scope,
                            enclosing_class_name,
                            enclosing_method_kind,
                        )
                        forwarded = []
                        for fv in compute_free_names(
                            clone_funcdef(
                                st,
                                st.name,
                                st.args,
                                st.return_ty,
                                inner_body,
                            ),
                            excluded,
                        ):
                            if (
                                name_in(scope_names, fv)
                                and not name_in(local_bound, fv)
                                and not is_discard_capture_name(fv)
                            ):
                                append_name_once(forwarded, fv)
                        extend_names_once(
                            forwarded,
                            forwarded_value_capture_names(
                                st,
                                excluded,
                                scope_names,
                                local_bound,
                            ),
                        )
                        for dep in called_sibling_names(st, sibling_names):
                            mapped = rename_map.get(dep)
                            if mapped is None:
                                continue
                            mapped_name = (
                                mapped[0] if isinstance(mapped, tuple) else mapped
                            )
                            dep_caps = hoisted_capture_params.get(
                                mapped_name,
                                (),
                            )
                            for fv in dep_caps:
                                if (
                                    name_in(scope_names, fv)
                                    and not name_in(local_bound, fv)
                                    and not is_discard_capture_name(fv)
                                ):
                                    append_name_once(forwarded, fv)
                        widened_names = []
                        extend_names_once(widened_names, free_names)
                        extend_names_once(widened_names, forwarded)
                        widened = tuple(sorted(widened_names))
                        widened = filter_self_capture_names(
                            widened,
                            st.name,
                            final_name,
                        )
                        widened = filter_sibling_capture_names(
                            widened,
                            sibling_names,
                        )
                        widened = filter_renamed_capture_names(widened)
                        if widened == free_names:
                            break
                        free_names = widened
                    # Update the adapter-wrap metadata (if this def was
                    # flagged at the body_uses_name_as_value gate).
                    cap_entry = hoist_wrap_caps.get(st.name)
                    if cap_entry is not None:
                        cap_entry["free_names"] = tuple(free_names)
                        cap_entry["hoisted_name"] = final_name
                        cap_entry["original_name"] = st.name
                        # Mirror under the hoisted name so
                        # ``_emit_name(<hoisted>)`` can find the
                        # entry when the rewrite_expr bare-Name
                        # rewrite has already swapped the ident.
                        hoist_wrap_caps[final_name] = cap_entry
                    hoisted_capture_params[final_name] = tuple(free_names)
                    if enclosing_class_name is not None:
                        hoisted_enclosing_class[final_name] = enclosing_class_name
                    if enclosing_method_kind is not None:
                        hoisted_enclosing_method_kind[final_name] = (
                            enclosing_method_kind
                        )
                    hoisted_fd = clone_funcdef(
                        st,
                        final_name,
                        new_args,
                        st.return_ty,
                        inner_body,
                    )
                    hoisted.append(hoisted_fd)
                    rename_map[st.name] = (final_name, free_names)
                    # Drop the original def from the current body.
                    continue
                new_stmts.append(
                    rewrite_stmt(
                        st,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    )
                )
            return tuple(new_stmts)

        def rewrite_stmt(
            stmt,
            rename_map,
            scope_names,
            enclosing_class_name,
            enclosing_method_kind,
        ):
            if isinstance(stmt, _If):
                return _replace(
                    stmt,
                    cond=rewrite_expr(stmt.cond, rename_map),
                    body=rewrite_body(
                        stmt.body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                    else_body=rewrite_body(
                        stmt.else_body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                )
            if isinstance(stmt, _While):
                return _replace(
                    stmt,
                    cond=rewrite_expr(stmt.cond, rename_map),
                    body=rewrite_body(
                        stmt.body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                    else_body=rewrite_body(
                        stmt.else_body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                )
            if isinstance(stmt, _For):
                return _replace(
                    stmt,
                    iter=rewrite_expr(stmt.iter, rename_map),
                    body=rewrite_body(
                        stmt.body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                    else_body=rewrite_body(
                        stmt.else_body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                )
            if isinstance(stmt, _Try):
                new_handlers = []
                for h in stmt.handlers:
                    new_handlers.append(
                        _replace(
                            h,
                            body=rewrite_body(
                                h.body,
                                rename_map,
                                scope_names,
                                enclosing_class_name,
                                enclosing_method_kind,
                            ),
                        )
                    )
                return _replace(
                    stmt,
                    body=rewrite_body(
                        stmt.body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                    else_body=rewrite_body(
                        stmt.else_body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                    finally_body=rewrite_body(
                        stmt.finally_body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                    handlers=tuple(new_handlers),
                )
            if isinstance(stmt, _With):
                return _replace(
                    stmt,
                    body=rewrite_body(
                        stmt.body,
                        rename_map,
                        scope_names,
                        enclosing_class_name,
                        enclosing_method_kind,
                    ),
                )
            if isinstance(stmt, _ExprStmt):
                return _replace(stmt, expr=rewrite_expr(stmt.expr, rename_map))
            if isinstance(stmt, _Assign):
                new_targets = []
                for target in stmt.targets:
                    new_targets.append(rewrite_expr(target, rename_map))
                return _replace(
                    stmt,
                    targets=tuple(new_targets),
                    value=rewrite_expr(stmt.value, rename_map),
                )
            if isinstance(stmt, _AugAssign):
                return _replace(
                    stmt,
                    target=rewrite_expr(stmt.target, rename_map),
                    value=rewrite_expr(stmt.value, rename_map),
                )
            if isinstance(stmt, _Return):
                if stmt.value is None:
                    return stmt
                return _replace(
                    stmt,
                    value=rewrite_expr(stmt.value, rename_map),
                )
            # ``del expr[idx]`` / ``del expr.attr`` — the expr and idx
            # may contain a nested Call to a hoisted sibling. Walk
            # each target through rewrite_expr so the rename lands.
            from ..py_ast import Delete as _Delete

            if isinstance(stmt, _Delete):
                new_targets = []
                for target in stmt.targets:
                    new_targets.append(rewrite_expr(target, rename_map))
                return _replace(
                    stmt,
                    targets=tuple(new_targets),
                )
            return stmt

        def rewrite_expr_tuple(items, rename_map):
            out = []
            for item in items:
                out.append(rewrite_expr(item, rename_map))
            return tuple(out)

        def rewrite_kwargs(items, rename_map):
            out = []
            for key, value in items:
                out.append((key, rewrite_expr(value, rename_map)))
            return tuple(out)

        def rewrite_expr(expr, rename_map):
            if isinstance(expr, _Call):
                # Only rewrite the Call's callee slot — leaves non-call
                # ``Name`` references (e.g. passing ``repl`` as a value
                # to ``re.sub(repl, ...)``) unrewritten so they fail
                # as ``unbound name 'repl'`` at the original call site,
                # which is a more honest error than ``unbound name
                # '__nested_repl'``. First-class function values are a
                # separate, larger feature.
                new_func = expr.func
                extra_kwargs: tuple = ()
                if isinstance(new_func, _Name) and new_func.ident in rename_map:
                    mapped = rename_map[new_func.ident]
                    if isinstance(mapped, tuple):
                        final_name, free_names = mapped
                    else:
                        final_name, free_names = mapped, ()
                    new_func = _replace(new_func, ident=final_name)
                    # Closure conversion: pass each captured var as a
                    # keyword argument with the capture's own name. The
                    # hoisted function has a synthetic param of the
                    # same ident, so kwarg-by-name fills that slot and
                    # does NOT collide with any user-supplied kwarg
                    # whose formal is closer to the front of the param
                    # list (``_specialize(clones, take_true=True)``
                    # would otherwise treat a positional capture as the
                    # 2nd positional param ``take_true`` and then the
                    # kwarg would duplicate). Name-based passing lets
                    # ``_resolve_call_kwargs`` resolve each by formal
                    # ident, regardless of the capture's position in
                    # the hoisted signature.
                    # Each capture value is ``Name(fv)`` resolved in the
                    # outer (caller) scope. If the outer rename_map has
                    # a hoisted sibling of the same name — e.g.
                    # ``exact_alias_source`` has itself been hoisted —
                    # the capture value needs to be the hoisted sibling
                    # name so ``_emit_name`` resolves it; otherwise a
                    # bare ``Name(original)`` would be unbound.
                    extra_items = []
                    for fv in free_names:
                        mapped_capture = rename_map.get(fv)
                        if mapped_capture is not None:
                            if isinstance(mapped_capture, tuple):
                                target = mapped_capture[0]
                            else:
                                target = mapped_capture
                        else:
                            target = fv
                        extra_items.append(
                            (
                                fv,
                                Name(span=expr.span, ty=_DYN, ident=target),
                            )
                        )
                    extra_kwargs = tuple(extra_items)
                else:
                    new_func = rewrite_expr(new_func, rename_map)
                    extra_kwargs = ()
                return _replace(
                    expr,
                    func=new_func,
                    args=rewrite_expr_tuple(expr.args, rename_map),
                    kwargs=rewrite_kwargs(expr.kwargs, rename_map) + extra_kwargs,
                )
            # Bare Name at value position: if it matches a hoisted
            # nested def in rename_map, rewrite to the hoisted symbol
            # so downstream ``_emit_name`` finds the function in
            # ``self.functions`` and can wrap it as a CPython callable
            # via ``py_cpy_wrap_pcc_1arg``. This converts patterns
            # like ``pattern.sub(repl, text)`` / ``am.register(K, repl)``
            # from ``unbound name`` to a usable callable.
            if isinstance(expr, _Name) and expr.ident in rename_map:
                mapped = rename_map[expr.ident]
                target = mapped[0] if isinstance(mapped, tuple) else mapped
                return _replace(expr, ident=target)
            if isinstance(expr, _Attr):
                return _replace(expr, obj=rewrite_expr(expr.obj, rename_map))
            if isinstance(expr, _Subscript):
                return _replace(
                    expr,
                    obj=rewrite_expr(expr.obj, rename_map),
                    idx=rewrite_expr(expr.idx, rename_map),
                )
            if isinstance(expr, _Slice):
                return _replace(
                    expr,
                    lo=rewrite_expr(expr.lo, rename_map),
                    hi=rewrite_expr(expr.hi, rename_map),
                    step=rewrite_expr(expr.step, rename_map),
                )
            if isinstance(expr, _BinOp):
                return _replace(
                    expr,
                    lhs=rewrite_expr(expr.lhs, rename_map),
                    rhs=rewrite_expr(expr.rhs, rename_map),
                )
            if isinstance(expr, _UnaryOp):
                return _replace(expr, operand=rewrite_expr(expr.operand, rename_map))
            if isinstance(expr, _Compare):
                return _replace(
                    expr,
                    lhs=rewrite_expr(expr.lhs, rename_map),
                    rhs=rewrite_expr(expr.rhs, rename_map),
                )
            if isinstance(expr, _BoolExpr):
                return _replace(
                    expr,
                    left=rewrite_expr(expr.left, rename_map),
                    right=rewrite_expr(expr.right, rename_map),
                )
            if isinstance(expr, _IfExpr):
                return _replace(
                    expr,
                    cond=rewrite_expr(expr.cond, rename_map),
                    then_e=rewrite_expr(expr.then_e, rename_map),
                    else_e=rewrite_expr(expr.else_e, rename_map),
                )
            if isinstance(expr, (_ListExpr, _TupleExpr)):
                return _replace(expr, elems=rewrite_expr_tuple(expr.elems, rename_map))
            if isinstance(expr, _DictExpr):
                pairs = []
                for key, value in expr.pairs:
                    pairs.append(
                        (
                            rewrite_expr(key, rename_map),
                            rewrite_expr(value, rename_map),
                        )
                    )
                return _replace(expr, pairs=tuple(pairs))
            if isinstance(expr, _Lambda):
                return _replace(expr, body=rewrite_expr(expr.body, rename_map))
            return expr

        from ..py_ast import (
            Attr as _Attr,
            ExprStmt as _ExprStmt2,
            ListExpr as _ListExpr,
        )

        def body_has_yield(stmts):
            """Detect a yield sentinel call anywhere in ``stmts``
            (not descending into nested defs)."""

            def generator_body_has_yield_expr(expr):
                if expr is None:
                    return False
                if (
                    isinstance(expr, _Call)
                    and isinstance(expr.func, _Name)
                    and expr.func.ident
                    in (
                        "_yield",
                        "_yield_from",
                        "__yield__",
                        "__yield_from__",
                    )
                ):
                    return True
                if isinstance(expr, _Call):
                    if generator_body_has_yield_expr(expr.func):
                        return True
                    for a in expr.args:
                        if generator_body_has_yield_expr(a):
                            return True
                    for _k, v in expr.kwargs:
                        if generator_body_has_yield_expr(v):
                            return True
                    return False
                if isinstance(expr, _Attr):
                    return generator_body_has_yield_expr(expr.obj)
                if isinstance(expr, _Subscript):
                    return generator_body_has_yield_expr(
                        expr.obj
                    ) or generator_body_has_yield_expr(expr.idx)
                if isinstance(expr, _Slice):
                    return (
                        generator_body_has_yield_expr(expr.lo)
                        or generator_body_has_yield_expr(expr.hi)
                        or generator_body_has_yield_expr(expr.step)
                    )
                if isinstance(expr, _BinOp):
                    return generator_body_has_yield_expr(
                        expr.lhs
                    ) or generator_body_has_yield_expr(expr.rhs)
                if isinstance(expr, _UnaryOp):
                    return generator_body_has_yield_expr(expr.operand)
                if isinstance(expr, _Compare):
                    return generator_body_has_yield_expr(
                        expr.lhs
                    ) or generator_body_has_yield_expr(expr.rhs)
                if isinstance(expr, _BoolExpr):
                    return generator_body_has_yield_expr(
                        expr.left
                    ) or generator_body_has_yield_expr(expr.right)
                if isinstance(expr, _ListExpr):
                    for e in expr.elems:
                        if generator_body_has_yield_expr(e):
                            return True
                    return False
                if isinstance(expr, _TupleExpr):
                    for e in expr.elems:
                        if generator_body_has_yield_expr(e):
                            return True
                    return False
                if isinstance(expr, _DictExpr):
                    for k, v in expr.pairs:
                        if generator_body_has_yield_expr(
                            k
                        ) or generator_body_has_yield_expr(v):
                            return True
                    return False
                if isinstance(expr, _IfExpr):
                    return (
                        generator_body_has_yield_expr(expr.cond)
                        or generator_body_has_yield_expr(expr.then_e)
                        or generator_body_has_yield_expr(expr.else_e)
                    )
                if isinstance(expr, _Lambda):
                    return False
                return False

            def generator_body_has_yield_stmt(stmt):
                if isinstance(stmt, _FuncDef) or isinstance(stmt, _ClassDef):
                    return False
                if isinstance(stmt, _ExprStmt):
                    return generator_body_has_yield_expr(stmt.expr)
                if isinstance(stmt, _Assign):
                    if generator_body_has_yield_expr(stmt.value):
                        return True
                    for t in stmt.targets:
                        if generator_body_has_yield_expr(t):
                            return True
                    return False
                if isinstance(stmt, _AugAssign):
                    return generator_body_has_yield_expr(
                        stmt.target
                    ) or generator_body_has_yield_expr(stmt.value)
                if isinstance(stmt, _Return):
                    return generator_body_has_yield_expr(stmt.value)
                if isinstance(stmt, _If):
                    return (
                        generator_body_has_yield_expr(stmt.cond)
                        or generator_body_has_yield_block(stmt.body)
                        or generator_body_has_yield_block(stmt.else_body)
                    )
                if isinstance(stmt, _While):
                    return (
                        generator_body_has_yield_expr(stmt.cond)
                        or generator_body_has_yield_block(stmt.body)
                        or generator_body_has_yield_block(stmt.else_body)
                    )
                if isinstance(stmt, _For):
                    return (
                        generator_body_has_yield_expr(stmt.target)
                        or generator_body_has_yield_expr(stmt.iter)
                        or generator_body_has_yield_block(stmt.body)
                        or generator_body_has_yield_block(stmt.else_body)
                    )
                if isinstance(stmt, _Try):
                    if generator_body_has_yield_block(stmt.body):
                        return True
                    for h in stmt.handlers:
                        if generator_body_has_yield_block(h.body):
                            return True
                    return generator_body_has_yield_block(
                        stmt.else_body
                    ) or generator_body_has_yield_block(stmt.finally_body)
                if isinstance(stmt, _With):
                    for ctx_expr, as_var in stmt.items:
                        if generator_body_has_yield_expr(
                            ctx_expr
                        ) or generator_body_has_yield_expr(as_var):
                            return True
                    return generator_body_has_yield_block(stmt.body)
                if isinstance(stmt, _Raise):
                    return generator_body_has_yield_expr(
                        stmt.exc
                    ) or generator_body_has_yield_expr(stmt.cause)
                if isinstance(stmt, _Delete):
                    for t in stmt.targets:
                        if generator_body_has_yield_expr(t):
                            return True
                    return False
                return False

            def generator_body_has_yield_block(block):
                for item in block:
                    if generator_body_has_yield_stmt(item):
                        return True
                return False

            for s in stmts:
                if generator_body_has_yield_stmt(s):
                    return True
            return False

        def rewrite_yield_in_stmts(stmts, accumulator_name):
            """Convert yield sentinel calls in the body into append /
            extend onto the accumulator list."""

            def rewrite_stmt(s):
                if isinstance(s, _FuncDef):
                    return s
                if isinstance(s, _ExprStmt2):
                    inner = s.expr
                    if (
                        isinstance(inner, _Call)
                        and isinstance(inner.func, _Name)
                        and inner.func.ident
                        in (
                            "_yield",
                            "_yield_from",
                            "__yield__",
                            "__yield_from__",
                        )
                        and len(inner.args) == 1
                    ):
                        method = (
                            "extend"
                            if inner.func.ident
                            in (
                                "_yield_from",
                                "__yield_from__",
                            )
                            else "append"
                        )
                        list_ty = ListType(
                            name="list",
                            elem=DynType(name="dyn"),
                        )
                        recv = Name(
                            span=inner.span,
                            ty=list_ty,
                            ident=accumulator_name,
                        )
                        call = Call(
                            span=inner.span,
                            ty=_DYN,
                            func=Attr(
                                span=inner.span,
                                ty=_DYN,
                                obj=recv,
                                name=method,
                            ),
                            args=(inner.args[0],),
                            kwargs=(),
                        )
                        return ExprStmt(span=s.span, expr=call)
                # Recurse into nested blocks.
                if isinstance(s, _If):
                    new_body = []
                    for x in s.body:
                        new_body.append(rewrite_stmt(x))
                    new_else_body = []
                    for x in s.else_body:
                        new_else_body.append(rewrite_stmt(x))
                    return _replace(
                        s,
                        body=tuple(new_body),
                        else_body=tuple(new_else_body),
                    )
                if isinstance(s, _While):
                    new_body = []
                    for x in s.body:
                        new_body.append(rewrite_stmt(x))
                    new_else_body = []
                    for x in s.else_body:
                        new_else_body.append(rewrite_stmt(x))
                    return _replace(
                        s,
                        body=tuple(new_body),
                        else_body=tuple(new_else_body),
                    )
                if isinstance(s, _For):
                    new_body = []
                    for x in s.body:
                        new_body.append(rewrite_stmt(x))
                    new_else_body = []
                    for x in s.else_body:
                        new_else_body.append(rewrite_stmt(x))
                    return _replace(
                        s,
                        body=tuple(new_body),
                        else_body=tuple(new_else_body),
                    )
                if isinstance(s, _Try):
                    new_body = []
                    for x in s.body:
                        new_body.append(rewrite_stmt(x))
                    new_else_body = []
                    for x in s.else_body:
                        new_else_body.append(rewrite_stmt(x))
                    new_finally_body = []
                    for x in s.finally_body:
                        new_finally_body.append(rewrite_stmt(x))
                    new_handlers = []
                    for h in s.handlers:
                        handler_body = []
                        for x in h.body:
                            handler_body.append(rewrite_stmt(x))
                        new_handlers.append(
                            _replace(h, body=tuple(handler_body))
                        )
                    return _replace(
                        s,
                        body=tuple(new_body),
                        else_body=tuple(new_else_body),
                        finally_body=tuple(new_finally_body),
                        handlers=tuple(new_handlers),
                    )
                if isinstance(s, _With):
                    new_body = []
                    for x in s.body:
                        new_body.append(rewrite_stmt(x))
                    return _replace(
                        s,
                        body=tuple(new_body),
                    )
                return s

            out = []
            for s in stmts:
                out.append(rewrite_stmt(s))
            return tuple(out)

        def body_needs_nested_rewrite(stmts) -> bool:
            def expr_needs_rewrite(expr) -> bool:
                if expr is None:
                    return False
                if isinstance(expr, _Lambda):
                    return True
                if isinstance(expr, tuple):
                    for item in expr:
                        if expr_needs_rewrite(item):
                            return True
                    return False
                if isinstance(expr, _Call):
                    if expr_needs_rewrite(expr.func):
                        return True
                    for item in expr.args:
                        if expr_needs_rewrite(item):
                            return True
                    for _key, value in expr.kwargs:
                        if expr_needs_rewrite(value):
                            return True
                    return False
                if isinstance(expr, _Attr):
                    return expr_needs_rewrite(expr.obj)
                if isinstance(expr, _Subscript):
                    return expr_needs_rewrite(expr.obj) or expr_needs_rewrite(expr.idx)
                if isinstance(expr, _Slice):
                    return (
                        expr_needs_rewrite(expr.lo)
                        or expr_needs_rewrite(expr.hi)
                        or expr_needs_rewrite(expr.step)
                    )
                if isinstance(expr, _BinOp):
                    return expr_needs_rewrite(expr.lhs) or expr_needs_rewrite(expr.rhs)
                if isinstance(expr, _UnaryOp):
                    return expr_needs_rewrite(expr.operand)
                if isinstance(expr, _Compare):
                    return expr_needs_rewrite(expr.lhs) or expr_needs_rewrite(expr.rhs)
                if isinstance(expr, _BoolExpr):
                    return expr_needs_rewrite(expr.left) or expr_needs_rewrite(expr.right)
                if isinstance(expr, _IfExpr):
                    return (
                        expr_needs_rewrite(expr.cond)
                        or expr_needs_rewrite(expr.then_e)
                        or expr_needs_rewrite(expr.else_e)
                    )
                if isinstance(expr, (_ListExpr, _TupleExpr)):
                    for item in expr.elems:
                        if expr_needs_rewrite(item):
                            return True
                    return False
                if isinstance(expr, _DictExpr):
                    for key, value in expr.pairs:
                        if expr_needs_rewrite(key) or expr_needs_rewrite(value):
                            return True
                    return False
                return False

            def stmt_needs_rewrite(stmt) -> bool:
                if isinstance(stmt, (_FuncDef, _ClassDef)):
                    return True
                if isinstance(stmt, _If):
                    return (
                        expr_needs_rewrite(stmt.cond)
                        or body_needs_nested_rewrite(stmt.body)
                        or body_needs_nested_rewrite(stmt.else_body)
                    )
                if isinstance(stmt, _While):
                    return (
                        expr_needs_rewrite(stmt.cond)
                        or body_needs_nested_rewrite(stmt.body)
                        or body_needs_nested_rewrite(stmt.else_body)
                    )
                if isinstance(stmt, _For):
                    return (
                        expr_needs_rewrite(stmt.target)
                        or expr_needs_rewrite(stmt.iter)
                        or body_needs_nested_rewrite(stmt.body)
                        or body_needs_nested_rewrite(stmt.else_body)
                    )
                if isinstance(stmt, _Try):
                    if body_needs_nested_rewrite(stmt.body):
                        return True
                    if body_needs_nested_rewrite(stmt.else_body):
                        return True
                    if body_needs_nested_rewrite(stmt.finally_body):
                        return True
                    for h in stmt.handlers:
                        if expr_needs_rewrite(h.exc_type):
                            return True
                        if body_needs_nested_rewrite(h.body):
                            return True
                    return False
                if isinstance(stmt, _With):
                    for ctx_expr, as_var in stmt.items:
                        if expr_needs_rewrite(ctx_expr) or expr_needs_rewrite(as_var):
                            return True
                    return body_needs_nested_rewrite(stmt.body)
                if isinstance(stmt, _Assign):
                    for target in stmt.targets:
                        if expr_needs_rewrite(target):
                            return True
                    return expr_needs_rewrite(stmt.value)
                if isinstance(stmt, _AugAssign):
                    return expr_needs_rewrite(stmt.target) or expr_needs_rewrite(stmt.value)
                if isinstance(stmt, _ExprStmt):
                    return expr_needs_rewrite(stmt.expr)
                if isinstance(stmt, _Return):
                    return expr_needs_rewrite(stmt.value)
                if isinstance(stmt, _Raise):
                    return expr_needs_rewrite(stmt.exc) or expr_needs_rewrite(stmt.cause)
                if isinstance(stmt, _Delete):
                    for target in stmt.targets:
                        if expr_needs_rewrite(target):
                            return True
                    return False
                if isinstance(stmt, (_Import, _ImportFrom, _Global, _Nonlocal)):
                    return False
                if isinstance(stmt, (_Pass, _Break, _Continue)):
                    return False
                for slot in _dataclass_field_names(stmt):
                    if slot != "span" and expr_needs_rewrite(
                        _dataclass_field_value(stmt, slot, None)
                    ):
                        return True
                return False

            for stmt in stmts:
                if stmt_needs_rewrite(stmt):
                    return True
            return False

        def transform_generator_body(fd):
            """Mark generator functions for lazy state-machine lowering."""
            if not body_has_yield(fd.body):
                return fd
            generator_func_names.add(fd.name)
            return clone_funcdef(fd, fd.name, fd.args, _DYN, fd.body)

        def hoist_stmt_kind(stmt) -> str:
            if isinstance(stmt, _If):
                return "If"
            if isinstance(stmt, _While):
                return "While"
            if isinstance(stmt, _For):
                return "For"
            if isinstance(stmt, _Try):
                return "Try"
            if isinstance(stmt, _With):
                return "With"
            if isinstance(stmt, _Assign):
                return "Assign"
            if isinstance(stmt, _AugAssign):
                return "AugAssign"
            if isinstance(stmt, _Return):
                return "Return"
            if isinstance(stmt, _ExprStmt):
                return "ExprStmt"
            if isinstance(stmt, _FuncDef):
                return "FuncDef"
            if isinstance(stmt, _ClassDef):
                return "ClassDef"
            return "Stmt"

        new_top_body = []
        for stmt in ast_module.body:
            if isinstance(stmt, _FuncDef):
                _hoist_log(debug_hoist, mod_name, "func begin " + stmt.name)
                stmt = transform_generator_body(stmt)
                if body_needs_nested_rewrite(stmt.body):
                    # Pre-pass: box any outer locals that nested defs
                    # mutate as free vars. After boxing, the hoist can
                    # safely closure-convert-by-value — the list reference
                    # is shared between outer and inner.
                    scope_names = []
                    for a in stmt.args:
                        if a.name != "":
                            append_name_once(scope_names, a.name)
                    boxed_body = box_outer_body(
                        stmt.body,
                        stmt.name,
                        tuple(scope_names),
                    )
                    extend_names_once(scope_names, collect_scope_bindings(boxed_body))
                    new_body = rewrite_body(boxed_body, {}, scope_names)
                else:
                    _hoist_log(debug_hoist, mod_name, "func skip " + stmt.name)
                    new_body = stmt.body
                new_top_body.append(
                    clone_funcdef(
                        stmt,
                        stmt.name,
                        stmt.args,
                        stmt.return_ty,
                        new_body,
                    )
                )
                _hoist_log(debug_hoist, mod_name, "func end " + stmt.name)
            elif isinstance(stmt, _ClassDef):
                # Rewrite each method's body.
                new_methods = []
                for m in stmt.body:
                    if isinstance(m, _FuncDef):
                        _hoist_log(debug_hoist, mod_name, "method begin " + stmt.name + "." + m.name)
                        m = transform_generator_body(m)
                        scope_names = []
                        for a in m.args:
                            if a.name != "":
                                append_name_once(scope_names, a.name)
                        if body_needs_nested_rewrite(m.body):
                            boxed_body = box_outer_body(
                                m.body,
                                m.name,
                                tuple(scope_names),
                            )
                            extend_names_once(
                                scope_names,
                                collect_scope_bindings(boxed_body),
                            )
                            new_body = rewrite_body(
                                boxed_body,
                                {},
                                scope_names,
                                stmt.name,
                                _hoist_method_kind(m),
                            )
                        else:
                            _hoist_log(debug_hoist, mod_name, "method skip " + stmt.name + "." + m.name)
                            new_body = m.body
                        new_methods.append(
                            clone_funcdef(
                                m,
                                m.name,
                                m.args,
                                m.return_ty,
                                new_body,
                            )
                        )
                        _hoist_log(debug_hoist, mod_name, "method end " + stmt.name + "." + m.name)
                    else:
                        new_methods.append(m)
                new_top_body.append(_replace(stmt, body=tuple(new_methods)))
            else:
                new_top_body.append(stmt)
        new_top_body.extend(hoisted)
        ast_module = _replace(
            ast_module,
            body=tuple(new_top_body),
        )
        setattr(self, "ast_module", ast_module)
        setattr(self, "_ast_body", ast_module.body)
        write_hoist_profile(
            hoist_profile_enabled,
            hoist_profile_path,
            hoist_stats,
        )
        return hoisted

    # -- user-function declaration / definition -----------------------






















    # ------------------------------------------------------- statements
