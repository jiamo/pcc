"""CPython bridge primitive helpers for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    ByteArrayType,
    BoolType,
    BytesType,
    ClassType,
    DictType,
    DynType,
    FloatType,
    FuncType,
    IntType,
    ListType,
    MemoryViewType,
    NoneType,
    SetType,
    StrType,
    TupleType,
    Type,
)
from . import marshal


_I64 = ir.IntType(64)


def _is_none_type_for_cpython_bridge(ty: Type) -> bool:
    if isinstance(ty, NoneType):
        return True
    # pcc1 self-host can cross module/class identity boundaries while still
    # preserving the frontend type object's nominal class/name.  Do not make
    # the CPython bridge depend solely on ``isinstance`` for the None sentinel.
    if type(ty).__name__ == "NoneType":
        return True
    return getattr(ty, "name", "") in ("None", "NoneType")


class CpyBridgeLoweringMixin:
    def _mark_cpy_value(self, value: ir.Value) -> ir.Value:
        self._cpy_values.add(value)
        return value

    def _emit_cpy_attr(self, obj_val: ir.Value, name: str) -> ir.Value:
        attr_ptr = self._ptr_to_cstr(self._cstr_global(name, f".cpy.attr.{name}"))
        val = self.builder.call(
            self.runtime["py_cpy_getattr"],
            [obj_val, attr_ptr],
            name=self._fresh(f"cpy.get.{name}"),
        )
        return self._mark_cpy_value(val)

    def _load_cpython_builtin(self, name: str) -> ir.Value:
        import os
        import sys

        module_name = ""
        try:
            module_name = self.ast_module.name
        except AttributeError:
            module_name = ""
        function_name = "<module>"
        current_func_def = getattr(self, "current_func_def", None)
        if current_func_def is not None:
            function_name = current_func_def.name
        strict_stub_filter = os.environ.get(
            "PCC_DEBUG_STRICT_NOLIB_STUB", ""
        ).strip()
        qualified_name = str(module_name) + "." + str(function_name)
        if strict_stub_filter and (
            strict_stub_filter == "1" or strict_stub_filter in qualified_name
        ):
            sys.stderr.write(
                "[pcc.strict-nolib-fallback] "
                + qualified_name
                + ": builtin="
                + str(name)
                + "\n"
            )
        if os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE"):
            sys.stderr.write(
                "debug: load_cpython_builtin module="
                + str(module_name)
                + " name="
                + str(name)
                + "\n"
            )
        mod_name_gv = self._cstr_global(
            "builtins",
            ".cpy.builtins_modname",
        )
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"],
            [self._ptr_to_cstr(mod_name_gv)],
            name=self._fresh("cpy.builtins"),
        )
        attr_gv = self._cstr_global(
            name,
            f".cpy.builtin.{name}",
        )
        fn_val = self.builder.call(
            self.runtime["py_cpy_getattr"],
            [mod_val, self._ptr_to_cstr(attr_gv)],
            name=self._fresh(f"cpy.builtin.{name}"),
        )
        return self._mark_cpy_value(fn_val)

    def _emit_value_as_pcc_object_or_bridge(
        self,
        value: ir.Value,
        value_ty: Type,
        name_hint: str,
        *,
        consume_valueclass_payload_fields: bool = False,
    ) -> ir.Value:
        if value in self._cpy_values:
            bridged = self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"],
                [value],
                name=self._fresh(name_hint),
            )
            self.builder.call(self.runtime["py_cpy_decref"], [value])
            return bridged
        boxed_valueclass = self._emit_valueclass_payload_to_object(
            value,
            value_ty,
            consume_fields=consume_valueclass_payload_fields,
        )
        if boxed_valueclass is not None:
            return boxed_valueclass
        return marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            value,
            value_ty,
        )

    def _marshal_to_cpython(self, v: ir.Value, ty: Type) -> tuple[ir.Value, bool]:
        """Convert a pcc-native value to a CPython PyObject*.

        Returns (cpython_value, owned) — ``owned`` is True when the
        caller must decref the result after use. Borrowed values
        (already-CPython DynType) return False.
        """
        # IR-level guard that fires before the declared-type dispatch:
        # if we actually hold a native-scalar payload (double / float /
        # int) we need to box, regardless of what inference claimed.
        # This catches cases where the type inferrer widens to ``int``
        # or a class annotation but the IR value is a raw double from
        # a ``load double, ptr %f.addr``.
        if isinstance(v.type, (ir.DoubleType, ir.FloatType)):
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_f64"],
                    [v],
                    name=self._fresh("cpy.from_f64.ir"),
                ),
                True,
            )
        if isinstance(v.type, ir.IntType):
            if v.type.width == 1:
                i64 = self.builder.zext(v, _I64, name=self._fresh("cpy.b2i64.ir"))
            elif v.type.width == 64:
                i64 = v
            elif v.type.width < 64:
                i64 = self.builder.sext(
                    v,
                    _I64,
                    name=self._fresh("cpy.sext64.ir"),
                )
            else:
                i64 = self.builder.trunc(
                    v,
                    _I64,
                    name=self._fresh("cpy.trunc64.ir"),
                )
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_i64"],
                    [i64],
                    name=self._fresh("cpy.from_i64.ir"),
                ),
                True,
            )
        if v in getattr(self, "_cpy_values", ()):
            return v, False
        if isinstance(ty, IntType) or isinstance(ty, BoolType):
            i64 = self._to_int64(v, ty)
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_i64"],
                    [i64],
                    name=self._fresh("cpy.from_i64"),
                ),
                True,
            )
        if isinstance(ty, FloatType):
            if isinstance(v.type, ir.PointerType):
                # CPython fallback paths like ``float(x)`` already
                # return a CPython ``float`` object; forwarding that
                # object is correct, and avoids trying to re-box a ptr
                # as though it were a native ``double``.
                return v, False
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_f64"],
                    [v],
                    name=self._fresh("cpy.from_f64"),
                ),
                True,
            )
        if isinstance(ty, StrType):
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pccstr"],
                    [v],
                    name=self._fresh("cpy.from_pccstr"),
                ),
                True,
            )
        if _is_none_type_for_cpython_bridge(ty):
            # None → CPython's Py_None (borrowed ref from the universal
            # converter on a pcc py_None). Use the same converter so we
            # don't have to teach codegen about the CPython Py_None sym.
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"],
                    [v],
                    name=self._fresh("cpy.from_pcc_none"),
                ),
                True,
            )
        if isinstance(
            ty,
            (
                ListType,
                DictType,
                TupleType,
                SetType,
                BytesType,
                ByteArrayType,
                MemoryViewType,
            ),
        ):
            # pcc-native object containers / byte buffers — rebuild as a
            # CPython object.
            # The universal converter walks the pcc object via type tag
            # and recurses through nested containers.
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"],
                    [v],
                    name=self._fresh(f"cpy.from_pcc_{type(ty).__name__.lower()[:-4]}"),
                ),
                True,
            )
        # DynType with a native integer / float / pointer payload: pick
        # the marshaller that matches the IR type we actually hold.
        if isinstance(ty, DynType):
            if isinstance(v.type, ir.IntType):
                if v.type.width == 1:
                    # bool → CPython bool via int(0/1).
                    i64 = self.builder.zext(v, _I64, name=self._fresh("b2i64"))
                else:
                    i64 = (
                        v
                        if v.type.width == 64
                        else self.builder.sext(
                            v,
                            _I64,
                            name=self._fresh("sext64"),
                        )
                    )
                return (
                    self.builder.call(
                        self.runtime["py_cpy_from_i64"],
                        [i64],
                        name=self._fresh("cpy.from_i64.dyn"),
                    ),
                    True,
                )
            if isinstance(v.type, (ir.FloatType, ir.DoubleType)):
                return (
                    self.builder.call(
                        self.runtime["py_cpy_from_f64"],
                        [v],
                        name=self._fresh("cpy.from_f64.dyn"),
                    ),
                    True,
                )
            if isinstance(v.type, ir.PointerType):
                # Native pcc object (not already a CPython ref) —
                # rebuild the corresponding CPython object by walking
                # the runtime type tag.
                return (
                    self.builder.call(
                        self.runtime["py_cpy_from_pcc_obj"],
                        [v],
                        name=self._fresh("cpy.from_pcc.dynobj"),
                    ),
                    True,
                )
        # ClassType / instance values are PyObject* in pcc-native form,
        # so route through the same py_cpy_from_pcc_obj bridge as the
        # DynType-pointer case. Without this clause, isinstance type
        # narrowing would surface ``Layer 1 cannot marshal ClassType``
        # at sites where the narrowed variable feeds a CPython call.
        if isinstance(ty, ClassType) and isinstance(v.type, ir.PointerType):
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"],
                    [v],
                    name=self._fresh("cpy.from_pcc.cls"),
                ),
                True,
            )
        if isinstance(ty, FuncType) and isinstance(v.type, ir.PointerType):
            # Function values are already opaque callable objects at
            # runtime. CPython-backed call paths use this for callback
            # arguments such as ``re.sub(..., repl, text)`` during
            # host-only pass self-compile probes.
            return v, False
        raise NotImplementedError(
            f"Layer 1 cannot marshal {type(ty).__name__} to CPython yet"
        )
