from __future__ import annotations

import os
import platform
import subprocess
import textwrap
from pathlib import Path

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_aarch64_darwin_compute import (
    _vector_lane_stride as compute_vector_lane_stride,
)
from pcc.backend.self_backend_aarch64_darwin_calls import (
    _vector_lane_stride as calls_vector_lane_stride,
)
from pcc.backend.self_backend_aarch64_darwin_materialize import materialize_value
from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.self_backend_ir import ParsedFunction, TypeDesc
from pcc.backend.self_backend_module_symbols import PreparedModuleSymbols


def _ptr_vector(count: int = 2) -> TypeDesc:
    return TypeDesc("array", count=count, elem=TypeDesc("void").ptr())


def test_compute_vector_lane_stride_accepts_pointer_lanes():
    lane, stride = compute_vector_lane_stride(_ptr_vector())
    assert lane.is_ptr
    assert stride == 8


def test_vector_intrinsic_lane_stride_still_rejects_pointer_lanes():
    with pytest.raises(BackendUnavailable):
        calls_vector_lane_stride(_ptr_vector())


def test_symbolic_ptr_vector_literal_materializes_to_gprs():
    func = ParsedFunction(
        name="probe",
        ret_type=TypeDesc("void"),
        args=[],
        is_global=True,
        is_vararg=False,
        blocks=[],
    )
    symbols = PreparedModuleSymbols(
        internal_prefix="__test_",
        defined_symbols=frozenset({"global_a"}),
        internal_symbols=frozenset(),
    )
    lines = materialize_value(
        func,
        "[ptr @global_a, ptr null]",
        _ptr_vector(),
        9,
        symbols,
    )
    text = "\n".join(lines)
    assert "global_a@PAGE" in text
    assert "movz x10, #0" in text


_PTR_VECTOR_IR = textwrap.dedent(
    """
    target triple = "arm64-apple-darwin23.0.0"

    @a = global i64 11
    @b = global i64 22

    define i32 @main() {
    entry:
      %v0 = insertelement [2 x ptr] zeroinitializer, ptr @a, i32 0
      %v1 = insertelement [2 x ptr] %v0, ptr @b, i32 1
      %p = extractelement [2 x ptr] %v1, i32 1
      %loaded = load i64, ptr %p
      %tr = trunc i64 %loaded to i32
      ret i32 %tr
    }
    """
)


_POISON_POINTER_IR = textwrap.dedent(
    """
    target triple = "arm64-apple-darwin23.0.0"

    define i32 @main() {
    entry:
      br label %do_store

    do_store:
      store i1 false, ptr poison, align 1
      ret i32 0
    }
    """
)


def test_poison_pointer_storage_address_emits_self_backend_asm():
    asm = emit_self_asm(_POISON_POINTER_IR)
    assert "movz x9, #0" in asm


def test_ptr_vector_insert_extract_emits_self_backend_asm():
    asm = emit_self_asm(_PTR_VECTOR_IR)
    # The exact register allocation can evolve, but pointer vector lowering
    # must materialize symbol addresses and perform scalar 8-byte lane moves.
    assert "_a@PAGE" in asm or "a@PAGE" in asm
    assert "_b@PAGE" in asm or "b@PAGE" in asm
    assert "str x" in asm
    assert "ldr x" in asm


@pytest.mark.skipif(
    platform.system() != "Darwin" or platform.machine() not in {"arm64", "aarch64"},
    reason="AArch64 Darwin self-backend runtime smoke",
)
def test_ptr_vector_insert_extract_runs_on_aarch64_darwin(tmp_path: Path):
    asm = emit_self_asm(_PTR_VECTOR_IR)
    asm_path = tmp_path / "ptr_vector.s"
    exe = tmp_path / "ptr_vector.out"
    asm_path.write_text(asm, encoding="utf-8")
    build = subprocess.run(
        [os.environ.get("CC", "cc"), str(asm_path), "-o", str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30)
    assert run.returncode == 22, run.stdout + run.stderr
