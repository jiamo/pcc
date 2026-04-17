import subprocess

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_analysis import value_has_uses
from pcc.backend.self_backend_aarch64_darwin_abi import (
    aggregate_reg_chunks,
    assign_abi_arg_regs,
    stack_arg_offsets,
)
from pcc.backend.self_backend_aarch64_darwin_calls import (
    emit_call_instruction,
    emit_fixed_stack_arg_load,
    emit_va_arg,
    emit_vararg_stack_arg,
    emit_vararg_start,
)
from pcc.backend.self_backend_aarch64_darwin_compute import (
    emit_compute_instruction,
)
from pcc.backend.self_backend_aarch64_darwin_flow import (
    emit_bit_count_intrinsic_call,
    emit_phi_assignments,
)
from pcc.backend.self_backend_aarch64_darwin_addr import (
    emit_gep_offset,
    emit_indexed_pointer_add,
    materialize_global_address,
    materialize_index_to_x10,
)
from pcc.backend.self_backend_aarch64_darwin_data import emit_global_initializer
from pcc.backend.self_backend_aarch64_darwin_memory import (
    emit_memory_instruction,
)
from pcc.backend.self_backend_aarch64_darwin_mem import (
    aggregate_copy_chunks,
    chunk_load_op,
    chunk_store_op,
    mem_load_op,
    mem_store_op,
    stack_load_op,
    stack_store_op,
)
from pcc.backend.self_backend_aarch64_darwin_materialize import (
    copy_large_aggregate_value_to_slot,
    materialize_indirect_aggregate_arg_pointer,
    materialize_pointer,
    materialize_value,
    store_large_aggregate_literal_to_address,
)
from pcc.backend.self_backend_aarch64_darwin_ops import (
    aarch64_cc,
    emit_binop,
    emit_cast,
    emit_fbinop,
    emit_fcmp_result,
    sign_extend_int_reg,
)
from pcc.backend.self_backend_aarch64_darwin_prologue import (
    emit_function_prologue,
)
from pcc.backend.self_backend_aarch64_darwin_regs import (
    align_pow2,
    emit_add_offset,
    emit_const_to_reg,
    emit_stack_adjust,
    pick_scratch_gpr,
)
from pcc.backend.self_backend_aarch64_darwin_returns import emit_return_terminator
from pcc.backend.self_backend_aarch64_darwin_symbols import (
    asm_symbol,
    block_edge_label,
    block_label,
    sanitize_label,
)
from pcc.backend.self_backend_aarch64_darwin_terminators import (
    emit_branch_terminator,
    emit_cond_branch_terminator,
    emit_epilogue,
    emit_switch_terminator,
    emit_unreachable_terminator,
)
from pcc.backend.self_backend_aarch64_darwin_slots import (
    copy_address_to_address,
    emit_slot_base_address,
    load_slot_to_value_regs,
    load_slot_to_reg,
    load_value_from_address,
    store_reg_to_slot,
    store_value_regs_to_slot,
    store_value_to_address,
    zero_address,
)
from pcc.backend.self_backend import emit_aarch64_darwin_asm
from pcc.backend.self_backend_dispatch import (
    emit_self_asm,
    self_backend_target_identity,
)
from pcc.backend.self_backend_x86_64_linux import emit_x86_64_linux_asm
from pcc.backend.self_backend_emit import emit_function_blocks
from pcc.backend.self_backend_instruction_dispatch import emit_instruction_dispatch
from pcc.backend.self_backend_ir import ArgInfo, SlotInfo, TypeDesc
from pcc.backend.self_backend_module_symbols import prepare_module_symbols
from pcc.backend.self_backend_parse import (
    aggregate_literal_to_bytes,
    decode_value_token,
    parse_self_backend_module,
)
from pcc.backend.self_backend_prepare import (
    prepare_module_for_target,
    prepare_parsed_function,
)
from pcc.backend.self_backend_stackprep import assign_stack_slots
from pcc.backend.self_backend_terminator_dispatch import emit_terminator_dispatch
from pcc.backend.self_backend_targets import known_self_backend_target_identities
from pcc.backend.self_backend_target_match import (
    is_aarch64_darwin_triple,
    is_x86_64_linux_triple,
)
from pcc.backend.self_backend_targets import (
    is_supported_self_backend_target_triple,
)
from pcc.evaluater.c_evaluator import CEvaluator
from pcc.project import TranslationUnit


def _compile_units(source: str, tmp_path):
    unit = TranslationUnit(
        name="main.c",
        path=str(tmp_path / "main.c"),
        source=source,
    )
    ev = CEvaluator(backend="self", allow_unimplemented_backend=True)
    compiled_units = ev.compile_translation_units(
        [unit],
        use_system_cpp=False,
        frontend_opt_level=0,
    )
    return ev, compiled_units


def _compile_multi_units(sources: list[tuple[str, str]], tmp_path):
    units = [
        TranslationUnit(
            name=name,
            path=str(tmp_path / name),
            source=source,
        )
        for name, source in sources
    ]
    ev = CEvaluator(backend="self", allow_unimplemented_backend=True)
    compiled_units = ev.compile_translation_units(
        units,
        use_system_cpp=False,
        frontend_opt_level=0,
    )
    return ev, compiled_units


