"""Native ``math`` module lowering helpers."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, Call, DynType, Expr, IntType, ListType, Name, TupleType
from . import marshal


_I64 = ir.IntType(64)
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
