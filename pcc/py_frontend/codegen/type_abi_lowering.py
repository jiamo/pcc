"""Type and ABI mapping helpers for Layer-1 Python codegen."""

from __future__ import annotations

import os
import sys
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Arg,
    BoolType,
    ByteArrayType,
    BytesType,
    ClassDef,
    ClassType,
    ComplexType,
    DictType,
    DynType,
    FloatType,
    FuncDef,
    FuncType,
    IntType,
    Import,
    ImportFrom,
    ListType,
    MemoryViewType,
    NoneType,
    StrType,
    TupleType,
    Type,
)
from .errors import L1CodegenError
from .layer1_support import _import_from_module_or_empty, _stmt_kind_name

_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
_CSTR = _I8.as_pointer()
_VOID = ir.VoidType()
_ClassDef = ClassDef
_PY_EXC_TYPEERROR = 3


class TypeAbiLoweringMixin:
    def _module_imports_raw_int_scaffold(self) -> bool:
        mod_name = self.ast_module.name or ""
        if mod_name == "pcc" or mod_name.startswith("pcc."):
            return True
        if mod_name == "bootstrap" or mod_name.startswith("bootstrap."):
            return True
        for stmt in self.ast_module.body:
            if isinstance(stmt, ImportFrom):
                import_module = _import_from_module_or_empty(stmt)
                if (
                    self._is_extern_scaffold_import_module(import_module)
                    or import_module == "pcc.unsafe"
                ):
                    return True
            if isinstance(stmt, Import):
                for mod_name, _as_name in stmt.names:
                    if (
                        self._is_extern_scaffold_import_module(mod_name)
                        or mod_name == "pcc.unsafe"
                    ):
                        return True
        return False

    def _module_imports_c_abi_export(self) -> bool:
        debug_codegen = bool(os.environ.get("PCC_DEBUG_CODEGEN_PHASES"))
        stmt_index = 0
        for stmt in self.ast_module.body:
            if debug_codegen:
                sys.stderr.write(
                    "[pcc.codegen] "
                    + (self.ast_module.name or "<module>")
                    + ":module flags c-abi scan "
                    + str(stmt_index)
                    + " "
                    + _stmt_kind_name(stmt)
                    + "\n"
                )
            if isinstance(stmt, FuncDef):
                if debug_codegen:
                    sys.stderr.write(
                        "[pcc.codegen] "
                        + (self.ast_module.name or "<module>")
                        + ":module flags c-abi func decorators\n"
                    )
                decorators = self._func_decorators(stmt)
                if debug_codegen:
                    sys.stderr.write(
                        "[pcc.codegen] "
                        + (self.ast_module.name or "<module>")
                        + ":module flags c-abi func decorator count "
                        + str(len(decorators))
                        + "\n"
                    )
                dec_index = 0
                while dec_index < len(decorators):
                    dec = decorators[dec_index]
                    if self._decorator_c_abi_export_symbol(dec) is not None:
                        return True
                    dec_index += 1
            if isinstance(stmt, _ClassDef):
                if debug_codegen:
                    sys.stderr.write(
                        "[pcc.codegen] "
                        + (self.ast_module.name or "<module>")
                        + ":module flags c-abi class body\n"
                    )
                for class_stmt in stmt.body:
                    if not isinstance(class_stmt, FuncDef):
                        continue
                    decorators = self._func_decorators(class_stmt)
                    if debug_codegen:
                        sys.stderr.write(
                            "[pcc.codegen] "
                            + (self.ast_module.name or "<module>")
                            + ":module flags c-abi method decorator count "
                            + str(len(decorators))
                            + "\n"
                        )
                    dec_index = 0
                    while dec_index < len(decorators):
                        dec = decorators[dec_index]
                        if self._decorator_c_abi_export_symbol(dec) is not None:
                            return True
                        dec_index += 1
            stmt_index += 1
        return False

    def _should_box_python_ints(self) -> bool:
        return not self._module_uses_raw_int_scaffold

    def _int_exprs_are_boxed(self) -> bool:
        return bool(self._box_int_locals)

    def _storage_ir_type(self, ty: Type) -> ir.Type:
        if isinstance(ty, IntType) and self._int_exprs_are_boxed():
            return _CSTR
        return self._map_type(ty)

    def _abi_ir_type(self, ty: Type, *, box_int_abi: bool) -> ir.Type:
        if box_int_abi and isinstance(ty, IntType):
            return _CSTR
        return self._map_type(ty)

    def _export_box_int_abi(self, info: dict) -> bool:
        return bool(info.get("box_int_abi", self._should_box_python_ints()))

    def _funcdef_uses_boxed_int_abi(
        self,
        fd: FuncDef,
        *,
        c_abi_sym: str | None,
    ) -> bool:
        if c_abi_sym is not None:
            return False
        if not self._should_box_python_ints():
            return False
        return not self._funcdef_uses_unboxed_typed_int_abi(fd)

    def _map_type(self, ty: Type) -> ir.Type:
        """Map a pcc_py :class:`Type` to its LLVM IR representation.

        Phase 1 scalars lower to native types; Phase 2 object types
        (str / list / dict / tuple / None) lower to ``PyObject*`` (an
        opaque pointer).
        """
        if isinstance(ty, IntType):
            # We always lower to i64 in L1 regardless of the declared
            # width; the type-infer layer is expected to have
            # range-checked narrower widths already. The ``width`` field
            # will matter once tagged-int codegen lands in Phase 2.
            return _I64
        if isinstance(ty, FloatType):
            return _DOUBLE
        if isinstance(ty, BoolType):
            return _I1
        if isinstance(ty, ClassType) and bool(getattr(ty, "valueclass", False)):
            payload_ty = self._valueclass_payload_ir_type(ty)
            if payload_ty is not None:
                return payload_ty
        if isinstance(
            ty,
            (
                StrType,
                BytesType,
                ByteArrayType,
                MemoryViewType,
                ListType,
                DictType,
                TupleType,
                ClassType,
                ComplexType,
            ),
        ):
            return _CSTR  # alias for i8* == PyObject*
        if isinstance(ty, NoneType):
            # None is a PyObject* (points to the global ``py_None``).
            # Using a pointer (not void) lets us store and load None in
            # locals uniformly with other object types.
            return _CSTR
        if isinstance(ty, DynType):
            # A generic PyObject* slot: covers class instances, results
            # of ``MyClass(args)`` construction, attribute fetches, and
            # anything else the type inferer did not narrow.
            return _CSTR
        if isinstance(ty, FuncType):
            # A first-class function value — at L1 the callable is
            # wrapped as a CPython object (lambda lowered to
            # ``operator.<getter>`` or a hoisted pcc FuncDef exposed
            # through PyCFunction wrapping). Either way the local slot
            # holds an opaque PyObject* pointer.
            return _CSTR
        if isinstance(ty, Type) or getattr(ty, "name", None) in (
            "None",
            "dyn",
            "Type",
        ):
            # A bare Type object means inference preserved an opaque runtime
            # type value rather than a concrete pcc scalar/container type.
            # Store it as PyObject* instead of failing the self-host path.
            return _CSTR
        raise NotImplementedError(
            f"Layer 1 does not handle type {type(ty).__name__} "
            f"(name={getattr(ty, 'name', '?')!r})"
        )

    def _valueclass_payload_ir_type(self, ty: Type) -> Optional[ir.Type]:
        if not isinstance(ty, ClassType):
            return None
        if not bool(getattr(ty, "valueclass", False)):
            return None
        if len(ty.fields) == 0:
            return None
        field_ir_types: list[ir.Type] = []
        for _field_name, field_ty in ty.fields:
            field_ir_ty = self._valueclass_field_payload_ir_type(field_ty)
            if field_ir_ty is None:
                return None
            field_ir_types.append(field_ir_ty)
        n_fields = len(field_ir_types)
        if n_fields == 1:
            return ir.LiteralStructType((field_ir_types[0],))
        if n_fields == 2:
            return ir.LiteralStructType((field_ir_types[0], field_ir_types[1]))
        if n_fields == 3:
            return ir.LiteralStructType(
                (
                    field_ir_types[0],
                    field_ir_types[1],
                    field_ir_types[2],
                )
            )
        if n_fields == 4:
            return ir.LiteralStructType(
                (
                    field_ir_types[0],
                    field_ir_types[1],
                    field_ir_types[2],
                    field_ir_types[3],
                )
            )
        return None

    def _valueclass_field_payload_ir_type(
        self,
        field_ty: Type,
    ) -> Optional[ir.Type]:
        if isinstance(field_ty, IntType):
            return _I64
        if isinstance(field_ty, FloatType):
            return _DOUBLE
        if isinstance(field_ty, BoolType):
            return _I1
        if isinstance(field_ty, ClassType) and bool(
            getattr(field_ty, "valueclass", False)
        ):
            return None
        if isinstance(
            field_ty,
            (
                StrType,
                BytesType,
                ByteArrayType,
                MemoryViewType,
                ListType,
                DictType,
                TupleType,
                ClassType,
                NoneType,
                DynType,
                FuncType,
                ComplexType,
            ),
        ):
            return _CSTR
        if isinstance(field_ty, Type) or getattr(field_ty, "name", None) in (
            "None",
            "dyn",
            "Type",
        ):
            return _CSTR
        return None

    def _is_valueclass_payload_type(self, ty: Type) -> bool:
        return self._valueclass_payload_ir_type(ty) is not None

    def _valueclass_field_info(
        self,
        ty: Type,
        attr_name: str,
    ) -> Optional[tuple[int, Type]]:
        if not isinstance(ty, ClassType):
            return None
        if not bool(getattr(ty, "valueclass", False)):
            return None
        for idx, (field_name, field_ty) in enumerate(ty.fields):
            if field_name == attr_name:
                return idx, field_ty
        return None

    def _emit_valueclass_payload_to_object(
        self,
        value: ir.Value,
        ty: Type,
    ) -> Optional[ir.Value]:
        if not isinstance(ty, ClassType):
            return None
        if not self._is_valueclass_payload_type(ty):
            return None
        if isinstance(value.type, ir.PointerType):
            return value
        if not hasattr(self, "class_lowering"):
            return None
        class_name = self._ensure_class_type_registered(ty)
        if class_name is None:
            return None
        info = self.class_lowering.classes.get(class_name)
        if info is None:
            return None

        from . import marshal

        cls_ptr = self.builder.load(
            info.global_var,
            name=self._fresh(f"value.{ty.name}.class"),
        )
        inst = self.builder.call(
            self.runtime["py_valuebox_new"],
            [cls_ptr],
            name=self._fresh(f"value.{ty.name}.box"),
        )
        for idx, (_field_name, field_ty) in enumerate(ty.fields):
            field_value = self.builder.extract_value(
                value,
                [idx],
                name=self._fresh(f"value.{ty.name}.box.field{idx}"),
            )
            field_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                field_value,
                field_ty,
            )
            self.builder.call(
                self.runtime["py_valuebox_set_field"],
                [inst, ir.Constant(_I32, idx), field_obj],
            )
        return inst

    def _emit_object_to_valueclass_payload(
        self,
        value: ir.Value,
        ty: Type,
    ) -> Optional[ir.Value]:
        if not isinstance(ty, ClassType):
            return None
        payload_ty = self._valueclass_payload_ir_type(ty)
        if payload_ty is None:
            return None
        if not isinstance(value.type, ir.PointerType):
            if str(value.type) == str(payload_ty):
                return value
            return None

        from . import marshal

        if not hasattr(self, "class_lowering"):
            return None
        class_name = self._ensure_class_type_registered(ty)
        if class_name is None:
            return None
        info = self.class_lowering.classes.get(class_name)
        if info is None:
            return None
        cls_ptr = self.builder.load(
            info.global_var,
            name=self._fresh(f"value.{ty.name}.unbox.class"),
        )
        is_instance = self.builder.call(
            self.runtime["py_obj_isinstance"],
            [value, cls_ptr],
            name=self._fresh(f"value.{ty.name}.unbox.isinstance"),
        )
        is_instance_ok = self.builder.icmp_signed(
            "!=",
            is_instance,
            ir.Constant(_I64, 0),
            name=self._fresh(f"value.{ty.name}.unbox.isinstance.ok"),
        )
        fn = self.current_function
        fail_bb = fn.append_basic_block(
            name=self._fresh(f"value.{ty.name}.unbox.typeerror"),
        )
        ok_bb = fn.append_basic_block(
            name=self._fresh(f"value.{ty.name}.unbox.ok"),
        )
        self.builder.cbranch(is_instance_ok, ok_bb, fail_bb)

        self.builder.position_at_end(fail_bb)
        exc = self.builder.call(
            self.runtime["py_exc_new"],
            [
                ir.Constant(_I64, _PY_EXC_TYPEERROR),
                self._ptr_to_cstr(
                    self._cstr_global(
                        f"expected {ty.name} valueclass instance",
                        self._fresh(f".err.value.{ty.name}.unbox"),
                    )
                ),
            ],
            name=self._fresh(f"value.{ty.name}.unbox.exc"),
        )
        self.builder.call(self.runtime["py_raise"], [exc])
        err_target = self._current_try_err_block()
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)

        self.builder.position_at_end(ok_bb)

        payload_slot = self._alloca_in_entry(
            payload_ty,
            name=self._fresh(f"value.{ty.name}.unbox.tmp"),
        )
        zero = ir.Constant(_I32, 0)
        for idx, (_field_name, field_ty) in enumerate(ty.fields):
            field_obj = self.builder.call(
                self.runtime["py_valuebox_get_field"],
                [value, ir.Constant(_I32, idx)],
                name=self._fresh(f"value.{ty.name}.unbox.field{idx}.obj"),
            )
            field_value = marshal.marshal_from_object(
                self.builder,
                self.module,
                self.runtime,
                field_obj,
                field_ty,
            )
            field_ptr = self.builder.gep(
                payload_slot,
                [zero, ir.Constant(_I32, idx)],
                inbounds=True,
                name=self._fresh(f"value.{ty.name}.unbox.field{idx}"),
            )
            self.builder.store(field_value, field_ptr)
        return self.builder.load(
            payload_slot,
            name=self._fresh(f"value.{ty.name}.unbox.payload"),
        )

    def _is_scalar(self, ty: Type) -> bool:
        return isinstance(ty, (IntType, FloatType, BoolType))

    def _is_object(self, ty: Type) -> bool:
        if isinstance(ty, ClassType) and bool(getattr(ty, "valueclass", False)):
            if self._is_valueclass_payload_type(ty):
                return False
        if isinstance(ty, Type) and not isinstance(ty, (IntType, FloatType, BoolType)):
            return True
        if getattr(ty, "name", None) in ("None", "dyn", "Type"):
            return True
        return isinstance(
            ty,
            (
                StrType,
                BytesType,
                ByteArrayType,
                MemoryViewType,
                ListType,
                DictType,
                TupleType,
                ClassType,
                NoneType,
                DynType,
                FuncType,
                ComplexType,
            ),
        )

    def _param_ir_and_bind_type(
        self,
        arg,
        *,
        require_annotation: bool,
        owner_name: str,
        box_int_params: bool = False,
    ) -> tuple[ir.Type, Type | None]:
        """Return the IR param type plus the env-binding type for ``arg``.

        ``*args`` and ``**kwargs`` lower as ordinary PyObject* params
        carrying a tuple / dict value respectively. That keeps function
        bodies compilable even before full L3 vararg semantics land.
        """
        if arg.kind in ("pos", "pos_only", "kw_only"):
            try:
                annotation = arg.annotation
            except AttributeError:
                annotation = None
            if annotation is None:
                if require_annotation:
                    raise L1CodegenError(
                        f"Layer 1 requires an annotation on parameter "
                        f"{arg.name!r} of function {owner_name!r}"
                    )
                return _CSTR, DynType(name="dyn")
            if box_int_params and isinstance(annotation, IntType):
                return _CSTR, annotation
            return self._map_type(annotation), annotation
        if arg.kind == "*args":
            return _CSTR, TupleType(name="tuple", elems=())
        if arg.kind == "**kwargs":
            return _CSTR, DictType(
                name="dict",
                key=StrType(name="str"),
                value=DynType(name="dyn"),
            )
        raise NotImplementedError(
            f"Layer 1 parameter kind {arg.kind!r} "
            f"(in function {owner_name!r}) not supported"
        )

    # -- nested-def hoisting -------------------------------------------
