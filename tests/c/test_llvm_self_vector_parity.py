from __future__ import annotations

import re
import subprocess

import pytest

from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.self_backend_targets import is_supported_self_backend_target_triple
from pcc.llvm_capi import binding as llvm
from tests.c_testsuite_cases import PccCompileResult, _host_cc, subprocess_env
from tests.self_backend_c_testsuite_common import assert_result_triplet_matches

pytestmark = pytest.mark.xdist_group(name="llvm_self_vector_parity")


@pytest.fixture(scope="module", autouse=True)
def _init_llvm() -> None:
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()


INT_VECTOR_IR = """
define i32 @main() {
entry:
  %ptr = alloca <4 x i32>, align 16
  store <4 x i32> <i32 1, i32 3, i32 5, i32 7>, ptr %ptr, align 16
  %lane = load <4 x i32>, ptr %ptr, align 16
  %elem = extractelement <4 x i32> %lane, i32 2
  ret i32 %elem
}
"""

PTR_VECTOR_IR = """
@a = global i32 11
@b = global i32 22
@c = global i32 33
@d = global i32 44

define i32 @main() {
entry:
  %slots = alloca <4 x ptr>, align 32
  %v0 = insertelement <4 x ptr> poison, ptr @a, i32 0
  %v1 = insertelement <4 x ptr> %v0, ptr @b, i32 1
  %v2 = insertelement <4 x ptr> %v1, ptr @c, i32 2
  %v3 = insertelement <4 x ptr> %v2, ptr @d, i32 3
  store <4 x ptr> %v3, ptr %slots, align 32
  %loaded = load <4 x ptr>, ptr %slots, align 32
  %elem_ptr = extractelement <4 x ptr> %loaded, i32 2
  %elem = load i32, ptr %elem_ptr
  ret i32 %elem
}
"""


def _host_triple() -> str:
    return llvm.Target.from_default_triple().triple


def _ensure_target_triple(ir_text: str, triple: str) -> str:
    if re.search(r"^target triple = ", ir_text, re.M):
        return ir_text
    return f'target triple = "{triple}"\n' + ir_text


def _result_from_completed_process(
    process: subprocess.CompletedProcess[str],
) -> PccCompileResult:
    return PccCompileResult(
        process.returncode,
        process.stdout,
        (process.stdout if process.returncode == 0 else process.stderr),
    )


def _run_llvm_from_ir(ir_text: str, tmp_path, triple: str) -> PccCompileResult:
    cc = _host_cc()
    ir = _ensure_target_triple(ir_text, triple)
    executable_path = tmp_path / "llvm_case.out"
    ir_path = tmp_path / "llvm_case.ll"
    ir_path.write_text(ir)

    compile_process = subprocess.run(
        [cc, "-x", "ir", str(ir_path), "-o", str(executable_path)],
        env=subprocess_env(),
        capture_output=True,
        text=True,
    )
    if compile_process.returncode != 0:
        return PccCompileResult(compile_process.returncode, "", compile_process.stderr)

    return _result_from_completed_process(
        subprocess.run(
            [str(executable_path)],
            env=subprocess_env(),
            capture_output=True,
            text=True,
        )
    )


def _run_self_from_ir(ir_text: str, tmp_path, triple: str) -> PccCompileResult:
    try:
        asm = emit_self_asm(_ensure_target_triple(ir_text, triple))
    except Exception as exc:
        return PccCompileResult(1, "", str(exc))

    cc = _host_cc()
    asm_path = tmp_path / "self_case.s"
    executable_path = tmp_path / "self_case.out"
    asm_path.write_text(asm)

    compile_process = subprocess.run(
        [cc, str(asm_path), "-o", str(executable_path)],
        env=subprocess_env(),
        capture_output=True,
        text=True,
    )
    if compile_process.returncode != 0:
        return PccCompileResult(compile_process.returncode, "", compile_process.stderr)

    return _result_from_completed_process(
        subprocess.run(
            [str(executable_path)],
            env=subprocess_env(),
            capture_output=True,
            text=True,
        )
    )


def _host_self_supported() -> str:
    triple = _host_triple()
    if not is_supported_self_backend_target_triple(triple):
        pytest.skip(f"self backend target not supported: {triple}")
    return triple


def test_llvm_self_int_vector_lane_matches(tmp_path):
    triple = _host_self_supported()
    llvm_result = _run_llvm_from_ir(INT_VECTOR_IR, tmp_path, triple)
    self_result = _run_self_from_ir(INT_VECTOR_IR, tmp_path, triple)
    assert_result_triplet_matches(
        "llvm-self-int-vector", "llvm", llvm_result, "self", self_result
    )


def test_llvm_self_ptr_vector_lane_matches(tmp_path):
    triple = _host_self_supported()
    llvm_result = _run_llvm_from_ir(PTR_VECTOR_IR, tmp_path, triple)
    self_result = _run_self_from_ir(PTR_VECTOR_IR, tmp_path, triple)
    assert_result_triplet_matches(
        "llvm-self-ptr-vector", "llvm", llvm_result, "self", self_result
    )