def _assemble_and_run(asm_path, tmp_path):
    exe_path = tmp_path / "a.out"
    subprocess.run(
        ["cc", str(asm_path), "-o", str(exe_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run([str(exe_path)], capture_output=True, text=True)


def _link_object_and_run(obj_path, tmp_path):
    exe_path = tmp_path / "obj.out"
    subprocess.run(
        ["cc", str(obj_path), "-o", str(exe_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run([str(exe_path)], capture_output=True, text=True)


def _compile_native_helper(source: str, obj_path):
    src_path = obj_path.with_suffix(".c")
    src_path.write_text(source)
    subprocess.run(
        [
            "cc",
            "-target",
            "arm64-apple-macos",
            "-c",
            str(src_path),
            "-o",
            str(obj_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_self_backend_emits_aarch64_darwin_asm_for_const_return(tmp_path):
    ev, compiled_units = _compile_units(
        "int main(void) { return 42; }\n",
        tmp_path,
    )
    asm_path = tmp_path / "main.s"

    ev.emit_compiled_units(compiled_units, emit_asm=str(asm_path), optimize=0)

    asm_text = asm_path.read_text()
    assert "_main:" in asm_text
    assert "movz w0, #42" in asm_text
    assert "ret" in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 42


def test_self_backend_dispatch_resolves_aarch64_darwin_target():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main() {
entry:
  ret i32 42
}
""".strip()

    assert (
        self_backend_target_identity("arm64-apple-darwin23.6.0")
        == "self-aarch64-darwin-v0"
    )
    assert emit_self_asm(ir_text) == emit_aarch64_darwin_asm(ir_text)


def test_self_backend_dispatch_resolves_x86_64_linux_target():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  ret i32 42
}
""".strip()

    assert (
        self_backend_target_identity("x86_64-unknown-linux-gnu")
        == "self-x86_64-linux-v0"
    )
    asm_text = emit_self_asm(ir_text)
    assert asm_text == emit_x86_64_linux_asm(ir_text)
    assert ".intel_syntax noprefix" in asm_text
    assert "mov eax, 42" in asm_text
    assert "ret" in asm_text


def test_self_backend_x86_64_linux_emits_direct_call_and_binop_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @add(i32 %a, i32 %b) {
entry:
  %sum = add i32 %a, %b
  ret i32 %sum
}

define i32 @main() {
entry:
  %r = call i32 (i32, i32) @add(i32 40, i32 2)
  ret i32 %r
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert ".globl add" in asm_text
    assert ".globl main" in asm_text
    assert "add r10d, r11d" in asm_text
    assert "mov edi, 40" in asm_text
    assert "mov esi, 2" in asm_text
    assert "call add" in asm_text


def test_self_backend_x86_64_linux_emits_global_double_compare_and_zext_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

@x = global double 1.000000e+02

define i32 @main() {
bb0:
  %.1 = load double, ptr @x
  %cmptmp = fcmp olt double %.1, 1.000000e+00
  %booltmp = zext i1 %cmptmp to i32
  ret i32 %booltmp
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert ".data" in asm_text
    assert ".double 1.000000e+02" in asm_text
    assert "movsd xmm10, QWORD PTR x[rip]" in asm_text
    assert "ucomisd xmm10, xmm11" in asm_text
    assert "setb al" in asm_text
    assert "movzx r10d, BYTE PTR [rbp -" in asm_text
    assert '.section .note.GNU-stack,"",@progbits' in asm_text


def test_self_backend_x86_64_linux_emits_pointer_global_initializer():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

@x = global i32 5
@p = global ptr @x

define i32 @main() {
bb0:
  %v = load ptr, ptr @p
  ret i32 0
}
    """.strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert ".quad x" in asm_text


def test_self_backend_x86_64_linux_emits_struct_and_array_globals():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

%pair = type { i32, i32 }

@v = global %pair zeroinitializer
@strlit = internal constant [6 x i8] [i8 104, i8 101, i8 108, i8 108, i8 111, i8 0]

define i32 @main() {
bb0:
  ret i32 0
}
    """.strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert ".zero 8" in asm_text
    assert ".byte 104" in asm_text
    assert ".byte 0" in asm_text


def test_self_backend_x86_64_linux_emits_nested_array_globals():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

@arr = global [2 x [3 x [5 x i32]]] [[3 x [5 x i32]] [[5 x i32] [i32 0, i32 0, i32 3, i32 5, i32 0], [5 x i32] [i32 1, i32 0, i32 0, i32 6, i32 7], [5 x i32] zeroinitializer], [3 x [5 x i32]] [[5 x i32] [i32 1, i32 2, i32 0, i32 0, i32 0], [5 x i32] [i32 0, i32 0, i32 0, i32 0, i32 7], [5 x i32] zeroinitializer]]

define i32 @main() {
bb0:
  ret i32 0
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert ".globl arr" in asm_text
    assert ".long 6" in asm_text
    assert ".long 7" in asm_text
    assert ".zero 20" in asm_text


def test_self_backend_x86_64_linux_emits_phi_merge_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
bb0:
  %cond = icmp ne i32 0, 1
  br i1 %cond, label %t, label %f

t:
  br label %merge

f:
  br label %merge

merge:
  %phi = phi i32 [ 7, %t ], [ 3, %f ]
  ret i32 %phi
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert ".Lmain_bb0_to_t:" in asm_text
    assert "mov r10d, 7" in asm_text
    assert "mov r10d, 3" in asm_text
    assert "mov DWORD PTR [rbp - 8], r10d" in asm_text
    assert "jmp .Lmain_merge" in asm_text


def test_self_backend_x86_64_linux_supports_pointer_valued_ssa_memory_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %x = alloca i32
  %p = alloca ptr
  store i32 0, ptr %x
  store ptr %x, ptr %p
  %pp = load ptr, ptr %p
  store i32 1, ptr %pp
  %v = load i32, ptr %x
  ret i32 %v
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "mov r11, QWORD PTR [rbp -" in asm_text
    assert "mov DWORD PTR [r11], r10d" in asm_text
    assert "mov r10d, DWORD PTR [rbp -" in asm_text


def test_self_backend_x86_64_linux_emits_div_rem_and_bitwise_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %q = sdiv i32 10, 2
  %r = srem i32 5, 3
  %o = or i32 %q, %r
  ret i32 %o
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "cdq" in asm_text
    assert "idiv r10d" in asm_text
    assert "or r10d, r11d" in asm_text


def test_self_backend_x86_64_linux_emits_sext_and_gep_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %arr = alloca [2 x i32]
  %idx64 = sext i32 1 to i64
  %ptr = getelementptr [2 x i32], ptr %arr, i64 0, i64 %idx64
  store i32 7, ptr %ptr
  %v = load i32, ptr %ptr
  ret i32 %v
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "movsxd r10, r10d" in asm_text
    assert "lea r11, [rbp -" in asm_text
    assert "lea r11, [r11 + r10*4]" in asm_text
    assert "mov DWORD PTR [r11], r10d" in asm_text


def test_self_backend_x86_64_linux_emits_ptrtoint_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i64 @main() {
entry:
  %x = alloca i32
  %pi = ptrtoint ptr %x to i64
  ret i64 %pi
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "lea r10, [rbp -" in asm_text
    assert "mov QWORD PTR [rbp -" in asm_text


def test_self_backend_x86_64_linux_emits_ptrtoint_i32_without_invalid_move():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %x = alloca i32
  %pi = ptrtoint ptr %x to i32
  ret i32 %pi
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "mov r10d, r10" not in asm_text
    assert "mov DWORD PTR [rbp -" in asm_text


def test_self_backend_x86_64_linux_emits_zext_i32_to_i64_without_invalid_move():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i64 @main() {
entry:
  %x = zext i32 1 to i64
  ret i64 %x
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "mov r10, r10d" not in asm_text
    assert "mov QWORD PTR [rbp -" in asm_text


def test_self_backend_x86_64_linux_emits_ptr_bitcast_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define ptr @main() {
entry:
  %x = alloca i32
  %p = bitcast ptr %x to ptr
  ret ptr %p
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "lea r10, [rbp -" in asm_text
    assert "mov QWORD PTR [rbp -" in asm_text


def test_self_backend_x86_64_linux_emits_trunc_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %x = trunc i32 257 to i8
  %y = sext i8 %x to i32
  ret i32 %y
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "mov r10d, 257" in asm_text
    assert "mov BYTE PTR [rbp -" in asm_text
    assert "movsx r10, r10b" in asm_text


def test_self_backend_x86_64_linux_emits_sitofp_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %f = sitofp i32 1 to float
  %cmp = fcmp oeq float %f, 0.000000e+00
  %r = zext i1 %cmp to i32
  ret i32 %r
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "cvtsi2ss xmm10, r10d" in asm_text
    assert "ucomiss xmm10, xmm11" in asm_text


def test_self_backend_x86_64_linux_emits_fptrunc_and_fpext_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %f = fptrunc double 1.000000e+00 to float
  %d = fpext float %f to double
  %cmp = fcmp oeq double %d, 1.000000e+00
  %r = zext i1 %cmp to i32
  ret i32 %r
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "cvtsd2ss xmm10, xmm10" in asm_text
    assert "cvtss2sd xmm10, xmm10" in asm_text
    assert "ucomisd xmm10, xmm11" in asm_text


def test_self_backend_x86_64_linux_emits_fbinop_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %sum = fadd double 1.000000e+00, 2.000000e+00
  %cmp = fcmp oeq double %sum, 3.000000e+00
  %r = zext i1 %cmp to i32
  ret i32 %r
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "addsd xmm10, xmm11" in asm_text
    assert "ucomisd xmm10, xmm11" in asm_text


def test_self_backend_x86_64_linux_emits_fneg_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %neg = fneg double 1.000000e+00
  %cmp = fcmp oeq double %neg, -1.000000e+00
  %r = zext i1 %cmp to i32
  ret i32 %r
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "mov r11, 0x8000000000000000" in asm_text
    assert "movq xmm11, r11" in asm_text
    assert "xorpd xmm10, xmm11" in asm_text


def test_self_backend_x86_64_linux_emits_fp_args_and_vararg_vector_count_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

declare i32 @printf(ptr, ...)

define i32 @use(float %x) {
entry:
  %cmp = fcmp oeq float %x, 0.000000e+00
  %r = zext i1 %cmp to i32
  ret i32 %r
}

@fmt = internal constant [4 x i8] c"%f\\0A\\00"

define i32 @main() {
entry:
  %a = call i32 (float) @use(float 1.000000e+00)
  %b = call i32 (ptr, ...) @printf(ptr @fmt, double 1.000000e+00)
  %sum = add i32 %a, %b
  ret i32 %sum
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "movss DWORD PTR [rbp -" in asm_text
    assert "movd xmm0, r11d" in asm_text
    assert "call use" in asm_text
    assert "movq xmm0, r11" in asm_text
    assert "mov al, 1" in asm_text
    assert "call printf" in asm_text


def test_self_backend_x86_64_linux_emits_fp_call_and_return_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

declare double @sin(double)

define double @id(double %x) {
entry:
  ret double %x
}

define i32 @main() {
entry:
  %a = call double @sin(double 2.000000e+00)
  %b = call double @id(double %a)
  %cmp = fcmp oeq double %b, %a
  %r = zext i1 %cmp to i32
  ret i32 %r
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "call sin" in asm_text
    assert "movsd QWORD PTR [rbp -" in asm_text
    assert "call id" in asm_text
    assert "movsd xmm0, QWORD PTR [rbp -" in asm_text


def test_self_backend_x86_64_linux_emits_unordered_fcmp_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %cmp = fcmp une double 1.000000e+00, 2.000000e+00
  %r = zext i1 %cmp to i32
  ret i32 %r
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "ucomisd xmm10, xmm11" in asm_text
    assert "setne al" in asm_text
    assert "setp bl" in asm_text
    assert "or al, bl" in asm_text


def test_self_backend_x86_64_linux_emits_aggregate_zeroinitializer_store_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
entry:
  %x = alloca [2 x i32]
  store [2 x i32] zeroinitializer, ptr %x
  %p = getelementptr [2 x i32], ptr %x, i64 0, i64 1
  %v = load i32, ptr %p
  ret i32 %v
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "lea r11, [rbp -" in asm_text
    assert "mov QWORD PTR [r11 + 0], rax" in asm_text


def test_self_backend_x86_64_linux_emits_aggregate_store_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

%S = type { i64, i64, i64 }

define i32 @main(%S %s) {
entry:
  %slot = alloca %S
  store %S %s, ptr %slot
  ret i32 0
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "lea r10, [rbp + 16]" in asm_text
    assert "lea r11, [rbp -" in asm_text
    assert "mov QWORD PTR [r11 + 16], rax" in asm_text


def test_self_backend_x86_64_linux_emits_aggregate_load_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

%S = type { i64, i64, i64 }

define i32 @main(%S %s) {
entry:
  %slot = alloca %S
  store %S %s, ptr %slot
  %v = load %S, ptr %slot
  ret i32 0
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "lea r10, [rbp -" in asm_text
    assert "lea r11, [rbp -" in asm_text
    assert "mov QWORD PTR [r11 + 16], rax" in asm_text


def test_self_backend_x86_64_linux_emits_switch_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

define i32 @main() {
bb0:
  switch i32 1, label %default [ i32 0, label %zero i32 1, label %one ]

zero:
  ret i32 0

one:
  ret i32 1

default:
  ret i32 2
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "je .Lmain_bb0_to_zero" in asm_text
    assert "je .Lmain_bb0_to_one" in asm_text
    assert "jmp .Lmain_bb0_to_default" in asm_text


def test_self_backend_x86_64_linux_emits_indirect_call_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

@fp = global ptr @zero

define i32 @zero() {
entry:
  ret i32 7
}

define i32 @main() {
entry:
  %p = load ptr, ptr @fp
  %r = call i32 (...) %p()
  ret i32 %r
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "mov r10, QWORD PTR fp[rip]" in asm_text
    assert "mov r11, QWORD PTR [rbp -" in asm_text
    assert "call r11" in asm_text


def test_self_backend_x86_64_linux_emits_memory_class_aggregate_args_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

%S = type { i64, i64, i64 }

declare void @sink(%S)

define i32 @probe(%S %s, ptr %p, i32 %x) {
entry:
  call void (%S) @sink(%S %s)
  %nonnull = icmp ne ptr %p, null
  %flag = zext i1 %nonnull to i32
  %sum = add i32 %flag, %x
  ret i32 %sum
}

define i32 @main(%S %s) {
entry:
  %r = call i32 (%S, ptr, i32) @probe(%S %s, ptr null, i32 7)
  ret i32 %r
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "lea r10, [rbp + 16]" in asm_text
    assert "mov QWORD PTR [r11 + 0], rax" in asm_text
    assert "mov QWORD PTR [r11 + 16], rax" in asm_text
    assert "mov QWORD PTR [rbp -" in asm_text and "], rdi" in asm_text
    assert "mov DWORD PTR [rbp -" in asm_text and "], esi" in asm_text
    assert "sub rsp, 32" in asm_text
    assert "lea r11, [rsp + 0]" in asm_text
    assert "xor rdi, rdi" in asm_text
    assert "mov esi, 7" in asm_text
    assert "call sink" in asm_text
    assert "call probe" in asm_text
    assert "add rsp, 32" in asm_text


def test_self_backend_x86_64_linux_lowers_constant_memcpy_intrinsic_subset():
    ir_text = """
target triple = "x86_64-unknown-linux-gnu"

declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)

define i32 @main() {
entry:
  %dst = alloca [4 x i64]
  %src = alloca [4 x i64]
  call void @llvm.memcpy.p0.p0.i64(ptr %dst, ptr %src, i64 32, i1 0)
  ret i32 0
}
""".strip()

    asm_text = emit_x86_64_linux_asm(ir_text)
    assert "call llvm.memcpy.p0.p0.i64" not in asm_text
    assert "lea r10, [rbp -" in asm_text
    assert "lea r11, [rbp -" in asm_text
    assert "mov QWORD PTR [r11 + 24], rax" in asm_text


def test_self_backend_target_registry_lists_current_target_identities():
    assert known_self_backend_target_identities() == (
        "self-aarch64-darwin-v0",
        "self-x86_64-linux-v0",
    )


def test_self_backend_target_matchers_cover_current_aliases():
    assert is_aarch64_darwin_triple("arm64-apple-darwin23.6.0") is True
    assert is_aarch64_darwin_triple("aarch64-apple-darwin") is True
    assert is_x86_64_linux_triple("x86_64-unknown-linux-gnu") is True
    assert is_x86_64_linux_triple("amd64-pc-linux-gnu") is True
    assert is_supported_self_backend_target_triple("arm64-apple-darwin23.6.0") is True
    assert is_supported_self_backend_target_triple("x86_64-unknown-linux-gnu") is True
    assert is_supported_self_backend_target_triple("wasm32-unknown-unknown") is False


def test_self_backend_shared_parser_decodes_module_shape():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@msg = internal constant [3 x i8] c"hi\\00"

define i32 @main() {
entry:
  ret i32 42
}
""".strip()

    module = parse_self_backend_module(ir_text)

    assert module.triple == "arm64-apple-darwin23.6.0"
    assert [global_.name for global_ in module.globals_] == ["msg"]
    assert [func.name for func in module.functions] == ["main"]
    assert module.functions[0].blocks[0].terminator is not None


def test_self_backend_shared_parser_accepts_decimal_float_values():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@x = global double 1.000000e+02

define i32 @main() {
bb0:
  %.1 = load double, ptr @x
  %cmptmp = fcmp olt double %.1, 1.000000e+00
  %booltmp = zext i1 %cmptmp to i32
  ret i32 %booltmp
}
""".strip()

    module = parse_self_backend_module(ir_text)
    assert module.globals_[0].initializer == "1.000000e+02"
    assert module.functions[0].blocks[0].instructions[1].data[-1] == "1.000000e+00"


def test_self_backend_shared_parser_accepts_anonymous_struct_return_header():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define internal fastcc { i64, ptr } @mk_pair(i64 %value, ptr %data) {
entry:
  %pair0 = insertvalue { i64, ptr } poison, i64 %value, 0
  %pair1 = insertvalue { i64, ptr } %pair0, ptr %data, 1
  ret { i64, ptr } %pair1
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]

    assert func.name == "mk_pair"
    assert func.ret_type == TypeDesc(
        "struct",
        fields=(TypeDesc("int", 64), TypeDesc("ptr", pointee=TypeDesc("void"))),
    )
    assert [arg.name for arg in func.args] == ["value", "data"]


def test_self_backend_shared_emit_skeleton_drives_block_labels_and_terminators():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i1 %cond) {
entry:
  br i1 %cond, label %t, label %f

t:
  %a = add i32 1, 2
  ret i32 %a

f:
  ret i32 0
}
""".strip()

    module = parse_self_backend_module(ir_text)
    func = module.functions[0]

    assert emit_function_blocks(
        func,
        block_label=lambda fn, bn: f"L_{fn}_{bn}",
        emit_instruction=lambda _func, _block, instr: [f"  ; {instr.kind}"],
        emit_terminator=lambda _func, _block, term: [f"  ; {term.kind}"],
    ) == [
        "L_main_entry:",
        "  ; br_cond",
        "",
        "L_main_t:",
        "  ; binop",
        "  ; ret",
        "",
        "L_main_f:",
        "  ; ret",
    ]


def test_self_backend_shared_terminator_dispatch_routes_cfg_shapes():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i1 %cond) {
entry:
  br i1 %cond, label %t, label %f

t:
  ret i32 7

f:
  unreachable
}
""".strip()

    module = parse_self_backend_module(ir_text)
    func = module.functions[0]

    assert emit_terminator_dispatch(
        func,
        func.blocks[0],
        func.blocks[0].terminator,
        emit_ret_void=lambda _func: ["ret_void"],
        emit_ret=lambda _func, ret_type, value: [f"ret {ret_type.describe()} {value}"],
        emit_br=lambda _func, source_block, target: [f"br {source_block}->{target}"],
        emit_br_cond=lambda _func, block_name, cond_name, true_target, false_target: [
            f"br_cond {block_name} {cond_name} {true_target} {false_target}"
        ],
        emit_switch=lambda _func, block_name, value_type, value, default_target, cases: [
            f"switch {block_name} {value_type.describe()} {value} {default_target} {len(cases)}"
        ],
        emit_unreachable=lambda: ["unreachable"],
    ) == ["br_cond entry cond t f"]

    assert emit_terminator_dispatch(
        func,
        func.blocks[1],
        func.blocks[1].terminator,
        emit_ret_void=lambda _func: ["ret_void"],
        emit_ret=lambda _func, ret_type, value: [f"ret {ret_type.describe()} {value}"],
        emit_br=lambda _func, source_block, target: [f"br {source_block}->{target}"],
        emit_br_cond=lambda _func, block_name, cond_name, true_target, false_target: [
            f"br_cond {block_name} {cond_name} {true_target} {false_target}"
        ],
        emit_switch=lambda _func, block_name, value_type, value, default_target, cases: [
            f"switch {block_name} {value_type.describe()} {value} {default_target} {len(cases)}"
        ],
        emit_unreachable=lambda: ["unreachable"],
    ) == ["ret i32 7"]

    assert emit_terminator_dispatch(
        func,
        func.blocks[2],
        func.blocks[2].terminator,
        emit_ret_void=lambda _func: ["ret_void"],
        emit_ret=lambda _func, ret_type, value: [f"ret {ret_type.describe()} {value}"],
        emit_br=lambda _func, source_block, target: [f"br {source_block}->{target}"],
        emit_br_cond=lambda _func, block_name, cond_name, true_target, false_target: [
            f"br_cond {block_name} {cond_name} {true_target} {false_target}"
        ],
        emit_switch=lambda _func, block_name, value_type, value, default_target, cases: [
            f"switch {block_name} {value_type.describe()} {value} {default_target} {len(cases)}"
        ],
        emit_unreachable=lambda: ["unreachable"],
    ) == ["unreachable"]


def test_self_backend_shared_instruction_dispatch_routes_memory_then_compute():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main() {
entry:
  %p = alloca i32
  %x = add i32 1, 2
  ret i32 %x
}
""".strip()

    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    entry = func.blocks[0]

    assert emit_instruction_dispatch(
        func,
        entry,
        entry.instructions[0],
        emit_memory=lambda _func, kind, _data: (
            [f"memory {kind}"] if kind == "alloca" else None
        ),
        emit_compute=lambda _func, kind, _data: (
            [f"compute {kind}"] if kind == "binop" else None
        ),
    ) == ["memory alloca"]

    assert emit_instruction_dispatch(
        func,
        entry,
        entry.instructions[1],
        emit_memory=lambda _func, kind, _data: (
            [f"memory {kind}"] if kind == "alloca" else None
        ),
        emit_compute=lambda _func, kind, _data: (
            [f"compute {kind}"] if kind == "binop" else None
        ),
    ) == ["compute binop"]


def test_self_backend_shared_analysis_reports_used_vs_unused_values():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main() {
entry:
  %used = add i32 1, 2
  %dead = add i32 3, 4
  ret i32 %used
}
""".strip()

    module = parse_self_backend_module(ir_text)
    func = module.functions[0]

    assert value_has_uses(func, "used") is True
    assert value_has_uses(func, "dead") is False


def test_self_backend_shared_prepare_seeds_block_map_and_arg_types():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @add(i32 %lhs, i32 %rhs) {
entry:
  %sum = add i32 %lhs, %rhs
  ret i32 %sum
}
""".strip()

    module = parse_self_backend_module(ir_text)
    func = module.functions[0]

    assert func.block_map == {}
    assert func.value_types == {}

    prepare_parsed_function(func)

    assert tuple(func.block_map) == ("entry",)
    assert func.value_types["lhs"].describe() == "i32"
    assert func.value_types["rhs"].describe() == "i32"


def test_self_backend_shared_stackprep_assigns_arg_local_and_result_slots():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @copy(i32 %value) {
entry:
  %slot = alloca i32
  store i32 %value, ptr %slot
  %loaded = load i32, ptr %slot
  ret i32 %loaded
}
""".strip()

    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    prepare_parsed_function(func)

    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    assert "value" in func.value_slots
    assert "loaded" in func.value_slots
    assert "slot" in func.alloca_slots
    assert func.hidden_sret_slot is None
    assert func.frame_size > 0


def test_self_backend_shared_module_symbols_capture_defined_and_internal_sets():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@g = global i32 0
@hidden = internal global i32 1

define i32 @main() {
entry:
  ret i32 0
}

define internal i32 @helper() {
entry:
  ret i32 1
}
""".strip()

    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )

    assert symbols.internal_prefix.startswith("__pccmod_")
    assert symbols.defined_symbols == frozenset({"g", "hidden", "main", "helper"})
    assert symbols.internal_symbols == frozenset({"hidden", "helper"})


def test_self_backend_shared_module_prepare_pipeline_builds_symbols_and_slots():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@hidden = internal global i32 1

define i32 @main(i32 %value) {
entry:
  %slot = alloca i32
  store i32 %value, ptr %slot
  %loaded = load i32, ptr %slot
  ret i32 %loaded
}
""".strip()

    prepared = prepare_module_for_target(
        ir_text,
        aggregate_returned_indirect=lambda _ty: False,
    )

    assert prepared.triple == "arm64-apple-darwin23.6.0"
    assert [global_.name for global_ in prepared.globals_] == ["hidden"]
    assert [func.name for func in prepared.functions] == ["main"]
    assert prepared.module_symbols.internal_symbols == frozenset({"hidden"})
    func = prepared.functions[0]
    assert "value" in func.value_slots
    assert "slot" in func.alloca_slots
    assert "loaded" in func.value_slots


def test_self_backend_aarch64_abi_helpers_cover_small_aggregate_and_stack_overflow():
    pair = TypeDesc(
        "struct", name="pair", fields=(TypeDesc("int", 64), TypeDesc("int", 32))
    )
    assert aggregate_reg_chunks(pair) == (8, 8)
    tiny = TypeDesc(
        "struct",
        name="tiny",
        fields=(TypeDesc("int", 8), TypeDesc("int", 8), TypeDesc("int", 8)),
    )
    assert aggregate_reg_chunks(tiny) == (3,)
    assert assign_abi_arg_regs([tiny])[0] == ("w0",)
    s11 = TypeDesc(
        "struct",
        name="s11",
        fields=((TypeDesc("array", count=11, elem=TypeDesc("int", 8))),),
    )
    assert aggregate_reg_chunks(s11) == (8, 3)
    assert assign_abi_arg_regs([s11])[0] == ("x0", "w1")

    arg_types = [TypeDesc("int", 64)] * 9
    assignments = assign_abi_arg_regs(arg_types)
    assert assignments[:8] == [(f"x{i}",) for i in range(8)]
    assert assignments[8] == ()
    assert stack_arg_offsets(arg_types, assignments)[8] == 16


def test_self_backend_aarch64_memory_helpers_cover_opcode_selection_and_copy_chunks():
    assert stack_load_op(TypeDesc("int", 8)) == "ldurb"
    assert stack_store_op(TypeDesc("int", 16)) == "sturh"
    assert mem_load_op(TypeDesc("ptr", pointee=TypeDesc("void"))) == "ldr"
    assert mem_store_op(TypeDesc("int", 8)) == "strb"
    assert chunk_load_op(4, stack=False) == "ldr"
    assert chunk_store_op(2, stack=True) == "sturh"
    assert aggregate_copy_chunks(13) == [(0, 8), (8, 4), (12, 1)]


def test_self_backend_aarch64_slot_helpers_support_odd_tail_aggregate_reg_chunks():
    s11 = TypeDesc(
        "struct",
        name="s11",
        fields=(TypeDesc("array", count=11, elem=TypeDesc("int", 8)),),
    )
    slot = SlotInfo(64, s11)

    store_lines = store_value_regs_to_slot(slot, 0)
    load_lines = load_slot_to_value_regs(slot, 0)

    assert store_lines[0] == "  sub x15, x29, #64"
    assert any("str x0, [x15]" in line for line in store_lines)
    assert any("strh w1" in line for line in store_lines)
    assert any("strb w" in line for line in store_lines)

    assert load_lines[0] == "  sub x15, x29, #64"
    assert any("ldr x0, [x15]" in line for line in load_lines)
    assert any("ldrh w17" in line for line in load_lines)
    assert any("ldrb w17" in line for line in load_lines)
    assert any("orr w1, w1, w17" in line for line in load_lines)

    conflict_store_lines = store_value_to_address("x13", s11, 12)
    conflict_load_lines = load_value_from_address("x12", s11, 12)
    assert conflict_store_lines[0] == "  mov x17, x13"
    assert any("str x12, [x17]" in line for line in conflict_store_lines)
    assert conflict_load_lines[0] == "  mov x17, x12"
    assert any("ldr x12, [x17]" in line for line in conflict_load_lines)


def test_self_backend_aarch64_register_helpers_cover_large_offsets_and_immediates():
    assert pick_scratch_gpr("w14", "x13") == "x15"
    assert emit_add_offset("x10", "x29", 32) == ["  add x10, x29, #32"]
    assert emit_add_offset("x10", "x29", -5000) == [
        "  movz x15, #5000, lsl #0",
        "  sub x10, x29, x15",
    ]
    assert emit_stack_adjust(64) == ["  add sp, sp, #64"]
    assert emit_const_to_reg(TypeDesc("int", 32), "w9", 0) == ["  movz w9, #0"]
    assert emit_const_to_reg(TypeDesc("int", 8), "w9", -120) == [
        "  movz w9, #136, lsl #0"
    ]
    assert emit_const_to_reg(TypeDesc("int", 16), "w9", -2) == [
        "  movz w9, #65534, lsl #0"
    ]
    assert align_pow2(8) == 3


def test_self_backend_aarch64_op_helpers_sign_extend_narrow_signed_icmp_operands():
    assert sign_extend_int_reg(TypeDesc("int", 1), "w9") == [
        "  and w9, w9, #1",
        "  neg w9, w9",
    ]
    assert sign_extend_int_reg(TypeDesc("int", 8), "w10") == ["  sxtb w10, w10"]
    assert sign_extend_int_reg(TypeDesc("int", 16), "w11") == ["  sxth w11, w11"]


def test_self_backend_aarch64_symbol_helpers_cover_internal_prefix_and_labels():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@hidden = internal global i32 1

define internal i32 @helper() {
entry.with.dot:
  ret i32 1
}
""".strip()

    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )

    assert asm_symbol("hidden", symbols).startswith(f"_{symbols.internal_prefix}hidden")
    assert asm_symbol("helper", symbols).startswith(f"_{symbols.internal_prefix}helper")
    assert asm_symbol("printf", symbols) == "_printf"
    assert sanitize_label("entry.with.dot") == "entrydotwithdotdot"
    assert block_label("helper", "entry.with.dot") == "L_helper_entrydotwithdotdot"
    assert (
        block_edge_label("helper", "entry.with.dot", "next-block")
        == "L_helper_entrydotwithdotdot_to_next_block"
    )


def test_self_backend_aarch64_data_helpers_emit_pointer_and_struct_initializers():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

%pair = type { i32, i8 }

@base = global i32 0
@ptr = global ptr getelementptr (i32, ptr @base, i64 0)
@labels = internal global [3 x ptr] [ptr inttoptr (i64 3 to ptr), ptr inttoptr (i64 1 to ptr), ptr inttoptr (i64 4 to ptr)]
@fp32 = global float 0x4026333340000000
@fp64 = global double 1.000000e+02
@pair = internal global %pair { i32 7, i8 3 }
""".strip()

    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(ir_text, list(module.globals_), list())

    globals_by_name = {global_.name: global_ for global_ in module.globals_}
    assert emit_global_initializer(globals_by_name["ptr"], symbols) == "  .quad _base"
    assert (
        emit_global_initializer(globals_by_name["labels"], symbols)
        == "  .quad 3\n  .quad 1\n  .quad 4"
    )
    assert (
        emit_global_initializer(globals_by_name["fp32"], symbols)
        == "  .long 1093769626"
    )
    assert (
        emit_global_initializer(globals_by_name["fp64"], symbols)
        == "  .double 1.000000e+02"
    )
    assert (
        emit_global_initializer(globals_by_name["pair"], symbols)
        == "  .long 7\n  .byte 3\n  .space 3"
    )

    globals_by_name = {
        "flag": type(
            "GlobalLike",
            (),
            {
                "name": "flag",
                "type": TypeDesc("int", 1),
                "initializer": "false",
            },
        )(),
        "poison64": type(
            "GlobalLike",
            (),
            {
                "name": "poison64",
                "type": TypeDesc("int", 64),
                "initializer": "poison",
            },
        )(),
    }
    assert emit_global_initializer(globals_by_name["flag"], symbols) == "  .byte 0"
    assert emit_global_initializer(globals_by_name["poison64"], symbols) == "  .quad 0"
    globals_by_name = {
        "flex": type(
            "GlobalLike",
            (),
            {
                "name": "flex",
                "type": TypeDesc(
                    "struct",
                    fields=[
                        TypeDesc("int", 32),
                        TypeDesc("array", count=0, elem=TypeDesc("int", 8)),
                    ],
                ),
                "initializer": "{ i32 7, [0 x i8] poison }",
            },
        )(),
    }
    assert emit_global_initializer(globals_by_name["flex"], symbols) == "  .long 7"


def test_self_backend_aggregate_literal_bytes_support_inttoptr_pointer_vector():
    vector_type = TypeDesc(
        "array",
        count=2,
        elem=TypeDesc("ptr", pointee=TypeDesc("void")),
    )

    assert aggregate_literal_to_bytes(
        vector_type,
        "<ptr inttoptr (i64 1 to ptr), ptr inttoptr (i64 -9223372036854775808 to ptr)>",
    ) == (
        (1).to_bytes(8, byteorder="little", signed=False)
        + (1 << 63).to_bytes(8, byteorder="little", signed=False)
    )
    assert (
        aggregate_literal_to_bytes(
            TypeDesc("array", count=4, elem=TypeDesc("int", 8)),
            'c"\\00\\01\\02\\03"',
        )
        == b"\x00\x01\x02\x03"
    )


def test_self_backend_aarch64_data_helpers_strip_trailing_align_on_array_initializers():
    globals_by_name = {
        "tbl": type(
            "GlobalLike",
            (),
            {
                "name": "tbl",
                "type": TypeDesc(
                    "array",
                    count=2,
                    elem=TypeDesc("ptr", pointee=TypeDesc("void")),
                ),
                "initializer": "[ptr @a, ptr @b], align 8",
            },
        )()
    }
    symbols = prepare_module_symbols(
        'target triple = "arm64-apple-darwin23.6.0"\n@a = global i32 0\n@b = global i32 0',
        [],
        [],
    )

    assert (
        emit_global_initializer(globals_by_name["tbl"], symbols)
        == "  .quad _a\n  .quad _b"
    )


def test_self_backend_aarch64_slot_helpers_cover_slot_addressing_and_aggregate_copies():
    i32_slot = SlotInfo(32, TypeDesc("int", 32))
    large_slot = SlotInfo(320, TypeDesc("int", 64))
    aggregate = TypeDesc(
        "struct",
        name="pair",
        fields=(TypeDesc("int", 64), TypeDesc("int", 32)),
    )

    assert emit_slot_base_address(i32_slot, "x12") == ["  sub x12, x29, #32"]
    assert store_reg_to_slot("w9", i32_slot) == ["  stur w9, [x29, #-32]"]
    assert load_slot_to_reg(large_slot, "x10") == [
        "  sub x15, x29, #320",
        "  ldur x10, [x15]",
    ]
    assert copy_address_to_address("x9", "x10", 13) == [
        "  ldr x14, [x9]",
        "  str x14, [x10]",
        "  add x16, x9, #8",
        "  add x17, x10, #8",
        "  ldr w14, [x16]",
        "  str w14, [x17]",
        "  add x16, x9, #12",
        "  add x17, x10, #12",
        "  ldrb w14, [x16]",
        "  strb w14, [x17]",
    ]
    assert zero_address("x11", 5) == [
        "  movz x14, #0",
        "  str w14, [x11]",
        "  add x16, x11, #4",
        "  strb w14, [x16]",
    ]
    assert copy_address_to_address("x14", "x13", 16) == [
        "  ldr x15, [x14]",
        "  str x15, [x13]",
        "  add x16, x14, #8",
        "  add x17, x13, #8",
        "  ldr x15, [x16]",
        "  str x15, [x17]",
    ]
    assert zero_address("x14", 5) == [
        "  movz x15, #0",
        "  str w15, [x14]",
        "  add x16, x14, #4",
        "  strb w15, [x16]",
    ]
    assert load_value_from_address("x9", aggregate, 0) == [
        "  ldr x0, [x9]",
        "  ldr x1, [x9, #8]",
    ]
    assert store_value_to_address("x9", aggregate, 0) == [
        "  str x0, [x9]",
        "  str x1, [x9, #8]",
    ]


def test_self_backend_aarch64_op_helpers_cover_binop_cast_and_condition_selection():
    i32 = TypeDesc("int", 32)
    i64 = TypeDesc("int", 64)
    f64 = TypeDesc("fp", 64)
    ptr = TypeDesc("ptr", pointee=TypeDesc("void"))

    assert emit_binop("add", i32) == ["  add w11, w9, w10"]
    assert emit_binop("urem", i64) == [
        "  udiv x11, x9, x10",
        "  msub x11, x11, x10, x9",
    ]
    assert emit_fbinop("fsub", f64) == ["  fsub d11, d9, d10"]
    assert emit_cast("zext", TypeDesc("int", 1), i64) == ["  and w10, w9, #1"]
    assert emit_cast("ptrtoint", ptr, i32) == ["  mov w10, w9"]
    assert emit_cast("fptosi", f64, i64) == ["  fcvtzs x10, d9"]
    assert aarch64_cc("uge") == "hs"
    assert emit_fcmp_result("ord") == ["  cset w11, vc"]
    assert emit_fcmp_result("ueq") == [
        "  cset w11, eq",
        "  cset w12, vs",
        "  orr w11, w11, w12",
    ]


def test_self_backend_aarch64_address_helpers_cover_global_and_gep_lowering():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@hidden = internal global i32 1
@external = external global i32

define i32 @main(i32 %idx) {
entry:
  %slot = alloca i32
  store i32 %idx, ptr %slot
  ret i32 0
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    assert materialize_global_address("hidden", "x9", symbols) == [
        f"  adrp x9, {asm_symbol('hidden', symbols)}@PAGE",
        f"  add x9, x9, {asm_symbol('hidden', symbols)}@PAGEOFF",
    ]
    assert materialize_global_address("external", "x10", symbols) == [
        "  adrp x10, _external@GOTPAGE",
        "  ldr x10, [x10, _external@GOTPAGEOFF]",
    ]
    assert emit_indexed_pointer_add(func, "3", 8) == ["  add x11, x9, #24"]
    assert materialize_index_to_x10(func, "idx") == [
        "  ldur w10, [x29, #-4]",
        "  sxtw x10, w10",
    ]
    pair = TypeDesc(
        "struct", name="pair", fields=(TypeDesc("int", 32), TypeDesc("int", 64))
    )
    assert emit_gep_offset(
        func,
        pair,
        ((TypeDesc("int", 64), "0"), (TypeDesc("int", 32), "1")),
    ) == [
        "  mov x11, x9",
        "  mov x9, x11",
        "  add x11, x9, #8",
    ]


def test_self_backend_aarch64_materialize_helpers_cover_constants_globals_and_large_aggregates():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

%pair = type { i64, i32 }

@hidden = internal global i32 1

define void @main(i32 %idx) {
entry:
  %slot = alloca i32
  %pairslot = alloca %pair
  store i32 %idx, ptr %slot
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(
        func, aggregate_returned_indirect=lambda ty: ty.describe() == "pair"
    )
    pair = TypeDesc(
        "struct", name="pair", fields=(TypeDesc("int", 64), TypeDesc("int", 32))
    )
    func.value_slots["pairslot"] = SlotInfo(32, pair)

    assert materialize_value(func, "7", TypeDesc("int", 32), 9, symbols) == [
        "  movz w9, #7, lsl #0"
    ]
    assert materialize_value(func, "idx", TypeDesc("int", 32), 9, symbols) == [
        "  ldur w9, [x29, #-4]"
    ]
    assert materialize_pointer(func, "@hidden", 9, symbols) == [
        f"  adrp x9, {asm_symbol('hidden', symbols)}@PAGE",
        f"  add x9, x9, {asm_symbol('hidden', symbols)}@PAGEOFF",
    ]
    assert materialize_pointer(func, "null", 9, symbols) == [
        "  movz x9, #0",
    ]
    assert materialize_indirect_aggregate_arg_pointer(
        func, "pairslot", pair, "x12"
    ) == [
        "  sub x12, x29, #32",
    ]
    dest_slot = SlotInfo(64, pair)
    assert copy_large_aggregate_value_to_slot(
        func, "zeroinitializer", pair, dest_slot
    ) == [
        f"  sub x15, x29, #{dest_slot.offset}",
        "  movz x14, #0",
        "  str x14, [x15]",
        "  add x16, x15, #8",
        "  str x14, [x16]",
    ]
    vector16 = TypeDesc("array", count=8, elem=TypeDesc("int", 16))
    vector_slot = SlotInfo(96, vector16)
    lines = copy_large_aggregate_value_to_slot(
        func,
        "<i16 poison, i16 0, i16 0, i16 0, i16 0, i16 0, i16 0, i16 0>",
        vector16,
        vector_slot,
    )
    assert lines[0] == f"  sub x15, x29, #{vector_slot.offset}"
    assert "  movz x14, #0" in lines
    assert "  str x14, [x15]" in lines
    assert "  add x16, x15, #8" in lines
    assert "  str x14, [x16]" in lines


def test_self_backend_parse_supports_aggregate_literals_and_extractvalue():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%triple = type { i32, i32, i32 }

declare i32 @sum_plus(%triple, i32)
declare %triple @mk_triple()

define i32 @main() {
entry:
  %call = call i32 @sum_plus(%triple { i32 1, i32 2, i32 3 }, i32 4)
  %agg = call %triple @mk_triple()
  %field = extractvalue %triple %agg, 1
  ret i32 %field
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]

    call_instr = func.blocks[0].instructions[0]
    assert call_instr.kind == "call"
    assert call_instr.data[4][0] == (
        TypeDesc(
            "struct",
            name="%triple",
            fields=(TypeDesc("int", 32), TypeDesc("int", 32), TypeDesc("int", 32)),
        ),
        "{ i32 1, i32 2, i32 3 }",
    )

    extract_instr = func.blocks[0].instructions[2]
    assert extract_instr.kind == "extractvalue"
    assert extract_instr.data[2] == "agg"
    assert extract_instr.data[3] == (1,)
    assert extract_instr.data[4] == TypeDesc("int", 32)
    assert extract_instr.data[5] == 4


def test_self_backend_parse_supports_insertvalue_and_parenthesized_scalar_constants():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%triple = type { i32, i8, i32 }

define i32 @main(i32 %x) {
entry:
  %agg = insertvalue %triple poison, i8 (i8 -1), 1
  %field = extractvalue %triple %agg, 1
  %ext = sext i8 %field to i32
  ret i32 %ext
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    insert_instr, extract_instr, _cast_instr = func.blocks[0].instructions
    triple = TypeDesc(
        "struct",
        name="%triple",
        fields=(TypeDesc("int", 32), TypeDesc("int", 8), TypeDesc("int", 32)),
    )

    assert insert_instr.kind == "insertvalue"
    assert insert_instr.data == (
        "agg",
        triple,
        "poison",
        TypeDesc("int", 8),
        "-1",
        (1,),
        4,
    )
    assert extract_instr.kind == "extractvalue"
    assert extract_instr.data == ("field", triple, "agg", (1,), TypeDesc("int", 8), 4)
    assert decode_value_token("splat (i8 -1)") == "-1"
    assert decode_value_token("(i32 -4)") == "-4"


def test_self_backend_parse_supports_insertvalue_aggregate_literal_with_commas():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%S = type { i8, i64 }

define %S @foo(i64 %tmp) {
entry:
  %ins = insertvalue %S { i8 undef, i64 poison }, i64 %tmp, 1
  ret %S %ins
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    insert_instr = func.blocks[0].instructions[0]
    struct_type = TypeDesc(
        "struct", name="%S", fields=(TypeDesc("int", 8), TypeDesc("int", 64))
    )

    assert insert_instr.kind == "insertvalue"
    assert insert_instr.data == (
        "ins",
        struct_type,
        "{ i8 undef, i64 poison }",
        TypeDesc("int", 64),
        "tmp",
        (1,),
        8,
    )


def test_self_backend_parse_supports_phi_incoming_constant_gep_with_commas():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
@g = global [8 x i8] zeroinitializer

define ptr @main(i1 %cond) {
entry:
  br i1 %cond, label %then, label %else
then:
  br label %join
else:
  br label %join
join:
  %p = phi ptr [ getelementptr inbounds nuw ([8 x i8], ptr @g, i64 0, i64 4), %then ], [ null, %else ]
  ret ptr %p
}
""".strip()
    module = parse_self_backend_module(ir_text)
    phi = next(
        block for block in module.functions[0].blocks if block.name == "join"
    ).phis[0]
    assert phi.incoming[0].value == "gepconst:g:4"
    assert phi.incoming[0].label == "then"


def test_self_backend_parse_supports_call_metadata_suffix():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @callee(ptr %p, i32 %x) {
entry:
  ret i32 %x
}

define i32 @main(ptr %p) {
entry:
  %r = call i32 @callee(ptr %p, i32 -1), !callees !0
  ret i32 %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    call_instr = module.functions[1].blocks[0].instructions[0]
    assert call_instr.kind == "call"
    assert call_instr.data[0] == "r"
    assert call_instr.data[2] == "callee"
    assert call_instr.data[4] == (
        (TypeDesc("ptr", pointee=TypeDesc("void")), "p"),
        (TypeDesc("int", 32), "-1"),
    )


def test_self_backend_parse_supports_tail_void_call_with_call_site_attrs():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare void @exit(i32)

define void @main() {
entry:
  tail call void @exit(i32 2) #7
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    call_instr = module.functions[0].blocks[0].instructions[0]
    assert call_instr.kind == "call"
    assert call_instr.data[0] is None
    assert call_instr.data[2] == "exit"
    assert call_instr.data[4] == ((TypeDesc("int", 32), "2"),)


def test_self_backend_parse_supports_vector_phi_constants_and_select_gep_values():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
@g = global [8 x i8] zeroinitializer

define ptr @main(i1 %cond) {
entry:
  br i1 %cond, label %then, label %else
then:
  br label %join
else:
  br label %join
join:
  %vec = phi <4 x i32> [ <i32 8, i32 0, i32 0, i32 0>, %then ], [ zeroinitializer, %else ]
  %p = select i1 %cond, ptr getelementptr ([8 x i8], ptr @g, i64 0, i64 4), ptr null
  ret ptr %p
}
""".strip()
    module = parse_self_backend_module(ir_text)
    join = next(block for block in module.functions[0].blocks if block.name == "join")
    phi = join.phis[0]
    select_instr = join.instructions[0]

    assert phi.type == TypeDesc("array", count=4, elem=TypeDesc("int", 32))
    assert phi.incoming[0].value == "<i32 8, i32 0, i32 0, i32 0>"
    assert select_instr.kind == "select"
    assert select_instr.data[2] == "cond"
    assert select_instr.data[3] == "gepconst:g:4"
    assert select_instr.data[4] == "null"


def test_self_backend_parse_supports_function_args_with_nested_attrs():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @setp(ptr nocapture noundef writeonly initializes((0, 4)) %p) {
entry:
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    assert func.args == [
        ArgInfo(name="p", type=TypeDesc("ptr", pointee=TypeDesc("void")))
    ]


def test_self_backend_parse_supports_numeric_ssa_labels_and_flagged_constant_gep():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@v = global i32 0

define i32 @main() {
bb0:
  %0 = load i32, ptr @v
  store i32 %0, ptr getelementptr inbounds nuw (i8, ptr @v, i64 0)
  br label %1

1:                                                ; preds = %bb0
  ret i32 %0
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    assert [block.name for block in func.blocks] == ["bb0", "1"]
    store_instr = func.blocks[0].instructions[1]
    assert store_instr.kind == "store"
    assert store_instr.data[1] == "%0"
    assert store_instr.data[3] == "gepconst:v:0"
    assert func.blocks[0].terminator.kind == "br"
    assert func.blocks[0].terminator.data == ("1",)


def test_self_backend_parse_supports_select_and_flagged_scalar_ops():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i64 %wide, i32 %n) {
entry:
  %t = trunc nsw i64 %wide to i32
  %cond = icmp samesign ugt i32 %n, 3
  %sel = select i1 %cond, i32 %t, i32 7
  ret i32 %sel
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    trunc_instr, icmp_instr, select_instr = func.blocks[0].instructions

    assert trunc_instr.kind == "cast"
    assert trunc_instr.data == (
        "trunc",
        "t",
        TypeDesc("int", 64),
        "wide",
        TypeDesc("int", 32),
    )
    assert icmp_instr.kind == "icmp"
    assert icmp_instr.data == ("ugt", "cond", TypeDesc("int", 32), "n", "3")
    assert select_instr.kind == "select"
    assert select_instr.data == ("sel", TypeDesc("int", 32), "cond", "t", "7")


def test_self_backend_parse_supports_boolean_i1_literals():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main() {
entry:
  %sel = select i1 false, i32 1, i32 2
  ret i32 %sel
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    select_instr = func.blocks[0].instructions[0]

    assert select_instr.kind == "select"
    assert select_instr.data == ("sel", TypeDesc("int", 32), "false", "1", "2")


def test_self_backend_parse_supports_vector_types_as_opaque_aggregates():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(ptr %p) {
entry:
  %vec = load <16 x i8>, ptr %p
  store <16 x i8> %vec, ptr %p
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    load_instr, store_instr = func.blocks[0].instructions
    vector_type = TypeDesc("array", count=16, elem=TypeDesc("int", 8))

    assert load_instr.kind == "load"
    assert load_instr.data == (
        "vec",
        vector_type,
        TypeDesc("ptr", pointee=TypeDesc("void")),
        "p",
    )
    assert store_instr.kind == "store"
    assert store_instr.data == (
        vector_type,
        "vec",
        TypeDesc("ptr", pointee=TypeDesc("void")),
        "p",
    )


def test_self_backend_parse_supports_vector_bitwise_binops():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<16 x i8> %lhs, <16 x i8> %rhs, ptr %out) {
entry:
  %vec = xor <16 x i8> %lhs, %rhs
  store <16 x i8> %vec, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    binop_instr = func.blocks[0].instructions[0]
    vector_type = TypeDesc("array", count=16, elem=TypeDesc("int", 8))

    assert binop_instr.kind == "binop"
    assert binop_instr.data == ("xor", "vec", vector_type, "lhs", "rhs")


def test_self_backend_parse_supports_freeze_and_call_result_attrs():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare ptr @calloc(i64, i64)

define i32 @main(i32 %n) {
entry:
  %tmp = freeze i32 %n
  %p = tail call dereferenceable_or_null(256) ptr @calloc(i64 64, i64 4)
  ret i32 %tmp
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    freeze_instr, call_instr = func.blocks[0].instructions

    assert freeze_instr.kind == "freeze"
    assert freeze_instr.data == ("tmp", TypeDesc("int", 32), "n")
    assert call_instr.kind == "call"
    assert call_instr.data[:4] == (
        "p",
        TypeDesc("ptr", pointee=TypeDesc("void")),
        "calloc",
        False,
    )


def test_self_backend_parse_supports_literal_struct_return_call():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare { i64, i1 } @llvm.umul.with.overflow.i64(i64, i64)

define i64 @main(i64 %lhs, i64 %rhs) {
entry:
  %mul = tail call { i64, i1 } @llvm.umul.with.overflow.i64(i64 %lhs, i64 %rhs)
  %value = extractvalue { i64, i1 } %mul, 0
  ret i64 %value
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    call_instr, extract_instr = func.blocks[0].instructions
    pair_type = TypeDesc("struct", fields=(TypeDesc("int", 64), TypeDesc("int", 1)))

    assert call_instr.kind == "call"
    assert call_instr.data == (
        "mul",
        pair_type,
        "llvm.umul.with.overflow.i64",
        False,
        ((TypeDesc("int", 64), "lhs"), (TypeDesc("int", 64), "rhs")),
        0,
        False,
    )
    assert extract_instr.kind == "extractvalue"
    assert extract_instr.data[:3] == ("value", pair_type, "mul")


def test_self_backend_parse_strips_branch_loop_metadata_from_labels():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(i1 %cond) {
entry:
  br i1 %cond, label %done, label %loop, !llvm.loop !0

loop:
  br label %done

done:
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    entry = module.functions[0].blocks[0]

    assert entry.terminator.kind == "br_cond"
    assert entry.terminator.data == ("cond", "done", "loop")


def test_self_backend_parse_folds_constant_conditional_branches():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main() {
entry:
  br i1 1, label %taken, label %skipped

taken:
  br i1 0, label %bad, label %done

skipped:
  ret void

bad:
  ret void

done:
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    entry, taken = module.functions[0].blocks[:2]

    assert entry.terminator.kind == "br"
    assert entry.terminator.data == ("taken",)
    assert taken.terminator.kind == "br"
    assert taken.terminator.data == ("done",)


def test_self_backend_parse_supports_switch_with_numeric_labels():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(i8 %x) {
entry:
  switch i8 %x, label %0 [ i8 1, label %1 i8 2, label %2 ]

0:
  ret void

1:
  ret void

2:
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    entry = module.functions[0].blocks[0]

    assert entry.terminator.kind == "switch"
    assert entry.terminator.data == (
        TypeDesc("int", 8),
        "x",
        "0",
        ((1, "1"), (2, "2")),
    )


def test_self_backend_parse_supports_gep_with_constant_gep_pointer_operand():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@base = global [16 x i8] zeroinitializer

define ptr @main(i64 %idx) {
entry:
  %p = getelementptr nuw i8, ptr getelementptr inbounds (i8, ptr @base, i64 4), i64 %idx
  ret ptr %p
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    gep_instr = func.blocks[0].instructions[0]

    assert gep_instr.kind == "gep"
    assert gep_instr.data == (
        "p",
        TypeDesc("int", 8),
        TypeDesc("ptr", pointee=TypeDesc("void")),
        "gepconst:base:4",
        ((TypeDesc("int", 64), "idx"),),
    )


def test_self_backend_parse_supports_vector_insert_and_shuffle_broadcast():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(i32 %x, ptr %out) {
entry:
  %ins = insertelement <4 x i32> poison, i32 %x, i64 0
  %spl = shufflevector <4 x i32> %ins, <4 x i32> poison, <4 x i32> zeroinitializer
  store <4 x i32> %spl, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    ins_instr, shuffle_instr, _store_instr = func.blocks[0].instructions
    vector_type = TypeDesc("array", count=4, elem=TypeDesc("int", 32))

    assert ins_instr.kind == "insertelement"
    assert ins_instr.data == (
        "ins",
        vector_type,
        "poison",
        TypeDesc("int", 32),
        "x",
        "0",
    )
    assert shuffle_instr.kind == "shufflevector"
    assert shuffle_instr.data == (
        "spl",
        vector_type,
        "ins",
        "poison",
        vector_type,
        "zeroinitializer",
    )


def test_self_backend_parse_and_materialize_support_inttoptr_constant_expr():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define ptr @main(i1 %cond) {
entry:
  %p = select i1 %cond, ptr inttoptr (i32 -1 to ptr), ptr null
  ret ptr %p
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    select_instr = func.blocks[0].instructions[0]

    assert select_instr.kind == "select"
    assert select_instr.data == (
        "p",
        TypeDesc("ptr", pointee=TypeDesc("void")),
        "cond",
        "inttoptrconst:-1",
        "null",
    )

    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert materialize_value(
        func,
        "inttoptrconst:-1",
        TypeDesc("ptr", pointee=TypeDesc("void")),
        9,
        symbols,
    ) == [
        "  movz x9, #65535, lsl #0",
        "  movk x9, #65535, lsl #16",
        "  movk x9, #65535, lsl #32",
        "  movk x9, #65535, lsl #48",
    ]
    assert materialize_pointer(func, "inttoptrconst:1", 9, symbols) == [
        "  movz x9, #1, lsl #0",
    ]


def test_self_backend_parse_and_materialize_support_ptrtoint_constant_gep_expr():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@x = global { i32, [5 x i32] } zeroinitializer

define i64 @main() {
entry:
  ret i64 ptrtoint (ptr getelementptr inbounds nuw (i8, ptr @x, i64 4) to i64)
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    ret_term = func.blocks[0].terminator

    assert ret_term.kind == "ret"
    assert ret_term.data == (TypeDesc("int", 64), "ptrtointconst:gepconst:x:4")

    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert materialize_value(
        func,
        "ptrtointconst:gepconst:x:4",
        TypeDesc("int", 64),
        9,
        symbols,
    ) == [
        f"  adrp x9, {asm_symbol('x', symbols)}@PAGE",
        f"  add x9, x9, {asm_symbol('x', symbols)}@PAGEOFF",
        "  add x9, x9, #4",
    ]


def test_self_backend_parse_supports_binop_operand_sub_ptrtoint_constant_expr():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@buffer = global [64 x i8] zeroinitializer

define i64 @main() {
entry:
  %mask = and i64 sub (i64 0, i64 ptrtoint (ptr @buffer to i64)), 48
  ret i64 %mask
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    binop_instr = func.blocks[0].instructions[0]

    assert binop_instr.kind == "binop"
    assert binop_instr.data == (
        "and",
        "mask",
        TypeDesc("int", 64),
        "negconst:ptrtointconst:@buffer",
        "48",
    )


def test_self_backend_aarch64_materialize_supports_negated_ptrtoint_constant_expr():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@buffer = global [64 x i8] zeroinitializer

define i64 @main() {
entry:
  ret i64 0
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    assert materialize_value(
        func,
        "negconst:ptrtointconst:@buffer",
        TypeDesc("int", 64),
        9,
        symbols,
    ) == [
        f"  adrp x9, {asm_symbol('buffer', symbols)}@PAGE",
        f"  add x9, x9, {asm_symbol('buffer', symbols)}@PAGEOFF",
        "  sub x9, xzr, x9",
    ]


def test_self_backend_parse_and_materialize_support_icmp_add_ptrtoint_constant_expr():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@strlit = internal constant [1 x i8] zeroinitializer

define i32 @main() {
entry:
  %ok = icmp eq i32 add (i32 ptrtoint (ptr @strlit to i32), i32 1), ptrtoint (ptr getelementptr inbounds nuw (i8, ptr @strlit, i64 1) to i32)
  ret i32 0
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    icmp_instr = func.blocks[0].instructions[0]

    assert icmp_instr.kind == "icmp"
    assert icmp_instr.data == (
        "eq",
        "ok",
        TypeDesc("int", 32),
        "addconst:ptrtointconst:@strlit:1",
        "ptrtointconst:gepconst:strlit:1",
    )

    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    assert materialize_value(
        func,
        "addconst:ptrtointconst:@strlit:1",
        TypeDesc("int", 32),
        9,
        symbols,
    ) == [
        f"  adrp x9, {asm_symbol('strlit', symbols)}@PAGE",
        f"  add x9, x9, {asm_symbol('strlit', symbols)}@PAGEOFF",
        "  add x9, x9, #1",
    ]


def test_self_backend_aarch64_materializes_nested_ptrtoint_constant_expr():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@strlit = internal constant [5 x i8] c"test\\00"

define i32 @main() {
entry:
  %v = mul i32 2, trunc (i64 add (i64 mul (i64 ptrtoint (ptr @strlit to i64), i64 7), i64 3) to i32)
  ret i32 %v
}
""".strip()
    module = parse_self_backend_module(ir_text)
    func = module.functions[0]
    binop_instr = func.blocks[0].instructions[0]

    assert binop_instr.kind == "binop"
    assert binop_instr.data[4].startswith("cexpr:trunc ")

    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    lines = materialize_value(
        func,
        binop_instr.data[4],
        TypeDesc("int", 32),
        9,
        symbols,
    )

    assert "  mul x9, x9, x10" in lines
    assert "  add x9, x9, #3" in lines


def test_self_backend_aarch64_materialize_helpers_cover_aggregate_literals_and_decimal_fp():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%triple = type { i32, i32, i32 }

define i32 @main() {
entry:
  ret i32 0
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    triple = TypeDesc(
        "struct",
        name="triple",
        fields=(TypeDesc("int", 32), TypeDesc("int", 32), TypeDesc("int", 32)),
    )

    assert materialize_value(func, "{ i32 1, i32 2, i32 3 }", triple, 9, symbols) == [
        "  movz x9, #1, lsl #0",
        "  movk x9, #2, lsl #32",
        "  movz w10, #3, lsl #0",
    ]
    assert materialize_value(func, "4.000000e+00", TypeDesc("fp", 64), 0, symbols) == [
        "  movz x12, #16400, lsl #48",
        "  fmov d0, x12",
    ]
    assert materialize_value(func, "poison", TypeDesc("int", 32), 9, symbols) == [
        "  movz w9, #0",
    ]
    assert materialize_value(func, "undef", TypeDesc("int", 32), 9, symbols) == [
        "  movz w9, #0",
    ]
    assert materialize_value(func, "poison", TypeDesc("fp", 64), 0, symbols) == [
        "  movz x12, #0",
        "  fmov d0, x12",
    ]
    assert materialize_value(
        func, "zeroinitializer", TypeDesc("fp", 32), 1, symbols
    ) == [
        "  movz w12, #0",
        "  fmov s1, w12",
    ]


def test_self_backend_aarch64_materialize_pointer_accepts_gep_constants():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@v = global [2 x i32] zeroinitializer

define ptr @main() {
entry:
  ret ptr getelementptr inbounds ([2 x i32], ptr @v, i64 0, i64 1)
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    assert materialize_pointer(func, "gepconst:v:4", 9, symbols) == [
        f"  adrp x9, {asm_symbol('v', symbols)}@PAGE",
        f"  add x9, x9, {asm_symbol('v', symbols)}@PAGEOFF",
        "  add x9, x9, #4",
    ]


def test_self_backend_aarch64_call_helpers_cover_varargs_and_fixed_stack_loads():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @probe(i32 %tag, ...) {
entry:
  %argp = alloca ptr
  %i = va_arg ptr %argp, i32
  ret i32 %i
}

define void @fixed(i32 %a0, i32 %a1, i32 %a2, i32 %a3, i32 %a4, i32 %a5, i32 %a6, i32 %a7, i32 %a8) {
entry:
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    probe = module.functions[0]
    fixed = module.functions[1]
    prepare_parsed_function(probe)
    prepare_parsed_function(fixed)
    assign_stack_slots(probe, aggregate_returned_indirect=lambda _ty: False)
    assign_stack_slots(fixed, aggregate_returned_indirect=lambda _ty: False)

    argp_slot = probe.alloca_slots["argp"]
    i_slot = probe.value_slots["i"]
    assert emit_vararg_start(probe, "argp", symbols) == [
        f"  sub x9, x29, #{argp_slot.offset}",
        "  add x10, x29, #16",
        "  str x10, [x9]",
    ]
    assert emit_va_arg(
        probe,
        "i",
        TypeDesc("ptr", pointee=TypeDesc("void")),
        "argp",
        TypeDesc("int", 32),
        symbols,
    ) == [
        f"  sub x9, x29, #{argp_slot.offset}",
        "  ldr x10, [x9]",
        "  ldr w11, [x10]",
        "  add x10, x10, #8",
        "  str x10, [x9]",
        f"  stur w11, [x29, #-{i_slot.offset}]",
    ]
    assert emit_vararg_stack_arg(probe, TypeDesc("int", 32), "7", 8, symbols) == [
        "  movz w12, #7, lsl #0",
        "  add x14, sp, #8",
        "  str w12, [x14]",
    ]
    assert emit_vararg_stack_arg(
        probe,
        TypeDesc("array", count=1, elem=TypeDesc("int", 64)),
        "[i64 10]",
        8,
        symbols,
    ) == [
        "  add x14, sp, #8",
        "  movz x12, #10, lsl #0",
        "  str x12, [x14]",
    ]
    aggregate_call_ir = """
target triple = "arm64-apple-darwin23.6.0"
%s8 = type { [8 x i8] }
%s9 = type { [9 x i8] }
%s10 = type { [10 x i8] }
%s11 = type { [11 x i8] }
%s12 = type { [12 x i8] }
%s13 = type { [13 x i8] }

declare void @sink(%s8, %s9, %s10, %s11, %s12, %s13)

define void @caller(%s8 %a, %s9 %b, %s10 %c, %s11 %d, %s12 %e, %s13 %f) {
entry:
  call void @sink(%s8 %a, %s9 %b, %s10 %c, %s11 %d, %s12 %e, %s13 %f)
  ret void
}
    """.strip()
    module = parse_self_backend_module(aggregate_call_ir)
    symbols = prepare_module_symbols(
        aggregate_call_ir, list(module.globals_), list(module.functions)
    )
    caller = module.functions[0]
    prepare_parsed_function(caller)
    assign_stack_slots(caller, aggregate_returned_indirect=lambda _ty: False)
    aggregate_call = caller.blocks[0].instructions[0]
    lines = emit_call_instruction(caller, *aggregate_call.data, symbols)
    assert "  add x14, sp, #16" in lines
    assert "  add x13, sp, #16" not in lines
    hfa_vararg_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare void @vsink(ptr, ...)

define void @hfa_caller(ptr %fmt, [4 x double] %a, [4 x double] %b) {
entry:
  call void (ptr, ...) @vsink(ptr %fmt, [4 x double] %a, [4 x double] %b)
  ret void
}
    """.strip()
    module = parse_self_backend_module(hfa_vararg_ir)
    symbols = prepare_module_symbols(
        hfa_vararg_ir, list(module.globals_), list(module.functions)
    )
    hfa_caller = module.functions[0]
    prepare_parsed_function(hfa_caller)
    assign_stack_slots(hfa_caller, aggregate_returned_indirect=lambda _ty: False)
    hfa_call = hfa_caller.blocks[0].instructions[0]
    lines = emit_call_instruction(hfa_caller, *hfa_call.data, symbols)
    assert "  sub sp, sp, #64" in lines
    assert "  str x12, [sp]" not in lines
    assert "  str x12, [x14]" not in lines
    assert any(line.startswith("  ldr x") and "[x12]" in line for line in lines)
    assert any(line.startswith("  str x") and "[sp]" in line for line in lines)
    assert "  add x14, sp, #32" in lines
    arg = fixed.args[-1]
    fixed.value_slots[arg.name] = SlotInfo(20, arg.type)
    stack_offset = stack_arg_offsets(
        [arg.type for arg in fixed.args],
        assign_abi_arg_regs([arg.type for arg in fixed.args]),
    )[-1]
    assert stack_offset == 16
    assert emit_fixed_stack_arg_load(fixed, arg, stack_offset) == [
        "  add x12, x29, #16",
        "  ldr w11, [x12]",
        f"  stur w11, [x29, #-{fixed.value_slots[arg.name].offset}]",
    ]
    call_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @foo(i32)

define i32 @main(i32 %x) {
entry:
  %r = call i32 (i32) @foo(i32 %x)
  ret i32 %r
}
""".strip()
    module = parse_self_backend_module(call_ir)
    symbols = prepare_module_symbols(
        call_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert emit_call_instruction(
        func,
        "r",
        TypeDesc("int", 32),
        "foo",
        False,
        ((TypeDesc("int", 32), "x"),),
        1,
        False,
        symbols,
    ) == [
        f"  ldur w0, [x29, #-{func.value_slots['x'].offset}]",
        "  bl _foo",
        f"  stur w0, [x29, #-{func.value_slots['r'].offset}]",
    ]
    assert emit_call_instruction(
        probe,
        None,
        TypeDesc("void"),
        "llvm.va_start.p0",
        False,
        ((TypeDesc("ptr", pointee=TypeDesc("void")), "argp"),),
        1,
        False,
        symbols,
    ) == emit_vararg_start(probe, "argp", symbols)
    assert (
        emit_call_instruction(
            probe,
            None,
            TypeDesc("void"),
            "llvm.lifetime.end.p0",
            False,
            (
                (TypeDesc("int", 64), "8"),
                (TypeDesc("ptr", pointee=TypeDesc("void")), "argp"),
            ),
            2,
            False,
            symbols,
        )
        == []
    )

    minmax_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @llvm.smax.i32(i32, i32)

define i32 @max2(i32 %a, i32 %b) {
entry:
  %r = call i32 @llvm.smax.i32(i32 %a, i32 %b)
  ret i32 %r
}
""".strip()
    module = parse_self_backend_module(minmax_ir)
    symbols = prepare_module_symbols(
        minmax_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert emit_call_instruction(
        func,
        "r",
        TypeDesc("int", 32),
        "llvm.smax.i32",
        False,
        ((TypeDesc("int", 32), "a"), (TypeDesc("int", 32), "b")),
        2,
        False,
        symbols,
    ) == [
        f"  ldur w9, [x29, #-{func.value_slots['a'].offset}]",
        f"  ldur w10, [x29, #-{func.value_slots['b'].offset}]",
        "  cmp w9, w10",
        "  csel w11, w9, w10, ge",
        f"  stur w11, [x29, #-{func.value_slots['r'].offset}]",
    ]

    vector_minmax_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare <4 x i16> @llvm.smin.v4i16(<4 x i16>, <4 x i16>)

define void @vmin(<4 x i16> %a, ptr %out) {
entry:
  %r = call <4 x i16> @llvm.smin.v4i16(<4 x i16> %a, <4 x i16> splat (i16 1))
  store <4 x i16> %r, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(vector_minmax_ir)
    symbols = prepare_module_symbols(
        vector_minmax_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]
    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert lines.count("  sxth w9, w9") == 4
    assert lines.count("  sxth w10, w10") == 4
    assert lines.count("  csel w11, w9, w10, le") == 4

    bswap_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare i16 @llvm.bswap.i16(i16)

define i16 @swap(i16 %x) {
entry:
  %r = call i16 @llvm.bswap.i16(i16 %x)
  ret i16 %r
}
""".strip()
    module = parse_self_backend_module(bswap_ir)
    symbols = prepare_module_symbols(
        bswap_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert emit_call_instruction(
        func,
        "r",
        TypeDesc("int", 16),
        "llvm.bswap.i16",
        False,
        ((TypeDesc("int", 16), "x"),),
        1,
        False,
        symbols,
    ) == [
        f"  ldurh w9, [x29, #-{func.value_slots['x'].offset}]",
        "  rev16 w10, w9",
        f"  sturh w10, [x29, #-{func.value_slots['r'].offset}]",
    ]

    vector_bswap_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare <4 x i16> @llvm.bswap.v4i16(<4 x i16>)

define void @swap(<4 x i16> %x, ptr %out) {
entry:
  %r = call <4 x i16> @llvm.bswap.v4i16(<4 x i16> %x)
  store <4 x i16> %r, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(vector_bswap_ir)
    symbols = prepare_module_symbols(
        vector_bswap_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]
    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert f"  sub x15, x29, #{func.value_slots['x'].offset}" in lines
    assert f"  sub x17, x29, #{func.value_slots['r'].offset}" in lines
    assert lines.count("  rev16 w10, w9") == 4
    assert sum(line.startswith("  strh w10, [") for line in lines) == 4

    memcpy_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)

define void @copy(ptr %dst, ptr %src, i64 %n) {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr %dst, ptr %src, i64 %n, i1 0)
  ret void
}
""".strip()
    module = parse_self_backend_module(memcpy_ir)
    symbols = prepare_module_symbols(
        memcpy_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert emit_call_instruction(
        func,
        None,
        TypeDesc("void"),
        "llvm.memcpy.p0.p0.i64",
        False,
        (
            (TypeDesc("ptr", pointee=TypeDesc("void")), "dst"),
            (TypeDesc("ptr", pointee=TypeDesc("void")), "src"),
            (TypeDesc("int", 64), "n"),
            (TypeDesc("int", 1), "0"),
        ),
        4,
        False,
        symbols,
    ) == [
        f"  ldur x0, [x29, #-{func.value_slots['dst'].offset}]",
        f"  ldur x1, [x29, #-{func.value_slots['src'].offset}]",
        f"  ldur x2, [x29, #-{func.value_slots['n'].offset}]",
        "  bl _memcpy",
    ]
    assert emit_call_instruction(
        func,
        None,
        TypeDesc("void"),
        "llvm.memmove.p0.p0.i64",
        False,
        (
            (TypeDesc("ptr", pointee=TypeDesc("void")), "dst"),
            (TypeDesc("ptr", pointee=TypeDesc("void")), "src"),
            (TypeDesc("int", 64), "n"),
            (TypeDesc("int", 1), "0"),
        ),
        4,
        False,
        symbols,
    ) == [
        f"  ldur x0, [x29, #-{func.value_slots['dst'].offset}]",
        f"  ldur x1, [x29, #-{func.value_slots['src'].offset}]",
        f"  ldur x2, [x29, #-{func.value_slots['n'].offset}]",
        "  bl _memmove",
    ]
    assert emit_call_instruction(
        func,
        None,
        TypeDesc("void"),
        "llvm.memset.p0.i64",
        False,
        (
            (TypeDesc("ptr", pointee=TypeDesc("void")), "dst"),
            (TypeDesc("int", 32), "255"),
            (TypeDesc("int", 64), "n"),
            (TypeDesc("int", 1), "0"),
        ),
        4,
        False,
        symbols,
    ) == [
        f"  ldur x0, [x29, #-{func.value_slots['dst'].offset}]",
        "  movz w1, #255, lsl #0",
        f"  ldur x2, [x29, #-{func.value_slots['n'].offset}]",
        "  bl _memset",
    ]


def test_self_backend_aarch64_call_helper_passes_indirect_aggregate_literal_from_temp_stack():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

%big = type { [32 x i8] }

declare void @sink(%big, ptr, ...)

define void @caller() {
entry:
  call void (%big, ptr, ...) @sink(%big { [32 x i8] c"abc\\00" }, ptr null, i32 42)
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    call_instr = func.blocks[0].instructions[0]
    lines = emit_call_instruction(func, *call_instr.data, symbols)

    assert "  movz x1, #0" in lines
    assert "  sub sp, sp, #48" in lines
    assert "  add x0, sp, #8" in lines
    assert "  movz x12, #25185, lsl #0" in lines
    assert "  movk x12, #99, lsl #16" in lines
    assert "  str x12, [x0]" in lines
    assert "  movz w12, #42, lsl #0" in lines
    assert "  str w12, [sp]" in lines
    assert "  bl _sink" in lines
    assert "  add sp, sp, #48" in lines


def test_self_backend_aarch64_call_helper_passes_indirect_aggregate_zero_from_temp_stack():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

%big = type { [32 x i8] }

declare void @sink(%big, ptr, ...)

define void @caller() {
entry:
  call void (%big, ptr, ...) @sink(%big zeroinitializer, ptr null, i32 42)
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    call_instr = func.blocks[0].instructions[0]
    lines = emit_call_instruction(func, *call_instr.data, symbols)

    assert "  sub sp, sp, #48" in lines
    assert "  add x0, sp, #8" in lines
    assert "  movz x12, #0" in lines
    assert "  str x12, [x0]" in lines
    assert "  movz w12, #42, lsl #0" in lines
    assert "  str w12, [sp]" in lines
    assert "  bl _sink" in lines
    assert "  add sp, sp, #48" in lines


def test_self_backend_aarch64_compute_helper_covers_extractvalue_subset():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%triple = type { i32, i32, i32 }

declare %triple @mk_triple()

define i32 @main() {
entry:
  %calltmp = call %triple @mk_triple()
  %field = extractvalue %triple %calltmp, 1
  ret i32 %field
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    extract_instr = func.blocks[0].instructions[1]
    call_slot = func.value_slots["calltmp"]
    field_slot = func.value_slots["field"]

    assert emit_compute_instruction(
        func, extract_instr.kind, extract_instr.data, symbols
    ) == [
        f"  sub x12, x29, #{call_slot.offset}",
        "  add x12, x12, #4",
        "  ldr w10, [x12]",
        f"  stur w10, [x29, #-{field_slot.offset}]",
    ]


def test_self_backend_aarch64_compute_helper_covers_insertvalue_subset():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%triple = type { i32, i8, i32 }

define i32 @main() {
entry:
  %agg = insertvalue %triple poison, i8 7, 1
  %field = extractvalue %triple %agg, 1
  %ext = sext i8 %field to i32
  ret i32 %ext
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    insert_instr = func.blocks[0].instructions[0]
    agg_slot = func.value_slots["agg"]

    assert emit_compute_instruction(
        func, insert_instr.kind, insert_instr.data, symbols
    ) == [
        f"  sub x15, x29, #{agg_slot.offset}",
        "  movz x14, #0",
        "  str x14, [x15]",
        "  add x16, x15, #8",
        "  str w14, [x16]",
        "  movz w9, #7, lsl #0",
        f"  sub x15, x29, #{agg_slot.offset}",
        "  add x15, x15, #4",
        "  strb w9, [x15]",
    ]


def test_self_backend_stackprep_and_compute_keep_large_insertvalue_chains_addressable():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%triple = type { i64, i64, i64 }

define void @main() {
entry:
  %agg0 = insertvalue %triple poison, i64 1, 0
  %agg1 = insertvalue %triple %agg0, i64 2, 1
  %agg2 = insertvalue %triple %agg1, i64 3, 2
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    first_insert, second_insert, third_insert = func.blocks[0].instructions

    assert "agg0" in func.value_slots
    assert "agg1" in func.value_slots
    assert "agg2" not in func.value_slots

    lines0 = emit_compute_instruction(
        func, first_insert.kind, first_insert.data, symbols
    )
    lines1 = emit_compute_instruction(
        func, second_insert.kind, second_insert.data, symbols
    )

    assert any(line.startswith("  sub x15, x29, #") for line in lines0)
    assert f"  sub x12, x29, #{func.value_slots['agg0'].offset}" in lines1
    assert f"  sub x13, x29, #{func.value_slots['agg1'].offset}" in lines1
    assert any("  movz x9, #2" in line or "  movz x14, #2" in line for line in lines1)


def test_self_backend_aarch64_compute_helper_supports_insertvalue_large_aggregate_member():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%wrapper = type { [11 x i8] }

define void @main([11 x i8] %inner) {
entry:
  %agg = insertvalue %wrapper poison, [11 x i8] %inner, 0
  %field = extractvalue %wrapper %agg, 0
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    insert_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(
        func, insert_instr.kind, insert_instr.data, symbols
    )
    assert f"  sub x15, x29, #{func.value_slots['agg'].offset}" in lines
    assert f"  sub x16, x29, #{func.value_slots['inner'].offset}" in lines
    assert any("ldr x14" in line or "ldur x14" in line for line in lines)
    assert any("str x14" in line or "stur x14" in line for line in lines)
    assert any("ldrh w14" in line or "ldr w14" in line for line in lines)
    assert any("strh w14" in line or "sturh w14" in line for line in lines)
    assert any("ldrb w14" in line for line in lines)
    assert any("strb w14" in line for line in lines)


def test_self_backend_aarch64_compute_helper_supports_insertvalue_aggregate_literal_member():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%inner = type { i32, i32, i32 }
%wrapper = type { i64, %inner }

define i32 @main() {
entry:
  %agg = insertvalue %wrapper poison, %inner { i32 1, i32 0, i32 0 }, 1
  %field = extractvalue %wrapper %agg, 1, 0
  ret i32 %field
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    insert_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(
        func, insert_instr.kind, insert_instr.data, symbols
    )

    assert f"  sub x15, x29, #{func.value_slots['agg'].offset}" in lines
    assert "  add x15, x15, #8" in lines
    assert "  movz x14, #1, lsl #0" in lines
    assert "  str x14, [x15]" in lines
    assert any(line.startswith("  str w14, [") for line in lines)


def test_self_backend_aarch64_compute_helper_supports_insertvalue_small_aggregate_member_via_address_copy():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
%wrapper = type { [13 x i8] }

define void @main([13 x i8] %inner) {
entry:
  %agg = insertvalue %wrapper poison, [13 x i8] %inner, 0
  %field = extractvalue %wrapper %agg, 0
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    insert_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(
        func, insert_instr.kind, insert_instr.data, symbols
    )
    assert f"  sub x15, x29, #{func.value_slots['agg'].offset}" in lines
    assert f"  sub x16, x29, #{func.value_slots['inner'].offset}" in lines
    assert any("ldr x14" in line or "ldur x14" in line for line in lines)
    assert any("str x14" in line or "stur x14" in line for line in lines)
    assert any("ldrb w14" in line for line in lines)
    assert any("strb w14" in line for line in lines)


def test_self_backend_aarch64_compute_helper_covers_extractelement_subset():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i8 @main(<16 x i8> %vec) {
entry:
  %elt = extractelement <16 x i8> %vec, i64 11
  ret i8 %elt
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    extract_instr = func.blocks[0].instructions[0]

    assert emit_compute_instruction(
        func, extract_instr.kind, extract_instr.data, symbols
    ) == [
        f"  sub x15, x29, #{func.value_slots['vec'].offset}",
        "  add x15, x15, #11",
        "  ldrb w10, [x15]",
        f"  sturb w10, [x29, #-{func.value_slots['elt'].offset}]",
    ]


def test_self_backend_aarch64_compute_helper_dispatches_binop_and_call_paths():
    binop_ir = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
""".strip()
    module = parse_self_backend_module(binop_ir)
    symbols = prepare_module_symbols(
        binop_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert emit_compute_instruction(
        func,
        "binop",
        ("add", "r", TypeDesc("int", 32), "x", "1"),
        symbols,
    ) == [
        f"  ldur w9, [x29, #-{func.value_slots['x'].offset}]",
        "  movz w10, #1, lsl #0",
        "  add w11, w9, w10",
        f"  stur w11, [x29, #-{func.value_slots['r'].offset}]",
    ]

    call_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @foo(i32)

define i32 @main(i32 %x) {
entry:
  %r = call i32 (i32) @foo(i32 %x)
  ret i32 %r
}
""".strip()
    module = parse_self_backend_module(call_ir)
    symbols = prepare_module_symbols(
        call_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert emit_compute_instruction(
        func,
        "call",
        (
            "r",
            TypeDesc("int", 32),
            "foo",
            False,
            ((TypeDesc("int", 32), "x"),),
            1,
            False,
        ),
        symbols,
    ) == [
        f"  ldur w0, [x29, #-{func.value_slots['x'].offset}]",
        "  bl _foo",
        f"  stur w0, [x29, #-{func.value_slots['r'].offset}]",
    ]


def test_self_backend_aarch64_compute_helper_dispatches_vector_bitwise_binop_path():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<16 x i8> %lhs, <16 x i8> %rhs, ptr %out) {
entry:
  %vec = xor <16 x i8> %lhs, %rhs
  store <16 x i8> %vec, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    lines = emit_compute_instruction(
        func,
        "binop",
        (
            "xor",
            "vec",
            TypeDesc("array", count=16, elem=TypeDesc("int", 8)),
            "lhs",
            "rhs",
        ),
        symbols,
    )

    assert lines[:3] == [
        "  sub x15, x29, #16",
        "  sub x16, x29, #32",
        "  sub x17, x29, #56",
    ]
    assert lines.count("  eor w11, w9, w10") == 16
    assert sum(line.startswith("  ldrb w9, [") for line in lines) == 16
    assert sum(line.startswith("  ldrb w10, [") for line in lines) == 16
    assert sum(line.startswith("  strb w11, [") for line in lines) == 16


def test_self_backend_aarch64_compute_helper_dispatches_vector_insert_shuffle_and_add_paths():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(i32 %x, ptr %out) {
entry:
  %ins = insertelement <4 x i32> poison, i32 %x, i64 0
  %spl = shufflevector <4 x i32> %ins, <4 x i32> poison, <4 x i32> zeroinitializer
  %sum = add <4 x i32> %spl, %spl
  store <4 x i32> %sum, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    ins_instr, shuffle_instr, add_instr, _store_instr = func.blocks[0].instructions

    ins_lines = emit_compute_instruction(func, ins_instr.kind, ins_instr.data, symbols)
    shuffle_lines = emit_compute_instruction(
        func, shuffle_instr.kind, shuffle_instr.data, symbols
    )
    add_lines = emit_compute_instruction(func, add_instr.kind, add_instr.data, symbols)

    assert any("movz x14, #0" in line for line in ins_lines)
    assert any(
        "stur w9, [x15]" in line or "str w9, [x15]" in line for line in ins_lines
    )
    assert sum("str w9" in line or "stur w9" in line for line in shuffle_lines) == 4
    assert add_lines.count("  add w11, w9, w10") == 4


def test_self_backend_aarch64_compute_helper_supports_vector_binop_with_scalar_splat_rhs():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<16 x i8> %lhs, ptr %out) {
entry:
  %vec = xor <16 x i8> %lhs, splat (i8 -1)
  store <16 x i8> %vec, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    binop_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(func, binop_instr.kind, binop_instr.data, symbols)
    assert f"  sub x15, x29, #{func.value_slots['lhs'].offset}" in lines
    assert f"  sub x17, x29, #{func.value_slots['vec'].offset}" in lines
    assert sum("movn w10" in line or "movz w10" in line for line in lines) == 16
    assert lines.count("  eor w11, w9, w10") == 16


def test_self_backend_aarch64_compute_helper_supports_vector_binop_with_aggregate_literal_rhs():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<4 x i32> %lhs, ptr %out) {
entry:
  %vec = add <4 x i32> %lhs, <i32 0, i32 -1, i32 -2, i32 -3>
  store <4 x i32> %vec, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    binop_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(func, binop_instr.kind, binop_instr.data, symbols)
    assert f"  sub x15, x29, #{func.value_slots['lhs'].offset}" in lines
    assert f"  sub x17, x29, #{func.value_slots['vec'].offset}" in lines
    assert sum("movn w10" in line or "movz w10" in line for line in lines) == 4
    assert lines.count("  add w11, w9, w10") == 4


def test_self_backend_aarch64_compute_helper_supports_vector_literal_ptrtoint_lanes():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@a = global [2 x i32] zeroinitializer

define void @main(<4 x i32> %lhs, ptr %out) {
entry:
  %vec = add <4 x i32> %lhs, <i32 add (i32 ptrtoint (ptr @a to i32), i32 -3), i32 add (i32 ptrtoint (ptr @a to i32), i32 -3), i32 add (i32 ptrtoint (ptr @a to i32), i32 -3), i32 add (i32 ptrtoint (ptr @a to i32), i32 -3)>
  store <4 x i32> %vec, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    binop_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(func, binop_instr.kind, binop_instr.data, symbols)

    assert sum(line.startswith("  adrp x10, ") for line in lines) == 4
    assert lines.count("  sub x10, x10, #3") == 4
    assert lines.count("  add w11, w9, w10") == 4


def test_self_backend_aarch64_compute_helper_supports_vector_select_with_vector_i1_cond():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<4 x i1> %cond, <4 x i32> %lhs, ptr %out) {
entry:
  %vec = select <4 x i1> %cond, <4 x i32> %lhs, <4 x i32> zeroinitializer
  store <4 x i32> %vec, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    select_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(
        func, select_instr.kind, select_instr.data, symbols
    )
    assert f"  sub x15, x29, #{func.value_slots['cond'].offset}" in lines
    assert f"  sub x16, x29, #{func.value_slots['lhs'].offset}" in lines
    assert f"  sub x14, x29, #{func.value_slots['vec'].offset}" in lines
    assert lines.count("  cmp w9, #0") == 4
    assert lines.count("  csel w12, w10, w11, ne") == 4


def test_self_backend_aarch64_compute_helper_supports_aggregate_select_with_scalar_i1_cond():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(i1 %cond, <4 x i32> %lhs, <4 x i32> %rhs, ptr %out) {
entry:
  %vec = select i1 %cond, <4 x i32> %lhs, <4 x i32> %rhs
  store <4 x i32> %vec, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    select_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(
        func, select_instr.kind, select_instr.data, symbols
    )

    assert f"  ldurb w9, [x29, #-{func.value_slots['cond'].offset}]" in lines
    assert f"  sub x10, x29, #{func.value_slots['lhs'].offset}" in lines
    assert f"  sub x11, x29, #{func.value_slots['rhs'].offset}" in lines
    assert f"  sub x12, x29, #{func.value_slots['vec'].offset}" in lines
    assert "  cmp w9, #0" in lines
    assert "  csel x13, x10, x11, ne" in lines
    assert any(line.startswith("  ldr x") and "[x13]" in line for line in lines)


def test_self_backend_aarch64_compute_helper_supports_vector_icmp_result():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<4 x i32> %lhs, <4 x i32> %rhs, ptr %out) {
entry:
  %cmp = icmp sgt <4 x i32> %lhs, %rhs
  store <4 x i1> %cmp, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    icmp_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(func, icmp_instr.kind, icmp_instr.data, symbols)
    assert f"  sub x15, x29, #{func.value_slots['lhs'].offset}" in lines
    assert f"  sub x16, x29, #{func.value_slots['rhs'].offset}" in lines
    assert f"  sub x17, x29, #{func.value_slots['cmp'].offset}" in lines
    assert lines.count("  cmp w9, w10") == 4
    assert lines.count("  cset w11, gt") == 4


def test_self_backend_aarch64_compute_helper_sign_extends_narrow_vector_icmp():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<8 x i16> %lhs, ptr %out) {
entry:
  %cmp = icmp slt <8 x i16> %lhs, zeroinitializer
  store <8 x i1> %cmp, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    icmp_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(func, icmp_instr.kind, icmp_instr.data, symbols)
    assert lines.count("  sxth w9, w9") == 8
    assert lines.count("  sxth w10, w10") == 8
    assert lines.count("  cmp w9, w10") == 8
    assert lines.count("  cset w11, lt") == 8


def test_self_backend_aarch64_compute_helper_supports_vector_icmp_with_scalar_splat_rhs():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<4 x i32> %lhs, ptr %out) {
entry:
  %cmp = icmp sgt <4 x i32> %lhs, splat (i32 -1)
  store <4 x i1> %cmp, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    icmp_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(func, icmp_instr.kind, icmp_instr.data, symbols)
    assert f"  sub x15, x29, #{func.value_slots['lhs'].offset}" in lines
    assert sum("movn w10" in line or "movz w10" in line for line in lines) == 4
    assert lines.count("  cmp w9, w10") == 4


def test_self_backend_aarch64_compute_helper_supports_vector_fp_int_bitcast():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<4 x double> %src, ptr %out) {
entry:
  %bits = bitcast <4 x double> %src to <4 x i64>
  store <4 x i64> %bits, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    cast_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(func, cast_instr.kind, cast_instr.data, symbols)

    assert f"  sub x15, x29, #{func.value_slots['src'].offset}" in lines
    assert f"  sub x17, x29, #{func.value_slots['bits'].offset}" in lines
    assert lines.count("  fmov x10, d9") == 4
    assert sum(line.startswith("  str x10, [") for line in lines) == 4


def test_self_backend_aarch64_compute_helper_supports_shufflevector_even_lane_gather():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<8 x i32> %lhs, ptr %out) {
entry:
  %vec = shufflevector <8 x i32> %lhs, <8 x i32> poison, <4 x i32> <i32 0, i32 2, i32 4, i32 6>
  store <4 x i32> %vec, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    shuffle_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(
        func, shuffle_instr.kind, shuffle_instr.data, symbols
    )
    assert f"  sub x15, x29, #{func.value_slots['lhs'].offset}" in lines
    assert f"  sub x17, x29, #{func.value_slots['vec'].offset}" in lines
    assert "  add x12, x15, #8" in lines
    assert "  add x12, x15, #16" in lines
    assert "  add x12, x15, #24" in lines
    assert sum(line.startswith("  str w9, [") for line in lines) == 4


def test_self_backend_aarch64_compute_helper_supports_two_source_shufflevector():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<4 x i32> %lhs, <4 x i32> %rhs, ptr %out) {
entry:
  %vec = shufflevector <4 x i32> %lhs, <4 x i32> %rhs, <4 x i32> <i32 3, i32 4, i32 5, i32 6>
  store <4 x i32> %vec, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    shuffle_instr = func.blocks[0].instructions[0]

    lines = emit_compute_instruction(
        func, shuffle_instr.kind, shuffle_instr.data, symbols
    )

    assert f"  sub x15, x29, #{func.value_slots['lhs'].offset}" in lines
    assert f"  sub x16, x29, #{func.value_slots['rhs'].offset}" in lines
    assert f"  sub x17, x29, #{func.value_slots['vec'].offset}" in lines
    assert "  add x12, x15, #12" in lines
    assert "  add x12, x16, #4" in lines
    assert "  add x12, x16, #8" in lines
    assert sum(line.startswith("  str w9, [") for line in lines) == 4


def test_self_backend_aarch64_call_helper_supports_abs_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i32 %x) {
entry:
  %r = call i32 @llvm.abs.i32(i32 %x, i1 false)
  ret i32 %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert f"  ldur w9, [x29, #-{func.value_slots['x'].offset}]" in lines
    assert "  cmp w9, #0" in lines
    assert "  cneg w10, w9, mi" in lines
    assert f"  stur w10, [x29, #-{func.value_slots['r'].offset}]" in lines

    i16_ir = """
target triple = "arm64-apple-darwin23.6.0"

define i16 @main(i16 %x) {
entry:
  %r = call i16 @llvm.abs.i16(i16 %x, i1 false)
  ret i16 %r
}
""".strip()
    module = parse_self_backend_module(i16_ir)
    symbols = prepare_module_symbols(
        i16_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert f"  ldurh w9, [x29, #-{func.value_slots['x'].offset}]" in lines
    assert "  sxth w9, w9" in lines
    assert "  cmp w9, #0" in lines
    assert "  cneg w10, w9, mi" in lines
    assert f"  sturh w10, [x29, #-{func.value_slots['r'].offset}]" in lines


def test_self_backend_aarch64_call_helper_supports_copysign_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define double @main(double %x, double %y) {
entry:
  %r = call double @llvm.copysign.f64(double %x, double %y)
  ret double %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert f"  ldur d9, [x29, #-{func.value_slots['x'].offset}]" in lines
    assert f"  ldur d10, [x29, #-{func.value_slots['y'].offset}]" in lines
    assert "  fmov x11, d9" in lines
    assert "  fmov x12, d10" in lines
    assert "  and x11, x11, #0x7fffffffffffffff" in lines
    assert "  and x12, x12, #0x8000000000000000" in lines
    assert "  orr x11, x11, x12" in lines
    assert "  fmov d11, x11" in lines
    assert f"  stur d11, [x29, #-{func.value_slots['r'].offset}]" in lines


def test_self_backend_aarch64_call_helper_supports_fabs_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define double @main(double %x) {
entry:
  %r = call double @llvm.fabs.f64(double %x)
  ret double %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert f"  ldur d9, [x29, #-{func.value_slots['x'].offset}]" in lines
    assert "  fabs d11, d9" in lines
    assert f"  stur d11, [x29, #-{func.value_slots['r'].offset}]" in lines


def test_self_backend_aarch64_call_helper_supports_floor_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define double @main(double %x) {
entry:
  %r = call double @llvm.floor.f64(double %x)
  ret double %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert f"  ldur d9, [x29, #-{func.value_slots['x'].offset}]" in lines
    assert "  frintm d11, d9" in lines
    assert f"  stur d11, [x29, #-{func.value_slots['r'].offset}]" in lines


def test_self_backend_aarch64_call_helper_supports_sqrt_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define double @main(double %x) {
entry:
  %r = call double @llvm.sqrt.f64(double %x)
  ret double %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert f"  ldur d9, [x29, #-{func.value_slots['x'].offset}]" in lines
    assert "  fsqrt d11, d9" in lines
    assert f"  stur d11, [x29, #-{func.value_slots['r'].offset}]" in lines


def test_self_backend_aarch64_call_helper_supports_fpclass_zero_masks():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i1 @main(double %x) {
entry:
  %r = call i1 @llvm.is.fpclass.f64(double %x, i32 96)
  ret i1 %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert f"  ldur d9, [x29, #-{func.value_slots['x'].offset}]" in lines
    assert "  fmov x12, d9" in lines
    assert "  cmp x12, x13" in lines
    assert "  cmp x12, #0" in lines
    assert f"  sturb w11, [x29, #-{func.value_slots['r'].offset}]" in lines


def test_self_backend_aarch64_call_helper_supports_fpclass_positive_finite_mask():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i1 @main(double %x) {
entry:
  %r = call i1 @llvm.is.fpclass.f64(double %x, i32 448)
  ret i1 %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert "  tst x12, x13" in lines
    assert "  and x15, x12, x13" in lines
    assert "  cset w15, ne" in lines
    assert "  and w14, w14, w15" in lines


def test_self_backend_aarch64_call_helper_supports_vector_reduce_mul_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @llvm.vector.reduce.mul.v4i32(<4 x i32>)

define i32 @main(<4 x i32> %vec) {
entry:
  %r = call i32 @llvm.vector.reduce.mul.v4i32(<4 x i32> %vec)
  ret i32 %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert f"  sub x15, x29, #{func.value_slots['vec'].offset}" in lines
    assert "  movz w11, #1" in lines
    assert lines.count("  mul w11, w11, w10") == 4
    assert f"  stur w11, [x29, #-{func.value_slots['r'].offset}]" in lines


def test_self_backend_aarch64_call_helper_supports_vector_reduce_or_and_umax_intrinsics():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @llvm.vector.reduce.or.v4i32(<4 x i32>)
declare i64 @llvm.vector.reduce.umax.v2i64(<2 x i64>)

define i64 @main(<4 x i32> %vec32, <2 x i64> %vec64) {
entry:
  %or = call i32 @llvm.vector.reduce.or.v4i32(<4 x i32> %vec32)
  %max = call i64 @llvm.vector.reduce.umax.v2i64(<2 x i64> %vec64)
  %ext = zext i32 %or to i64
  %sum = add i64 %ext, %max
  ret i64 %sum
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    or_instr, umax_instr, _cast_instr, _sum_instr = func.blocks[0].instructions

    or_lines = emit_call_instruction(func, *or_instr.data, symbols)
    umax_lines = emit_call_instruction(func, *umax_instr.data, symbols)

    assert or_lines.count("  orr w11, w11, w10") == 4
    assert f"  stur w11, [x29, #-{func.value_slots['or'].offset}]" in or_lines
    assert umax_lines.count("  cmp x11, x10") == 2
    assert umax_lines.count("  csel x11, x11, x10, hs") == 2
    assert f"  stur x11, [x29, #-{func.value_slots['max'].offset}]" in umax_lines


def test_self_backend_aarch64_call_helper_supports_ucmp_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @llvm.ucmp.i32.i64(i64, i64)

define i32 @main(i64 %lhs, i64 %rhs) {
entry:
  %r = call i32 @llvm.ucmp.i32.i64(i64 %lhs, i64 %rhs)
  ret i32 %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert "  cmp x9, x10" in lines
    assert "  cset w11, hi" in lines
    assert "  cset w12, lo" in lines
    assert "  sub w11, w11, w12" in lines
    assert f"  stur w11, [x29, #-{func.value_slots['r'].offset}]" in lines


def test_self_backend_aarch64_compute_helper_accepts_i32_shuffle_mask_for_broadcast():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(i16 %x, ptr %out) {
entry:
  %ins = insertelement <8 x i16> poison, i16 %x, i64 0
  %spl = shufflevector <8 x i16> %ins, <8 x i16> poison, <8 x i32> zeroinitializer
  store <8 x i16> %spl, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    _ins_instr, shuffle_instr, _store_instr = func.blocks[0].instructions

    shuffle_lines = emit_compute_instruction(
        func, shuffle_instr.kind, shuffle_instr.data, symbols
    )
    assert shuffle_lines[:2] == [
        f"  sub x15, x29, #{func.value_slots['ins'].offset}",
        f"  sub x17, x29, #{func.value_slots['spl'].offset}",
    ]
    assert sum(line.startswith("  strh w9, [") for line in shuffle_lines) == 8


def test_self_backend_aarch64_compute_helper_dispatches_vector_cast_path():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(<4 x i16> %lhs, ptr %out) {
entry:
  %wide = sext <4 x i16> %lhs to <4 x i32>
  %narrow = trunc <4 x i32> %wide to <4 x i16>
  store <4 x i16> %narrow, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    sext_instr, trunc_instr, _store_instr = func.blocks[0].instructions

    sext_lines = emit_compute_instruction(
        func, sext_instr.kind, sext_instr.data, symbols
    )
    trunc_lines = emit_compute_instruction(
        func, trunc_instr.kind, trunc_instr.data, symbols
    )

    lhs_slot = func.value_slots["lhs"]
    wide_slot = func.value_slots["wide"]
    narrow_slot = func.value_slots["narrow"]
    assert sext_lines[:2] == [
        f"  sub x15, x29, #{lhs_slot.offset}",
        f"  sub x17, x29, #{wide_slot.offset}",
    ]
    assert sext_lines.count("  sxth w10, w9") == 4
    assert sum(line.startswith("  str w10, [") for line in sext_lines) == 4
    assert trunc_lines[:2] == [
        f"  sub x15, x29, #{wide_slot.offset}",
        f"  sub x17, x29, #{narrow_slot.offset}",
    ]
    assert trunc_lines.count("  mov w10, w9") == 4
    assert sum(line.startswith("  strh w10, [") for line in trunc_lines) == 4


def test_self_backend_aarch64_call_helpers_cover_vector_usub_sat_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare <4 x i32> @llvm.usub.sat.v4i32(<4 x i32>, <4 x i32>)

define void @main(<4 x i32> %lhs, <4 x i32> %rhs, ptr %out) {
entry:
  %sum = call <4 x i32> @llvm.usub.sat.v4i32(<4 x i32> %lhs, <4 x i32> %rhs)
  store <4 x i32> %sum, ptr %out
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    lines = emit_call_instruction(
        func,
        *call_instr.data,
        module_symbols=symbols,
    )

    assert lines[:3] == [
        "  sub x15, x29, #16",
        "  sub x16, x29, #32",
        "  sub x17, x29, #56",
    ]
    assert lines.count("  subs w11, w9, w10") == 4
    assert lines.count("  csel w11, w11, wzr, hs") == 4
    assert sum(line.startswith("  str w11, [") for line in lines) == 4


def test_self_backend_aarch64_call_helpers_cover_scalar_usub_sat_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @llvm.usub.sat.i32(i32, i32)

define i32 @main(i32 %lhs, i32 %rhs) {
entry:
  %sum = call i32 @llvm.usub.sat.i32(i32 %lhs, i32 %rhs)
  ret i32 %sum
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    assert emit_call_instruction(
        func,
        *call_instr.data,
        module_symbols=symbols,
    ) == [
        f"  ldur w9, [x29, #-{func.value_slots['lhs'].offset}]",
        f"  ldur w10, [x29, #-{func.value_slots['rhs'].offset}]",
        "  subs w11, w9, w10",
        "  csel w11, w11, wzr, hs",
        f"  stur w11, [x29, #-{func.value_slots['sum'].offset}]",
    ]


def test_self_backend_aarch64_call_helpers_cover_scalar_uadd_sat_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @llvm.uadd.sat.i32(i32, i32)

define i32 @main(i32 %lhs, i32 %rhs) {
entry:
  %sum = call i32 @llvm.uadd.sat.i32(i32 %lhs, i32 %rhs)
  ret i32 %sum
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    assert emit_call_instruction(
        func,
        *call_instr.data,
        module_symbols=symbols,
    ) == [
        f"  ldur w9, [x29, #-{func.value_slots['lhs'].offset}]",
        f"  ldur w10, [x29, #-{func.value_slots['rhs'].offset}]",
        "  adds w11, w9, w10",
        "  csinv w11, w11, wzr, lo",
        f"  stur w11, [x29, #-{func.value_slots['sum'].offset}]",
    ]


def test_self_backend_aarch64_call_helpers_cover_umul_overflow_and_assume_intrinsics():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare void @llvm.assume(i1)
declare { i64, i1 } @llvm.umul.with.overflow.i64(i64, i64)

define i64 @main(i64 %lhs, i64 %rhs, i1 %cond) {
entry:
  call void @llvm.assume(i1 %cond)
  %mul = call { i64, i1 } @llvm.umul.with.overflow.i64(i64 %lhs, i64 %rhs)
  %value = extractvalue { i64, i1 } %mul, 0
  ret i64 %value
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assume_instr, call_instr, _extract_instr = func.blocks[0].instructions

    assert (
        emit_call_instruction(
            func,
            *assume_instr.data,
            module_symbols=symbols,
        )
        == []
    )
    assert emit_call_instruction(
        func,
        *call_instr.data,
        module_symbols=symbols,
    ) == [
        f"  ldur x9, [x29, #-{func.value_slots['lhs'].offset}]",
        f"  ldur x10, [x29, #-{func.value_slots['rhs'].offset}]",
        "  mul x11, x9, x10",
        "  umulh x12, x9, x10",
        "  cmp x12, #0",
        "  cset w12, ne",
        f"  stur x11, [x29, #-{func.value_slots['mul'].offset}]",
        f"  stur x12, [x29, #-{func.value_slots['mul'].offset - 8}]",
    ]

    i32_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare { i32, i1 } @llvm.uadd.with.overflow.i32(i32, i32)
declare { i32, i1 } @llvm.umul.with.overflow.i32(i32, i32)

define i32 @main(i32 %lhs, i32 %rhs) {
entry:
  %add = call { i32, i1 } @llvm.uadd.with.overflow.i32(i32 %lhs, i32 %rhs)
  %mul = call { i32, i1 } @llvm.umul.with.overflow.i32(i32 %lhs, i32 %rhs)
  %addvalue = extractvalue { i32, i1 } %add, 0
  %value = extractvalue { i32, i1 } %mul, 0
  %sum = add i32 %addvalue, %value
  ret i32 %sum
}
""".strip()
    module = parse_self_backend_module(i32_ir)
    symbols = prepare_module_symbols(
        i32_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    add_instr, mul_instr, _add_extract_instr, _mul_extract_instr, _sum_instr = (
        func.blocks[0].instructions
    )
    add_lines = emit_call_instruction(func, *add_instr.data, symbols)
    mul_lines = emit_call_instruction(func, *mul_instr.data, symbols)
    assert "  adds w11, w9, w10" in add_lines
    assert "  cset w12, hs" in add_lines
    assert "  orr x11, x11, x12, lsl #32" in add_lines
    assert "  umull x11, w9, w10" in mul_lines
    assert "  lsr x12, x11, #32" in mul_lines
    assert "  uxtw x11, w11" in mul_lines
    assert "  orr x11, x11, x12, lsl #32" in mul_lines

    smul_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare { i64, i1 } @llvm.smul.with.overflow.i64(i64, i64)
declare { i32, i1 } @llvm.smul.with.overflow.i32(i32, i32)

define i64 @main(i64 %lhs64, i64 %rhs64, i32 %lhs32, i32 %rhs32) {
entry:
  %mul64 = call { i64, i1 } @llvm.smul.with.overflow.i64(i64 %lhs64, i64 %rhs64)
  %mul32 = call { i32, i1 } @llvm.smul.with.overflow.i32(i32 %lhs32, i32 %rhs32)
  %value64 = extractvalue { i64, i1 } %mul64, 0
  %value32 = extractvalue { i32, i1 } %mul32, 0
  %ext = sext i32 %value32 to i64
  %sum = add i64 %value64, %ext
  ret i64 %sum
}
""".strip()
    module = parse_self_backend_module(smul_ir)
    symbols = prepare_module_symbols(
        smul_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    (
        smul64_instr,
        smul32_instr,
        _extract64_instr,
        _extract32_instr,
        _cast_instr,
        _sum_instr,
    ) = func.blocks[0].instructions
    smul64_lines = emit_call_instruction(func, *smul64_instr.data, symbols)
    smul32_lines = emit_call_instruction(func, *smul32_instr.data, symbols)
    assert "  mul x11, x9, x10" in smul64_lines
    assert "  smulh x12, x9, x10" in smul64_lines
    assert "  asr x13, x11, #63" in smul64_lines
    assert "  cmp x12, x13" in smul64_lines
    assert "  smull x11, w9, w10" in smul32_lines
    assert "  sxtw x13, w11" in smul32_lines
    assert "  cmp x11, x13" in smul32_lines
    assert "  orr x11, x11, x12, lsl #32" in smul32_lines


def test_self_backend_aarch64_call_helpers_cover_fshl_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @llvm.fshl.i32(i32, i32, i32)

define i32 @main(i32 %lhs, i32 %rhs, i32 %shift) {
entry:
  %rot = call i32 @llvm.fshl.i32(i32 %lhs, i32 %rhs, i32 %shift)
  ret i32 %rot
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    assert emit_call_instruction(
        func,
        *call_instr.data,
        module_symbols=symbols,
    ) == [
        f"  ldur w9, [x29, #-{func.value_slots['lhs'].offset}]",
        f"  ldur w10, [x29, #-{func.value_slots['rhs'].offset}]",
        f"  ldur w11, [x29, #-{func.value_slots['shift'].offset}]",
        "  and w11, w11, #31",
        "  neg w12, w11",
        "  and w12, w12, #31",
        "  lslv w14, w9, w11",
        "  lsrv w13, w10, w12",
        "  orr w14, w14, w13",
        f"  stur w14, [x29, #-{func.value_slots['rot'].offset}]",
    ]

    vector_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare <2 x i64> @llvm.fshl.v2i64(<2 x i64>, <2 x i64>, <2 x i64>)
declare <8 x i16> @llvm.fshl.v8i16(<8 x i16>, <8 x i16>, <8 x i16>)

define void @main(<2 x i64> %q, <8 x i16> %h, ptr %outq, ptr %outh) {
entry:
  %rq = call <2 x i64> @llvm.fshl.v2i64(<2 x i64> %q, <2 x i64> %q, <2 x i64> splat (i64 56))
  %rh = call <8 x i16> @llvm.fshl.v8i16(<8 x i16> %h, <8 x i16> %h, <8 x i16> splat (i16 7))
  store <2 x i64> %rq, ptr %outq
  store <8 x i16> %rh, ptr %outh
  ret void
}
""".strip()
    module = parse_self_backend_module(vector_ir)
    symbols = prepare_module_symbols(
        vector_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    func.value_slots["q"] = SlotInfo(5000, func.value_slots["q"].type)
    func.value_slots["rq"] = SlotInfo(5032, func.value_slots["rq"].type)
    fshl_q_instr, fshl_h_instr, _store_q_instr, _store_h_instr = func.blocks[
        0
    ].instructions
    q_lines = emit_call_instruction(func, *fshl_q_instr.data, symbols)
    h_lines = emit_call_instruction(func, *fshl_h_instr.data, symbols)

    assert "  sub x16, x29, x15" not in q_lines
    assert "  sub x16, x29, x14" in q_lines
    assert q_lines.count("  and x11, x11, #63") == 2
    assert q_lines.count("  lslv x13, x9, x11") == 2
    assert q_lines.count("  lsrv x12, x10, x12") == 2
    assert h_lines.count("  and w11, w11, #15") == 8
    assert h_lines.count("  lslv w13, w9, w11") == 8
    assert h_lines.count("  lsrv w12, w10, w12") == 8

    i16_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare i16 @llvm.fshl.i16(i16, i16, i16)

define i16 @main(i16 %lhs, i16 %rhs, i16 %shift) {
entry:
  %rot = call i16 @llvm.fshl.i16(i16 %lhs, i16 %rhs, i16 %shift)
  ret i16 %rot
}
""".strip()
    module = parse_self_backend_module(i16_ir)
    symbols = prepare_module_symbols(
        i16_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]
    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert "  and w11, w11, #15" in lines
    assert "  lslv w14, w9, w11" in lines
    assert "  lsrv w13, w10, w12" in lines
    assert f"  sturh w14, [x29, #-{func.value_slots['rot'].offset}]" in lines


def test_self_backend_aarch64_call_helpers_cover_fshr_intrinsic():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

declare i32 @llvm.fshr.i32(i32, i32, i32)

define i32 @main(i32 %lhs, i32 %rhs, i32 %shift) {
entry:
  %rot = call i32 @llvm.fshr.i32(i32 %lhs, i32 %rhs, i32 %shift)
  ret i32 %rot
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]

    assert emit_call_instruction(
        func,
        *call_instr.data,
        module_symbols=symbols,
    ) == [
        f"  ldur w9, [x29, #-{func.value_slots['lhs'].offset}]",
        f"  ldur w10, [x29, #-{func.value_slots['rhs'].offset}]",
        f"  ldur w11, [x29, #-{func.value_slots['shift'].offset}]",
        "  and w11, w11, #31",
        "  neg w12, w11",
        "  and w12, w12, #31",
        "  lsrv w14, w10, w11",
        "  lslv w13, w9, w12",
        "  orr w14, w14, w13",
        f"  stur w14, [x29, #-{func.value_slots['rot'].offset}]",
    ]

    i16_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare i16 @llvm.fshr.i16(i16, i16, i16)

define i16 @main(i16 %lhs, i16 %rhs, i16 %shift) {
entry:
  %rot = call i16 @llvm.fshr.i16(i16 %lhs, i16 %rhs, i16 %shift)
  ret i16 %rot
}
""".strip()
    module = parse_self_backend_module(i16_ir)
    symbols = prepare_module_symbols(
        i16_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    call_instr = func.blocks[0].instructions[0]
    lines = emit_call_instruction(func, *call_instr.data, symbols)
    assert "  and w11, w11, #15" in lines
    assert "  lsrv w14, w10, w11" in lines
    assert "  lslv w13, w9, w12" in lines
    assert f"  sturh w14, [x29, #-{func.value_slots['rot'].offset}]" in lines


def test_self_backend_aarch64_compute_helper_dispatches_select_path():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i1 %cond, i32 %lhs, i32 %rhs) {
entry:
  %sel = select i1 %cond, i32 %lhs, i32 %rhs
  ret i32 %sel
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    assert emit_compute_instruction(
        func,
        "select",
        ("sel", TypeDesc("int", 32), "cond", "lhs", "rhs"),
        symbols,
    ) == [
        f"  ldur w10, [x29, #-{func.value_slots['lhs'].offset}]",
        f"  ldur w11, [x29, #-{func.value_slots['rhs'].offset}]",
        f"  ldurb w9, [x29, #-{func.value_slots['cond'].offset}]",
        "  cmp w9, #0",
        "  csel w12, w10, w11, ne",
        f"  stur w12, [x29, #-{func.value_slots['sel'].offset}]",
    ]


def test_self_backend_aarch64_compute_helper_dispatches_freeze_path():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i32 %x) {
entry:
  %tmp = freeze i32 %x
  ret i32 %tmp
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    assert emit_compute_instruction(
        func,
        "freeze",
        ("tmp", TypeDesc("int", 32), "x"),
        symbols,
    ) == [
        f"  ldur w10, [x29, #-{func.value_slots['x'].offset}]",
        f"  stur w10, [x29, #-{func.value_slots['tmp'].offset}]",
    ]


def test_self_backend_aarch64_memory_helper_dispatches_alloca_store_and_load_paths():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main() {
entry:
  %p = alloca i32
  store i32 7, ptr %p
  %r = load i32, ptr %p
  ret i32 %r
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    assert emit_memory_instruction(func, "alloca", (), symbols) == []
    assert emit_memory_instruction(
        func,
        "store",
        (TypeDesc("int", 32), "7", TypeDesc("ptr", pointee=TypeDesc("int", 32)), "p"),
        symbols,
    ) == [
        "  movz w9, #7, lsl #0",
        f"  stur w9, [x29, #-{func.alloca_slots['p'].offset}]",
    ]
    assert emit_memory_instruction(
        func,
        "load",
        ("r", TypeDesc("int", 32), TypeDesc("ptr", pointee=TypeDesc("int", 32)), "p"),
        symbols,
    ) == [
        f"  ldur w10, [x29, #-{func.alloca_slots['p'].offset}]",
        f"  stur w10, [x29, #-{func.value_slots['r'].offset}]",
    ]

    vec4f64 = TypeDesc("array", count=4, elem=TypeDesc("fp", 64))
    assert store_large_aggregate_literal_to_address(
        vec4f64,
        "<double 0.000000e+00, double 1.000000e+00, double 2.000000e+00, double 3.000000e+00>",
        "x9",
    ) == [
        "  movz x14, #0",
        "  str x14, [x9]",
        "  add x16, x9, #8",
        "  movz x14, #16368, lsl #48",
        "  str x14, [x16]",
        "  add x16, x9, #16",
        "  movz x14, #16384, lsl #48",
        "  str x14, [x16]",
        "  add x16, x9, #24",
        "  movz x14, #16392, lsl #48",
        "  str x14, [x16]",
    ]

    pointer_pair = TypeDesc(
        "struct",
        fields=(
            TypeDesc("ptr", pointee=TypeDesc("void")),
            TypeDesc("ptr", pointee=TypeDesc("void")),
        ),
    )
    pointer_ir = """
target triple = "arm64-apple-darwin23.6.0"

declare void @target()
""".strip()
    pointer_symbols = prepare_module_symbols(pointer_ir, [], [])
    lines = store_large_aggregate_literal_to_address(
        pointer_pair,
        "{ ptr @target, ptr null }",
        "x0",
        data_reg_64="x12",
        data_reg_32="w12",
        module_symbols=pointer_symbols,
    )
    assert "  adrp x12, _target@GOTPAGE" in lines
    assert "  ldr x12, [x12, _target@GOTPAGEOFF]" in lines
    assert "  str x12, [x0]" in lines
    assert "  add x16, x0, #8" in lines
    assert "  str x12, [x16]" in lines

    literal_store_ir = """
target triple = "arm64-apple-darwin23.6.0"

@gd = global [4 x double] zeroinitializer

define void @seed() {
entry:
  store <4 x double> <double 0.000000e+00, double 1.000000e+00, double 2.000000e+00, double 3.000000e+00>, ptr @gd
  ret void
}
""".strip()
    module = parse_self_backend_module(literal_store_ir)
    symbols = prepare_module_symbols(
        literal_store_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    store_instr = func.blocks[0].instructions[0]
    assert emit_memory_instruction(func, "store", store_instr.data, symbols) == [
        f"  adrp x9, {asm_symbol('gd', symbols)}@PAGE",
        f"  add x9, x9, {asm_symbol('gd', symbols)}@PAGEOFF",
        "  movz x14, #0",
        "  str x14, [x9]",
        "  add x16, x9, #8",
        "  movz x14, #16368, lsl #48",
        "  str x14, [x16]",
        "  add x16, x9, #16",
        "  movz x14, #16384, lsl #48",
        "  str x14, [x16]",
        "  add x16, x9, #24",
        "  movz x14, #16392, lsl #48",
        "  str x14, [x16]",
    ]


def test_self_backend_aarch64_compute_helper_supports_vector_fcmp():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define void @main() {
entry:
  %a = alloca <4 x float>
  %b = alloca <4 x float>
  %va = load <4 x float>, ptr %a
  %vb = load <4 x float>, ptr %b
  %cmp = fcmp oeq <4 x float> %va, %vb
  %wide = zext <4 x i1> %cmp to <4 x i32>
  ret void
}
""".strip()
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    fcmp_instr = func.blocks[0].instructions[4]

    assert fcmp_instr.kind == "fcmp"
    lines = emit_compute_instruction(func, fcmp_instr.kind, fcmp_instr.data, symbols)

    assert any(line == "  fcmp s9, s10" for line in lines)
    assert lines.count("  cset w11, eq") == 4
    assert any(line == "  strb w11, [x17]" for line in lines)


def test_self_backend_aarch64_prologue_helper_covers_arg_spills_and_hidden_sret():
    stack_arg_ir = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i32 %a0, i32 %a1, i32 %a2, i32 %a3, i32 %a4, i32 %a5, i32 %a6, i32 %a7, i32 %a8) {
entry:
  %t0 = add i32 %a0, %a7
  %t1 = add i32 %t0, %a8
  ret i32 %t1
}
""".strip()
    module = parse_self_backend_module(stack_arg_ir)
    symbols = prepare_module_symbols(
        stack_arg_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    prologue = emit_function_prologue(func, symbols)
    assert prologue[:6] == [
        "",
        ".p2align 2",
        ".globl _main",
        "_main:",
        "  stp x29, x30, [sp, #-16]!",
        "  mov x29, sp",
    ]
    assert f"  stur w0, [x29, #-{func.value_slots['a0'].offset}]" in prologue
    assert f"  stur w7, [x29, #-{func.value_slots['a7'].offset}]" in prologue
    assert prologue[-3:] == [
        "  add x12, x29, #16",
        "  ldr w11, [x12]",
        f"  stur w11, [x29, #-{func.value_slots['a8'].offset}]",
    ]

    sret_ir = """
target triple = "arm64-apple-darwin23.6.0"
%S = type { i64, i64, i64 }

define %S @mk(i32 %x) {
entry:
  %tmp = add i32 %x, 1
  ret %S zeroinitializer
}
""".strip()
    module = parse_self_backend_module(sret_ir)
    symbols = prepare_module_symbols(
        sret_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda ty: ty.is_struct)
    prologue = emit_function_prologue(func, symbols)
    assert f"  stur x8, [x29, #-{func.hidden_sret_slot.offset}]" in prologue
    assert f"  stur w0, [x29, #-{func.value_slots['x'].offset}]" in prologue


def test_self_backend_aarch64_flow_helpers_cover_bitcount_and_phi_assignment():
    bitcount_ir = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i32 %x) {
entry:
  %r = call i32 (i32, i1) @llvm.ctlz.i32(i32 %x, i1 0)
  ret i32 %r
}
    """.strip()
    module = parse_self_backend_module(bitcount_ir)
    symbols = prepare_module_symbols(
        bitcount_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert emit_bit_count_intrinsic_call(
        func,
        "r",
        TypeDesc("int", 32),
        "llvm.ctlz.i32",
        ((TypeDesc("int", 32), "x"), (TypeDesc("int", 1), "0")),
        symbols,
    ) == [
        f"  ldur w9, [x29, #-{func.value_slots['x'].offset}]",
        "  clz w11, w9",
        f"  stur w11, [x29, #-{func.value_slots['r'].offset}]",
    ]
    assert emit_bit_count_intrinsic_call(
        func,
        "r",
        TypeDesc("int", 32),
        "llvm.ctpop.i32",
        ((TypeDesc("int", 32), "x"),),
        symbols,
    ) == [
        f"  ldur w9, [x29, #-{func.value_slots['x'].offset}]",
        "  mov w10, w9",
        "  fmov d10, x10",
        "  cnt v10.8b, v10.8b",
        "  addv b10, v10.8b",
        "  umov w11, v10.b[0]",
        f"  stur w11, [x29, #-{func.value_slots['r'].offset}]",
    ]

    phi_ir = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i1 %cond) {
entry:
  br i1 %cond, label %t, label %f

t:
  %a = add i32 1, 2
  br label %merge

f:
  %b = add i32 3, 4
  br label %merge

merge:
  %phi = phi i32 [%a, %t], [%b, %f]
  ret i32 %phi
}
""".strip()
    module = parse_self_backend_module(phi_ir)
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    phi_lines = emit_phi_assignments(
        func, source_block="t", target_block="merge", module_symbols=symbols
    )
    assert phi_lines[0] == "  sub sp, sp, #16"
    assert f"  ldur w9, [x29, #-{func.value_slots['a'].offset}]" in phi_lines
    assert f"  stur w9, [x29, #-{func.value_slots['phi'].offset}]" in phi_lines
    assert phi_lines[-1] == "  add sp, sp, #16"


def test_self_backend_aarch64_phi_assignments_preserve_parallel_copy_sources():
    phi_ir = """
target triple = "arm64-apple-darwin23.6.0"

define ptr @main(ptr %p) {
entry:
  br label %loop

loop:
  %cur = phi ptr [%p, %entry], [%next_keyword, %step]
  %opt = phi ptr [%p, %entry], [%next_opt, %step]
  ret ptr %cur

step:
  %next_opt = getelementptr i8, ptr %opt, i64 56
  %next_keyword = load ptr, ptr %next_opt
  br label %loop
}
""".strip()
    module = parse_self_backend_module(phi_ir)
    symbols = prepare_module_symbols(
        phi_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    lines = emit_phi_assignments(
        func, source_block="step", target_block="loop", module_symbols=symbols
    )
    assert lines[0] == "  sub sp, sp, #16"
    assert f"  ldur x9, [x29, #-{func.value_slots['next_keyword'].offset}]" in lines
    assert f"  ldur x9, [x29, #-{func.value_slots['next_opt'].offset}]" in lines
    cur_store = f"  stur x9, [x29, #-{func.value_slots['cur'].offset}]"
    opt_store = f"  stur x9, [x29, #-{func.value_slots['opt'].offset}]"
    assert cur_store in lines
    assert opt_store in lines
    assert lines[-1] == "  add sp, sp, #16"


def test_self_backend_aarch64_phi_assignments_support_large_aggregate_literals():
    phi_ir = """
target triple = "arm64-apple-darwin23.6.0"

define void @main(ptr %out) {
entry:
  br label %loop

loop:
  %vec = phi <16 x i32> [ <i32 0, i32 1, i32 2, i32 3, i32 4, i32 5, i32 6, i32 7, i32 8, i32 9, i32 10, i32 11, i32 12, i32 13, i32 14, i32 15>, %entry ], [ %next, %step ]
  store <16 x i32> %vec, ptr %out
  ret void

step:
  %next = add <16 x i32> %vec, splat (i32 16)
  br label %loop
}
""".strip()
    module = parse_self_backend_module(phi_ir)
    symbols = prepare_module_symbols(
        phi_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    lines = emit_phi_assignments(
        func, source_block="entry", target_block="loop", module_symbols=symbols
    )

    assert "  movz x14, #14, lsl #0" in lines
    assert "  movk x14, #15, lsl #32" in lines
    assert any(line.startswith("  str x14, [") for line in lines)
    assert f"  sub x14, x29, #{func.value_slots['vec'].offset}" in lines
    assert f"  sub x13, x29, #{func.value_slots['vec'].offset}" not in lines


def test_self_backend_aarch64_phi_assignments_keep_temp_address_alive_for_large_slots():
    args = ", ".join(f"ptr %a{i}" for i in range(40))
    phi_lines = "\n".join(
        f"  %cur{i} = phi ptr [%a{i}, %entry], [%next{i}, %step]" for i in range(40)
    )
    next_lines = "\n".join(f"  %next{i} = bitcast ptr %a{i} to ptr" for i in range(40))
    phi_ir = f"""
target triple = "arm64-apple-darwin23.6.0"

define ptr @main({args}) {{
entry:
  br label %loop

loop:
{phi_lines}
  ret ptr %cur39

step:
{next_lines}
  br label %loop
}}
""".strip()
    module = parse_self_backend_module(phi_ir)
    symbols = prepare_module_symbols(
        phi_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)

    assert func.value_slots["next39"].offset > 255
    lines = emit_phi_assignments(
        func, source_block="step", target_block="loop", module_symbols=symbols
    )

    assert "  mov x15, sp" not in lines
    assert "  mov x13, sp" in lines
    assert f"  sub x15, x29, #{func.value_slots['next39'].offset}" in lines
    assert "  str x9, [x13]" in lines
    assert f"  sub x15, x29, #{func.value_slots['cur39'].offset}" in lines
    assert "  stur x9, [x15]" in lines
    assert lines[-1] == "  add sp, sp, #16"


def test_self_backend_aarch64_terminator_helpers_cover_epilogue_branch_and_switch():
    branch_ir = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main(i1 %cond) {
entry:
  br i1 %cond, label %t, label %f

t:
  %a = add i32 1, 2
  br label %merge

f:
  %b = add i32 3, 4
  br label %merge

merge:
  %phi = phi i32 [%a, %t], [%b, %f]
  ret i32 %phi
}
""".strip()
    module = parse_self_backend_module(branch_ir)
    symbols = prepare_module_symbols(
        branch_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    func.frame_size = 32
    assert emit_epilogue(func) == [
        "  add sp, sp, #32",
        "  ldp x29, x30, [sp], #16",
        "  ret",
    ]
    assert emit_branch_terminator(
        func,
        source_block="t",
        target="merge",
        module_symbols=symbols,
    ) == [
        "  sub sp, sp, #16",
        "  mov x13, sp",
        f"  ldur w9, [x29, #-{func.value_slots['a'].offset}]",
        "  str w9, [x13]",
        "  mov x13, sp",
        "  ldr w9, [x13]",
        f"  stur w9, [x29, #-{func.value_slots['phi'].offset}]",
        "  add sp, sp, #16",
        "  b L_main_merge",
    ]
    assert emit_cond_branch_terminator(
        func,
        block_name="entry",
        cond_name="cond",
        true_target="t",
        false_target="f",
        module_symbols=symbols,
    ) == [
        f"  ldurb w9, [x29, #-{func.value_slots['cond'].offset}]",
        "  cbz w9, L_main_entry_to_f",
        "  b L_main_t",
        "L_main_entry_to_f:",
        "  b L_main_f",
    ]

    switch_ir = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main() {
bb0:
  switch i32 2, label %switch_default [
    i32 1, label %switch_case
    i32 2, label %switch_case.1
  ]

switch_default:
  ret i32 1

switch_case:
  ret i32 2

switch_case.1:
  ret i32 3
}
""".strip()
    module = parse_self_backend_module(switch_ir)
    symbols = prepare_module_symbols(
        switch_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert emit_switch_terminator(
        func,
        block_name="bb0",
        value_type=TypeDesc("int", 32),
        value="2",
        default_target="switch_default",
        cases=((1, "switch_case"), (2, "switch_case.1")),
        module_symbols=symbols,
    ) == [
        "  movz w9, #2, lsl #0",
        "  movz w10, #1, lsl #0",
        "  cmp w9, w10",
        "  b.eq L_main_bb0_to_switch_case",
        "  movz w10, #2, lsl #0",
        "  cmp w9, w10",
        "  b.eq L_main_bb0_to_switch_casedot1",
        "  b L_main_bb0_to_switch_default",
        "L_main_bb0_to_switch_case:",
        "  b L_main_switch_case",
        "L_main_bb0_to_switch_casedot1:",
        "  b L_main_switch_casedot1",
        "L_main_bb0_to_switch_default:",
        "  b L_main_switch_default",
    ]
    assert emit_unreachable_terminator() == ["  brk #0"]


def test_self_backend_aarch64_return_helper_covers_scalar_and_hidden_sret_paths():
    scalar_ir = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main() {
entry:
  ret i32 7
}
""".strip()
    module = parse_self_backend_module(scalar_ir)
    symbols = prepare_module_symbols(
        scalar_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    assert emit_return_terminator(
        func,
        ret_type=TypeDesc("int", 32),
        value="7",
        module_symbols=symbols,
    ) == [
        "  movz w0, #7, lsl #0",
        "  ldp x29, x30, [sp], #16",
        "  ret",
    ]

    agg_ir = """
target triple = "arm64-apple-darwin23.6.0"
%S = type { i64, i64, i64 }

define %S @mk_zero() {
bb0:
  ret %S zeroinitializer
}
""".strip()
    module = parse_self_backend_module(agg_ir)
    symbols = prepare_module_symbols(
        agg_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda ty: ty.is_struct)
    assert emit_return_terminator(
        func,
        ret_type=TypeDesc(
            "struct",
            name="S",
            fields=(TypeDesc("int", 64), TypeDesc("int", 64), TypeDesc("int", 64)),
        ),
        value="zeroinitializer",
        module_symbols=symbols,
    ) == [
        f"  ldur x12, [x29, #-{func.hidden_sret_slot.offset}]",
        "  movz x14, #0",
        "  str x14, [x12]",
        "  add x16, x12, #8",
        "  str x14, [x16]",
        "  add x16, x12, #16",
        "  str x14, [x16]",
        "  add sp, sp, #16",
        "  ldp x29, x30, [sp], #16",
        "  ret",
    ]

    literal_ir = """
target triple = "arm64-apple-darwin23.6.0"
%S = type { i64, i64, i64 }

define %S @mk_literal() {
bb0:
  ret %S { i64 1, i64 2, i64 3 }
}
""".strip()
    module = parse_self_backend_module(literal_ir)
    symbols = prepare_module_symbols(
        literal_ir, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda ty: ty.is_struct)
    literal_lines = emit_return_terminator(
        func,
        ret_type=TypeDesc(
            "struct",
            name="S",
            fields=(TypeDesc("int", 64), TypeDesc("int", 64), TypeDesc("int", 64)),
        ),
        value="{ i64 1, i64 2, i64 3 }",
        module_symbols=symbols,
    )
    assert literal_lines[:7] == [
        f"  ldur x12, [x29, #-{func.hidden_sret_slot.offset}]",
        "  movz x14, #1, lsl #0",
        "  str x14, [x12]",
        "  add x16, x12, #8",
        "  movz x14, #2, lsl #0",
        "  str x14, [x16]",
        "  add x16, x12, #16",
    ]
    assert "  movz x14, #3, lsl #0" in literal_lines


def test_self_backend_emits_direct_call_subset(tmp_path):
    source = "int helper(void) { return 5; }\n" "int main(void) { return helper(); }\n"
    ev, compiled_units = _compile_units(source, tmp_path)
    asm_path = tmp_path / "call_main.s"

    ev.emit_compiled_units(compiled_units, emit_asm=str(asm_path), optimize=0)

    asm_text = asm_path.read_text()
    assert "_helper:" in asm_text
    assert "_main:" in asm_text
    assert "bl _helper" in asm_text
    assert "stp x29, x30, [sp, #-16]!" in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 5


def test_self_backend_emits_i32_arg_arithmetic_and_call(tmp_path):
    source = (
        "int add(int a, int b) { return a + b; }\n"
        "int main(void) { return add(2, 3); }\n"
    )
    ev, compiled_units = _compile_units(source, tmp_path)
    asm_path = tmp_path / "add_main.s"

    ev.emit_compiled_units(compiled_units, emit_asm=str(asm_path), optimize=0)

    asm_text = asm_path.read_text()
    assert "_add:" in asm_text
    assert "add w11, w9, w10" in asm_text
    assert "bl _add" in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 5


def test_self_backend_emits_branch_phi_subset(tmp_path):
    source = (
        "int max2(int a, int b) { if (a < b) return b; return a; }\n"
        "int main(void) { return max2(2, 7); }\n"
    )
    ev, compiled_units = _compile_units(source, tmp_path)
    asm_path = tmp_path / "max2_main.s"

    ev.emit_compiled_units(compiled_units, emit_asm=str(asm_path), optimize=0)

    asm_text = asm_path.read_text()
    assert "cset w11, lt" in asm_text
    assert "cbz w9" in asm_text
    assert "bl _max2" in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 7


def test_self_backend_emits_object_file(tmp_path):
    ev, compiled_units = _compile_units(
        "int main(void) { return 11; }\n",
        tmp_path,
    )
    obj_path = tmp_path / "main.o"

    ev.emit_compiled_units(compiled_units, emit_obj=str(obj_path), optimize=0)

    assert obj_path.is_file()
    run = _link_object_and_run(obj_path, tmp_path)
    assert run.returncode == 11


def test_self_backend_can_execute_via_evaluate(tmp_path):
    source = (
        "int max2(int a, int b) { if (a < b) return b; return a; }\n"
        "int main(void) { return max2(3, 9) - 9; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_system_link_uses_self_emitter_not_llvm_object_path(
    tmp_path, monkeypatch
):
    import pcc.evaluater.c_evaluator as c_evaluator

    calls = []
    original_emit_self_asm = c_evaluator.emit_self_asm

    def recording_emit_self_asm(ir_text):
        calls.append(ir_text)
        return original_emit_self_asm(ir_text)

    def fail_llvm_object_path(self, *args, **kwargs):
        raise AssertionError("strict self backend gate reached LLVM object path")

    monkeypatch.setattr(c_evaluator, "emit_self_asm", recording_emit_self_asm)
    monkeypatch.setattr(
        c_evaluator.CEvaluator, "_prepare_llvm_module", fail_llvm_object_path
    )
    unit = TranslationUnit(
        name="main.c",
        path=str(tmp_path / "main.c"),
        source="int main(void) { return 7; }\n",
    )

    result = c_evaluator.CEvaluator(
        backend="self",
        allow_unimplemented_backend=True,
    ).run_translation_units_with_system_cc(
        [unit],
        optimize=0,
        use_system_cpp=False,
        timeout=30,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 7
    assert len(calls) == 1
    assert "define i32 @main" in calls[0]


def test_self_backend_emitter_failure_does_not_fallback_to_llvm(tmp_path, monkeypatch):
    import pcc.evaluater.c_evaluator as c_evaluator

    def fail_self_emitter(_ir_text):
        raise BackendUnavailable("strict self backend sentinel")

    def fail_llvm_object_path(self, *args, **kwargs):
        raise AssertionError("strict self backend gate fell back to LLVM object path")

    monkeypatch.setattr(c_evaluator, "emit_self_asm", fail_self_emitter)
    monkeypatch.setattr(
        c_evaluator.CEvaluator, "_prepare_llvm_module", fail_llvm_object_path
    )
    unit = TranslationUnit(
        name="main.c",
        path=str(tmp_path / "main.c"),
        source="int main(void) { return 0; }\n",
    )

    with pytest.raises(BackendUnavailable, match="strict self backend sentinel"):
        c_evaluator.CEvaluator(
            backend="self",
            allow_unimplemented_backend=True,
        ).run_translation_units_with_system_cc(
            [unit],
            optimize=0,
            use_system_cpp=False,
            timeout=30,
            capture_output=True,
            text=True,
        )


def test_self_backend_supports_multi_tu_internal_symbol_collisions(tmp_path):
    ev, compiled_units = _compile_multi_units(
        [
            (
                "foo.c",
                "static int helper(void) { return 1; }\n"
                "int foo(void) { return helper(); }\n",
            ),
            (
                "main.c",
                "static int helper(void) { return 2; }\n"
                "extern int foo(void);\n"
                "int main(void) { return foo() + helper() - 3; }\n",
            ),
        ],
        tmp_path,
    )

    obj_path = tmp_path / "multi_internal.o"
    ev.emit_compiled_units(compiled_units, emit_obj=str(obj_path), optimize=0)

    run = _link_object_and_run(obj_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_i64_call_and_casts(tmp_path):
    source = (
        "long addl(long a, long b) { return a + b; }\n"
        "int main(void) { return (int)(addl(40, 2) - 42); }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_void_call_and_pointer_store(tmp_path):
    source = (
        "void setp(int *p) { *p = 9; }\n"
        "int main(void) { int x = 0; setp(&x); return x - 9; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_pointer_return_and_load(tmp_path):
    source = (
        "int *id(int *p) { return p; }\n"
        "int main(void) { int x = 3; return *id(&x) - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_keeps_byte_local_store_from_corrupting_neighbor_pointer_slot(
    tmp_path,
):
    source = (
        "int main(void) {\n"
        "  long x = 7;\n"
        "  long *p = &x;\n"
        "  char c = 1;\n"
        "  return (int)(*p - 7);\n"
        "}\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_simple_global_scalar(tmp_path):
    source = "int g = 7; int main(void) { return g - 7; }\n"

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_local_array_indexing(tmp_path):
    source = "int main(void) { int a[2]; a[1] = 4; return a[1] - 4; }\n"

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_simple_struct_field(tmp_path):
    source = (
        "struct S { int x; }; int main(void) { struct S s; s.x = 3; return s.x - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_resolves_forward_referenced_named_types_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

%S = type { i32, %U, i32 }
%U = type { i64 }

define i32 @main() {
bb0:
  %s = alloca %S
  %tail = getelementptr inbounds %S, ptr %s, i64 0, i32 2
  store i32 7, ptr %tail
  %v = load i32, ptr %tail
  %ret = sub i32 %v, 7
  ret i32 %ret
}
"""

    asm_path = tmp_path / "forward_named_type.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)

    assert run.returncode == 0


def test_self_backend_allows_dotted_internal_symbol_names_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

@strlit.1 = private constant [3 x i8] [i8 104, i8 105, i8 0]

define i32 @main() {
bb0:
  ret i32 0
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_strlit.1:" in asm_text
    assert "_main:" in asm_text


def test_self_backend_supports_globals_only_module_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

@g = internal global i32 7
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_g:" in asm_text
    assert ".globl _g" not in asm_text
    assert ".subsections_via_symbols" in asm_text


def test_self_backend_supports_empty_module_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert asm_text == "\n"


def test_self_backend_supports_struct_pointer_stride_gep_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

%S = type { i32, i32 }

define i32 @main() {
bb0:
  %arr = alloca [2 x %S]
  %base = getelementptr inbounds [2 x %S], ptr %arr, i64 0, i64 0
  %p = getelementptr inbounds %S, ptr %base, i64 1
  %field = getelementptr inbounds %S, ptr %p, i64 0, i32 1
  store i32 9, ptr %field
  %v = load i32, ptr %field
  %ret = sub i32 %v, 9
  ret i32 %ret
}
"""

    asm_path = tmp_path / "struct_stride_gep.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)

    assert run.returncode == 0


def test_self_backend_supports_pointer_icmp_against_null_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %p = alloca i32
  %cond = icmp ne ptr %p, null
  %ret = zext i1 %cond to i32
  ret i32 %ret
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_main:" in asm_text
    assert "cmp x9, x10" in asm_text
    assert "cset w11, ne" in asm_text


def test_self_backend_supports_nested_array_gep_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define ptr @probe(ptr %arr) {
bb0:
  %p = getelementptr [53 x [2 x ptr]], ptr %arr, i64 0, i64 1, i64 0
  ret ptr %p
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_probe:" in asm_text
    assert "add x11, x9, #16" in asm_text


def test_self_backend_supports_zero_length_external_array_decay_gep_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

@tbl = external global [0 x i8]

define ptr @probe(i64 %i) {
bb0:
  %p = getelementptr [0 x i8], ptr @tbl, i64 0, i64 %i
  ret ptr %p
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_probe:" in asm_text
    assert "mov x11, x9" in asm_text
    assert "add x11, x9, x10" in asm_text


def test_self_backend_supports_large_struct_field_gep_offset_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

%S = type { [5000 x i8], i32 }

define i32 @main() {
bb0:
  %s = alloca %S
  %field = getelementptr inbounds %S, ptr %s, i64 0, i32 1
  store i32 9, ptr %field
  %v = load i32, ptr %field
  %ret = sub i32 %v, 9
  ret i32 %ret
}
"""

    asm_path = tmp_path / "large_struct_gep.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    asm_text = asm_path.read_text()
    assert "add x11, x9, #5000" not in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_nested_array_global_initializers_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

@tbl = internal constant [2 x [3 x i32]] [[3 x i32] [i32 1, i32 2, i32 3], [3 x i32] [i32 4, i32 5, i32 6]]

define i32 @main() {
bb0:
  %row = getelementptr [2 x [3 x i32]], ptr @tbl, i64 0, i64 1
  %elt = getelementptr [3 x i32], ptr %row, i64 0, i64 2
  %v = load i32, ptr %elt
  ret i32 %v
}
"""

    asm_path = tmp_path / "nested_array_global.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    asm_text = asm_path.read_text()
    assert "_tbl:" in asm_text

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 6


def test_self_backend_supports_aggregate_zeroinitializer_store_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %a = alloca [5 x i32]
  store [5 x i32] zeroinitializer, ptr %a
  %elt = getelementptr [5 x i32], ptr %a, i64 0, i64 4
  %v = load i32, ptr %elt
  ret i32 %v
}
"""

    asm_path = tmp_path / "aggregate_zero_store.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_large_by_value_aggregate_args_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i64 @pick([4 x i64] %s) {
bb0:
  %local = alloca [4 x i64]
  store [4 x i64] %s, ptr %local
  %elt = getelementptr [4 x i64], ptr %local, i64 0, i64 2
  %v = load i64, ptr %elt
  ret i64 %v
}

define i32 @main() {
bb0:
  %s = alloca [4 x i64]
  %p0 = getelementptr [4 x i64], ptr %s, i64 0, i64 0
  %p1 = getelementptr [4 x i64], ptr %s, i64 0, i64 1
  %p2 = getelementptr [4 x i64], ptr %s, i64 0, i64 2
  %p3 = getelementptr [4 x i64], ptr %s, i64 0, i64 3
  store i64 1, ptr %p0
  store i64 2, ptr %p1
  store i64 3, ptr %p2
  store i64 4, ptr %p3
  %v = load [4 x i64], ptr %s
  %ret64 = call i64 ([4 x i64]) @pick([4 x i64] %v)
  %ret32 = trunc i64 %ret64 to i32
  %delta = sub i32 %ret32, 3
  ret i32 %delta
}
"""

    asm_path = tmp_path / "large_byval_arg.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_large_aggregate_return_and_by_value_arg_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

%S = type { i64, i64, i64 }

define %S @mk(i64 %x) {
bb0:
  %s = alloca %S
  %p0 = getelementptr %S, ptr %s, i64 0, i32 0
  %p1 = getelementptr %S, ptr %s, i64 0, i32 1
  %p2 = getelementptr %S, ptr %s, i64 0, i32 2
  store i64 %x, ptr %p0
  %x1 = add i64 %x, 1
  store i64 %x1, ptr %p1
  %x2 = add i64 %x, 2
  store i64 %x2, ptr %p2
  %v = load %S, ptr %s
  ret %S %v
}

define i64 @use(%S %s) {
bb0:
  %local = alloca %S
  store %S %s, ptr %local
  %p1 = getelementptr %S, ptr %local, i64 0, i32 1
  %p2 = getelementptr %S, ptr %local, i64 0, i32 2
  %a = load i64, ptr %p1
  %b = load i64, ptr %p2
  %sum = add i64 %a, %b
  ret i64 %sum
}

define i32 @main() {
bb0:
  %s = call %S (i64) @mk(i64 10)
  %v = call i64 (%S) @use(%S %s)
  %d = sub i64 %v, 23
  %r = trunc i64 %d to i32
  ret i32 %r
}
"""

    asm_path = tmp_path / "large_agg_ret_and_byval.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_large_aggregate_phi_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

%S = type { i32, i32, i32, i64, i64, i32 }

define internal %S @mk_a() {
bb0:
  %s = alloca %S
  %f0 = getelementptr inbounds %S, ptr %s, i64 0, i32 0
  %f1 = getelementptr inbounds %S, ptr %s, i64 0, i32 1
  %f2 = getelementptr inbounds %S, ptr %s, i64 0, i32 2
  %f3 = getelementptr inbounds %S, ptr %s, i64 0, i32 3
  %f4 = getelementptr inbounds %S, ptr %s, i64 0, i32 4
  %f5 = getelementptr inbounds %S, ptr %s, i64 0, i32 5
  store i32 1, ptr %f0
  store i32 2, ptr %f1
  store i32 3, ptr %f2
  store i64 10, ptr %f3
  store i64 20, ptr %f4
  store i32 4, ptr %f5
  %v = load %S, ptr %s
  ret %S %v
}

define internal %S @mk_b() {
bb0:
  %s = alloca %S
  %f0 = getelementptr inbounds %S, ptr %s, i64 0, i32 0
  %f1 = getelementptr inbounds %S, ptr %s, i64 0, i32 1
  %f2 = getelementptr inbounds %S, ptr %s, i64 0, i32 2
  %f3 = getelementptr inbounds %S, ptr %s, i64 0, i32 3
  %f4 = getelementptr inbounds %S, ptr %s, i64 0, i32 4
  %f5 = getelementptr inbounds %S, ptr %s, i64 0, i32 5
  store i32 5, ptr %f0
  store i32 6, ptr %f1
  store i32 7, ptr %f2
  store i64 30, ptr %f3
  store i64 40, ptr %f4
  store i32 8, ptr %f5
  %v = load %S, ptr %s
  ret %S %v
}

define i32 @main() {
bb0:
  %cond = icmp eq i32 0, 0
  br i1 %cond, label %t, label %f

t:
  %a = call %S @mk_a()
  br label %merge

f:
  %b = call %S @mk_b()
  br label %merge

merge:
  %phi = phi %S [%a, %t], [%b, %f]
  %slot = alloca %S
  store %S %phi, ptr %slot
  %p0 = getelementptr inbounds %S, ptr %slot, i64 0, i32 0
  %p3 = getelementptr inbounds %S, ptr %slot, i64 0, i32 3
  %x = load i32, ptr %p0
  %y = load i64, ptr %p3
  %y32 = trunc i64 %y to i32
  %sum = add i32 %x, %y32
  %ret = sub i32 %sum, 11
  ret i32 %ret
}
"""

    asm_path = tmp_path / "large_aggregate_phi.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_large_aggregate_zeroinitializer_return_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

%S = type { i64, i64, i64 }

define %S @mk_zero() {
bb0:
  ret %S zeroinitializer
}

define i32 @main() {
bb0:
  %s = call %S @mk_zero()
  %slot = alloca %S
  store %S %s, ptr %slot
  %p0 = getelementptr inbounds %S, ptr %slot, i64 0, i32 0
  %x = load i64, ptr %p0
  %r = trunc i64 %x to i32
  ret i32 %r
}
"""

    asm_path = tmp_path / "large_agg_zero_ret.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_small_aggregate_variadic_stack_arg_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

%S = type { i64, i32 }

define internal void @sink(ptr %fmt, ...) {
bb0:
  ret void
}

define i32 @main() {
bb0:
  %s = alloca %S
  %p0 = getelementptr inbounds %S, ptr %s, i64 0, i32 0
  %p1 = getelementptr inbounds %S, ptr %s, i64 0, i32 1
  store i64 7, ptr %p0
  store i32 9, ptr %p1
  %v = load %S, ptr %s
  call void (ptr, ...) @sink(ptr null, %S %v)
  ret i32 0
}
"""

    asm_path = tmp_path / "small_aggregate_vararg.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_inline_gep_call_arg_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

@msg = private constant [4 x i8] c"ok\\0A\\00"

define internal void @sink(ptr %p) {
bb0:
  ret void
}

define i32 @main() {
bb0:
  call void (ptr) @sink(ptr getelementptr inbounds ([4 x i8], ptr @msg, i64 0, i64 0))
  ret i32 0
}
"""

    asm_path = tmp_path / "inline_gep_call_arg.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_small_aggregate_fixed_stack_arg_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

%S = type { i64, i32 }

define i32 @take(i64 %a0, i64 %a1, i64 %a2, i64 %a3, i64 %a4, i64 %a5, i64 %a6, i64 %a7, %S %s) {
bb0:
  %slot = alloca %S
  store %S %s, ptr %slot
  %p0 = getelementptr inbounds %S, ptr %slot, i64 0, i32 0
  %p1 = getelementptr inbounds %S, ptr %slot, i64 0, i32 1
  %x = load i64, ptr %p0
  %y = load i32, ptr %p1
  %x32 = trunc i64 %x to i32
  %sum = add i32 %x32, %y
  %ret = sub i32 %sum, 16
  ret i32 %ret
}

define i32 @main() {
bb0:
  %s = alloca %S
  %p0 = getelementptr inbounds %S, ptr %s, i64 0, i32 0
  %p1 = getelementptr inbounds %S, ptr %s, i64 0, i32 1
  store i64 7, ptr %p0
  store i32 9, ptr %p1
  %v = load %S, ptr %s
  %r = call i32 (i64, i64, i64, i64, i64, i64, i64, i64, %S) @take(i64 0, i64 1, i64 2, i64 3, i64 4, i64 5, i64 6, i64 7, %S %v)
  ret i32 %r
}
"""

    asm_path = tmp_path / "small_aggregate_fixed_stack_arg.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_ctlz_cttz_intrinsics_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

declare i32 @llvm.ctlz.i32(i32, i1)
declare i32 @llvm.cttz.i32(i32, i1)
declare i64 @llvm.ctlz.i64(i64, i1)
declare i64 @llvm.cttz.i64(i64, i1)

define i32 @main() {
bb0:
  %a = call i32 (i32, i1) @llvm.ctlz.i32(i32 8, i1 0)
  %b = call i32 (i32, i1) @llvm.cttz.i32(i32 8, i1 0)
  %c = call i64 (i64, i1) @llvm.ctlz.i64(i64 16, i1 0)
  %d = call i64 (i64, i1) @llvm.cttz.i64(i64 16, i1 0)
  %c32 = trunc i64 %c to i32
  %d32 = trunc i64 %d to i32
  %s1 = add i32 %a, %b
  %s2 = add i32 %c32, %d32
  %sum = add i32 %s1, %s2
  %ret = sub i32 %sum, 94
  ret i32 %ret
}
"""

    asm_path = tmp_path / "bitcount_intrinsics.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_ctpop_intrinsics_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

declare i32 @llvm.ctpop.i32(i32)
declare i64 @llvm.ctpop.i64(i64)

define i32 @main() {
bb0:
  %a = call i32 @llvm.ctpop.i32(i32 15)
  %b = call i64 @llvm.ctpop.i64(i64 31)
  %b32 = trunc i64 %b to i32
  %sum = add i32 %a, %b32
  %ret = sub i32 %sum, 9
  ret i32 %ret
}
"""

    asm_path = tmp_path / "bitcount_intrinsics.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_copysign_intrinsic_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

declare double @llvm.copysign.f64(double, double)

define i32 @main() {
bb0:
  %r = call double @llvm.copysign.f64(double 1.500000e+00, double -2.000000e+00)
  %i = fptosi double %r to i32
  %ret = add i32 %i, 1
  ret i32 %ret
}
"""

    asm_path = tmp_path / "copysign_intrinsic.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_nested_array_alloca_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %rankVal = alloca [16 x [17 x i32]]
  %row = getelementptr [16 x [17 x i32]], ptr %rankVal, i64 0, i64 3
  %elt = getelementptr [17 x i32], ptr %row, i64 0, i64 5
  store i32 9, ptr %elt
  %v = load i32, ptr %elt
  %ret = sub i32 %v, 9
  ret i32 %ret
}
"""

    asm_path = tmp_path / "nested_array_alloca.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_nested_aggregate_zeroinitializer_store_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %a = alloca [3 x [4 x ptr]]
  store [3 x [4 x ptr]] zeroinitializer, ptr %a
  ret i32 0
}
"""

    asm_path = tmp_path / "nested_aggregate_zero_store.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_large_aggregate_zero_store_beyond_addr_imm_range_in_ir(
    tmp_path,
):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %a = alloca [4097 x i16]
  store [4097 x i16] zeroinitializer, ptr %a
  ret i32 0
}
"""

    asm_path = tmp_path / "large_aggregate_zero_store_big_offset.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_gep_with_large_element_size_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %a = alloca [2 x [70000 x i8]]
  %row = getelementptr [2 x [70000 x i8]], ptr %a, i64 0, i64 1
  %elt = getelementptr [70000 x i8], ptr %row, i64 0, i64 0
  store i8 9, ptr %elt
  %v = load i8, ptr %elt
  %r = zext i8 %v to i32
  %ret = sub i32 %r, 9
  ret i32 %ret
}
"""

    asm_path = tmp_path / "large_elem_gep.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_global_ptr_initializer_with_nonzero_gep_offset_in_ir(
    tmp_path,
):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

@arr = global [2 x i32] [i32 7, i32 9]
@p = global ptr getelementptr inbounds ([2 x i32], ptr @arr, i64 0, i64 1)

define i32 @main() {
bb0:
  %x = load ptr, ptr @p
  %v = load i32, ptr %x
  %ret = sub i32 %v, 9
  ret i32 %ret
}
"""

    asm_path = tmp_path / "global_gep_offset_init.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 0


def test_self_backend_supports_unreachable_terminator_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @probe(i32 %x) {
bb0:
  %cond = icmp eq i32 %x, 0
  br i1 %cond, label %ok, label %trap

ok:
  ret i32 0

trap:
  unreachable
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_probe:" in asm_text
    assert "brk #0" in asm_text


def test_self_backend_supports_llvm_trap_intrinsic_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

declare void @llvm.trap()

define void @probe(i1 %cond) {
bb0:
  br i1 %cond, label %trap, label %ok

trap:
  tail call void @llvm.trap()
  unreachable

ok:
  ret void
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_probe:" in asm_text
    assert "bl _llvm.trap" not in asm_text
    assert asm_text.count("brk #0") >= 1


def test_self_backend_skips_dead_large_aggregate_load_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

%S = type { [24 x i8] }

define i32 @main() {
bb0:
  %s = alloca %S
  %unused = load %S, ptr %s
  ret i32 0
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_main:" in asm_text
    assert "_unused" not in asm_text


def test_self_backend_supports_fneg_of_double_immediate_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define double @probe() {
bb0:
  %neg = fneg double 0x4077280000000000
  ret double %neg
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_probe:" in asm_text
    assert "fneg d11, d9" in asm_text


def test_self_backend_runs_unordered_fcmp_semantics_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %ueq = fcmp ueq double 0x7ff8000000000000, 0x3ff0000000000000
  %une = fcmp une double 0x7ff8000000000000, 0x3ff0000000000000
  %uno = fcmp uno double 0x7ff8000000000000, 0x3ff0000000000000
  %ult = fcmp ult double 0x7ff8000000000000, 0x3ff0000000000000
  %uge = fcmp uge double 0x7ff8000000000000, 0x3ff0000000000000
  %one = fcmp one double 0x7ff8000000000000, 0x3ff0000000000000
  %olt = fcmp olt double 0x3ff0000000000000, 0x4000000000000000
  %ogt = fcmp ogt double 0x4000000000000000, 0x3ff0000000000000
  %ueq32 = zext i1 %ueq to i32
  %une32 = zext i1 %une to i32
  %uno32 = zext i1 %uno to i32
  %ult32 = zext i1 %ult to i32
  %uge32 = zext i1 %uge to i32
  %one32 = zext i1 %one to i32
  %olt32 = zext i1 %olt to i32
  %ogt32 = zext i1 %ogt to i32
  %s1 = shl i32 %une32, 1
  %s2 = shl i32 %uno32, 2
  %s3 = shl i32 %ult32, 3
  %s4 = shl i32 %uge32, 4
  %s5 = shl i32 %one32, 5
  %s6 = shl i32 %olt32, 6
  %s7 = shl i32 %ogt32, 7
  %a1 = or i32 %ueq32, %s1
  %a2 = or i32 %a1, %s2
  %a3 = or i32 %a2, %s3
  %a4 = or i32 %a3, %s4
  %a5 = or i32 %a4, %s5
  %a6 = or i32 %a5, %s6
  %mask = or i32 %a6, %s7
  %ret = sub i32 %mask, 223
  ret i32 %ret
}
"""

    asm_path = tmp_path / "fcmp_runtime.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)

    assert run.returncode == 0


def test_self_backend_runs_ordered_fcmp_semantics_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %ord_nan = fcmp ord double 0x7ff8000000000000, 0x3ff0000000000000
  %ord_ok = fcmp ord double 0x3ff0000000000000, 0x4000000000000000
  %ord_nan32 = zext i1 %ord_nan to i32
  %ord_ok32 = zext i1 %ord_ok to i32
  %s1 = shl i32 %ord_ok32, 1
  %mask = or i32 %ord_nan32, %s1
  %ret = sub i32 %mask, 2
  ret i32 %ret
}
"""

    asm_path = tmp_path / "fcmp_ord_runtime.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)

    assert run.returncode == 0


def test_self_backend_runs_narrow_negative_constant_zext_and_switch_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %p = alloca i8
  store i8 -120, ptr %p
  %loaded = load i8, ptr %p
  %wide = zext i8 -120 to i32
  %ok = icmp eq i32 %wide, 136
  br i1 %ok, label %switchbb, label %fail_zext

switchbb:
  switch i8 %loaded, label %fail_switch [
    i8 -120, label %ok_switch
  ]

ok_switch:
  ret i32 0

fail_zext:
  ret i32 1

fail_switch:
  ret i32 2
}
"""

    asm_path = tmp_path / "narrow_neg_const_runtime.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)

    assert run.returncode == 0


def test_self_backend_runs_signed_narrow_icmp_after_load_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  %p = alloca i16
  store i16 -1, ptr %p
  %v = load i16, ptr %p
  %lt = icmp slt i16 %v, 0
  %gt = icmp sgt i16 %v, 0
  %lt32 = zext i1 %lt to i32
  %gt32 = zext i1 %gt to i32
  %gtbad = shl i32 %gt32, 1
  %ret = or i32 %lt32, %gtbad
  %adj = sub i32 %ret, 1
  ret i32 %adj
}
"""

    asm_path = tmp_path / "signed_narrow_icmp_runtime.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)

    assert run.returncode == 0


def test_self_backend_runs_signed_narrow_division_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

@x = global i8 50
@y = global i16 -5

define i32 @main() {
bb0:
  %lhs8 = load i8, ptr @x
  %rhs16 = load i16, ptr @y
  %lhs16 = zext i8 %lhs8 to i16
  %q = sdiv i16 %lhs16, %rhs16
  %tr = trunc i16 %q to i8
  store i8 %tr, ptr @x
  %masked = and i16 %q, 255
  %ok = icmp eq i16 %masked, 246
  br i1 %ok, label %okbb, label %fail

okbb:
  ret i32 0

fail:
  ret i32 1
}
"""

    asm_path = tmp_path / "signed_narrow_division_runtime.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)

    assert run.returncode == 0


def test_self_backend_supports_multiline_switch_terminator_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
bb0:
  switch i32 2, label %switch_default [
    i32 1, label %switch_case
    i32 2, label %switch_case.1
  ], !llvm.loop !0

switch_default:
  ret i32 3

switch_case:
  ret i32 1

switch_case.1:
  ret i32 0
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_main:" in asm_text
    assert asm_text.count("b.eq") == 2
    assert "L_main_bb0_to_switch_case:" in asm_text
    assert "L_main_bb0_to_switch_casedot1:" in asm_text


def test_self_backend_accepts_variadic_function_signature_in_ir():
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define ptr @lua_pushfstring(ptr %.1, ptr %.2, ...) {
bb0:
  ret ptr null
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "_lua_pushfstring:" in asm_text
    assert "movz x0, #0" in asm_text


def test_self_backend_runs_simple_varargs_stack_lowering_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

@str = private constant [2 x i8] [i8 65, i8 0]

declare void @llvm.va_start.p0(ptr)
declare void @llvm.va_end.p0(ptr)

define i32 @probe(i32 %tag, ...) {
bb0:
  %argp = alloca ptr
  call void @llvm.va_start.p0(ptr %argp)
  %i = va_arg ptr %argp, i32
  %d = va_arg ptr %argp, double
  %p = va_arg ptr %argp, ptr
  call void @llvm.va_end.p0(ptr %argp)
  %ch = load i8, ptr %p, align 1
  %ch32 = zext i8 %ch to i32
  %d32 = fptosi double %d to i32
  %sum1 = add i32 %i, %d32
  %sum2 = add i32 %sum1, %ch32
  ret i32 %sum2
}

define i32 @main() {
bb0:
  %v = call i32 (i32, ...) @probe(i32 0, i32 2, double 0x400c000000000000, ptr @str)
  %sub = sub i32 %v, 70
  ret i32 %sub
}
"""

    asm_path = tmp_path / "varargs_runtime.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)

    assert run.returncode == 0


def test_self_backend_supports_local_string_literal(tmp_path):
    source = 'int main(void) { char *s = "hi"; return s[0] - 104; }\n'

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_global_string_pointer_init(tmp_path):
    source = 'char *s = "hi"; int main(void) { return s[1] - 105; }\n'

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_double_call_and_int_casts(tmp_path):
    source = (
        "double sum2(int a, int b) { return (double)a + (double)b; }\n"
        "int main(void) { return (int)sum2(1, 3) - 4; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_float_call_and_int_casts(tmp_path):
    source = (
        "float sum2f(int a, int b) { return (float)a + (float)b; }\n"
        "int main(void) { return (int)sum2f(1, 3) - 4; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_double_compare(tmp_path):
    source = (
        "int gt2(int a, int b) { double da = (double)a; double db = (double)b; return da > db; }\n"
        "int main(void) { return gt2(5, 2) - 1; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_indirect_function_pointer_call(tmp_path):
    source = (
        "typedef int (*binop_t)(int, int);\n"
        "int add(int a, int b) { return a + b; }\n"
        "int apply(binop_t f, int a, int b) { return f(a, b); }\n"
        "int main(void) { return apply(add, 2, 3) - 5; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_fixed_stack_args_beyond_x7(tmp_path):
    source = (
        "int pick9(int a, int b, int c, int d, int e, int f, int g, int h, int i) { return i; }\n"
        "int main(void) { return pick9(1, 2, 3, 4, 5, 6, 7, 8, 9) - 9; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_materializes_large_local_stack_slots_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define i32 @main() {
entry:
  %pad = alloca [4704 x i8], align 1
  %x = alloca i64, align 8
  store i64 42, ptr %x, align 8
  %value = load i64, ptr %x, align 8
  %trunc = trunc i64 %value to i32
  ret i32 %trunc
}
"""

    asm_path = tmp_path / "large_stack_slot.s"
    asm_path.write_text(emit_aarch64_darwin_asm(ir_text))

    run = _assemble_and_run(asm_path, tmp_path)
    assert run.returncode == 42


def test_self_backend_supports_large_struct_assignment_by_memory_copy(tmp_path):
    source = (
        "struct Triple { long a; long b; long c; };\n"
        "int main(void) {\n"
        "  struct Triple x;\n"
        "  struct Triple y;\n"
        "  x.a = 1; x.b = 2; x.c = 3;\n"
        "  y = x;\n"
        "  return (int)(y.c - 3);\n"
        "}\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_external_global_data_symbol(tmp_path):
    helper_obj = tmp_path / "helper_global.o"
    _compile_native_helper("int extg = 7;\n", helper_obj)

    ev, compiled_units = _compile_units(
        "extern int extg; int main(void) { return extg - 7; }\n",
        tmp_path,
    )
    obj_path = tmp_path / "main_extg.o"
    ev.emit_compiled_units(compiled_units, emit_obj=str(obj_path), optimize=0)

    exe_path = tmp_path / "extg.out"
    subprocess.run(
        ["cc", str(obj_path), str(helper_obj), "-o", str(exe_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    run = subprocess.run([str(exe_path)], capture_output=True, text=True)

    assert run.returncode == 0


def test_self_backend_supports_small_struct_argument_by_value(tmp_path):
    source = (
        "struct Pair { int a; int b; };\n"
        "int sum(struct Pair p) { return p.a + p.b; }\n"
        "int main(void) { struct Pair p; p.a = 1; p.b = 2; return sum(p) - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_small_struct_return_by_value(tmp_path):
    source = (
        "struct Pair { int a; int b; };\n"
        "struct Pair mk(void) { struct Pair p; p.a = 1; p.b = 2; return p; }\n"
        "int main(void) { struct Pair p = mk(); return p.a + p.b - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_single_word_struct_return_by_value(tmp_path):
    source = (
        "struct One { int a; };\n"
        "struct One mk(void) { struct One p; p.a = 7; return p; }\n"
        "int main(void) { struct One p = mk(); return p.a - 7; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_two_register_struct_argument_by_value(tmp_path):
    source = (
        "struct Pair64 { long a; long b; };\n"
        "int sum(struct Pair64 p) { return (int)(p.a + p.b); }\n"
        "int main(void) { struct Pair64 p; p.a = 1; p.b = 2; return sum(p) - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_two_register_struct_return_by_value(tmp_path):
    source = (
        "struct Quad { int a; int b; int c; int d; };\n"
        "struct Quad mk(void) { struct Quad p; p.a = 1; p.b = 2; p.c = 3; p.d = 4; return p; }\n"
        "int main(void) { struct Quad p = mk(); return p.a + p.b + p.c + p.d - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_partial_tail_two_register_struct_argument_by_value(
    tmp_path,
):
    source = (
        "struct Triple { int a; int b; int c; };\n"
        "int sum(struct Triple p) { return p.a + p.b + p.c; }\n"
        "int main(void) { struct Triple p; p.a = 1; p.b = 2; p.c = 3; return sum(p) - 6; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_partial_tail_two_register_struct_return_by_value(
    tmp_path,
):
    source = (
        "struct Triple { int a; int b; int c; };\n"
        "struct Triple mk(void) { struct Triple p; p.a = 1; p.b = 2; p.c = 3; return p; }\n"
        "int main(void) { struct Triple p = mk(); return p.a + p.b + p.c - 6; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
    )

    assert result == 0


def test_self_backend_supports_external_mixed_aggregate_and_scalar_call_boundary(
    tmp_path,
):
    helper_obj = tmp_path / "helper.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "int sum_plus(struct Triple p, int extra) { return p.a + p.b + p.c + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern int sum_plus(struct Triple p, int extra);\n"
        "int main(void) { struct Triple p; p.a = 1; p.b = 2; p.c = 3; return sum_plus(p, 4) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_mixed_aggregate_and_fp_call_boundary(tmp_path):
    helper_obj = tmp_path / "helper_fp.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "double sum_plus_fp(struct Triple p, double extra) { return (double)(p.a + p.b + p.c) + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern double sum_plus_fp(struct Triple p, double extra);\n"
        "int main(void) { struct Triple p; p.a = 1; p.b = 2; p.c = 3; return (int)sum_plus_fp(p, 4.0) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_two_aggregate_call_boundary(tmp_path):
    helper_obj = tmp_path / "helper_two_agg.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "int sum_two(struct Triple a, struct Triple b) { return a.a + a.b + a.c + b.a + b.b + b.c; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern int sum_two(struct Triple a, struct Triple b);\n"
        "int main(void) { struct Triple a; struct Triple b; a.a = 1; a.b = 2; a.c = 3; b.a = 4; b.b = 5; b.c = 6; return sum_two(a, b) - 21; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_aggregate_then_pointer_call_boundary(tmp_path):
    helper_obj = tmp_path / "helper_agg_ptr.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "int sum_ptr(struct Triple a, int *p) { return a.a + a.b + a.c + *p; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern int sum_ptr(struct Triple a, int *p);\n"
        "int main(void) { struct Triple a; int x = 4; a.a = 1; a.b = 2; a.c = 3; return sum_ptr(a, &x) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_partial_tail_aggregate_return_boundary(
    tmp_path,
):
    helper_obj = tmp_path / "helper_ret_triple.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "int main(void) { struct Triple p = mk_triple(); return p.a + p.b + p.c - 6; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_two_register_aggregate_return_boundary(
    tmp_path,
):
    helper_obj = tmp_path / "helper_ret_pair.o"
    _compile_native_helper(
        (
            "struct Pair64 { long a; long b; };\n"
            "struct Pair64 mk_pair(void) { struct Pair64 p = {1, 2}; return p; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Pair64 { long a; long b; };\n"
        "extern struct Pair64 mk_pair(void);\n"
        "int main(void) { struct Pair64 p = mk_pair(); return (int)(p.a + p.b) - 3; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_heterogeneous_aggregate_call_boundary(tmp_path):
    helper_obj = tmp_path / "helper_pair_triple.o"
    _compile_native_helper(
        (
            "struct Pair64 { long a; long b; };\n"
            "struct Triple { int a; int b; int c; };\n"
            "int sum_pair_triple(struct Pair64 a, struct Triple b) { return (int)(a.a + a.b) + b.a + b.b + b.c; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Pair64 { long a; long b; };\n"
        "struct Triple { int a; int b; int c; };\n"
        "extern int sum_pair_triple(struct Pair64 a, struct Triple b);\n"
        "int main(void) { struct Pair64 a; struct Triple b; a.a = 1; a.b = 2; b.a = 3; b.b = 4; b.c = 5; return sum_pair_triple(a, b) - 15; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_external_aggregate_return_to_external_call_chain(
    tmp_path,
):
    helper_obj = tmp_path / "helper_chain.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "int sum_plus(struct Triple p, int extra) { return p.a + p.b + p.c + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern int sum_plus(struct Triple p, int extra);\n"
        "int main(void) { struct Triple p = mk_triple(); return sum_plus(p, 4) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_nested_external_partial_tail_return_to_external_call(
    tmp_path,
):
    helper_obj = tmp_path / "helper_nested_same.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "int sum_plus(struct Triple p, int extra) { return p.a + p.b + p.c + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern int sum_plus(struct Triple p, int extra);\n"
        "int main(void) { return sum_plus(mk_triple(), 4) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_nested_external_mixed_shape_aggregate_transition(
    tmp_path,
):
    helper_obj = tmp_path / "helper_nested_hetero.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Pair64 { long a; long b; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "struct Pair64 mk_pair(void) { struct Pair64 p = {4, 5}; return p; }\n"
            "int sum_pair_triple(struct Pair64 a, struct Triple b) { return (int)(a.a + a.b) + b.a + b.b + b.c; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "struct Pair64 { long a; long b; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern struct Pair64 mk_pair(void);\n"
        "extern int sum_pair_triple(struct Pair64 a, struct Triple b);\n"
        "int main(void) { return sum_pair_triple(mk_pair(), mk_triple()) - 15; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_nested_external_partial_tail_return_to_fp_boundary(
    tmp_path,
):
    helper_obj = tmp_path / "helper_nested_fp.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "double sum_plus_fp(struct Triple p, double extra) { return (double)(p.a + p.b + p.c) + extra; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern double sum_plus_fp(struct Triple p, double extra);\n"
        "int main(void) { return (int)sum_plus_fp(mk_triple(), 4.0) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_nested_external_partial_tail_return_to_pointer_boundary(
    tmp_path,
):
    helper_obj = tmp_path / "helper_nested_ptr.o"
    _compile_native_helper(
        (
            "struct Triple { int a; int b; int c; };\n"
            "struct Triple mk_triple(void) { struct Triple p = {1, 2, 3}; return p; }\n"
            "int sum_ptr(struct Triple p, int *x) { return p.a + p.b + p.c + *x; }\n"
        ),
        helper_obj,
    )

    source = (
        "struct Triple { int a; int b; int c; };\n"
        "extern struct Triple mk_triple(void);\n"
        "extern int sum_ptr(struct Triple p, int *x);\n"
        "int main(void) { int x = 4; return sum_ptr(mk_triple(), &x) - 10; }\n"
    )

    result = CEvaluator(backend="self", allow_unimplemented_backend=True).evaluate(
        source,
        optimize=0,
        base_dir=str(tmp_path),
        use_system_cpp=False,
        link_args=[str(helper_obj)],
    )

    assert result == 0


def test_self_backend_supports_scalar_condition_aggregate_select_in_ir(tmp_path):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

define void @main(i1 %cond, <4 x i32> %lhs, <4 x i32> %rhs, ptr %out) {
bb0:
  %v = select i1 %cond, <4 x i32> %lhs, <4 x i32> %rhs
  store <4 x i32> %v, ptr %out
  ret void
}
"""

    asm_text = emit_aarch64_darwin_asm(ir_text)

    assert "  csel x13, x10, x11, ne" in asm_text
