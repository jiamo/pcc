"""β4.2 end-to-end: pcc.llvm_capi.ir → pcc.llvm_capi.binding → JIT.

Exercises the full LLVM-C-via-ctypes path — no llvmlite import —
by running the same computations that passed β4.1 parity through
our own binding layer.

This is the β4.2 acceptance gate: if the text-first IR from
``pcc.llvm_capi.ir`` parses, verifies, and JIT-executes correctly
via ``pcc.llvm_capi.binding``, the closed loop is proven end-to-end.
"""
from __future__ import annotations

import ctypes

import pytest

from pcc.llvm_capi import ir as pcc_ir
from pcc.llvm_capi import binding as pcc_bind


@pytest.fixture(scope="module", autouse=True)
def _init_llvm():
    pcc_bind.initialize_native_target()
    pcc_bind.initialize_native_asmprinter()


def _jit_call_ii_i(mod_text: str, fn_name: str, arg: int) -> int:
    """JIT ``int fn_name(int)`` via pcc.llvm_capi.binding."""
    mod = pcc_bind.parse_assembly(mod_text)
    mod.verify()
    tm = pcc_bind.Target.from_default_triple().create_target_machine()
    ee = pcc_bind.create_mcjit_compiler(mod, tm)
    ee.finalize_object()
    addr = ee.get_function_address(fn_name)
    assert addr, f"function {fn_name!r} not found"
    cfn = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_int32)(addr)
    return cfn(arg)


def _jit_call_iii_i(mod_text: str, fn_name: str, a: int, b: int) -> int:
    """JIT ``int fn_name(int, int)`` via pcc.llvm_capi.binding."""
    mod = pcc_bind.parse_assembly(mod_text)
    mod.verify()
    tm = pcc_bind.Target.from_default_triple().create_target_machine()
    ee = pcc_bind.create_mcjit_compiler(mod, tm)
    ee.finalize_object()
    addr = ee.get_function_address(fn_name)
    cfn = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_int32, ctypes.c_int32)(addr)
    return cfn(a, b)


# ---------------------------------------------------------------------------
# Tests — build IR via pcc.llvm_capi.ir, JIT via pcc.llvm_capi.binding
# ---------------------------------------------------------------------------


def test_add_builds_and_runs():
    m = pcc_ir.Module("t")
    i32 = pcc_ir.IntType(32)
    fty = pcc_ir.FunctionType(i32, [i32, i32])
    fn = pcc_ir.Function(m, fty, name="add")
    b = pcc_ir.IRBuilder(fn.append_basic_block("entry"))
    b.ret(b.add(fn.args[0], fn.args[1], name="r"))
    result = _jit_call_iii_i(str(m), "add", 15, 27)
    assert result == 42


def test_fib_recursive():
    m = pcc_ir.Module("t")
    i32 = pcc_ir.IntType(32)
    fty = pcc_ir.FunctionType(i32, [i32])
    fn = pcc_ir.Function(m, fty, name="fib")
    entry = fn.append_basic_block("entry")
    then_b = fn.append_basic_block("then")
    else_b = fn.append_basic_block("else")
    b = pcc_ir.IRBuilder(entry)
    n = fn.args[0]
    two = pcc_ir.Constant(i32, 2)
    cond = b.icmp_signed("<", n, two)
    b.cbranch(cond, then_b, else_b)

    b.position_at_end(then_b)
    b.ret(n)

    b.position_at_end(else_b)
    one = pcc_ir.Constant(i32, 1)
    n1 = b.sub(n, one)
    n2 = b.sub(n, two)
    r1 = b.call(fn, [n1])
    r2 = b.call(fn, [n2])
    r = b.add(r1, r2)
    b.ret(r)

    result = _jit_call_ii_i(str(m), "fib", 10)
    assert result == 55


def test_loop_with_phi():
    """Sum 0..n-1 via while-loop + phi for the accumulator."""
    m = pcc_ir.Module("t")
    i32 = pcc_ir.IntType(32)
    fty = pcc_ir.FunctionType(i32, [i32])
    fn = pcc_ir.Function(m, fty, name="sum0")
    entry = fn.append_basic_block("entry")
    loop = fn.append_basic_block("loop")
    exit_b = fn.append_basic_block("exit")
    b = pcc_ir.IRBuilder(entry)
    b.branch(loop)

    b.position_at_end(loop)
    i_phi = b.phi(i32, name="i")
    s_phi = b.phi(i32, name="s")
    i_phi.add_incoming(pcc_ir.Constant(i32, 0), entry)
    s_phi.add_incoming(pcc_ir.Constant(i32, 0), entry)

    cond = b.icmp_signed("<", i_phi, fn.args[0])
    # Need to stash the increments into new blocks or not?
    # Do arithmetic in loop, then cbranch.
    i_next = b.add(i_phi, pcc_ir.Constant(i32, 1))
    s_next = b.add(s_phi, i_phi)
    i_phi.add_incoming(i_next, loop)
    s_phi.add_incoming(s_next, loop)
    b.cbranch(cond, loop, exit_b)

    b.position_at_end(exit_b)
    b.ret(s_phi)

    # sum of 0..9 = 45
    result = _jit_call_ii_i(str(m), "sum0", 10)
    assert result == 45, f"got {result}"


def test_module_functions_iteration():
    """``ModuleRef.functions`` — used by ir_passes."""
    m = pcc_ir.Module("t")
    i32 = pcc_ir.IntType(32)
    fty = pcc_ir.FunctionType(i32, [])

    f1 = pcc_ir.Function(m, fty, name="one")
    f2 = pcc_ir.Function(m, fty, name="two")

    b = pcc_ir.IRBuilder(f1.append_basic_block("e"))
    b.ret(pcc_ir.Constant(i32, 1))
    b = pcc_ir.IRBuilder(f2.append_basic_block("e"))
    b.ret(pcc_ir.Constant(i32, 2))

    parsed = pcc_bind.parse_assembly(str(m))
    names = [fn.name for fn in parsed.functions]
    assert names == ["one", "two"], f"got {names}"


def test_object_emission():
    """``TargetMachine.emit_object`` — needed for --emit-obj and
    self-host link step."""
    m = pcc_ir.Module("t")
    i32 = pcc_ir.IntType(32)
    fty = pcc_ir.FunctionType(i32, [])
    fn = pcc_ir.Function(m, fty, name="answer")
    b = pcc_ir.IRBuilder(fn.append_basic_block("e"))
    b.ret(pcc_ir.Constant(i32, 42))

    parsed = pcc_bind.parse_assembly(str(m))
    parsed.verify()
    tm = pcc_bind.Target.from_default_triple().create_target_machine()
    obj_bytes = tm.emit_object(parsed)
    # Mach-O / ELF magic bytes
    assert len(obj_bytes) > 64, "object too small"
    # Must start with a recognized object-file magic: Mach-O (cf fa ed fe),
    # ELF (7f 45 4c 46 = ".ELF"), or COFF (4d 5a = "MZ" on Windows).
    head = obj_bytes[:4]
    is_macho = head in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
    is_elf = obj_bytes[:4] == b"\x7fELF"
    is_coff = obj_bytes[:2] == b"MZ"
    assert is_macho or is_elf or is_coff, (
        f"unexpected object magic: {head!r}"
    )


def test_verify_rejects_bad_ir():
    """Verifier should catch type mismatches."""
    bad_ir = """
define i32 @bad() {
entry:
  ret i32
}
"""
    with pytest.raises(RuntimeError):
        pcc_bind.parse_assembly(bad_ir)
