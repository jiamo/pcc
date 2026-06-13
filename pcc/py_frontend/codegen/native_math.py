"""Native ``math`` module lowering helpers."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, Call, DynType, Expr, IntType, ListType, Name, TupleType
from . import marshal


_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()
_DOUBLE = ir.DoubleType()



class NativeMathLoweringMixin:
    def _get_sqrt_function(self) -> ir.Function:
        name = "sqrt"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE])
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

    def _emit_native_math_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "math"
        ):
            return None
        return self._emit_native_math_value_call(
            "math." + attr.name,
            expr.args,
            expr.kwargs,
        )

    def _emit_native_math_value_call(
        self,
        kind: str,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if kind == "math.prod":
            return self._emit_native_math_prod(args, kwargs)
        if kind == "math.trunc" and len(args) == 1 and not kwargs:
            raw = self._emit_expr(args[0])
            value = self._to_double(raw, args[0].ty)
            return self.builder.fptosi(
                value,
                _I64,
                name=self._fresh("math.trunc.i64"),
            )
        if kind == "math.gcd" and len(args) == 2 and not kwargs:
            left = self._emit_expr_as_i64(args[0])
            right = self._emit_expr_as_i64(args[1])
            return self._emit_i64_gcd(left, right)
        if kind == "math.factorial" and len(args) == 1 and not kwargs:
            # factorial(n): bignum-correct product via py_int_mul (n! overflows
            # i64 at n>=21). Negative n raises ValueError, like CPython.
            n = self._emit_expr_as_i64(args[0])
            fn = self.current_function
            neg_bb = fn.append_basic_block(name=self._fresh("math.fact.neg"))
            ok_bb = fn.append_basic_block(name=self._fresh("math.fact.ok"))
            is_neg = self.builder.icmp_signed(
                "<", n, ir.Constant(_I64, 0), name=self._fresh("math.fact.isneg")
            )
            self.builder.cbranch(is_neg, neg_bb, ok_bb)
            self.builder.position_at_end(neg_bb)
            self._emit_builtin_exception_and_branch(
                "ValueError",
                "factorial() not defined for negative values",
                getattr(args[0], "span", None),
            )
            self.builder.position_at_end(ok_bb)
            acc_slot = self._alloca_in_entry(_CSTR, name="math.fact.acc.addr")
            one = self.builder.call(
                self.runtime["py_int_from_i64"],
                [ir.Constant(_I64, 1)],
                name=self._fresh("math.fact.one"),
            )
            self.builder.store(one, acc_slot)
            i_slot = self._alloca_in_entry(_I64, name="math.fact.i.addr")
            self.builder.store(ir.Constant(_I64, 2), i_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("math.fact.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("math.fact.body"))
            end_bb = fn.append_basic_block(name=self._fresh("math.fact.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            i_cur = self.builder.load(i_slot, name=self._fresh("math.fact.i"))
            keep = self.builder.icmp_signed(
                "<=", i_cur, n, name=self._fresh("math.fact.keep")
            )
            self.builder.cbranch(keep, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            acc = self.builder.load(acc_slot, name=self._fresh("math.fact.acc"))
            i_box = self.builder.call(
                self.runtime["py_int_from_i64"],
                [i_cur],
                name=self._fresh("math.fact.ibox"),
            )
            new_acc = self.builder.call(
                self.runtime["py_int_mul"],
                [acc, i_box],
                name=self._fresh("math.fact.mul"),
            )
            self.builder.store(new_acc, acc_slot)
            nxt = self.builder.add(
                i_cur, ir.Constant(_I64, 1), name=self._fresh("math.fact.next")
            )
            self.builder.store(nxt, i_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return self.builder.load(acc_slot, name=self._fresh("math.fact.result"))
        if kind == "math.isqrt" and len(args) == 1 and not kwargs:
            # isqrt(n): bignum-correct integer square root (floor sqrt) via the
            # py_int_isqrt C helper (integer Newton iteration on boxed ints — it
            # never goes through float, so isqrt((10**30)+1) is exact). The
            # helper raises ValueError for negative n and sets a Python
            # exception; the post-call err-check branches to the error path.
            arg_obj = self._emit_as_object(args[0])
            res = self.builder.call(
                self.runtime["py_int_isqrt"],
                [arg_obj],
                name=self._fresh("math.isqrt"),
            )
            self._emit_post_call_err_check(getattr(args[0], "span", None))
            return res
        if kind == "math.floor" and len(args) == 1 and not kwargs:
            raw = self._emit_expr(args[0])
            value = self._to_double(raw, args[0].ty)
            floored = self.builder.call(
                self._get_floor_intrinsic(),
                [value],
                name=self._fresh("math.floor.f64"),
            )
            return self.builder.fptosi(
                floored,
                _I64,
                name=self._fresh("math.floor.i64"),
            )
        if kind == "math.ceil" and len(args) == 1 and not kwargs:
            raw = self._emit_expr(args[0])
            value = self._to_double(raw, args[0].ty)
            ceiled = self.builder.call(
                self._get_ceil_intrinsic(),
                [value],
                name=self._fresh("math.ceil.f64"),
            )
            return self.builder.fptosi(
                ceiled,
                _I64,
                name=self._fresh("math.ceil.i64"),
            )
        if kind == "math.sqrt" and len(args) == 1 and not kwargs:
            raw = self._emit_expr(args[0])
            value = self._to_double(raw, args[0].ty)
            return self.builder.call(
                self._get_sqrt_function(),
                [value],
                name=self._fresh("math.sqrt"),
            )
        if kind == "math.pow" and len(args) == 2 and not kwargs:
            raw = self._emit_expr(args[0])
            value = self._to_double(raw, args[0].ty)
            raw2 = self._emit_expr(args[1])
            value2 = self._to_double(raw2, args[1].ty)
            return self.builder.call(
                self._get_pow_function(),
                [value, value2],
                name=self._fresh("math.pow"),
            )
        return None

    def _emit_i64_abs(self, value: ir.Value, name: str) -> ir.Value:
        zero = ir.Constant(_I64, 0)
        neg = self.builder.icmp_signed("<", value, zero, name=self._fresh(name + ".neg"))
        flipped = self.builder.sub(zero, value, name=self._fresh(name + ".flip"))
        return self.builder.select(neg, flipped, value, name=self._fresh(name))

    def _emit_i64_gcd(self, left: ir.Value, right: ir.Value) -> ir.Value:
        fn = self.current_function
        a_slot = self._alloca_in_entry(_I64, name="math.gcd.a.addr")
        b_slot = self._alloca_in_entry(_I64, name="math.gcd.b.addr")
        self.builder.store(self._emit_i64_abs(left, "math.gcd.a.abs"), a_slot)
        self.builder.store(self._emit_i64_abs(right, "math.gcd.b.abs"), b_slot)

        cond_bb = fn.append_basic_block(name=self._fresh("math.gcd.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("math.gcd.body"))
        end_bb = fn.append_basic_block(name=self._fresh("math.gcd.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        b_cur = self.builder.load(b_slot, name=self._fresh("math.gcd.b"))
        done = self.builder.icmp_signed(
            "==",
            b_cur,
            ir.Constant(_I64, 0),
            name=self._fresh("math.gcd.done"),
        )
        self.builder.cbranch(done, end_bb, body_bb)

        self.builder.position_at_end(body_bb)
        a_cur = self.builder.load(a_slot, name=self._fresh("math.gcd.a"))
        b_body = self.builder.load(b_slot, name=self._fresh("math.gcd.b.cur"))
        rem = self.builder.srem(a_cur, b_body, name=self._fresh("math.gcd.rem"))
        self.builder.store(b_body, a_slot)
        self.builder.store(rem, b_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        return self.builder.load(a_slot, name=self._fresh("math.gcd.result"))

    def _emit_native_math_prod(
        self,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if not args or len(args) > 2:
            return None
        start_expr = args[1] if len(args) == 2 else None
        for key, value in kwargs:
            if key != "start" or start_expr is not None:
                return None
            start_expr = value

        arg = args[0]
        arg_ty = arg.ty
        if not isinstance(arg_ty, (ListType, TupleType, DynType)):
            return None

        src_val = self._emit_expr(arg)
        src_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            src_val,
            arg_ty,
        )
        n_val = self.builder.call(
            self.runtime["py_obj_len"],
            [src_obj],
            name=self._fresh("math.prod.src.len"),
        )
        acc_init = (
            self._emit_expr_as_i64(start_expr)
            if start_expr is not None
            else ir.Constant(_I64, 1)
        )
        fn = self.current_function
        idx_slot = self._alloca_in_entry(_I64, name="math.prod.idx.addr")
        acc_slot = self._alloca_in_entry(_I64, name="math.prod.acc.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        self.builder.store(acc_init, acc_slot)

        cond_bb = fn.append_basic_block(name=self._fresh("math.prod.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("math.prod.body"))
        step_bb = fn.append_basic_block(name=self._fresh("math.prod.step"))
        end_bb = fn.append_basic_block(name=self._fresh("math.prod.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("math.prod.idx"))
        keep = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("math.prod.keep"),
        )
        self.builder.cbranch(keep, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("math.prod.idx.box"),
        )
        elem_obj = self.builder.call(
            self.runtime["py_obj_getitem"],
            [src_obj, idx_box],
            name=self._fresh("math.prod.elem"),
        )
        elem_i64 = marshal.marshal_from_object(
            self.builder,
            self.module,
            self.runtime,
            elem_obj,
            IntType(name="int"),
        )
        acc_cur = self.builder.load(acc_slot, name=self._fresh("math.prod.acc"))
        acc_next = self.builder.mul(
            acc_cur,
            elem_i64,
            name=self._fresh("math.prod.next.acc"),
        )
        self.builder.store(acc_next, acc_slot)
        self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        nxt = self.builder.add(
            cur,
            ir.Constant(_I64, 1),
            name=self._fresh("math.prod.next.idx"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        return self.builder.load(acc_slot, name=self._fresh("math.prod.result"))


__all__ = ["NativeMathLoweringMixin"]
