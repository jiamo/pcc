"""print(...) lowering for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import BoolType, Call, FloatType, IntType
from . import marshal


_I8 = ir.IntType(8)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()


class PrintLoweringMixin:
    def _emit_print_call(self, call: Call) -> None:
        # print(*items) / print(a, *rest): a positional *splat. The per-arg
        # paths below would emit the *m marker (Call(Name("*"),...)) as a
        # Name("*") lookup -> runtime "NameError: name '*'". Expand the args
        # into a runtime tuple first. Only sep/end kwargs are handled natively
        # here; other kwargs (file=, flush=) fall through to the cpython path.
        if self._has_starred_unpack(call.args) and all(
            k in ("sep", "end") for k, _ in (call.kwargs or ())
        ):
            self._emit_print_many_splat(call)
            return
        if call.kwargs:
            only_sep_end = True
            for k, _ in call.kwargs:
                if k != "sep" and k != "end":
                    only_sep_end = False
            if only_sep_end:
                self._emit_print_many(call)
                return
            if self._try_emit_native_file_stream_print(call):
                return
            fn_val = self._load_cpython_builtin("print")
            self._finish_cpy_call_kw(
                fn_val,
                "print",
                call.args,
                call.kwargs,
            )
            return

        if len(call.args) == 0:
            nl_gv = self._cstr_global("\n", ".fmt_nl")
            self.builder.call(self._printf, [self._ptr_to_cstr(nl_gv)])
            return

        if len(call.args) > 1:
            self._emit_print_many(call)
            return

        arg = call.args[0]
        arg_ty = arg.ty

        if isinstance(arg_ty, IntType):
            exact_obj = self._maybe_emit_exact_int_object(arg)
            if exact_obj is not None:
                self.builder.call(self.runtime["py_print"], [exact_obj])
                return

        value = self._emit_expr(arg)

        if value in self._cpy_values:
            pcc_str = self.builder.call(
                self.runtime["py_cpy_to_pcc_str"],
                [value],
                name=self._fresh("cpy.str"),
            )
            self.builder.call(self.runtime["py_print"], [pcc_str])
            self.builder.call(self.runtime["py_cpy_decref"], [value])
            return

        if isinstance(arg_ty, IntType):
            if isinstance(value.type, ir.PointerType):
                self.builder.call(self.runtime["py_print"], [value])
                return
            fmt = self._ptr_to_cstr(self._get_fmt_int())
            self.builder.call(self._printf, [fmt, value])
            return
        if isinstance(arg_ty, FloatType):
            self._emit_print_float_value(value)
            return
        if isinstance(arg_ty, BoolType):
            true_fmt = self._ptr_to_cstr(self._get_fmt_bool_true())
            false_fmt = self._ptr_to_cstr(self._get_fmt_bool_false())
            chosen = self.builder.select(
                value, true_fmt, false_fmt, name=self._fresh("bool_fmt")
            )
            self.builder.call(self._printf, [chosen])
            return

        boxed_valueclass = self._emit_valueclass_payload_to_object(value, arg_ty)
        if boxed_valueclass is not None:
            obj = boxed_valueclass
        else:
            obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, value, arg_ty
            )
        self.builder.call(self.runtime["py_print"], [obj])

    def _emit_print_float_value(self, value: ir.Value) -> None:
        # Box the raw double and route through the runtime py_print, which
        # formats floats via py_float_repr_shortest (CPython shortest-round-trip
        # repr) and flushes/newlines consistently. The old inline-printf path
        # used "%g" (6 significant figures) for non-integral values, so e.g.
        # print(10/3) emitted "3.33333" instead of "3.3333333333333335".
        boxed = self.builder.call(
            self.runtime["py_float_from_f64"],
            [value],
            name=self._fresh("print.float.box"),
        )
        self.builder.call(self.runtime["py_print"], [boxed])

    def _emit_print_many(self, call: Call) -> None:
        n = len(call.args)
        n_val = ir.Constant(_I64, n)
        tup = self.builder.call(
            self.runtime["py_tuple_new"], [n_val], name=self._fresh("pr.args")
        )
        tup_root = self._alloca_in_entry(_CSTR, name=self._fresh("pr.args.root"))
        self.builder.store(tup, tup_root)
        self._emit_entry_gc_frame_enter(self._gc_one_slot_frame_map(), tup_root)
        for i, arg in enumerate(call.args):
            if isinstance(arg.ty, IntType):
                exact_obj = self._maybe_emit_exact_int_object(arg)
                if exact_obj is not None:
                    v_obj = exact_obj
                    idx = ir.Constant(_I64, i)
                    self.builder.call(
                        self.runtime["py_tuple_set_item"], [tup, idx, v_obj]
                    )
                    continue
            v = self._emit_expr(arg)
            if v in self._cpy_values:
                pcc_str = self.builder.call(
                    self.runtime["py_cpy_to_pcc_str"],
                    [v],
                    name=self._fresh("cpy.str"),
                )
                self.builder.call(self.runtime["py_cpy_decref"], [v])
                v_obj = pcc_str
            else:
                boxed_valueclass = self._emit_valueclass_payload_to_object(v, arg.ty)
                if boxed_valueclass is not None:
                    v_obj = boxed_valueclass
                else:
                    v_obj = marshal.marshal_to_object(
                        self.builder, self.module, self.runtime, v, arg.ty
                    )
            idx = ir.Constant(_I64, i)
            self.builder.call(self.runtime["py_tuple_set_item"], [tup, idx, v_obj])

        sep_obj: Optional[ir.Value] = None
        end_obj: Optional[ir.Value] = None
        for k, vexpr in call.kwargs:
            v = self._emit_expr(vexpr)
            boxed = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, vexpr.ty
            )
            if k == "sep":
                sep_obj = boxed
            elif k == "end":
                end_obj = boxed
        if sep_obj is None:
            sep_obj = self._emit_literal_str(" ")
        if end_obj is None:
            end_obj = self._emit_literal_str("\n")
        self.builder.call(self.runtime["py_print_many"], [tup, sep_obj, end_obj])
        self._emit_gc_frame_leave_for_slot(tup_root)

    def _emit_print_many_splat(self, call: Call) -> None:
        """``print(*items)`` / ``print(a, *rest, b)``: build the positional
        args as a runtime list (``_emit_pcc_args_list`` expands every splat and
        appends the plain args), convert it to a tuple via
        ``py_call_merge_posargs`` (an empty base + the list as *args), then hand
        that tuple to ``py_print_many`` like the fixed-arity path. Only sep/end
        kwargs reach here (gated by the caller)."""
        lst = self._emit_pcc_args_list(call.args, "print.splat")
        tup = self.builder.call(
            self.runtime["py_call_merge_posargs"],
            [ir.Constant(_CSTR, None), lst],
            name=self._fresh("pr.splat.args"),
        )
        tup_root = self._alloca_in_entry(_CSTR, name=self._fresh("pr.splat.root"))
        self.builder.store(tup, tup_root)
        self._emit_entry_gc_frame_enter(self._gc_one_slot_frame_map(), tup_root)
        sep_obj: Optional[ir.Value] = None
        end_obj: Optional[ir.Value] = None
        for k, vexpr in call.kwargs:
            v = self._emit_expr(vexpr)
            boxed = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, vexpr.ty
            )
            if k == "sep":
                sep_obj = boxed
            elif k == "end":
                end_obj = boxed
        if sep_obj is None:
            sep_obj = self._emit_literal_str(" ")
        if end_obj is None:
            end_obj = self._emit_literal_str("\n")
        self.builder.call(self.runtime["py_print_many"], [tup, sep_obj, end_obj])
        self._emit_gc_frame_leave_for_slot(tup_root)
