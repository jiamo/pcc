"""Owned-local and GC-root helper lowering for Layer-1 codegen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Attr,
    BinOp,
    BoolType,
    BytesLit,
    Call,
    ClassType,
    DictExpr,
    DynType,
    Expr,
    FloatType,
    IntType,
    ListExpr,
    ListType,
    Name,
    NoneType,
    StrLit,
    StrType,
    Subscript,
    TupleExpr,
    TupleType,
    Type,
)


_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_CSTR = _I8.as_pointer()

_UNSAFE_RAW_POINTER_RETURNS = frozenset(
    {
        "malloc",
        "cstr",
        "global_addr",
        "global_load_ptr",
        "calloc",
        "realloc",
        "ptr_add",
        "null",
        "tag_int",
        "load_ptr",
        "memset",
        "memcpy",
        "memmove",
        "getenv",
        "target_sys_platform",
        "target_platform_machine",
        "call_ptr1",
        "call_ptr2",
    }
)


class OwnershipLoweringMixin:
    def _gc_retain(self, obj: ir.Value, name: str = "") -> ir.Value:
        return self.builder.call(
            self.runtime["pcc_gc_retain"],
            [obj],
            name=name or self._fresh("gc.retain"),
        )

    def _release_context_label(self, kind: str) -> str:
        fn_name = "<module>"
        if self.current_func_def is not None:
            fn_name = self.current_func_def.name
        cls_name = getattr(self.current_class, "name", "")
        if cls_name:
            return f"{kind}:{cls_name}.{fn_name}"
        return f"{kind}:{fn_name}"

    def _release_expr_label(self, kind: str, expr: Expr) -> str:
        label = f"{self._release_context_label(kind)}:{type(expr).__name__}"
        span = getattr(expr, "span", None)
        if span is not None:
            label += f":{span.file}:{span.line}:{span.col}"
        return label

    def _debug_check_release(self, obj: ir.Value, label: str) -> None:
        if not self._debug_release_checks:
            return
        if "pcc_debug_check_release" not in self.runtime:
            return
        name_gv = self._cstr_global(
            label,
            self._fresh(".release.name"),
        )
        self.builder.call(
            self.runtime["pcc_debug_check_release"],
            [self._ptr_to_cstr(name_gv), obj],
        )

    def _gc_release(self, obj: ir.Value, label: Optional[str] = None) -> None:
        self._debug_check_release(
            obj,
            label or self._release_context_label("release"),
        )
        self.builder.call(self.runtime["pcc_gc_release"], [obj])

    def _gc_release_if_owned(self, obj: ir.Value, source_expr: Expr) -> None:
        if obj is None:
            return
        if not isinstance(obj.type, ir.PointerType):
            return
        if not self._raw_scaffold_object_rhs_is_owned(source_expr):
            return
        if not self._expr_returns_owned_object(source_expr):
            return
        if obj in getattr(self, "_cpy_values", ()):
            return
        self._gc_release(obj, self._release_expr_label("owned", source_expr))

    def _expr_returns_owned_object(self, expr: Expr) -> bool:
        if self._expr_returns_unsafe_raw_pointer(expr):
            return False
        if isinstance(expr, Name) and self._name_returns_owned_function_value(
            expr.ident
        ):
            return True
        if (
            isinstance(expr, Call)
            and isinstance(expr.func, Name)
            and expr.func.ident == "_dataclass_field_value"
        ):
            return False
        if isinstance(
            expr,
            (
                Call,
                ListExpr,
                DictExpr,
                TupleExpr,
                StrLit,
                BytesLit,
                Subscript,
            ),
        ):
            return True
        if isinstance(expr, BinOp) and isinstance(
            expr.ty,
            (StrType, ListType, TupleType),
        ):
            return True
        if isinstance(expr, Attr):
            return self._attr_expr_returns_owned_object(expr)
        return False

    def _attr_expr_returns_owned_object(self, expr: Attr) -> bool:
        """Return true for attribute loads known to produce a new ref."""
        current_class = self.current_class
        if (
            current_class is not None
            and isinstance(expr.obj, Name)
            and expr.obj.ident == "self"
        ):
            if (
                self.class_lowering.lookup_field_index(current_class, expr.name)
                is not None
            ):
                return True
            if (
                self.class_lowering.lookup_class_attr(current_class, expr.name)
                is not None
            ):
                return False

        if isinstance(expr.obj, Name):
            hint = self._class_hint_for_expr(expr.obj)
            if hint is not None:
                class_info = self.class_lowering.classes.get(hint)
                if (
                    class_info is not None
                    and self.class_lowering.lookup_field_index(class_info, expr.name)
                    is not None
                ):
                    return True
        return False

    def _name_returns_owned_function_value(self, ident: str) -> bool:
        if ident in self.env:
            return False
        if ident in getattr(self, "_module_globals", {}):
            return False
        if ident in self.functions:
            return True
        direct_hoist = f"__nested_{ident}"
        if direct_hoist in self.functions:
            return True
        matches = [
            name for name in self.functions if name.startswith(f"{direct_hoist}_")
        ]
        return len(matches) == 1

    def _expr_returns_unsafe_raw_pointer(self, expr: Expr) -> bool:
        if not isinstance(expr, Call):
            return False
        if not isinstance(expr.func, Name):
            return False
        intrinsic = self._unsafe_intrinsic_for_name(expr.func.ident)
        return intrinsic in _UNSAFE_RAW_POINTER_RETURNS

    def _container_store_temp_needs_release(
        self,
        expr: Expr,
        value_ty: Type,
        is_cpy: bool,
    ) -> bool:
        if is_cpy:
            return True
        if self._expr_returns_owned_object(expr):
            return True
        return isinstance(value_ty, (IntType, FloatType, BoolType, NoneType))

    def _raw_scaffold_object_rhs_is_owned(self, expr: Expr) -> bool:
        if not self._module_uses_raw_int_scaffold:
            return True
        # Runtime ports / bootstrap modules that export C ABI symbols
        # usually manage object refs by hand. For ordinary user scripts
        # that import pcc.extern just to reach native APIs, track only
        # object-producing expressions we can identify with reasonable
        # confidence.
        if self._module_has_c_abi_export:
            return isinstance(expr, (ListExpr, DictExpr, TupleExpr, StrLit, BytesLit))
        if isinstance(expr, (ListExpr, DictExpr, TupleExpr, StrLit, BytesLit)):
            return True
        if isinstance(expr, Subscript):
            return False
        if self._expr_returns_unsafe_raw_pointer(expr):
            return False
        if not isinstance(expr, Call):
            return self._expr_returns_owned_object(expr)

        if isinstance(expr.func, Name):
            callee = expr.func.ident
            if (
                hasattr(self, "class_lowering")
                and callee in self.class_lowering.classes
            ):
                return True
            if callee in getattr(self, "functions", {}):
                return self._expr_returns_owned_object(expr)

        func_ty = getattr(expr.func, "ty", None)
        if isinstance(func_ty, ClassType):
            # Class constructors always produce Python objects.
            return True

        if isinstance(expr.func, Name) and expr.func.ident in {
            "list",
            "dict",
            "tuple",
            "set",
            "frozenset",
        }:
            return True

        return False

    def _mark_owned_local_if_object(
        self,
        name: str,
        ir_ty: ir.Type,
        expr: Optional[Expr] = None,
    ) -> None:
        if self.current_func_def is None:
            return
        if expr is not None and not self._raw_scaffold_object_rhs_is_owned(expr):
            return
        if name in getattr(self, "_current_global_names", set()):
            return
        if name in getattr(self, "_current_param_names", set()):
            return
        if not isinstance(ir_ty, ir.PointerType):
            return
        if not self._ir_type_matches(ir_ty, _CSTR):
            return
        if expr is not None and not self._expr_returns_owned_object(expr):
            return
        self._owned_local_names.add(name)
        slot = self.env.get(name)
        if slot is not None:
            alloca, _slot_ir_ty, _decl_ty = slot
            self._ensure_owned_local_gc_root(name, alloca, ir_ty)

    def _as_gc_ptr(
        self,
        value: ir.Value,
        *,
        name: str = "",
    ) -> ir.Value:
        if value.type == _CSTR:
            return value
        cast_name = name
        if cast_name == "":
            cast_name = self._fresh("gc.ptr")
        return self.builder.bitcast(
            value,
            _CSTR,
            name=cast_name,
        )

    def _emit_entry_gc_frame_enter(
        self,
        frame_map: ir.Value,
        slots: ir.Value,
    ) -> None:
        # Reuse self.builder rather than constructing a local
        # ``tmp_builder = ir.IRBuilder(entry)``: under pcc-py self-host
        # the local-variable IRBuilder alias didn't reliably register in
        # ``_ir_builder_env_flags``, causing ``tmp_builder.bitcast(...)``
        # to fall through scaffold dispatch and pick ``Value.bitcast``
        # (1 positional arg) instead of the 3-arg IRBuilder.bitcast,
        # producing "Value.bitcast: too many positional args" during the
        # pcc1→pcc2 stage. Save/restore the builder's insertion point
        # around the entry-block emission.
        fn = self.current_function
        entry = fn.blocks[0]
        saved_block = self.builder._block
        terminator = None
        for instr in reversed(entry._instrs):
            if self._instruction_is_terminator(instr):
                terminator = instr
                break
        if terminator is not None:
            self.builder.position_before(terminator)
        else:
            self.builder.position_at_end(entry)
        frame_map_ptr = frame_map
        if frame_map_ptr.type != _CSTR:
            frame_map_ptr = self.builder.bitcast(
                frame_map,
                _CSTR,
                name=self._fresh("gc.frame.map.ptr"),
            )
        slots_ptr = slots
        if slots_ptr.type != _CSTR:
            slots_ptr = self.builder.bitcast(
                slots,
                _CSTR,
                name=self._fresh("gc.frame.slots.ptr"),
            )
        self.builder.call(
            self.runtime["pcc_gc_frame_enter"],
            [frame_map_ptr, slots_ptr],
        )
        self.builder.position_at_end(saved_block)

    def _emit_current_gc_frame_enter(
        self,
        frame_map: ir.Value,
        slots: ir.Value,
    ) -> None:
        frame_map_ptr = frame_map
        if frame_map_ptr.type != _CSTR:
            frame_map_ptr = self.builder.bitcast(
                frame_map,
                _CSTR,
                name=self._fresh("gc.frame.map.ptr"),
            )
        slots_ptr = slots
        if slots_ptr.type != _CSTR:
            slots_ptr = self.builder.bitcast(
                slots,
                _CSTR,
                name=self._fresh("gc.frame.slots.ptr"),
            )
        self.builder.call(
            self.runtime["pcc_gc_frame_enter"],
            [frame_map_ptr, slots_ptr],
        )

    def _gc_one_slot_frame_map(self) -> ir.GlobalVariable:
        name = ".pcc.gc.frame.map.1"
        existing = self.module.globals.get(name)
        if existing is not None:
            return existing
        gv = ir.GlobalVariable(self.module, _I32, name=name)
        gv.linkage = "internal"
        gv.global_constant = True
        gv.initializer = ir.Constant(_I32, 1)
        return gv

    def _ensure_owned_local_gc_root(
        self,
        name: str,
        alloca: ir.Value,
        ir_ty: ir.Type,
    ) -> None:
        if self.current_func_def is None:
            return
        if name in getattr(self, "_gc_rooted_local_names", set()):
            return
        if name in getattr(self, "_current_global_names", set()):
            return
        if name in getattr(self, "_current_param_names", set()):
            return
        if not isinstance(ir_ty, ir.PointerType):
            return
        if not self._ir_type_matches(ir_ty, _CSTR):
            return
        frame_map = self._gc_one_slot_frame_map()
        self._emit_entry_gc_frame_enter(frame_map, alloca)
        self._gc_rooted_local_names.add(name)
        self._patch_fn_err_exit_gc_root_leave(name, alloca)

    def _emit_gc_frame_leave_for_slot(self, alloca: ir.Value) -> None:
        self.builder.call(
            self.runtime["pcc_gc_frame_leave"],
            [
                self._as_gc_ptr(
                    alloca,
                    name=self._fresh("gc.frame.leave.ptr"),
                )
            ],
        )

    def _patch_fn_err_exit_gc_root_leave(
        self,
        name: str,
        alloca: ir.Value,
    ) -> None:
        fn = self.current_function
        if fn is None:
            return
        err_bb = self._fn_err_exit_blocks.get(fn.name)
        if err_bb is None:
            return
        patched = self._fn_err_exit_gc_root_names.setdefault(fn.name, set())
        if name in patched:
            return
        terminator = None
        for instr in reversed(err_bb._instrs):
            if self._instruction_is_terminator(instr):
                terminator = instr
                break
        if terminator is None:
            return
        save_block = self.builder._block
        self.builder.position_before(terminator)
        self._emit_gc_frame_leave_for_slot(alloca)
        self.builder.position_at_end(save_block)
        patched.add(name)

    def _discard_owned_local_gc_root(self, name: str, alloca: ir.Value) -> None:
        if name not in getattr(self, "_gc_rooted_local_names", set()):
            return
        self._emit_gc_frame_leave_for_slot(alloca)
        self._gc_rooted_local_names.discard(name)

    def _ensure_owned_local_flag(self, name: str) -> ir.Value:
        flag = self._owned_local_flag_slots.get(name)
        if flag is not None:
            return flag
        flag = self._alloca_in_entry(_I1, name=f"{name}.owned")
        self._store_entry_initializer(flag, ir.Constant(_I1, 0))
        self._owned_local_flag_slots[name] = flag
        return flag

    def _emit_release_owned_local_if_flagged(
        self,
        name: str,
        alloca: ir.Value,
    ) -> None:
        flag = self._ensure_owned_local_flag(name)
        is_owned = self.builder.load(
            flag,
            name=self._fresh(f"{name}.owned.load"),
        )
        fn = self.current_function
        release_bb = fn.append_basic_block(name=self._fresh(f"{name}.owned.release"))
        cont_bb = fn.append_basic_block(name=self._fresh(f"{name}.owned.cont"))
        self.builder.cbranch(is_owned, release_bb, cont_bb)
        self.builder.position_at_end(release_bb)
        old_value = self.builder.load(
            alloca,
            name=self._fresh(f"{name}.overwrite"),
        )
        self._gc_release(old_value)
        self.builder.store(ir.Constant(_I1, 0), flag)
        self.builder.branch(cont_bb)
        self.builder.position_at_end(cont_bb)

    def _unpack_target_value_is_owned(self, value_ty: Type) -> bool:
        # Dynamic unpack sites can carry either a real PyObject* or a native
        # pointer-shaped value (for example dataclass / AST string fields in
        # the bootstrap compiler). Without stronger element ownership metadata,
        # treating DynType unpack results as owned PyObject*s is unsafe.
        return not isinstance(value_ty, DynType)

    def _mark_owned_local_for_unpack_target(
        self,
        target: Name,
        value_ty: Type,
        value_is_owned: Optional[bool] = None,
    ) -> None:
        """Mark a tuple-unpack ``Name`` target as owning the
        assignment source value.

        Unpacked elements come from APIs returning new refs (tuple/list
        getitem), so local-name bindings should be released on scope
        exit just like regular assignments.
        """
        if value_is_owned is None:
            value_is_owned = self._unpack_target_value_is_owned(value_ty)
        if not value_is_owned:
            return
        if self.current_func_def is None:
            return
        if target.ident in getattr(self, "_current_global_names", set()):
            return
        if target.ident in getattr(self, "_current_param_names", set()):
            return
        slot = self.env.get(target.ident)
        if slot is None:
            return
        _alloca, ir_ty, _decl_ty = slot
        if not isinstance(ir_ty, ir.PointerType):
            return
        if not self._ir_type_matches(ir_ty, _CSTR):
            return
        self._owned_local_names.add(target.ident)
        self._ensure_owned_local_gc_root(target.ident, _alloca, ir_ty)

    def _release_existing_owned_local(self, name: str) -> None:
        if name not in getattr(self, "_owned_local_names", set()):
            return
        slot = self.env.get(name)
        if slot is None:
            return
        alloca, ir_ty, _decl_ty = slot
        if not isinstance(ir_ty, ir.PointerType):
            return
        if not self._ir_type_matches(ir_ty, _CSTR):
            return
        if name in self._owned_local_has_value:
            self._emit_release_owned_local_if_flagged(name, alloca)
        self._discard_owned_local_gc_root(name, alloca)
        self._owned_local_names.discard(name)
        self._owned_local_has_value.discard(name)

    def _maybe_emit_discard_assignment(self, target: Name, value_expr: Expr) -> bool:
        if target.ident != "_" or self.current_func_def is None:
            return False
        if not self._raw_scaffold_object_rhs_is_owned(value_expr):
            return False
        if not self._expr_returns_owned_object(value_expr):
            return False
        self._release_existing_owned_local(target.ident)
        value = self._emit_expr(value_expr)
        self._gc_release_if_owned(value, value_expr)
        self.env.pop(target.ident, None)
        self.env_class_hint.pop(target.ident, None)
        self.env_list_elem_class_hint.pop(target.ident, None)
        if hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags.pop(target.ident, None)
        self._weak_dict_env_flags.pop(target.ident, None)
        return True

    def _emit_owned_local_cleanup(self, skip_name: Optional[str] = None) -> None:
        if self.current_func_def is None:
            return
        # In raw-int-scaffold mode, object-local ownership handling is
        # conservative for C-ABI-exporting/runtime modules, but user
        # scripts can still accumulate tracked locals from object-producing
        # assignments.
        for name in sorted(getattr(self, "_owned_local_names", set())):
            if name in getattr(self, "_current_global_names", set()):
                continue
            if name in getattr(self, "_current_param_names", set()):
                continue
            slot = self.env.get(name)
            if slot is None:
                continue
            alloca, ir_ty, _declared_ty = slot
            if not isinstance(ir_ty, ir.PointerType):
                continue
            if not self._ir_type_matches(ir_ty, _CSTR):
                continue
            if name in self._owned_local_has_value and name != skip_name:
                self._emit_release_owned_local_if_flagged(name, alloca)
            if name in getattr(self, "_gc_rooted_local_names", set()):
                self._emit_gc_frame_leave_for_slot(alloca)
        if not getattr(self, "_runtime_threads_enabled", False):
            return
        for name in sorted(getattr(self, "_gc_rooted_local_names", set())):
            if name in getattr(self, "_owned_local_names", set()):
                continue
            if name in getattr(self, "_current_global_names", set()):
                continue
            if name in getattr(self, "_current_param_names", set()):
                continue
            slot = self.env.get(name)
            if slot is None:
                continue
            alloca, ir_ty, _declared_ty = slot
            if not isinstance(ir_ty, ir.PointerType):
                continue
            if not self._ir_type_matches(ir_ty, _CSTR):
                continue
            pinned = self.builder.load(
                alloca,
                name=self._fresh("gc.root.cleanup.unpin"),
            )
            self.builder.call(self.runtime["pcc_gc_unpin"], [pinned])
            self.builder.call(
                self.runtime["pcc_gc_store_root"],
                [
                    self._as_gc_ptr(
                        alloca,
                        name=self._fresh("gc.root.cleanup.ptr"),
                    ),
                    ir.Constant(_CSTR, None),
                ],
            )
            self._emit_gc_frame_leave_for_slot(alloca)

    def _store_entry_initializer(self, ptr: ir.Value, value: ir.Value) -> None:
        # Use self.builder rather than ``tmp_builder = ir.IRBuilder(entry)``:
        # the local-variable IRBuilder alias doesn't reliably register in
        # _ir_builder_env_flags under pcc-py self-host, so method calls on
        # it fall through scaffold dispatch into CPython fallback (see
        # _emit_entry_gc_frame_enter for the same pattern).
        fn = self.current_function
        entry = fn.blocks[0]
        saved_block = self.builder._block
        terminator = None
        for instr in reversed(entry._instrs):
            if self._instruction_is_terminator(instr):
                terminator = instr
                break
        if terminator is not None:
            self.builder.position_before(terminator)
        else:
            self.builder.position_at_end(entry)
        self.builder.store(value, ptr)
        self.builder.position_at_end(saved_block)
