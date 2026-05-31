"""Native ``dataclasses`` helper lowering."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, Call, Name
from . import marshal


_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()



class NativeDataclassesLoweringMixin:
    def _emit_native_dataclasses_replace_call(self,
        expr: Call,
    ) -> Optional[ir.Value]:
        kwdict_unpack = self._split_starstar_kwargs_unpack(expr.args)
        if kwdict_unpack is None:
            positional = expr.args
            kwdict_expr = None
        else:
            positional, kwdict_expr = kwdict_unpack
        if len(positional) != 1:
            return None
        kind = None
        if isinstance(expr.func, Name):
            kind = self._native_builtin_value_for_name(expr.func.ident)
            if (
                kind is None
                and expr.func.ident in ("replace", "_replace")
                and expr.func.ident not in self.env
                and expr.func.ident not in getattr(self, "_module_globals", {})
            ):
                kind = "dataclasses.replace"
        elif isinstance(expr.func, Attr):
            kind = self._native_builtin_value_kind_for_expr(expr.func)
        if kind != "dataclasses.replace":
            return None
        obj_val = self._emit_expr(positional[0])
        obj_val = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            obj_val,
            positional[0].ty,
        )
        if kwdict_expr is not None:
            if expr.kwargs:
                return None
            kwdict_val = self._emit_expr(kwdict_expr)
            kwdict_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                kwdict_val,
                kwdict_expr.ty,
            )
            return self.builder.call(
                self.runtime["py_dataclass_replace_from_dict"],
                [obj_val, kwdict_obj],
                name=self._fresh("dataclasses.replace.kwdict"),
            )

        n_kw = len(expr.kwargs)
        if n_kw == 0:
            names_ptr = ir.Constant(_CSTR, None)
            vals_ptr = ir.Constant(_CSTR, None)
        else:
            names_arr_ty = ir.ArrayType(_CSTR, n_kw)
            vals_arr_ty = ir.ArrayType(_CSTR, n_kw)
            names_arr = self._alloca_in_entry(
                names_arr_ty,
                name=self._fresh("replace.kwn"),
            )
            vals_arr = self._alloca_in_entry(
                vals_arr_ty,
                name=self._fresh("replace.kwv"),
            )
            for i, (kw_name, kw_expr) in enumerate(expr.kwargs):
                name_gv = self._cstr_global(
                    kw_name,
                    f".replace.kwname.{i}.{kw_name}",
                )
                ngep = self.builder.gep(
                    names_arr,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"replace.kwn.{i}"),
                )
                self.builder.store(self._ptr_to_cstr(name_gv), ngep)

                raw_v = self._emit_expr(kw_expr)
                val_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    raw_v,
                    kw_expr.ty,
                )
                vgep = self.builder.gep(
                    vals_arr,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"replace.kwv.{i}"),
                )
                self.builder.store(val_obj, vgep)
            names_ptr = self.builder.bitcast(
                names_arr,
                _CSTR,
                name=self._fresh("replace.kwn.p"),
            )
            vals_ptr = self.builder.bitcast(
                vals_arr,
                _CSTR,
                name=self._fresh("replace.kwv.p"),
            )

        return self.builder.call(
            self.runtime["py_dataclass_replace"],
            [obj_val, ir.Constant(_I64, n_kw), names_ptr, vals_ptr],
            name=self._fresh("dataclasses.replace"),
        )


__all__ = ["NativeDataclassesLoweringMixin"]
