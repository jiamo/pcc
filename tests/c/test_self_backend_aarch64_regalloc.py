from __future__ import annotations

from pcc.backend.self_backend_aarch64_darwin_abi import (
    aggregate_returned_indirect,
)
from pcc.backend.self_backend_aarch64_darwin_regalloc import (
    allocate_aarch64_block_registers,
)
from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.self_backend_ir import (
    ParsedInstr,
    TypeDesc,
    text_key_mapping_get,
)
from pcc.backend.self_backend_prepare import prepare_module_for_target


_TRIPLE = 'target triple = "arm64-apple-darwin25.5.0"\n'


_HOT_BLOCK_IR = _TRIPLE + """
define i64 @hot(i64 %a, i64 %b) {
entry:
  %v0 = add i64 %a, %b
  %v1 = mul i64 %v0, 3
  %v2 = xor i64 %v1, %a
  %v3 = add i64 %v2, %v0
  ret i64 %v3
}
"""


def _prepared_function(ir_text: str):
    prepared = prepare_module_for_target(
        ir_text,
        aggregate_returned_indirect=aggregate_returned_indirect,
    )
    assert len(prepared.functions) == 1
    return prepared.functions[0]


def test_aarch64_block_local_linear_scan_reuses_expired_registers():
    func = _prepared_function(_HOT_BLOCK_IR)
    allocate_aarch64_block_registers(func)

    assert text_key_mapping_get(func.value_registers, "v0") == 1
    assert text_key_mapping_get(func.value_registers, "v1") == 2
    # v1 dies as v2 is defined, then v0/v2 both die as v3 is defined.
    assert text_key_mapping_get(func.value_registers, "v2") == 2
    assert text_key_mapping_get(func.value_registers, "v3") == 1
    # Slots remain present as the mandatory fail-closed representation.
    assert all(name in func.value_slots for name in ("v0", "v1", "v2", "v3"))


def test_aarch64_hot_block_avoids_intermediate_slot_traffic():
    asm = emit_self_asm(_HOT_BLOCK_IR)
    assert "_hot:" in asm

    # The spill-only lowering has fourteen x29-relative scalar transfers for
    # this unoptimized IR (two argument stores, eight operand loads, four
    # result stores).  Block-local allocation leaves only argument traffic;
    # later peepholes may legitimately reduce that count further.
    stack_transfers = [
        line
        for line in asm.splitlines()
        if "[x29, #-" in line
        and line.strip().split(None, 1)[0] in {"ldr", "ldur", "str", "stur"}
    ]
    assert len(stack_transfers) <= 5, stack_transfers


def test_aarch64_call_barrier_spills_touching_values_but_allows_post_call_locals():
    ir_text = _TRIPLE + """
declare i64 @opaque(i64)

define i64 @with_call(i64 %arg) {
entry:
  %pre = add i64 %arg, 1
  %before = xor i64 %pre, 2
  %called = call i64 @opaque(i64 %before)
  %after = add i64 %called, %before
  ret i64 %after
}
"""
    func = _prepared_function(ir_text)
    allocate_aarch64_block_registers(func)

    # pre dies strictly before the call.  before is both a call operand and
    # live after the call; called is a call result.  The latter two stay in
    # their mandatory slots, while pre/after are on one side of the clobber.
    assert text_key_mapping_get(func.value_registers, "pre") == 1
    assert text_key_mapping_get(func.value_registers, "before") is None
    assert text_key_mapping_get(func.value_registers, "called") is None
    assert text_key_mapping_get(func.value_registers, "after") == 1
    asm = emit_self_asm(ir_text)
    assert "bl _opaque" in asm
    assert "[x29, #-" in asm


def test_aarch64_linear_scan_spills_farthest_interval_under_pressure():
    ir_text = _TRIPLE + """
define i64 @pressure(i64 %arg) {
entry:
  %v0 = add i64 %arg, 0
  %v1 = add i64 %arg, 1
  %v2 = add i64 %arg, 2
  %v3 = add i64 %arg, 3
  %v4 = add i64 %arg, 4
  %v5 = add i64 %arg, 5
  %v6 = add i64 %arg, 6
  %v7 = add i64 %arg, 7
  %v8 = add i64 %arg, 8
  %r0 = add i64 %v0, %v8
  %r1 = add i64 %v1, %r0
  %r2 = add i64 %v2, %r1
  %r3 = add i64 %v3, %r2
  %r4 = add i64 %v4, %r3
  %r5 = add i64 %v5, %r4
  %r6 = add i64 %v6, %r5
  %r7 = add i64 %v7, %r6
  ret i64 %r7
}
"""
    func = _prepared_function(ir_text)
    allocate_aarch64_block_registers(func)

    # Nine simultaneously-live values exceed the eight-register pool.  The
    # farthest-ending v7 interval loses its optional mapping and therefore
    # uses the stack slot that stack preparation already assigned.
    assert text_key_mapping_get(func.value_registers, "v7") is None
    assert text_key_mapping_get(func.value_registers, "v8") is not None
    assert "v7" in func.value_slots


