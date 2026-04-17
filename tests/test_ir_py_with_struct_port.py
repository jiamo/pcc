"""Integration test: with ``recursive_stdlib=True`` and the
``pcc/stdlib/struct.py`` port present, ir.py's own py_cpy_* count
drops (its ``import struct`` resolves natively).

Issue 11.D verification — proves the chain:
  recursive_stdlib walker (11.B.1)
  + codegen native-import wiring (11.B.1.2)
  + pcc/stdlib/ registry (11.C.1)
  + struct port (11.C.2)
delivers concrete py_cpy_* reduction in ir.py.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest


_IR_PY = Path(__file__).resolve().parent.parent / "pcc" / "llvm_capi" / "ir.py"


def _count_py_cpy_in_module_funcs(ir_text: str, mod_prefix: str) -> int:
    """Count py_cpy_* calls in functions belonging to ``mod_prefix``."""
    n = 0
    pattern = re.compile(
        r"define[^\n]+@(" + re.escape(mod_prefix)
        + r"[A-Za-z0-9_]+)[^{]*\{(.+?)\n\}",
        re.DOTALL,
    )
    for m in pattern.finditer(ir_text):
        n += len(re.findall(r"\bcall [^\n]*@py_cpy_", m.group(2)))
    return n


def test_ir_py_struct_calls_routed_natively():
    """ir.py's ``import struct`` + ``_struct.pack/unpack`` should
    resolve via the pcc/stdlib/struct.py port when
    recursive_stdlib=True. Net effect: ir.py's own py_cpy_* count
    drops vs the no-recursive-stdlib path."""
    from pcc.py_frontend.pipeline import compile_python

    with tempfile.TemporaryDirectory() as td:
        out_baseline = Path(td) / "baseline.ll"
        out_with_port = Path(td) / "with_port.ll"
        compile_python(
            str(_IR_PY), str(out_baseline),
            emit_llvm_only=True, ir_scaffold_mode="on",
        )
        compile_python(
            str(_IR_PY), str(out_with_port),
            emit_llvm_only=True, ir_scaffold_mode="on",
            recursive_stdlib=True,
        )

        # Both compiles should succeed.
        assert out_baseline.exists()
        assert out_with_port.exists()

        baseline_text = out_baseline.read_text()
        with_port_text = out_with_port.read_text()

        # ir.py's OWN portion (functions matching the ir module
        # mangle prefix) should have fewer py_cpy_* with the port.
        n_baseline = _count_py_cpy_in_module_funcs(
            baseline_text, "user_pcc_llvm_capi_ir_",
        )
        n_with_port = _count_py_cpy_in_module_funcs(
            with_port_text, "user_pcc_llvm_capi_ir_",
        )

        assert n_with_port < n_baseline, (
            f"ir.py portion didn't drop: baseline={n_baseline}, "
            f"with_port={n_with_port}"
        )
        # Sanity: drop must be meaningful (≥10%). Tracks current
        # observed reduction (~15% via _float_bits helpers); future
        # native dispatch for int.to_bytes etc. should push higher.
        assert n_with_port <= n_baseline * 0.90, (
            f"ir.py drop smaller than expected: "
            f"baseline={n_baseline}, with_port={n_with_port}"
        )


def test_float_bits_helpers_pulled_into_compile():
    """The pcc/stdlib/_float_bits.py module — which provides the
    int-form helpers ir.py now uses directly — should appear in the
    combined IR when compiled with recursive_stdlib=True."""
    from pcc.py_frontend.pipeline import compile_python

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "with_port.ll"
        compile_python(
            str(_IR_PY), str(out),
            emit_llvm_only=True, ir_scaffold_mode="on",
            recursive_stdlib=True,
        )
        text = out.read_text()
        assert re.search(
            r"@user_pcc_stdlib__float_bits__float64_to_bits\b", text,
        ), "float_bits._float64_to_bits should be defined in combined IR"
        assert re.search(
            r"@user_pcc_stdlib__float_bits__round_to_float32\b", text,
        ), "float_bits._round_to_float32 should be defined"


def test_ir_py_portion_drops_with_recursive_stdlib():
    """Per-file metric: ir.py's OWN portion of the combined IR has
    fewer py_cpy_* with recursive_stdlib=True than without.

    The _float_bits helpers add their own py_cpy_* (mostly from
    builtin lookups not yet covered — int.to_bytes etc.); reducing
    those is a future Issue 11.A extension. The per-file metric is
    the right gate for Issue 1 closure on a file-by-file basis."""
    from pcc.py_frontend.pipeline import compile_python

    with tempfile.TemporaryDirectory() as td:
        out_solo = Path(td) / "solo.ll"
        out_combined = Path(td) / "combined.ll"
        compile_python(
            str(_IR_PY), str(out_solo),
            emit_llvm_only=True, ir_scaffold_mode="on",
        )
        compile_python(
            str(_IR_PY), str(out_combined),
            emit_llvm_only=True, ir_scaffold_mode="on",
            recursive_stdlib=True,
        )

        n_solo = _count_py_cpy_in_module_funcs(
            out_solo.read_text(), "user_pcc_llvm_capi_ir_",
        )
        n_combined = _count_py_cpy_in_module_funcs(
            out_combined.read_text(), "user_pcc_llvm_capi_ir_",
        )
        assert n_combined < n_solo, (
            f"ir.py portion should drop: "
            f"solo={n_solo}, combined={n_combined}"
        )
