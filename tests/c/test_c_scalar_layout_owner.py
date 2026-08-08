from __future__ import annotations

import inspect
import subprocess

from pcc.c_abi_layout import builtin_scalar_layout, pointer_scalar_layout
from pcc.codegen.c_codegen import (
    LLVMCodeGenerator,
    _ir_type_align_static,
    _ir_type_size_static,
)
from pcc.evaluater.c_evaluator import CEvaluator
from pcc.llvm_capi.compat import ir_c as ir
from pcc.ssa.builder import SSABuilder


def test_scalar_layout_consumers_share_one_contract():
    assert "integer_scalar_layout(" in inspect.getsource(_ir_type_size_static)
    assert "floating_scalar_layout(" in inspect.getsource(_ir_type_size_static)
    assert "pointer_scalar_layout(" in inspect.getsource(_ir_type_size_static)
    assert "integer_scalar_layout(" in inspect.getsource(_ir_type_align_static)
    assert "integer_scalar_layout(" in inspect.getsource(LLVMCodeGenerator._ir_type_size)
    assert "floating_scalar_layout(" in inspect.getsource(LLVMCodeGenerator._ir_type_size)
    assert "pointer_scalar_layout(" in inspect.getsource(LLVMCodeGenerator._ir_type_size)
    assert "integer_scalar_layout(" in inspect.getsource(LLVMCodeGenerator._ir_type_align)
    assert "builtin_scalar_layout(" in inspect.getsource(SSABuilder._builtin_type_size)
    assert "builtin_scalar_layout(" in inspect.getsource(SSABuilder._builtin_type_align)
    assert "pointer_scalar_layout(" in inspect.getsource(SSABuilder._ast_type_size)
    assert "pointer_scalar_layout(" in inspect.getsource(SSABuilder._ast_type_align)


def test_scalar_layout_ir_and_ssa_facts_match():
    cases = (
        (ir.IntType(8), ["char"]),
        (ir.IntType(16), ["short"]),
        (ir.IntType(32), ["int"]),
        (ir.IntType(64), ["long"]),
        (ir.HalfType(), ["_Float16"]),
        (ir.FloatType(), ["float"]),
        (ir.DoubleType(), ["double"]),
    )
    codegen = LLVMCodeGenerator()
    for ir_type, names in cases:
        layout = builtin_scalar_layout(names)
        assert _ir_type_size_static(ir_type) == layout.size
        assert _ir_type_align_static(ir_type) == layout.alignment
        assert codegen._ir_type_size(ir_type) == layout.size
        assert codegen._ir_type_align(ir_type) == layout.alignment
        assert SSABuilder._builtin_type_size(names) == layout.size
        assert SSABuilder._builtin_type_align(names) == layout.alignment

    pointer = ir.IntType(8).as_pointer()
    assert _ir_type_size_static(pointer) == pointer_scalar_layout().size
    assert _ir_type_align_static(pointer) == pointer_scalar_layout().alignment


def test_scalar_layout_matches_native_compiler(tmp_path):
    source = r"""
        int main(void) {
            return
                sizeof(char) + sizeof(short) + sizeof(int) + sizeof(long) +
                sizeof(long long) + sizeof(float) + sizeof(double) + sizeof(void *) +
                _Alignof(char) + _Alignof(short) + _Alignof(int) + _Alignof(long) +
                _Alignof(long long) + _Alignof(float) + _Alignof(double) +
                _Alignof(void *);
        }
    """
    src = tmp_path / "scalar_layout.c"
    native = tmp_path / "scalar_layout_native"
    src.write_text(source, encoding="utf-8")
    build = subprocess.run(
        ["cc", str(src), "-o", str(native)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr
    native_run = subprocess.run([str(native)], capture_output=True, timeout=30)
    assert native_run.returncode == 86

    assert CEvaluator().evaluate(source, optimize=False) == native_run.returncode