def test_aarch64_phi_inputs_and_results_keep_stack_slots():
    ir_text = _TRIPLE + """
define i64 @with_phi(i64 %arg) {
entry:
  %seed = add i64 %arg, 1
  br label %merge
merge:
  %joined = phi i64 [ %seed, %entry ]
  %local = add i64 %joined, 2
  %result = xor i64 %local, 3
  ret i64 %result
}
"""
    func = _prepared_function(ir_text)
    allocate_aarch64_block_registers(func)

    assert text_key_mapping_get(func.value_registers, "seed") is None
    assert text_key_mapping_get(func.value_registers, "joined") is None
    assert text_key_mapping_get(func.value_registers, "local") == 1
    assert text_key_mapping_get(func.value_registers, "result") == 1
    assert "seed" in func.value_slots
    assert "joined" in func.value_slots


def test_aarch64_cross_block_values_spill_while_new_block_locals_allocate():
    ir_text = _TRIPLE + """
define i64 @cross_block(i64 %arg) {
entry:
  %seed = add i64 %arg, 1
  br label %next
next:
  %result = add i64 %seed, 2
  ret i64 %result
}
"""
    func = _prepared_function(ir_text)
    allocate_aarch64_block_registers(func)

    assert text_key_mapping_get(func.value_registers, "seed") is None
    assert text_key_mapping_get(func.value_registers, "result") == 1
    assert "seed" in func.value_slots


def test_aarch64_false_hash_cross_block_use_cannot_escape_to_register():
    class DifferentHashText(str):
        def __hash__(self):
            return super().__hash__() ^ 1

    ir_text = _TRIPLE + """
define i64 @false_hash_boundary(i64 %arg) {
entry:
  %seed = add i64 %arg, 1
  %local = xor i64 %seed, 2
  %sink = add i64 %local, 3
  br label %next
next:
  ret i64 %seed
}
"""
    func = _prepared_function(ir_text)
    # Native bootstrap has produced equal SSA-name text objects with unequal
    # hashes.  Model that boundary at the cross-block use: a raw dict/set
    # liveness implementation would see only the entry-block spelling and
    # could leave seed in a caller-saved register past its proven lifetime.
    func.blocks[1].terminator = ParsedInstr(
        "ret", (TypeDesc("int", 64), DifferentHashText("seed"))
    )
    allocate_aarch64_block_registers(func)

    assert text_key_mapping_get(func.value_registers, "seed") is None
    assert text_key_mapping_get(func.value_registers, "local") == 1
    assert text_key_mapping_get(func.value_slots, "seed") is not None


def test_aarch64_float_and_vector_ssa_keep_mandatory_spill_slots():
    float_ir = _TRIPLE + """
define double @float_lane(double %a, double %b) {
entry:
  %sum = fadd double %a, %b
  ret double %sum
}
"""
    float_func = _prepared_function(float_ir)
    allocate_aarch64_block_registers(float_func)

    vector_ir = _TRIPLE + """
define <2 x i64> @vector_lane(<2 x i64> %a, <2 x i64> %b) {
entry:
  %sum = add <2 x i64> %a, %b
  ret <2 x i64> %sum
}
"""
    vector_func = _prepared_function(vector_ir)
    allocate_aarch64_block_registers(vector_func)

    assert float_func.value_registers == {}
    assert vector_func.value_registers == {}
    assert "sum" in float_func.value_slots
    assert "sum" in vector_func.value_slots


def test_aarch64_integer_intervals_allocate_inside_mixed_float_block():
    ir_text = _TRIPLE + """
define double @mixed_lane(i64 %arg, double %a, double %b) {
entry:
  %count = add i64 %arg, 1
  %sum = fadd double %a, %b
  %next = xor i64 %count, 2
  %converted = sitofp i64 %next to double
  %result = fadd double %sum, %converted
  ret double %result
}
"""
    func = _prepared_function(ir_text)
    allocate_aarch64_block_registers(func)

    # Floating definitions retain slots without disabling unrelated scalar
    # intervals in the same block (the pinned value-array hot loop is mixed).
    assert text_key_mapping_get(func.value_registers, "count") == 1
    assert text_key_mapping_get(func.value_registers, "next") == 1
    assert text_key_mapping_get(func.value_registers, "sum") is None
    assert text_key_mapping_get(func.value_registers, "converted") is None
    assert text_key_mapping_get(func.value_registers, "result") is None
    assert all(
        text_key_mapping_get(func.value_slots, name) is not None
        for name in ("count", "next", "sum", "converted", "result")
    )


def test_aarch64_pointer_ssa_uses_the_same_block_local_register_class():
    ir_text = _TRIPLE + """
define i64 @pointer_lane(i64 %arg) {
entry:
  %slot = alloca i64
  %ptr = getelementptr i64, ptr %slot, i64 0
  store i64 %arg, ptr %ptr
  %loaded = load i64, ptr %ptr
  ret i64 %loaded
}
"""
    func = _prepared_function(ir_text)
    allocate_aarch64_block_registers(func)

    assert text_key_mapping_get(func.value_registers, "ptr") == 1
    assert text_key_mapping_get(func.value_registers, "loaded") == 1
