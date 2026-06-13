"""Core LLVM/helper utilities for L1CodeGen."""

from __future__ import annotations

from pcc.llvm_capi.compat import ir

from .runtime_abi import declare_runtime_global

_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_CSTR = _I8.as_pointer()


def _instruction_opname_text(instr) -> str:
    """Return an LLVM instruction opname without reading cached metadata.

    pcc1/pcc2 can cross an object boundary where InstructionRecord.opname is
    not visible even though the textual instruction is intact. The text form is
    the source of truth for these small placement checks.
    """
    stripped = str(instr).strip()
    if stripped.startswith("%"):
        eq = stripped.find(" = ")
        if eq >= 0:
            stripped = stripped[eq + 3 :]
    if stripped.startswith("call "):
        return "call"
    if stripped.startswith("br "):
        return "br"
    if stripped.startswith("ret void"):
        return "ret void"
    if stripped.startswith("ret "):
        return "ret"
    if stripped.startswith("switch "):
        return "switch"
    if stripped.startswith("alloca "):
        return "alloca"
    if stripped.startswith("load "):
        return "load"
    if stripped.startswith("store "):
        return "store"
    if stripped.startswith("unreachable"):
        return "unreachable"
    if stripped.startswith("phi "):
        return "phi"
    idx = stripped.find(" ")
    if idx >= 0:
        return stripped[:idx]
    return stripped


def _instruction_is_terminator_text(instr) -> bool:
    opname = _instruction_opname_text(instr)
    if opname == "br":
        return True
    if opname == "ret":
        return True
    if opname == "ret void":
        return True
    if opname == "unreachable":
        return True
    if opname == "switch":
        return True
    return False


class CoreHelperMixin:
    def _instruction_opname_text(self, instr) -> str:
        return _instruction_opname_text(instr)

    def _instruction_is_terminator(self, instr) -> bool:
        return _instruction_is_terminator_text(instr)

    def _builder_block_is_terminated(self) -> bool:
        block = getattr(self.builder, "_block", None)
        if block is None:
            return True
        instrs = block._instrs
        if len(instrs) == 0:
            return False
        return self._instruction_is_terminator(instrs[len(instrs) - 1])

    def _zero_of(self, ir_ty: ir.Type) -> ir.Value:
        if isinstance(ir_ty, ir.IntType):
            return ir.Constant(ir_ty, 0)
        if isinstance(ir_ty, (ir.FloatType, ir.DoubleType)):
            return ir.Constant(ir_ty, 0.0)
        if isinstance(ir_ty, ir.PointerType):
            # NULL pointer — used as a safe fall-through return for
            # object-typed functions.
            return ir.Constant(ir_ty, None)
        if isinstance(ir_ty, ir.LiteralStructType):
            return ir.Constant(ir_ty, None)
        raise NotImplementedError(f"no zero value for type {ir_ty}")

    def _ir_type_matches(self, actual: ir.Type, expected: ir.Type) -> bool:
        if actual is None or expected is None:
            return False
        if actual is expected:
            return True
        try:
            actual_width = actual.width
            expected_width = expected.width
            return actual_width == expected_width
        except AttributeError:
            pass
        try:
            actual_pointee = actual.pointee
            expected_pointee = expected.pointee
            return self._ir_type_matches(actual_pointee, expected_pointee)
        except AttributeError:
            pass
        return False

    def _emit_thread_safepoint(self) -> None:
        """Emit the cheap thread safepoint poll used by loop/function gates."""
        if not getattr(self, "_thread_safepoints_enabled", False):
            return
        if self._builder_block_is_terminated():
            return
        flag_gv = declare_runtime_global(self.module, "pcc_thread_stop_requested")
        flag = self.builder.load(
            flag_gv,
            name=self._fresh("thread.safepoint.flag"),
        )
        need_slow = self.builder.icmp_unsigned(
            "!=",
            flag,
            ir.Constant(_I32, 0),
            name=self._fresh("thread.safepoint.need"),
        )
        fn = self.current_function
        slow_block = fn.append_basic_block(
            name=self._fresh("thread.safepoint.slow"),
        )
        cont_block = fn.append_basic_block(
            name=self._fresh("thread.safepoint.cont"),
        )
        self.builder.cbranch(need_slow, slow_block, cont_block)
        self.builder.position_at_end(slow_block)
        self.builder.call(self.runtime["pcc_thread_safepoint"], [])
        if not self._builder_block_is_terminated():
            self.builder.branch(cont_block)
        self.builder.position_at_end(cont_block)

    def _alloca_in_entry(
        self,
        ir_ty: ir.Type,
        name: str,
        *,
        init_null: bool = False,
    ) -> ir.AllocaInstr:
        """Emit an alloca into the function's entry block.

        Uses ``self.builder`` with position save/restore rather than a
        local ``tmp_builder = ir.IRBuilder(entry)`` alias: under pcc-py
        self-host, local IRBuilder aliases don't reliably register in
        ``_ir_builder_env_flags``, so ``tmp_builder.METHOD`` falls
        through scaffold dispatch into a same-named method on a
        different class (``LowBuilder.store`` etc.). Same fix as in
        ``_emit_entry_gc_frame_enter`` and ``_store_entry_initializer``.
        """
        fn = self.current_function
        entry = getattr(self, "_current_entry_block", None)
        if entry is None:
            entry = fn.blocks[0]
        saved_block = getattr(self.builder, "_block", None)
        # Position at the end of entry, but before the first non-alloca
        # instruction if entry already has body content.
        insert_before = None
        cached_fn = getattr(self, "_entry_alloca_insert_before_function", None)
        cached_instr = getattr(self, "_entry_alloca_insert_before_instr", None)
        if cached_fn is fn and cached_instr is not None:
            insert_before = cached_instr
        elif (
            len(entry._instrs) > 0
            and self._instruction_opname_text(entry._instrs[len(entry._instrs) - 1])
            != "alloca"
        ):
            for instr in entry._instrs:
                if self._instruction_opname_text(instr) != "alloca":
                    insert_before = instr
                    break
            self._entry_alloca_insert_before_function = fn
            self._entry_alloca_insert_before_instr = insert_before
        else:
            self._entry_alloca_insert_before_function = fn
            self._entry_alloca_insert_before_instr = None
        if insert_before is not None:
            self.builder.position_before(insert_before)
        else:
            self.builder.position_at_end(entry)
        alloca = self.builder.alloca(ir_ty, name=name)
        if init_null and isinstance(ir_ty, ir.PointerType):
            if self._ir_type_matches(ir_ty, _CSTR):
                self.builder.store(
                    ir.Constant(ir_ty, None),
                    alloca,
                )
        # Restore the main builder's insertion point.
        if saved_block is not None:
            self.builder.position_at_end(saved_block)
        return alloca
