"""Subscript and slice lowering helpers for L1CodeGen."""
from __future__ import annotations

import os
import sys
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    BoolType,
    ByteArrayType,
    BytesType,
    Call,
    ClassType,
    DictType,
    DynType,
    Expr,
    IntType,
    ListExpr,
    ListType,
    MemoryViewType,
    Name,
    Slice,
    StrLit,
    StrType,
    Subscript,
    TupleExpr,
    TupleType,
    Type,
)
from . import marshal
from .runtime_abi import declare_runtime_global


_I8 = ir.IntType(8)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()


def _same_type_kind(a: Type, b: Type) -> bool:
    return type(a) is type(b)


class SubscriptLoweringMixin:
    def _emit_index_expr_as_i64(self, expr: Expr) -> ir.Value:
        if isinstance(expr.ty, (IntType, BoolType)):
            return self._emit_expr_as_i64(expr)
        obj = self._emit_as_object(expr)
        idx = self.builder.call(
            self.runtime["py_obj_index_i64"],
            [obj],
            name=self._fresh("index"),
        )
        self._emit_post_call_err_check(expr.span)
        return idx

    def _emit_subscript_store(self, target: Subscript, value_expr: Expr) -> None:
        rhs = self._emit_expr(value_expr)
        self._emit_subscript_store_value(
            target,
            rhs,
            value_expr.ty,
            release_expr=value_expr,
        )

    def _emit_subscript_store_value(
        self,
        target: Subscript,
        rhs: ir.Value,
        value_ty: Type,
        release_expr: Optional[Expr] = None,
    ) -> None:
        obj = self._emit_expr(target.obj)
        obj_ty = target.obj.ty
        idx_expr = target.idx
        rhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, rhs, value_ty
        )

        def release_rhs() -> None:
            if release_expr is not None:
                self._gc_release_if_owned(rhs, release_expr)

        if isinstance(idx_expr, Slice):
            if obj not in getattr(self, "_cpy_values", ()):
                lo_obj = self._emit_slice_bound_object(idx_expr.lo)
                hi_obj = self._emit_slice_bound_object(idx_expr.hi)
                step_obj = self._emit_slice_bound_object(idx_expr.step)
                if isinstance(obj_ty, ListType):
                    self.builder.call(
                        self.runtime["py_list_set_slice"],
                        [obj, lo_obj, hi_obj, step_obj, rhs_obj],
                    )
                    release_rhs()
                    return
                if isinstance(obj_ty, (ClassType, DynType)):
                    self.builder.call(
                        self.runtime["py_obj_set_slice"],
                        [obj, lo_obj, hi_obj, step_obj, rhs_obj],
                    )
                    release_rhs()
                    return
                raise NotImplementedError(
                    f"Layer 1 slice assignment on type "
                    f"{type(obj_ty).__name__} not supported"
                )

            def _as_cpy_obj(obj_v: ir.Value) -> ir.Value:
                if obj_v in getattr(self, "_cpy_values", ()):
                    return obj_v
                return self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"],
                    [obj_v],
                    name=self._fresh("cpy.slice.obj"),
                )

            def _slice_bound(e):
                if e is None:
                    gv = declare_runtime_global(self.module, "py_None")
                    none = self.builder.load(gv, name=self._fresh("none"))
                    return self.builder.call(
                        self.runtime["py_cpy_from_pcc_obj"],
                        [none],
                        name=self._fresh("cpy.none"),
                    )
                v = self._emit_expr(e)
                obj_v = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    v,
                    e.ty,
                )
                return _as_cpy_obj(obj_v)

            obj_cpy = _as_cpy_obj(obj)
            rhs_cpy = _as_cpy_obj(rhs_obj)
            slice_fn = self._load_cpython_builtin("slice")
            lo_cpy = _slice_bound(idx_expr.lo)
            hi_cpy = _slice_bound(idx_expr.hi)
            step_cpy = _slice_bound(idx_expr.step)
            slice_obj = self.builder.call(
                self.runtime["py_cpy_call3"],
                [slice_fn, lo_cpy, hi_cpy, step_cpy],
                name=self._fresh("cpy.slice"),
            )
            setitem_gv = self._cstr_global(
                "__setitem__",
                ".cpy.attr.__setitem__",
            )
            setitem_fn = self.builder.call(
                self.runtime["py_cpy_getattr"],
                [obj_cpy, self._ptr_to_cstr(setitem_gv)],
                name=self._fresh("cpy.setitem.fn"),
            )
            self.builder.call(
                self.runtime["py_cpy_call2"],
                [setitem_fn, slice_obj, rhs_cpy],
                name=self._fresh("cpy.setitem"),
            )
            release_rhs()
            return
        if obj in getattr(self, "_cpy_values", ()):
            cpy_key, key_owned = self._marshal_to_cpython(
                self._emit_expr(idx_expr),
                idx_expr.ty,
            )
            cpy_val, val_owned = self._marshal_to_cpython(
                rhs_obj,
                value_ty,
            )
            self.builder.call(
                self.runtime["py_cpy_setitem"],
                [obj, cpy_key, cpy_val],
            )
            if key_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_key])
            if val_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            release_rhs()
            return
        weak_dict_kind = self._weak_dict_kind_for_expr(target.obj)
        if weak_dict_kind == "value":
            key_obj = self._emit_as_object(idx_expr)
            self.builder.call(
                self.runtime["py_weak_value_dict_set"],
                [obj, key_obj, rhs_obj],
                name=self._fresh("weak.value.dict.set"),
            )
            release_rhs()
            return
        if weak_dict_kind == "key":
            key_obj = self._emit_as_object(idx_expr)
            self.builder.call(
                self.runtime["py_weak_key_dict_set"],
                [obj, key_obj, rhs_obj],
                name=self._fresh("weak.key.dict.set"),
            )
            release_rhs()
            return
        if isinstance(obj_ty, ListType):
            idx_i64 = self._emit_index_expr_as_i64(idx_expr)
            self.builder.call(self.runtime["py_list_set"], [obj, idx_i64, rhs_obj])
            release_rhs()
            return
        if isinstance(obj_ty, DictType):
            key_obj = self._emit_as_object(idx_expr)
            self.builder.call(self.runtime["py_dict_set"], [obj, key_obj, rhs_obj])
            release_rhs()
            return
        if isinstance(obj_ty, TupleType):
            raise NotImplementedError(
                "tuples are immutable - subscript-assignment not allowed"
            )
        key_obj = self._emit_as_object(idx_expr)
        self.builder.call(self.runtime["py_obj_setitem"], [obj, key_obj, rhs_obj])
        release_rhs()

    def _emit_slice_bound_object(self, expr: Optional[Expr]) -> ir.Value:
        """Emit a slice bound as a pcc PyObject*."""
        if expr is None:
            gv = declare_runtime_global(self.module, "py_None")
            return self.builder.load(gv, name=self._fresh("none"))
        return self._emit_as_object(expr)

    def _emit_slice_object_expr(self, sl: Slice) -> ir.Value:
        """Emit a Python ``slice`` object for expression contexts.

        Runtime slicing helpers consume loose ``lo`` / ``hi`` / ``step``
        bounds, but CPython-backed tuple-index paths such as ``obj[:, None]``
        need a real slice object as an element of the index tuple.
        """

        def _bound_cpy(e: Optional[Expr]) -> ir.Value:
            if e is None:
                gv = declare_runtime_global(self.module, "py_None")
                none = self.builder.load(gv, name=self._fresh("none"))
                return self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"],
                    [none],
                    name=self._fresh("cpy.slice.none"),
                )
            v = self._emit_expr(e)
            cpy, _owned = self._marshal_to_cpython(v, e.ty)
            return cpy

        slice_fn = self._load_cpython_builtin("slice")
        lo_cpy = _bound_cpy(sl.lo)
        hi_cpy = _bound_cpy(sl.hi)
        step_cpy = _bound_cpy(sl.step)
        result = self.builder.call(
            self.runtime["py_cpy_call3"],
            [slice_fn, lo_cpy, hi_cpy, step_cpy],
            name=self._fresh("cpy.slice.expr"),
        )
        return self._mark_cpy_value(result)

    def _emit_slice_load(self, expr: Subscript) -> ir.Value:
        debug_codegen = bool(os.environ.get("PCC_DEBUG_CODEGEN_PHASES"))

        def slice_log(label: str) -> None:
            if not debug_codegen:
                return
            mod_name = self.ast_module.name or "<module>"
            func_name = (
                self.current_func_def.name
                if self.current_func_def is not None
                else "<top>"
            )
            sys.stderr.write(
                "[pcc.codegen] "
                + mod_name
                + ":"
                + func_name
                + ":slice "
                + label
                + "\n"
            )

        slice_log("begin")
        sl = expr.idx
        assert isinstance(sl, Slice)
        slice_log("emit obj begin")
        obj = self._emit_expr(expr.obj)
        slice_log("emit obj end")
        if obj in getattr(self, "_cpy_values", ()):

            def _bound_cpy(e: Optional[Expr]) -> ir.Value:
                if e is None:
                    gv = declare_runtime_global(self.module, "py_None")
                    none = self.builder.load(gv, name=self._fresh("none"))
                    return self.builder.call(
                        self.runtime["py_cpy_from_pcc_obj"],
                        [none],
                        name=self._fresh("cpy.none"),
                    )
                v = self._emit_expr(e)
                boxed = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    v,
                    e.ty,
                )
                if boxed in getattr(self, "_cpy_values", ()):
                    return boxed
                return self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"],
                    [boxed],
                    name=self._fresh("cpy.slice.bound"),
                )

            slice_fn = self._load_cpython_builtin("slice")
            lo_cpy = _bound_cpy(sl.lo)
            hi_cpy = _bound_cpy(sl.hi)
            step_cpy = _bound_cpy(sl.step)
            slice_obj = self.builder.call(
                self.runtime["py_cpy_call3"],
                [slice_fn, lo_cpy, hi_cpy, step_cpy],
                name=self._fresh("cpy.slice"),
            )
            result = self.builder.call(
                self.runtime["py_cpy_getitem"],
                [obj, slice_obj],
                name=self._fresh("cpy.slice.getitem"),
            )
            return self._mark_cpy_value(result)
        slice_log("obj ty begin")
        obj_ty = expr.obj.ty
        slice_log("obj ty end")

        def _bound(e: Optional[Expr]) -> ir.Value:
            if e is None:
                slice_log("bound none")
                return ir.Constant(_CSTR, None)
            slice_log("bound expr emit begin")
            v = self._emit_expr(e)
            slice_log("bound expr emit end")
            slice_log("bound marshal begin")
            return marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                v,
                e.ty,
            )

        slice_log("lo begin")
        lo = _bound(sl.lo)
        slice_log("lo end")
        slice_log("hi begin")
        hi = _bound(sl.hi)
        slice_log("hi end")
        slice_log("step begin")
        step = _bound(sl.step)
        slice_log("step end")
        if isinstance(obj_ty, ListType):
            helper = "py_list_slice"
        elif isinstance(obj_ty, TupleType):
            helper = "py_tuple_slice"
        elif isinstance(obj_ty, StrType):
            helper = "py_str_slice"
        elif isinstance(obj_ty, (BytesType, ByteArrayType, MemoryViewType)):
            helper = "py_bytes_slice"
        elif isinstance(obj_ty, DynType):
            helper = "py_obj_slice"
        else:
            raise NotImplementedError(
                f"Layer 1 slice on type {type(obj_ty).__name__} not supported"
            )
        slice_log("call begin")
        return self.builder.call(
            self.runtime[helper],
            [obj, lo, hi, step],
            name=self._fresh("slice"),
        )

    def _maybe_emit_runtime_dict_lookup(
        self,
        expr: Subscript,
    ) -> Optional[ir.Function]:
        if not isinstance(expr.obj, Attr):
            return None
        if expr.obj.name != "runtime":
            return None
        if not isinstance(expr.obj.obj, Name) or expr.obj.obj.ident != "self":
            return None
        if not isinstance(expr.idx, StrLit):
            return None
        name = expr.idx.value
        fn = self.runtime.get(name)
        if isinstance(fn, ir.Function):
            return fn
        return None

    def _maybe_emit_cpython_subscript_key_literal(
        self,
        expr: Expr,
    ) -> Optional[ir.Value]:
        if isinstance(expr, ListExpr):
            ops: list[tuple[str, ir.Value, Type]] = []
            for el in expr.elems:
                if (
                    isinstance(el, Call)
                    and isinstance(el.func, Name)
                    and el.func.ident == "*"
                    and len(el.args) == 1
                ):
                    return None
                nested = self._maybe_emit_cpython_subscript_key_literal(el)
                value = nested if nested is not None else self._emit_expr(el)
                ops.append(("append", value, el.ty))
            return self._emit_cpython_list_ops(ops)
        if isinstance(expr, TupleExpr):
            ops: list[tuple[str, ir.Value, Type]] = []
            for el in expr.elems:
                if (
                    isinstance(el, Call)
                    and isinstance(el.func, Name)
                    and el.func.ident in ("*", "__starred__")
                    and len(el.args) == 1
                ):
                    return None
                nested = self._maybe_emit_cpython_subscript_key_literal(el)
                value = nested if nested is not None else self._emit_expr(el)
                ops.append(("append", value, el.ty))
            return self._emit_cpython_tuple_ops(ops)
        return None

    def _emit_subscript_load(self, expr: Subscript) -> ir.Value:
        if isinstance(expr.idx, Slice):
            return self._emit_slice_load(expr)
        dunder = self._try_dispatch_dunder_unary(expr, "__getitem__", (expr.idx,))
        if dunder is not None:
            return dunder
        native_os_environ_item = self._emit_native_os_environ_subscript(expr)
        if native_os_environ_item is not None:
            return native_os_environ_item

        obj = self._emit_expr(expr.obj)
        if obj in getattr(self, "_cpy_values", ()):
            literal_key = self._maybe_emit_cpython_subscript_key_literal(expr.idx)
            if literal_key is not None:
                cpy_key, owned = literal_key, False
            else:
                key_val = self._emit_expr(expr.idx)
                cpy_key, owned = self._marshal_to_cpython(key_val, expr.idx.ty)
            result = self.builder.call(
                self.runtime["py_cpy_getitem"],
                [obj, cpy_key],
                name=self._fresh("cpy.getitem"),
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_key])
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
            self._gc_release_if_owned(obj, expr.obj)
            return result
        obj_ty = expr.obj.ty
        if isinstance(obj_ty, ListType):
            idx = self._emit_index_expr_as_i64(expr.idx)
            got = self.builder.call(
                self.runtime["py_list_getitem"],
                [obj, idx],
                name=self._fresh("list.getitem"),
            )
            # py_list_getitem raises IndexError on out-of-range; emit the
            # post-call err check so a surrounding try/except can catch it
            # (was py_list_get, which returned NULL silently -> "<null>").
            self._emit_post_call_err_check(getattr(expr, "span", None))
            self._gc_release_if_owned(obj, expr.obj)
            return self._coerce_from_object(got, obj_ty.elem)
        if isinstance(obj_ty, TupleType):
            idx = self._emit_index_expr_as_i64(expr.idx)
            got = self.builder.call(
                self.runtime["py_tuple_get"],
                [obj, idx],
                name=self._fresh("tup.get"),
            )
            elem_ty: Type
            if obj_ty.elems:
                first = obj_ty.elems[0]
                if all(_same_type_kind(e, first) for e in obj_ty.elems):
                    elem_ty = first
                else:
                    self._gc_release_if_owned(obj, expr.obj)
                    return got
            else:
                self._gc_release_if_owned(obj, expr.obj)
                return got
            self._gc_release_if_owned(obj, expr.obj)
            return self._coerce_from_object(got, elem_ty)
        if isinstance(obj_ty, DictType):
            key_obj = self._emit_as_object(expr.idx)
            got = self.builder.call(
                self.runtime["py_dict_getitem"],
                [obj, key_obj],
                name=self._fresh("dict.getitem"),
            )
            # py_dict_getitem raises KeyError (carrying the key) when missing;
            # emit the post-call err check so a surrounding try/except can catch
            # it (was py_dict_get, which returned NULL silently -> "<null>").
            self._emit_post_call_err_check(getattr(expr, "span", None))
            self._gc_release_if_owned(obj, expr.obj)
            return self._coerce_from_object(got, obj_ty.value)
        if isinstance(obj_ty, StrType):
            idx_obj = self._emit_as_object(expr.idx)
            result = self.builder.call(
                self.runtime["py_str_index"],
                [obj, idx_obj],
                name=self._fresh("str.idx"),
            )
            self._gc_release_if_owned(obj, expr.obj)
            return result
        if isinstance(obj_ty, (BytesType, ByteArrayType, MemoryViewType)):
            idx_obj = self._emit_as_object(expr.idx)
            got = self.builder.call(
                self.runtime["py_bytes_getitem"],
                [obj, idx_obj],
                name=self._fresh("bytes.idx"),
            )
            result = marshal.marshal_from_object(
                self.builder,
                self.module,
                self.runtime,
                got,
                expr.ty,
            )
            self._gc_release_if_owned(obj, expr.obj)
            return result
        key_obj = self._emit_as_object(expr.idx)
        result = self.builder.call(
            self.runtime["py_obj_getitem"],
            [obj, key_obj],
            name=self._fresh("obj.getitem"),
        )
        self._gc_release_if_owned(obj, expr.obj)
        return result
