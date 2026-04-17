"""Phase 9 ratchet: pcc/llvm_capi/ir.py py_cpy_* count is monotonically
decreasing.

This is the gate test for Issue 1 closure. ir.py must reach 0 py_cpy_*
in ON mode for the bootstrap binary to be link-clean (no libpython).

Each Phase 9 sub-task should DECREASE the numbers in
``ir_py_baseline.json``. No commit may increase them. When a fix lands,
re-capture the baseline (smaller numbers) and re-run pytest.
"""
from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BASELINE_JSON = _REPO_ROOT / "tests" / "ir_py_baseline.json"
_IR_PY = _REPO_ROOT / "pcc" / "llvm_capi" / "ir.py"


def _load_baseline() -> dict:
    with open(_BASELINE_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def ir_py_on_counts() -> dict:
    """Compile ir.py in ON mode, return per-symbol py_cpy_* counts."""
    from pcc.py_frontend.pipeline import compile_python

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "ir.ll"
        compile_python(
            str(_IR_PY), str(out),
            emit_llvm_only=True,
            ir_scaffold_mode="on",
        )
        text = out.read_text()
        # Per-symbol call counts: only CALL instructions (excludes
        # declare/define lines so we don't over-count by the number of
        # extern declarations).
        call_pattern = re.compile(
            r"\bcall [^\n]*@(py_cpy_[a-z0-9_]+)"
        )
        by_sym = dict(Counter(call_pattern.findall(text)))
        n_total = sum(by_sym.values())
        by_sym["_total"] = n_total
        return by_sym


def test_ir_py_total_does_not_regress(ir_py_on_counts):
    """Aggregate count must not exceed baseline. Path A's success
    requires this number to monotonically reach 0."""
    baseline = _load_baseline()
    expected_total = baseline["_total"]
    actual_total = ir_py_on_counts["_total"]
    assert actual_total <= expected_total, (
        f"ir.py py_cpy_* total regressed: {actual_total} > "
        f"{expected_total}. If this is intentional progress (a "
        f"REDUCTION), recapture baseline JSON. If it's an unintended "
        f"regression, find the new dynamic-Python idiom that crept in."
    )


def test_ir_py_per_symbol_does_not_regress(ir_py_on_counts):
    """No single ``py_cpy_*`` symbol's count grows past its baseline.
    Per-symbol granularity catches localised regressions: e.g. a fix
    that drops total by 10 but increases ``py_cpy_call1`` by 2 still
    fails this test (something else got worse during the fix).

    Symbols that didn't appear in the baseline must stay at 0 (i.e.
    no NEW dynamic-Python symbol may surface).
    """
    baseline = _load_baseline()
    failures: list[str] = []

    for sym, baseline_count in baseline.items():
        if sym.startswith("_"):
            continue
        actual = ir_py_on_counts.get(sym, 0)
        if actual > baseline_count:
            failures.append(
                f"{sym}: {actual} > baseline {baseline_count}"
            )

    for sym, actual in ir_py_on_counts.items():
        if sym.startswith("_"):
            continue
        if sym in baseline:
            continue
        if actual > 0:
            failures.append(
                f"{sym}: {actual} (NEW symbol — was 0 in baseline)"
            )

    assert not failures, (
        "ir.py per-symbol regressions:\n  "
        + "\n  ".join(failures)
        + "\n(if intentional reduction, recapture baseline)"
    )


def test_ir_py_baseline_self_consistent():
    """Sanity: the JSON's _total field equals the sum of per-symbol
    counts. Catches editing typos in the baseline."""
    baseline = _load_baseline()
    expected = baseline["_total"]
    actual = sum(
        v for k, v in baseline.items()
        if not k.startswith("_") and isinstance(v, int)
    )
    assert actual == expected, (
        f"baseline JSON inconsistent: _total={expected} but "
        f"per-symbol sum={actual}"
    )
