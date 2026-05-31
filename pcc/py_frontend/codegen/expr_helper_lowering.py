"""Misc expression helper lowering for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    BoolLit,
    BoolType,
    DynType,
    Expr,
    FloatType,
    IntLit,
    IntType,
    Name,
    StrLit,
    Subscript,
    Type,
)
from . import marshal


_I1 = ir.IntType(1)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()


class ExprHelperLoweringMixin:
    def _join_reversed_strs(self, parts: list[str]) -> str:
        rev: list[str] = []
        i = len(parts) - 1
        while i >= 0:
            rev.append(parts[i])
            i -= 1
        return ".".join(rev)

    def _tuple_elems_are_uniform(
        self,
        elems: tuple[Type, ...],
        first: Type,
    ) -> bool:
        i = 0
        while i < len(elems):
            if elems[i] != first:
                return False
            i += 1
        return True

    def _lambda_simple_subscript(self, expr, param_name):
        """If ``expr`` is ``Name(param)[IntLit|StrLit]``, return the
        Python literal value. Else None."""
        if not isinstance(expr, Subscript):
            return None
        if not (isinstance(expr.obj, Name) and expr.obj.ident == param_name):
            return None
        if isinstance(expr.idx, IntLit):
            return expr.idx.value
        if isinstance(expr.idx, StrLit):
            return expr.idx.value
        return None


    # -- Comprehensions -----------------------------------------------


    def _emit_comprehension_innermost(
        self,
        kind: str,
        container: ir.Value,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        if kind == "dict":
            k = self._emit_expr(key_expr)
            v = self._emit_expr(val_expr)
            k_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                k,
                key_expr.ty,
            )
            v_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                v,
                val_expr.ty,
            )
            self.builder.call(
                self.runtime["py_dict_set"],
                [container, k_obj, v_obj],
            )
            return
        v = self._emit_expr(elt_expr)
        v_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            v,
            elt_expr.ty,
        )
        fn_name = "py_list_append" if kind == "list" else "py_set_add"
        self.builder.call(self.runtime[fn_name], [container, v_obj])

    def _emit_comprehension_level(
        self,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        if idx >= len(generators):
            self._emit_comprehension_innermost(
                kind,
                container,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        self._emit_comprehension_generator(
            kind,
            container,
            generators,
            tuple_unpacks,
            idx,
            elt_expr,
            key_expr,
            val_expr,
        )













    def _emit_as_object(self, expr: Expr) -> ir.Value:
        """Emit ``expr`` and marshal the result to PyObject*."""
        if isinstance(expr.ty, IntType):
            exact = self._maybe_emit_exact_int_object(expr)
            if exact is not None:
                return exact
        v = self._emit_expr(expr)
        boxed_valueclass = self._emit_valueclass_payload_to_object(v, expr.ty)
        if boxed_valueclass is not None:
            return boxed_valueclass
        return marshal.marshal_to_object(
            self.builder, self.module, self.runtime, v, expr.ty
        )


    # -- BinOp ---------------------------------------------------------






    def _python_floordiv_i64(self, a: ir.Value, b: ir.Value) -> ir.Value:
        """Python-correct signed floor division on i64.

        ``q = a sdiv b; r = a srem b; if (r != 0) && ((r < 0) != (b < 0))
        then q = q - 1``.
        """
        b_is_zero = self.builder.icmp_signed(
            "==", b, ir.Constant(_I64, 0), name=self._fresh("fdiv_bz")
        )
        self._emit_zero_division_check(
            b_is_zero, "division by zero"
        )
        q = self.builder.sdiv(a, b, name=self._fresh("q"))
        r = self.builder.srem(a, b, name=self._fresh("r"))
        zero = ir.Constant(_I64, 0)
        one = ir.Constant(_I64, 1)
        r_nz = self.builder.icmp_signed("!=", r, zero, name=self._fresh("r_nz"))
        r_neg = self.builder.icmp_signed("<", r, zero, name=self._fresh("r_neg"))
        b_neg = self.builder.icmp_signed("<", b, zero, name=self._fresh("b_neg"))
        sign_diff = self.builder.xor(r_neg, b_neg, name=self._fresh("sign_diff"))
        need_fix = self.builder.and_(r_nz, sign_diff, name=self._fresh("need_fix"))
        q_minus_1 = self.builder.sub(q, one, name=self._fresh("q_fix"))
        return self.builder.select(need_fix, q_minus_1, q, name=self._fresh("floordiv"))

    def _python_mod_i64(self, a: ir.Value, b: ir.Value) -> ir.Value:
        """Python-correct signed mod on i64; sign follows divisor.

        ``r = a srem b; if (r != 0) && ((r < 0) != (b < 0)) then r = r + b``.
        """
        b_is_zero = self.builder.icmp_signed(
            "==", b, ir.Constant(_I64, 0), name=self._fresh("mod_bz")
        )
        self._emit_zero_division_check(
            b_is_zero, "division by zero"
        )
        r = self.builder.srem(a, b, name=self._fresh("r"))
        zero = ir.Constant(_I64, 0)
        r_nz = self.builder.icmp_signed("!=", r, zero, name=self._fresh("r_nz"))
        r_neg = self.builder.icmp_signed("<", r, zero, name=self._fresh("r_neg"))
        b_neg = self.builder.icmp_signed("<", b, zero, name=self._fresh("b_neg"))
        sign_diff = self.builder.xor(r_neg, b_neg, name=self._fresh("sign_diff"))
        need_fix = self.builder.and_(r_nz, sign_diff, name=self._fresh("need_fix"))
        r_plus_b = self.builder.add(r, b, name=self._fresh("r_fix"))
        return self.builder.select(need_fix, r_plus_b, r, name=self._fresh("mod"))

    def _get_floor_intrinsic(self) -> ir.Function:
        name = "llvm.floor.f64"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    def _get_rint_function(self) -> ir.Function:
        # libm rint(): round to nearest, ties to even (the default FP rounding
        # mode), which matches CPython's round() banker's rounding. Used
        # instead of floor(x + 0.5), which rounds half away from zero.
        name = "rint"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    def _get_fmod_function(self) -> ir.Function:
        name = "fmod"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE, _DOUBLE])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    def _get_pow_function(self) -> ir.Function:
        name = "pow"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE, _DOUBLE])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    # -- UnaryOp -------------------------------------------------------

    def _emit_expr_as_i64(self, expr: Expr) -> ir.Value:
        """Emit an expression and coerce the result to ``i64``.

        Accepts native int/bool (fast path) and object-typed integers
        (via ``py_int_to_i64``, for e.g. a ``dict`` value that was typed
        as int but materialised as PyObject*).
        """
        if isinstance(expr, IntLit):
            return ir.Constant(_I64, int(expr.value))
        if isinstance(expr, BoolLit):
            return ir.Constant(_I64, 1 if bool(expr.value) else 0)
        value = self._emit_expr(expr)
        if isinstance(expr.ty, IntType):
            if self._ir_type_matches(value.type, _I64):
                return value
            if isinstance(value.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, value, expr.ty
                )
            return self.builder.sext(value, _I64, name=self._fresh("sext64"))
        if isinstance(expr.ty, BoolType):
            if self._ir_type_matches(value.type, _I1):
                return self.builder.zext(value, _I64, name=self._fresh("b2i"))
            if isinstance(value.type, ir.PointerType):
                i = marshal.marshal_from_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    value,
                    IntType(name="int"),
                )
                return i
            return self.builder.zext(value, _I64, name=self._fresh("b2i"))
        if isinstance(expr.ty, FloatType):
            return self.builder.fptosi(value, _I64, name=self._fresh("f2i"))
        if isinstance(expr.ty, DynType) or self._is_object(expr.ty):
            # Go through the runtime.
            boxed = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, value, expr.ty
            )
            return marshal.marshal_from_object(
                self.builder,
                self.module,
                self.runtime,
                boxed,
                IntType(name="int"),
            )
        raise NotImplementedError(
            f"Layer 1 cannot reduce {type(expr.ty).__name__} to i64"
        )
