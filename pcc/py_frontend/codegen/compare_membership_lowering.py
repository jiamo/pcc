"""Comparison and membership lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    BoolExpr,
    BoolLit,
    BoolType,
    ByteArrayType,
    BytesType,
    ClassType,
    Compare,
    DictType,
    DynType,
    Expr,
    FloatLit,
    FloatType,
    IntLit,
    IntType,
    ListType,
    MemoryViewType,
    NoneLit,
    NoneType,
    StrType,
    TupleExpr,
    TupleType,
    Type,
)
from . import marshal


_I1 = ir.IntType(1)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
_CSTR = ir.IntType(8).as_pointer()


def _same_type_kind(a: Type, b: Type) -> bool:
    return type(a) is type(b)


class CompareMembershipLoweringMixin:
    def _emit_compare(self, expr: Compare) -> ir.Value:
        # Identity against None: pointer compare against @py_None.
        if expr.op in ("is", "is not"):
            return self._emit_identity_compare(expr)
        if expr.op in ("in", "not in"):
            return self._emit_membership(expr)

        if (
            self._int_exprs_are_boxed()
            and expr.op in ("==", "!=", "<", "<=", ">", ">=")
            and isinstance(expr.lhs.ty, (IntType, BoolType))
            and isinstance(expr.rhs.ty, (IntType, BoolType))
        ):
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            lhs_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                lhs,
                expr.lhs.ty,
            )
            rhs_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                rhs,
                expr.rhs.ty,
            )
            cmp_i32 = self.builder.call(
                self.runtime["py_int_cmp"],
                [lhs_obj, rhs_obj],
                name=self._fresh("int.obj.cmp"),
            )
            return self.builder.icmp_signed(
                expr.op,
                cmp_i32,
                ir.Constant(_I32, 0),
                name=self._fresh("int.obj.cmp.i1"),
            )

        exact_int_cmp = self._emit_exact_int_compare(expr)
        if exact_int_cmp is not None:
            return exact_int_cmp

        valueclass_eq = self._emit_valueclass_payload_eq(expr)
        if valueclass_eq is not None:
            return valueclass_eq

        # Class-based comparison dunder fast path.
        cmp_dunder = {
            "==": "__eq__",
            "!=": "__ne__",
            "<": "__lt__",
            "<=": "__le__",
            ">": "__gt__",
            ">=": "__ge__",
        }.get(expr.op)
        if cmp_dunder is not None:
            dunder = self._try_dispatch_dunder_unary(expr.lhs, cmp_dunder, (expr.rhs,))
            if dunder is not None:
                if self._ir_type_matches(dunder.type, _I1):
                    return dunder
                if isinstance(dunder.type, ir.IntType) and dunder.type.width > 1:
                    return self.builder.icmp_signed(
                        "!=",
                        dunder,
                        ir.Constant(dunder.type, 0),
                        name=self._fresh("dunder.i1"),
                    )
                if isinstance(dunder.type, ir.PointerType):
                    # Returned PyObject*: run py_obj_truthy to get i1.
                    as_i32 = self.builder.call(
                        self.runtime["py_obj_truthy"],
                        [dunder],
                        name=self._fresh("dunder.truthy"),
                    )
                    return self.builder.trunc(
                        as_i32,
                        _I1,
                        name=self._fresh("dunder.truthy.i1"),
                    )
                return dunder

        lhs_ty = expr.lhs.ty
        rhs_ty = expr.rhs.ty
        lhs_looks_cpy = self._expr_looks_cpython(expr.lhs)
        rhs_looks_cpy = self._expr_looks_cpython(expr.rhs)

        if lhs_looks_cpy or rhs_looks_cpy:
            if expr.op in ("==", "!=") and lhs_looks_cpy != rhs_looks_cpy:
                cpy_expr = expr.lhs if lhs_looks_cpy else expr.rhs
                other_expr = expr.rhs if lhs_looks_cpy else expr.lhs
                if isinstance(
                    other_expr.ty,
                    (StrType, NoneType, IntType, BoolType, FloatType),
                ):
                    cpy_raw = self._emit_expr(cpy_expr)
                    cpy_obj = self._emit_value_as_pcc_object_or_bridge(
                        cpy_raw,
                        cpy_expr.ty,
                        "cpy.cmp.bridge",
                    )
                    other_obj = self._emit_as_object(other_expr)
                    eq = self.builder.call(
                        self.runtime["py_obj_eq"],
                        [cpy_obj, other_obj],
                        name=self._fresh("cpy.obj.eq"),
                    )
                    eq_i1 = self.builder.icmp_signed(
                        "!=",
                        eq,
                        ir.Constant(_I32, 0),
                        name=self._fresh("cpy.obj.eq.i1"),
                    )
                    if expr.op == "!=":
                        return self.builder.not_(
                            eq_i1,
                            name=self._fresh("cpy.obj.ne"),
                        )
                    return eq_i1
            recv_expr = expr.lhs
            other_expr = expr.rhs
            recv_op = expr.op
            if not lhs_looks_cpy and rhs_looks_cpy:
                recv_expr = expr.rhs
                other_expr = expr.lhs
                recv_op = {
                    "==": "==",
                    "!=": "!=",
                    "<": ">",
                    "<=": ">=",
                    ">": "<",
                    ">=": "<=",
                }.get(expr.op, expr.op)
            method_name = {
                "==": "__eq__",
                "!=": "__ne__",
                "<": "__lt__",
                "<=": "__le__",
                ">": "__gt__",
                ">=": "__ge__",
            }.get(recv_op)
            if method_name is not None:
                recv_val = self._emit_expr(recv_expr)
                recv_cpy, recv_owned = self._marshal_to_cpython(
                    recv_val,
                    recv_expr.ty,
                )
                result = self._emit_cpy_method_call_src(
                    recv_cpy,
                    method_name,
                    (other_expr,),
                )
                if recv_owned:
                    self.builder.call(
                        self.runtime["py_cpy_decref"],
                        [recv_cpy],
                    )
                as_i32 = self.builder.call(
                    self.runtime["py_cpy_truthy"],
                    [result],
                    name=self._fresh("cpy.cmp.i32"),
                )
                return self.builder.icmp_signed(
                    "!=",
                    as_i32,
                    ir.Constant(_I32, 0),
                    name=self._fresh("cpy.cmp.i1"),
                )

        # String equality → runtime py_str_eq fast path. Relational str
        # ops fall through to the generic object compare helpers.
        if (
            isinstance(lhs_ty, StrType)
            and isinstance(rhs_ty, StrType)
            and expr.op in ("==", "!=")
        ):
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            eq = self.builder.call(
                self.runtime["py_str_eq"],
                [lhs, rhs],
                name=self._fresh("str.eq"),
            )
            eq_i1 = self.builder.icmp_signed(
                "!=", eq, ir.Constant(_I32, 0), name=self._fresh("str.eq.i1")
            )
            if expr.op == "!=":
                return self.builder.not_(eq_i1, name=self._fresh("str.ne"))
            return eq_i1

        if expr.op in ("==", "!=") and (
            isinstance(lhs_ty, StrType) or isinstance(rhs_ty, StrType)
        ):
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            if not isinstance(lhs.type, ir.PointerType):
                lhs = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    lhs,
                    lhs_ty,
                )
            if not isinstance(rhs.type, ir.PointerType):
                rhs = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    rhs,
                    rhs_ty,
                )
            eq = self.builder.call(
                self.runtime["py_obj_eq"],
                [lhs, rhs],
                name=self._fresh("obj.str.eq"),
            )
            eq_i1 = self.builder.icmp_signed(
                "!=",
                eq,
                ir.Constant(_I32, 0),
                name=self._fresh("obj.str.eq.i1"),
            )
            if expr.op == "!=":
                return self.builder.not_(
                    eq_i1,
                    name=self._fresh("obj.str.ne"),
                )
            return eq_i1

        # Object-vs-object equality (for two boxed operands): delegate.
        if self._is_object(lhs_ty) and self._is_object(rhs_ty):
            runtime_name = {
                "==": "py_obj_eq",
                "!=": "py_obj_eq",
                "<": "py_obj_lt",
                "<=": "py_obj_le",
                ">": "py_obj_gt",
                ">=": "py_obj_ge",
            }.get(expr.op)
            if runtime_name is None:
                raise NotImplementedError(
                    f"Layer 2 does not handle object compare op {expr.op!r}"
                )
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            if not isinstance(lhs.type, ir.PointerType):
                lhs = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    lhs,
                    lhs_ty,
                )
            if not isinstance(rhs.type, ir.PointerType):
                rhs = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    rhs,
                    rhs_ty,
                )
            cmp_i32 = self.builder.call(
                self.runtime[runtime_name],
                [lhs, rhs],
                name=self._fresh("obj.cmp"),
            )
            cmp_i1 = self.builder.icmp_signed(
                "!=",
                cmp_i32,
                ir.Constant(_I32, 0),
                name=self._fresh("obj.cmp.i1"),
            )
            if expr.op == "!=":
                return self.builder.not_(cmp_i1, name=self._fresh("obj.ne"))
            return cmp_i1

        lhs = self._emit_expr(expr.lhs)
        rhs = self._emit_expr(expr.rhs)
        if isinstance(lhs_ty, FloatType) or isinstance(rhs_ty, FloatType):
            lf = self._to_double(lhs, lhs_ty)
            rf = self._to_double(rhs, rhs_ty)
            return self.builder.fcmp_ordered(expr.op, lf, rf, name=self._fresh("fcmp"))
        if (
            isinstance(lhs_ty, DynType) or isinstance(rhs_ty, DynType)
        ) and expr.op in ("<", "<=", ">", ">="):
            # A DynType operand may be a float at runtime; the int fast path
            # (_to_int64) would misread the boxed-float pointer. Route through
            # the runtime ordering compare (py_obj_lt/le/gt/ge ->
            # py_obj_cmp_threeway, which is float-aware). Mirrors the DynType
            # arithmetic dispatch in binary_op_lowering.
            runtime_name = {
                "<": "py_obj_lt",
                "<=": "py_obj_le",
                ">": "py_obj_gt",
                ">=": "py_obj_ge",
            }[expr.op]
            lo = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, lhs, lhs_ty
            )
            ro = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, rhs, rhs_ty
            )
            cmp_i64 = self.builder.call(
                self.runtime[runtime_name], [lo, ro], name=self._fresh("dyn.cmp")
            )
            return self.builder.icmp_signed(
                "!=", cmp_i64, ir.Constant(_I64, 0), name=self._fresh("dyn.cmp.i1")
            )
        lv = self._to_int64(lhs, lhs_ty)
        rv = self._to_int64(rhs, rhs_ty)
        return self.builder.icmp_signed(expr.op, lv, rv, name=self._fresh("icmp"))

    def _emit_valueclass_payload_eq(self, expr: Compare) -> Optional[ir.Value]:
        if expr.op not in ("==", "!="):
            return None
        lhs_ty = expr.lhs.ty
        rhs_ty = expr.rhs.ty
        if not isinstance(lhs_ty, ClassType) or not isinstance(rhs_ty, ClassType):
            return None
        if not self._is_valueclass_payload_type(lhs_ty):
            return None
        if not self._is_valueclass_payload_type(rhs_ty):
            return None
        if (lhs_ty.module, lhs_ty.name) != (rhs_ty.module, rhs_ty.name):
            return None
        if len(lhs_ty.fields) != len(rhs_ty.fields):
            return None

        lhs = self._emit_expr(expr.lhs)
        rhs = self._emit_expr(expr.rhs)
        acc: Optional[ir.Value] = None
        for idx, (_field_name, field_ty) in enumerate(lhs_ty.fields):
            lhs_field = self.builder.extract_value(
                lhs,
                [idx],
                name=self._fresh("value.eq.l"),
            )
            rhs_field = self.builder.extract_value(
                rhs,
                [idx],
                name=self._fresh("value.eq.r"),
            )
            if isinstance(field_ty, FloatType):
                field_eq = self.builder.fcmp_ordered(
                    "==",
                    lhs_field,
                    rhs_field,
                    name=self._fresh("value.eq.fcmp"),
                )
            elif isinstance(lhs_field.type, ir.PointerType):
                obj_eq = self.builder.call(
                    self.runtime["py_obj_eq"],
                    [lhs_field, rhs_field],
                    name=self._fresh("value.eq.obj"),
                )
                field_eq = self.builder.icmp_signed(
                    "!=",
                    obj_eq,
                    ir.Constant(obj_eq.type, 0),
                    name=self._fresh("value.eq.obj.i1"),
                )
            else:
                field_eq = self.builder.icmp_signed(
                    "==",
                    lhs_field,
                    rhs_field,
                    name=self._fresh("value.eq.icmp"),
                )
            if acc is None:
                acc = field_eq
            else:
                acc = self.builder.and_(
                    acc,
                    field_eq,
                    name=self._fresh("value.eq.and"),
                )
        if acc is None:
            acc = ir.Constant(_I1, 1)
        if expr.op == "!=":
            return self.builder.not_(acc, name=self._fresh("value.ne"))
        return acc

    def _emit_identity_compare(self, expr: Compare) -> ir.Value:
        """``is`` / ``is not`` — pointer compare, typically against None.

        Both operands are marshalled to PyObject* and compared as
        pointers. Interning of small ints / bools is handled by the
        runtime (``py_int_from_i64`` returns the canonical global for
        small ints), so ``is`` behaves consistently with CPython on
        those.

        Fast path: if one operand is a NoneLit and the other is a native
        scalar (int/float/bool), the answer is a compile-time constant
        (False for ``is``, True for ``is not``).
        """
        lhs_module = self._native_module_name_for_object_expr(expr.lhs)
        rhs_module = self._native_module_name_for_object_expr(expr.rhs)
        if lhs_module is not None and rhs_module is not None:
            same = lhs_module == rhs_module
            if expr.op == "is not":
                same = not same
            return ir.Constant(_I1, 1 if same else 0)

        # Constant-fold ``<native> is None`` and ``<native> is not None``.
        native_lhs = self._is_native_scalar_type(expr.lhs.ty)
        native_rhs = self._is_native_scalar_type(expr.rhs.ty)
        none_lhs = isinstance(expr.lhs, NoneLit) or isinstance(expr.lhs.ty, NoneType)
        none_rhs = isinstance(expr.rhs, NoneLit) or isinstance(expr.rhs.ty, NoneType)
        if (native_lhs and none_rhs) or (native_rhs and none_lhs):
            # The native value can never be literally the py_None pointer.
            return ir.Constant(_I1, 1 if expr.op == "is not" else 0)

        def identity_temp_needs_release(src: Expr, raw: ir.Value) -> bool:
            if not isinstance(raw.type, ir.PointerType):
                return False
            if self._expr_returns_owned_object(src):
                return True
            return isinstance(src, (IntLit, FloatLit, BoolLit))

        lhs = self._emit_expr(expr.lhs)
        rhs = self._emit_expr(expr.rhs)
        lhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, lhs, expr.lhs.ty
        )
        rhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, rhs, expr.rhs.ty
        )
        # Compare pointers as integers so the IR is independent of the
        # llvmlite version's pointer-compare support.
        lhs_i = self.builder.ptrtoint(lhs_obj, _I64, name=self._fresh("is.l"))
        rhs_i = self.builder.ptrtoint(rhs_obj, _I64, name=self._fresh("is.r"))
        eq = self.builder.icmp_signed("==", lhs_i, rhs_i, name=self._fresh("is"))
        result = eq
        if expr.op == "is not":
            result = self.builder.not_(eq, name=self._fresh("is_not"))
        if identity_temp_needs_release(expr.lhs, lhs):
            self._gc_release(lhs_obj)
        if identity_temp_needs_release(expr.rhs, rhs):
            self._gc_release(rhs_obj)
        return result
    def _emit_membership(self, expr: Compare) -> ir.Value:
        """``in`` / ``not in`` over str / list / dict / set / tuple."""
        container_ty = expr.rhs.ty
        weak_dict_kind = self._weak_dict_kind_for_expr(expr.rhs)
        rhs = self._emit_expr(expr.rhs)
        if rhs in getattr(self, "_cpy_values", ()):
            container_cpy, container_owned = self._marshal_to_cpython(
                rhs,
                container_ty,
            )
            result = self._emit_cpy_method_call_src(
                container_cpy,
                "__contains__",
                (expr.lhs,),
            )
            if container_owned:
                self.builder.call(
                    self.runtime["py_cpy_decref"],
                    [container_cpy],
                )
            as_i32 = self.builder.call(
                self.runtime["py_cpy_truthy"],
                [result],
                name=self._fresh("cpy.contains.i32"),
            )
            contains = self.builder.icmp_signed(
                "!=",
                as_i32,
                ir.Constant(_I32, 0),
                name=self._fresh("cpy.contains.i1"),
            )
            if expr.op == "not in":
                return self.builder.not_(
                    contains,
                    name=self._fresh("cpy.not_in"),
                )
            return contains
        lhs = self._emit_expr(expr.lhs)
        lhs_ty = expr.lhs.ty

        if weak_dict_kind == "value":
            key = self._emit_value_as_pcc_object_or_bridge(
                lhs,
                lhs_ty,
                "weak.value.dict.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_weak_value_dict_contains"],
                [rhs, key],
                name=self._fresh("weak.value.dict.in"),
            )
            result = self.builder.icmp_signed(
                "!=",
                res_i32,
                ir.Constant(_I64, 0),
                name=self._fresh("weak.value.dict.in.i1"),
            )
            if expr.op == "not in":
                return self.builder.not_(
                    result,
                    name=self._fresh("weak.value.dict.notin"),
                )
            return result

        if isinstance(container_ty, StrType):
            # Needle is expected to be a pcc str (single char or
            # substring). When the lhs type is DynType (e.g. a
            # comprehension loop variable bound by ``for ch in s``
            # where the comp-scope inference didn't propagate the
            # element type), we still have a ``PyObject*`` — py_str_*
            # helpers tolerate foreign types by length/bytes compare.
            needle = lhs
            if not isinstance(lhs.type, ir.PointerType):
                needle = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, lhs, lhs_ty
                )
            res_i32 = self.builder.call(
                self.runtime["py_str_contains"],
                [rhs, needle],
                name=self._fresh("str.in"),
            )
        elif isinstance(container_ty, ListType):
            needle = self._emit_value_as_pcc_object_or_bridge(
                lhs,
                lhs_ty,
                "cpy.list.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_list_contains"],
                [rhs, needle],
                name=self._fresh("list.in"),
            )
        elif isinstance(container_ty, DictType):
            key = self._emit_value_as_pcc_object_or_bridge(
                lhs,
                lhs_ty,
                "cpy.dict.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_dict_contains"],
                [rhs, key],
                name=self._fresh("dict.in"),
            )
        elif isinstance(container_ty, TupleType):
            # Tuple literal fast path: unroll against static elements.
            # General tuple values can use the runtime's generic
            # ``py_obj_contains`` dispatcher, which already handles
            # tuple containers via linear scan.
            if isinstance(expr.rhs, TupleExpr):
                return self._emit_membership_tuple_literal(
                    lhs, lhs_ty, expr.rhs, negate=(expr.op == "not in")
                )
            key = self._emit_value_as_pcc_object_or_bridge(
                lhs,
                lhs_ty,
                "cpy.tuple.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_obj_contains"],
                [rhs, key],
                name=self._fresh("tuple.in"),
            )
        elif isinstance(container_ty, DynType) or isinstance(container_ty, Type):
            # DynType / imprecise Type container — route through the runtime
            # ``py_obj_contains`` dispatcher. This covers self-host paths
            # where Optional[dict] or getattr(...) inference collapses to the
            # abstract Type base; known concrete str/list/dict/tuple cases
            # above still keep their specialized fast paths.
            key = self._emit_value_as_pcc_object_or_bridge(
                lhs,
                lhs_ty,
                "cpy.obj.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_obj_contains"],
                [rhs, key],
                name=self._fresh("obj.in"),
            )
            result = self.builder.icmp_signed(
                "!=",
                res_i32,
                ir.Constant(_I32, 0),
                name=self._fresh("obj.in.i1"),
            )
            if expr.op == "not in":
                result = self.builder.not_(
                    result,
                    name=self._fresh("obj.notin"),
                )
            return result
        else:
            key = self._emit_value_as_pcc_object_or_bridge(
                lhs,
                lhs_ty,
                "cpy.obj.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_obj_contains"],
                [rhs, key],
                name=self._fresh("obj.in"),
            )
            result = self.builder.icmp_signed(
                "!=",
                res_i32,
                ir.Constant(_I32, 0),
                name=self._fresh("obj.in.i1"),
            )
            if expr.op == "not in":
                result = self.builder.not_(
                    result,
                    name=self._fresh("obj.notin"),
                )
            return result

        res = self.builder.icmp_signed(
            "!=", res_i32, ir.Constant(_I32, 0), name=self._fresh("in.i1")
        )
        if expr.op == "not in":
            return self.builder.not_(res, name=self._fresh("not_in"))
        return res
    def _emit_membership_tuple_literal(
        self, lhs: ir.Value, lhs_ty: Type, rhs: TupleExpr, negate: bool
    ) -> ir.Value:
        """Unroll ``x in (a, b, c)`` as ``x==a or x==b or x==c``."""
        lhs_obj = self._emit_value_as_pcc_object_or_bridge(
            lhs,
            lhs_ty,
            "cpy.tup.lit.in.key",
        )
        acc: Optional[ir.Value] = None
        for el in rhs.elems:
            v = self._emit_expr(el)
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, el.ty
            )
            eq_i32 = self.builder.call(
                self.runtime["py_obj_eq"],
                [lhs_obj, v_obj],
                name=self._fresh("tup.eq"),
            )
            eq_i1 = self.builder.icmp_signed(
                "!=",
                eq_i32,
                ir.Constant(_I32, 0),
                name=self._fresh("tup.eq.i1"),
            )
            if acc is None:
                acc = eq_i1
            else:
                acc = self.builder.or_(acc, eq_i1, name=self._fresh("tup.or"))
        if acc is None:
            # Empty tuple: ``x in ()`` is always False.
            acc = ir.Constant(_I1, 0)
        if negate:
            return self.builder.not_(acc, name=self._fresh("tup.not_in"))
        return acc
    def _emit_boolexpr(self, expr: BoolExpr) -> ir.Value:
        # Short-circuit via branch. ``and`` / ``or`` return either the
        # left operand or the right operand; only the pure-bool case
        # should collapse to i1.
        fn = self.current_function

        lhs = self._emit_expr(expr.left)
        lhs_b = self._truthy(lhs, expr.left.ty)
        result_ty = expr.ty
        lhs_val = None
        if not isinstance(result_ty, BoolType):
            lhs_val = self._coerce(lhs, expr.left.ty, result_ty)

        rhs_bb = fn.append_basic_block(name=self._fresh("bool.rhs"))
        end_bb = fn.append_basic_block(name=self._fresh("bool.end"))
        entry_bb = self.builder._block

        if expr.op == "and":
            # if lhs then compute rhs else short-circuit false.
            self.builder.cbranch(lhs_b, rhs_bb, end_bb)
        elif expr.op == "or":
            # if lhs then short-circuit true else compute rhs.
            self.builder.cbranch(lhs_b, end_bb, rhs_bb)
        else:
            raise NotImplementedError(f"Layer 1 bool op {expr.op!r} not supported")

        if not isinstance(result_ty, BoolType):
            self.builder.position_at_end(rhs_bb)
            rhs = self._emit_expr(expr.right)
            rhs_val = self._coerce(rhs, expr.right.ty, result_ty)
            rhs_exit = self.builder._block
            self.builder.branch(end_bb)

            self.builder.position_at_end(end_bb)
            phi = self.builder.phi(
                self._storage_ir_type(result_ty), name=self._fresh(expr.op)
            )
            phi.add_incoming(lhs_val, entry_bb)
            phi.add_incoming(rhs_val, rhs_exit)
            return phi

        self.builder.position_at_end(rhs_bb)
        rhs = self._emit_expr(expr.right)
        rhs_b = self._truthy(rhs, expr.right.ty)
        rhs_exit = self.builder._block
        self.builder.branch(end_bb)

        self.builder.position_at_end(end_bb)
        phi = self.builder.phi(_I1, name=self._fresh(expr.op))
        if expr.op == "and":
            phi.add_incoming(ir.Constant(_I1, 0), entry_bb)
            phi.add_incoming(rhs_b, rhs_exit)
        else:  # "or"
            phi.add_incoming(ir.Constant(_I1, 1), entry_bb)
            phi.add_incoming(rhs_b, rhs_exit)
        return phi
