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
        flag = self.builder.load_atomic(
            flag_gv,
            "acquire",
            4,
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
        # O(1) insertion cursor: a per-function numeric index, bumped per
        # insert. The earlier shape cached the first non-alloca INSTRUCTION
        # and repositioned with ``position_before(instr)``, which rescans
        # the entry block's instruction list on every call (one identity
        # compare per record). A function with N rooted calls grows an
        # N-alloca entry prefix, so codegen went O(N^2) per function —
        # invisible at host list speed, but the pcc1-executed stage2
        # regressed 282s -> 5245s (and the pcc1 self-host getattr-default
        # bug made the instr cache never hit there; see marshal
        # ``_stash_overflow_slot``'s NOTE, same discipline used here:
        # direct attribute reads, attrs initialised in layer1_init).
        # Ordering among entry allocas is irrelevant — each executes once.
        if self._entry_alloca_insert_before_function is not fn:
            insert_index = 0
            n_entry = len(entry._instrs)
            while insert_index < n_entry and (
                self._instruction_opname_text(entry._instrs[insert_index])
                == "alloca"
            ):
                insert_index = insert_index + 1
            self._entry_alloca_insert_before_function = fn
            self._entry_alloca_insert_index = insert_index
        insert_index = self._entry_alloca_insert_index
        saved_block = self.builder._block
        saved_pos = self.builder._pos
        end_marker = self.builder._END
        self.builder._block = entry
        self.builder._pos = insert_index
        alloca = self.builder.alloca(ir_ty, name=name)
        emitted = 1
        if init_null and isinstance(ir_ty, ir.PointerType):
            if self._ir_type_matches(ir_ty, _CSTR):
                self.builder.store(
                    ir.Constant(ir_ty, None),
                    alloca,
                )
                emitted = 2
        self._entry_alloca_insert_index = insert_index + emitted
        # Restore the caller's insertion point; if the caller was emitting
        # into the entry block itself at/after the insertion cursor, the
        # inserted records shifted its position.
        if saved_block is not None:
            self.builder._block = saved_block
            if (
                saved_block is entry
                and saved_pos != end_marker
                and saved_pos >= insert_index
            ):
                self.builder._pos = saved_pos + emitted
            else:
                self.builder._pos = saved_pos
        return alloca
