"""β4.1 parity test: pcc.llvm_capi.ir vs llvmlite.ir.

Build the same module via both builders and run **Level-1 semantic
equivalence**:

1. Both outputs ``parse_assembly`` successfully
2. Both ``verify`` without errors
3. Both produce modules with the same function signatures, global
   variable types, and block structure

Text-level alignment (anonymous-temp numbering `%.N`, formatting
detail) is β4.3 polish — tracked by ``test_llvm_capi_ir_text_parity``
(added later), not a hard Level-1 gate here.
"""
from __future__ import annotations

import subprocess
import sys
import re

import llvmlite.binding as llvm
import llvmlite.ir as llvmlite_ir
import pytest

from pcc.llvm_capi import ir as pcc_ir
from pcc.evaluater.c_evaluator import (
    _compile_preprocessed_translation_unit_artifact,
)

llvm.initialize_native_target()


def _parse_verify(text: str):
    """Parse IR text and run LLVM's verifier. Returns the ModuleRef."""
    mod = llvm.parse_assembly(text)
    mod.verify()
    return mod


def test_native_ir_builder_emits_verifiable_c_debug_metadata(tmp_path) -> None:
    artifact = _compile_preprocessed_translation_unit_artifact(
        "debug_probe.c",
        "int add(int a, int b) { return a + b; }\n"
        "int main(void) { return add(1, 2) != 3; }\n",
        emit_debug=True,
    )
    ir_text = artifact["ir_text"]
    _parse_verify(ir_text)
    assert "!DICompileUnit(" in ir_text
    assert "!DISubprogram(" in ir_text
    assert "!llvm.dbg.cu = !{" in ir_text
    assert "!llvm.module.flags = !{" in ir_text
    assert "!dbg !" in ir_text
    compile_unit_match = re.search(
        r"!llvm\.dbg\.cu = !\{\s*!(\d+)\s*\}", ir_text
    )
    assert compile_unit_match is not None
    assert re.search(
        rf"^!{compile_unit_match.group(1)} = distinct !DICompileUnit\(",
        ir_text,
        re.MULTILINE,
    )
    call_lines = [line for line in ir_text.splitlines() if " call " in line]
    assert call_lines
    assert all("!dbg !" in line for line in call_lines)

    llvm_path = tmp_path / "debug_probe.ll"
    object_path = tmp_path / "debug_probe.o"
    llvm_path.write_text(ir_text, encoding="utf-8")
    compiled = subprocess.run(
        ["clang", "-c", str(llvm_path), "-o", str(object_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert "invalid debug" not in compiled.stderr.lower()
    debug_command = (
        ["dwarfdump", "--debug-info", str(object_path)]
        if sys.platform == "darwin"
        else ["readelf", "--debug-dump=info", str(object_path)]
    )
    debug_dump = subprocess.run(
        debug_command,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert debug_dump.returncode == 0, debug_dump.stdout + debug_dump.stderr
    assert "DW_TAG_compile_unit" in debug_dump.stdout
    assert "DW_TAG_subprogram" in debug_dump.stdout


def _diff(a: str, b: str) -> str:
    # Show side-by-side diff on failure
    out = []
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        left = la[i] if i < len(la) else ""
        right = lb[i] if i < len(lb) else ""
        marker = "  " if left == right else "* "
        out.append(f"{marker}{i:3d}: {left!r:60}  |  {right!r}")
    return "\n".join(out)


def _structural_signature(mod_ref) -> tuple:
    """Return a structural signature of a ModuleRef for comparison.

    Captures function name + signature text + block count + terminator
    type per block. Skips the anonymous-temp numbering which diverges
    innocuously between llvmlite and pcc.llvm_capi text output."""
    sig = []
    for fn in mod_ref.functions:
        block_sigs = []
        for blk in fn.blocks:
            instrs = list(blk.instructions)
            # Last instruction is the terminator
            last = instrs[-1] if instrs else None
            block_sigs.append((
                len(instrs),
                str(last.opcode) if last else None,
            ))
        sig.append((
            fn.name,
            str(fn.type),
            tuple(block_sigs),
        ))
    globals_sig = []
    for g in mod_ref.global_variables:
        globals_sig.append((g.name, str(g.type)))
    return (tuple(sig), tuple(globals_sig))


def _check_parity(build_ll, build_pcc):
    """Build a module with each builder and verify Level-1 semantic
    equivalence: both parse, both verify, same structural signature."""
    ll_mod_obj = build_ll()
    pcc_mod_obj = build_pcc()

    try:
        ll_ref = _parse_verify(str(ll_mod_obj))
    except Exception as e:
        pytest.fail(f"llvmlite module rejected by LLVM parser: {e}")
    try:
        pcc_ref = _parse_verify(str(pcc_mod_obj))
    except Exception as e:
        pytest.fail(
            f"pcc.llvm_capi module rejected by LLVM parser: {e}\n"
            f"=== source ===\n{pcc_mod_obj}"
        )

    ll_sig = _structural_signature(ll_ref)
    pcc_sig = _structural_signature(pcc_ref)

    if ll_sig != pcc_sig:
        pytest.fail(
            f"structural signature mismatch:\n"
            f"llvmlite: {ll_sig}\n"
            f"pcc:      {pcc_sig}\n"
            f"=== llvmlite source ===\n{ll_mod_obj}\n"
            f"=== pcc source ===\n{pcc_mod_obj}"
        )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_scaffold_function_type_dynamic_false_is_not_vararg():
    i64 = pcc_ir.IntType(64)
    void = pcc_ir.VoidType()

    fty_false = pcc_ir.FunctionType___init___dyn(void, [i64], False)
    fty_none = pcc_ir.FunctionType___init___dyn(void, [i64], None)
    fty_true = pcc_ir.FunctionType___init___dyn(void, [i64], True)
    fty_fixed_arity_false = pcc_ir.FunctionType___init__1_dyn_va(
        void, i64, False,
    )
    fty_three = pcc_ir.FunctionType___init__3(void, i64, i64, i64)

    assert str(fty_false) == "void (i64)"
    assert str(fty_none) == "void (i64)"
    assert str(fty_fixed_arity_false) == "void (i64)"
    assert str(fty_true) == "void (i64, ...)"
    assert str(fty_three) == "void (i64, i64, i64)"


def test_function_initializes_value_runtime_fields():
    m = pcc_ir.Module("t")
    i32 = pcc_ir.IntType(32)
    fty = pcc_ir.FunctionType(i32, [i32])
    fn = pcc_ir.Function(m, fty, name="identity")

    assert str(fn) == "@identity"
    assert fn.type.pointee is fty
    assert fn._instr is None
    assert fn._flags == []
    assert fn._is_unsigned is False
    assert fn._pcc_unsigned_pointee is False
    assert fn._pcc_unsigned_return is False


def test_simple_add():
    def ll_build():
        m = llvmlite_ir.Module("t")
        i32 = llvmlite_ir.IntType(32)
        fty = llvmlite_ir.FunctionType(i32, [i32, i32])
        fn = llvmlite_ir.Function(m, fty, name="add")
        b = llvmlite_ir.IRBuilder(fn.append_basic_block("entry"))
        b.ret(b.add(fn.args[0], fn.args[1], name="sum"))
        return m

    def pcc_build():
        m = pcc_ir.Module("t")
        i32 = pcc_ir.IntType(32)
        fty = pcc_ir.FunctionType(i32, [i32, i32])
        fn = pcc_ir.Function(m, fty, name="add")
        b = pcc_ir.IRBuilder(fn.append_basic_block("entry"))
        b.ret(b.add(fn.args[0], fn.args[1], name="sum"))
        return m

    _check_parity(ll_build, pcc_build)


def test_branch_and_phi():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        i1 = ir_mod.IntType(1)
        fty = ir_mod.FunctionType(i32, [i1])
        fn = ir_mod.Function(m, fty, name="pick")
        entry = fn.append_basic_block("entry")
        then_b = fn.append_basic_block("then")
        else_b = fn.append_basic_block("else")
        join = fn.append_basic_block("join")
        b = ir_mod.IRBuilder(entry)
        b.cbranch(fn.args[0], then_b, else_b)
        b.position_at_end(then_b)
        b.branch(join)
        b.position_at_end(else_b)
        b.branch(join)
        b.position_at_end(join)
        phi = b.phi(i32, name="r")
        phi.add_incoming(ir_mod.Constant(i32, 1), then_b)
        phi.add_incoming(ir_mod.Constant(i32, 2), else_b)
        b.ret(phi)
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_phi_backedge_added_after_builder_moves_blocks():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        fty = ir_mod.FunctionType(i32, [])
        fn = ir_mod.Function(m, fty, name="count")
        entry = fn.append_basic_block("entry")
        loop = fn.append_basic_block("loop")
        body = fn.append_basic_block("body")
        done = fn.append_basic_block("done")
        b = ir_mod.IRBuilder(entry)
        b.branch(loop)
        b.position_at_end(loop)
        index = b.phi(i32, name="index")
        index.add_incoming(ir_mod.Constant(i32, 0), entry)
        keep_going = b.icmp_signed("<", index, ir_mod.Constant(i32, 4))
        b.cbranch(keep_going, body, done)
        b.position_at_end(body)
        next_index = b.add(index, ir_mod.Constant(i32, 1), name="next")
        b.branch(loop)
        # This is the ordinary loop-construction order: the backedge value is
        # unavailable while the builder is still positioned in the phi block.
        index.add_incoming(next_index, body)
        b.position_at_end(done)
        b.ret(index)
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_arith_ops():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        fty = ir_mod.FunctionType(i32, [i32, i32])
        fn = ir_mod.Function(m, fty, name="arith")
        b = ir_mod.IRBuilder(fn.append_basic_block("entry"))
        a, c = fn.args[0], fn.args[1]
        x1 = b.add(a, c, name="add")
        x2 = b.sub(x1, c, name="sub")
        x3 = b.mul(x2, c, name="mul")
        x4 = b.sdiv(x3, c, name="sdiv")
        x5 = b.and_(x4, c, name="and")
        x6 = b.or_(x5, c, name="or")
        x7 = b.xor(x6, c, name="xor")
        x8 = b.shl(x7, ir_mod.Constant(i32, 2), name="shl")
        b.ret(x8)
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_memory_ops():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        fty = ir_mod.FunctionType(i32, [])
        fn = ir_mod.Function(m, fty, name="mem")
        b = ir_mod.IRBuilder(fn.append_basic_block("entry"))
        ptr = b.alloca(i32, name="slot")
        b.store(ir_mod.Constant(i32, 42), ptr)
        v = b.load(ptr, name="v")
        b.ret(v)
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_atomic_load_store_ops():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        fty = ir_mod.FunctionType(i32, [])
        fn = ir_mod.Function(m, fty, name="atomic_mem")
        b = ir_mod.IRBuilder(fn.append_basic_block("entry"))
        ptr = b.alloca(i32, name="slot")
        b.store_atomic(ir_mod.Constant(i32, 42), ptr, "seq_cst", 4)
        v = b.load_atomic(ptr, "seq_cst", 4, name="v")
        b.ret(v)
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_icmp_select():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        fty = ir_mod.FunctionType(i32, [i32, i32])
        fn = ir_mod.Function(m, fty, name="maxi")
        b = ir_mod.IRBuilder(fn.append_basic_block("entry"))
        a, c = fn.args[0], fn.args[1]
        cond = b.icmp_signed(">", a, c, name="gt")
        r = b.select(cond, a, c, name="r")
        b.ret(r)
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_call_and_declaration():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        # declare i32 @extfunc(i32)
        ext_fty = ir_mod.FunctionType(i32, [i32])
        ext = ir_mod.Function(m, ext_fty, name="extfunc")
        # define i32 @caller(i32)
        fty = ir_mod.FunctionType(i32, [i32])
        fn = ir_mod.Function(m, fty, name="caller")
        b = ir_mod.IRBuilder(fn.append_basic_block("entry"))
        r = b.call(ext, [fn.args[0]], name="r")
        b.ret(r)
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_global_variable():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        gv = ir_mod.GlobalVariable(m, i32, name="counter")
        gv.initializer = ir_mod.Constant(i32, 0)
        gv.linkage = "internal"
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_thread_local_global_variable():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        gv = ir_mod.GlobalVariable(m, i32, name="counter")
        gv.initializer = ir_mod.Constant(i32, 7)
        gv.storage_class = "thread_local"
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_void_return():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        fty = ir_mod.FunctionType(ir_mod.VoidType(), [])
        fn = ir_mod.Function(m, fty, name="nothing")
        b = ir_mod.IRBuilder(fn.append_basic_block("entry"))
        b.ret_void()
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_casts():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        i64 = ir_mod.IntType(64)
        i8 = ir_mod.IntType(8)
        fty = ir_mod.FunctionType(i64, [i32])
        fn = ir_mod.Function(m, fty, name="casts")
        b = ir_mod.IRBuilder(fn.append_basic_block("entry"))
        x = fn.args[0]
        s = b.sext(x, i64, name="s")
        z = b.zext(x, i64, name="z")
        t = b.trunc(x, i8, name="t")
        # Use the trunc'd result back up so all three get used
        _ = b.zext(t, i32)
        _ = z
        b.ret(s)
        return m

    _check_parity(lambda: _build(llvmlite_ir), lambda: _build(pcc_ir))


def test_pcc_ir_builder_elides_same_type_casts():
    m = pcc_ir.Module("t")
    i64 = pcc_ir.IntType(64)
    double = pcc_ir.DoubleType()
    ptr = pcc_ir.IntType(8).as_pointer()
    fty = pcc_ir.FunctionType(i64, [i64, double, ptr])
    fn = pcc_ir.Function(m, fty, name="same_type_casts")
    b = pcc_ir.IRBuilder(fn.append_basic_block("entry"))

    x = fn.args[0]
    y = fn.args[1]
    p = fn.args[2]
    assert b.sext(x, i64, name="bad_sext") is x
    assert b.zext(x, i64, name="bad_zext") is x
    assert b.trunc(x, i64, name="bad_trunc") is x
    assert b.fpext(y, double, name="bad_fpext") is y
    assert b.fptrunc(y, double, name="bad_fptrunc") is y
    assert b.bitcast(p, ptr, name="bad_bitcast") is p
    b.ret(x)

    text = str(m)
    assert "sext i64" not in text
    assert "zext i64" not in text
    assert "trunc i64" not in text
    assert "fpext double" not in text
    assert "fptrunc double" not in text
    assert "bitcast ptr" not in text
    _parse_verify(text)


def test_pcc_ir_builder_bitcast_function_operand_uses_symbol_ref():
    m = pcc_ir.Module("function_bitcast")
    void = pcc_ir.VoidType()
    fn_ty = pcc_ir.FunctionType(void, [])
    target = pcc_ir.Function(m, fn_ty, name="target")
    user = pcc_ir.Function(m, fn_ty, name="user")
    b = pcc_ir.IRBuilder(user.append_basic_block("entry"))

    b.bitcast(target, pcc_ir.IntType(8).as_pointer(), name="fp")
    b.ret_void()

    text = str(m)
    assert "bitcast ptr @target to ptr" in text
    assert "bitcast ; ModuleID" not in text
    _parse_verify(text)


def test_pcc_ir_value_ref_accepts_structural_function_like_object():
    class FunctionLike:
        def __init__(self):
            self.name = "target"
            self.ftype = object()

        def __str__(self):
            return "bad-module-text"

    assert pcc_ir._value_ref(FunctionLike()) == "@target"


# ---------------------------------------------------------------------------
# Execution parity — JIT the pcc.llvm_capi-built module and verify the
# result matches the llvmlite-built one. This is the strongest Level-1
# gate: if LLVM JITs both to correct machine code with matching output,
# semantic equivalence is proven.
# ---------------------------------------------------------------------------


import ctypes as _ctypes

llvm.initialize_native_asmprinter()


def _jit_call_int_int(mod_text: str, fn_name: str, arg: int) -> int:
    """JIT the module, call ``fn_name(arg) -> int32``, return result."""
    mod = llvm.parse_assembly(mod_text)
    mod.verify()
    tm = llvm.Target.from_default_triple().create_target_machine()
    ee = llvm.create_mcjit_compiler(mod, tm)
    ee.finalize_object()
    ee.run_static_constructors()
    addr = ee.get_function_address(fn_name)
    cfn = _ctypes.CFUNCTYPE(_ctypes.c_int32, _ctypes.c_int32)(addr)
    return cfn(arg)


def test_execution_parity_add():
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        fty = ir_mod.FunctionType(i32, [i32])
        fn = ir_mod.Function(m, fty, name="add_seven")
        b = ir_mod.IRBuilder(fn.append_basic_block("entry"))
        seven = ir_mod.Constant(i32, 7)
        r = b.add(fn.args[0], seven, name="r")
        b.ret(r)
        return m

    ll_text = str(_build(llvmlite_ir))
    pcc_text = str(_build(pcc_ir))

    ll_result = _jit_call_int_int(ll_text, "add_seven", 10)
    pcc_result = _jit_call_int_int(pcc_text, "add_seven", 10)

    assert ll_result == 17 == pcc_result


def test_execution_parity_recursive():
    """Fibonacci — recursive branch, phi-less implementation."""
    def _build(ir_mod):
        m = ir_mod.Module("t")
        i32 = ir_mod.IntType(32)
        fty = ir_mod.FunctionType(i32, [i32])
        fn = ir_mod.Function(m, fty, name="fib")
        entry = fn.append_basic_block("entry")
        then_b = fn.append_basic_block("then")
        else_b = fn.append_basic_block("else")
        b = ir_mod.IRBuilder(entry)
        n = fn.args[0]
        two = ir_mod.Constant(i32, 2)
        cond = b.icmp_signed("<", n, two)
        b.cbranch(cond, then_b, else_b)

        b.position_at_end(then_b)
        b.ret(n)

        b.position_at_end(else_b)
        one = ir_mod.Constant(i32, 1)
        n1 = b.sub(n, one)
        n2 = b.sub(n, two)
        r1 = b.call(fn, [n1])
        r2 = b.call(fn, [n2])
        r = b.add(r1, r2)
        b.ret(r)
        return m

    ll_text = str(_build(llvmlite_ir))
    pcc_text = str(_build(pcc_ir))

    ll_result = _jit_call_int_int(ll_text, "fib", 10)
    pcc_result = _jit_call_int_int(pcc_text, "fib", 10)

    assert ll_result == 55 == pcc_result, f"ll={ll_result} pcc={pcc_result}"
