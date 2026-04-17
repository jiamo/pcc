"""Baseline regression tests for the stage1 closure probe.

This is the gate that protects ongoing closed-world (Path A) work from
regressing. It does NOT block reductions — it only blocks growth.

What's tested:
- The stage1 tight closure compiles and produces IR (no codegen
  regressions in the frontend's own self-compile path).
- The total ``py_cpy_*`` fallback count does not exceed the captured
  baseline (with a small ratchet allowed for noise).
- Per-module fallback counts do not regress past the captured numbers.

When Path A migrates a file successfully, the baseline JSON should be
re-captured (lower numbers); the ratchet is one-way (allow shrink,
forbid growth).

Run only this file:
    pytest tests/test_fallback_baseline.py -v

Closes Issue 9 (closure-probe baseline not in CI).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BASELINE_JSON = _REPO_ROOT / "tests" / "fallback_baseline.json"

# Allow a small ratchet for noise. Anything beyond this fails the gate.
# Path A's job is to push numbers DOWN; this just blocks creep upward.
_RATCHET_PERCENT = 5.0
_BRIDGE_CPY_SYMBOLS = frozenset({
    "py_cpy_to_pcc_obj",
    "py_cpy_to_pcc_str",
})


def _load_baseline() -> dict:
    with open(_BASELINE_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _within_ratchet(actual: int, baseline: int) -> bool:
    """Allow up to _RATCHET_PERCENT growth (rounded up) from baseline.

    Equality (or shrinkage) always passes; growth past ratchet fails.
    """
    if actual <= baseline:
        return True
    cap = baseline + max(1, int(baseline * _RATCHET_PERCENT / 100.0))
    return actual <= cap


def _count_py_cpy_calls(ir_text: str) -> int:
    return len(re.findall(r"\bcall [^\n]*@py_cpy_", ir_text))


def _split_py_cpy_calls(ir_text: str) -> dict[str, int]:
    symbols = re.findall(r"\bcall [^\n]*@(py_cpy_[a-z0-9_]+)", ir_text)
    bridge = sum(1 for sym in symbols if sym in _BRIDGE_CPY_SYMBOLS)
    total = len(symbols)
    return {
        "total": total,
        "bridge": bridge,
        "non_bridge": total - bridge,
    }


def _per_module_and_multi(srcs, mods, *, ir_scaffold_mode: str):
    """Codegen each module independently and as a multi-file bundle.

    Returns ``(per_module_dict, multi_ok, total_fallbacks_or_none)``.
    ``ir_scaffold_mode`` selects ``off`` (the historical baseline) or
    ``on`` (the closed-world dispatch path); each gets its own ratchet.
    """
    from pcc.py_frontend import type_infer as _type_infer
    from pcc.py_frontend.codegen import layer1 as _layer1
    from pcc.parse.py_lift import parse_and_lift
    import importlib.util as _imputil

    spec = _imputil.spec_from_file_location(
        "_probe_stage1_closure",
        str(_REPO_ROOT / "scripts" / "probe_stage1_closure.py"),
    )
    probe_mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(probe_mod)

    per_module: dict[str, int] = {}
    per_module_ok = 0
    for src, mod in zip(srcs, mods):
        try:
            with open(src, "r", encoding="utf-8") as f:
                source = f.read()
            ast_mod = parse_and_lift(source, src, mod)
            typed = _type_infer.infer_module(ast_mod)
            cg = _layer1.L1CodeGen(
                typed,
                emit_cpy_main_exitcode=False,
                ir_scaffold_mode=ir_scaffold_mode,
            )
            ir = cg.generate(typed)
            ir_text = str(ir)
            per_module[mod] = _count_py_cpy_calls(ir_text)
            per_module_ok += 1
        except Exception:
            per_module[mod] = -1
    # The multi-file path doesn't take an ir_scaffold_mode kw today;
    # set the env var the pipeline reads so multi-file inherits the
    # mode for the duration of the call.
    saved = os.environ.get("PCC_IR_SCAFFOLD")
    if ir_scaffold_mode == "on":
        os.environ["PCC_IR_SCAFFOLD"] = "on"
    else:
        os.environ.pop("PCC_IR_SCAFFOLD", None)
    try:
        multi_ok, ir_text, _ = probe_mod._try_full_multi_compile(srcs, mods)
    finally:
        if saved is None:
            os.environ.pop("PCC_IR_SCAFFOLD", None)
        else:
            os.environ["PCC_IR_SCAFFOLD"] = saved
    split = (
        _split_py_cpy_calls(ir_text)
        if multi_ok else {"total": None, "bridge": None, "non_bridge": None}
    )
    return {
        "per_module_ok": per_module_ok,
        "per_module": per_module,
        "multi_ok": multi_ok,
        "total_fallbacks": split["total"],
        "bridge_calls": split["bridge"],
        "non_bridge_fallbacks": split["non_bridge"],
        "ir_lines": ir_text.count("\n") if multi_ok else 0,
    }


@pytest.fixture(scope="module")
def closure_compile():
    """Run the tight stage1 closure once per test module (OFF mode)."""
    sys.path.insert(0, str(_REPO_ROOT))
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    import importlib.util as _imputil

    spec = _imputil.spec_from_file_location(
        "_probe_stage1_closure",
        str(_REPO_ROOT / "scripts" / "probe_stage1_closure.py"),
    )
    probe_mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(probe_mod)

    entry = str(_REPO_ROOT / "pcc" / "__main__.py")
    srcs, mods = probe_mod._tightened_closure(entry)

    info = _per_module_and_multi(srcs, mods, ir_scaffold_mode="off")
    info["files"] = len(srcs)
    return info


@pytest.fixture(scope="module")
def closure_compile_on():
    """Same as ``closure_compile`` but with ir_scaffold_mode='on'."""
    sys.path.insert(0, str(_REPO_ROOT))
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    import importlib.util as _imputil

    spec = _imputil.spec_from_file_location(
        "_probe_stage1_closure",
        str(_REPO_ROOT / "scripts" / "probe_stage1_closure.py"),
    )
    probe_mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(probe_mod)

    entry = str(_REPO_ROOT / "pcc" / "__main__.py")
    srcs, mods = probe_mod._tightened_closure(entry)

    info = _per_module_and_multi(srcs, mods, ir_scaffold_mode="on")
    info["files"] = len(srcs)
    return info


def test_closure_per_module_codegen_passes(closure_compile):
    """Every module in the tight closure must still independently
    codegen. A regression here means we broke the frontend's
    self-compile path — fix immediately."""
    baseline = _load_baseline()
    expected = baseline["closure"]["per_module_pass"]
    assert closure_compile["per_module_ok"] >= expected, (
        f"per-module codegen regressed: "
        f"{closure_compile['per_module_ok']}/{closure_compile['files']} "
        f"vs baseline {expected}"
    )


def test_closure_multi_file_compile_succeeds(closure_compile):
    """The closure must still combine into one IR module."""
    assert closure_compile["multi_ok"], (
        "multi-file compile regressed; closure no longer assembles"
    )


def test_total_fallbacks_under_ratchet(closure_compile):
    """Aggregate ``py_cpy_*`` count must not exceed baseline + ratchet.

    This is the headline metric Path A drives toward zero.
    """
    baseline = _load_baseline()
    expected = baseline["totals"]["fallbacks_total"]
    actual = closure_compile["total_fallbacks"]
    assert actual is not None, "no IR produced; cannot count fallbacks"
    assert _within_ratchet(actual, expected), (
        f"fallback total grew past ratchet: "
        f"{actual} vs baseline {expected} (+{_RATCHET_PERCENT}%); "
        f"if this is intentional reduction, recapture baseline JSON"
    )


def test_per_module_fallbacks_under_ratchet(closure_compile):
    """No single module's fallback count grows past baseline + ratchet.

    Catches regressions localized to one file (e.g. someone adds a
    dynamic idiom to layer1 that doubles its count). Also catches the
    inverse: any module not in baseline (currently at 0) must stay 0,
    so we don't lose ground we've already gained.
    """
    baseline = _load_baseline()
    baseline_per_module = baseline["per_module"]

    failures: list[str] = []
    for mod, expected in baseline_per_module.items():
        actual = closure_compile["per_module"].get(mod)
        if actual is None:
            failures.append(f"{mod}: missing from current run")
            continue
        if actual == -1:
            failures.append(f"{mod}: per-module codegen failed")
            continue
        if not _within_ratchet(actual, expected):
            failures.append(
                f"{mod}: {actual} vs baseline {expected} "
                f"(+{_RATCHET_PERCENT}%)"
            )

    for mod, actual in closure_compile["per_module"].items():
        if mod in baseline_per_module:
            continue
        if actual == -1:
            failures.append(f"{mod}: per-module codegen failed")
            continue
        if actual != 0:
            failures.append(
                f"{mod}: {actual} fallbacks (baseline implicitly 0); "
                f"a previously-clean module regressed"
            )

    assert not failures, (
        "per-module fallback regressions:\n  " + "\n  ".join(failures)
    )


# ---- ON-mode ratchets (closed-world Path A) ---------------------------
#
# These mirror the OFF ratchets above but for ir_scaffold_mode='on'. ON
# is the path Issue 1 pushes toward zero; the baseline locks our
# accumulated wins so future codegen / runtime work can't quietly undo
# them. Per-module entries not in the baseline must stay 0 (same shape
# as the OFF gate).


def test_on_mode_total_fallbacks_under_ratchet(closure_compile_on):
    baseline = _load_baseline()
    expected = baseline["on_mode_totals"]["fallbacks_total_multi"]
    actual = closure_compile_on["total_fallbacks"]
    assert actual is not None, "no IR produced; cannot count fallbacks"
    assert _within_ratchet(actual, expected), (
        f"ON-mode fallback total grew past ratchet: "
        f"{actual} vs baseline {expected} (+{_RATCHET_PERCENT}%); "
        f"if this is intentional reduction, recapture baseline JSON"
    )


def test_on_mode_bridge_calls_do_not_regress(closure_compile_on):
    baseline = _load_baseline()
    expected = baseline["on_mode_totals"]["bridge_calls_multi"]
    actual = closure_compile_on["bridge_calls"]
    assert actual is not None, "no IR produced; cannot count bridge calls"
    assert actual <= expected, (
        f"ON-mode CPython bridge calls regressed: {actual} > "
        f"baseline {expected}. Bridge calls still require libpython, so "
        f"reducing non-bridge py_cpy_* by adding more bridge calls is not "
        f"Issue 1 progress."
    )


def test_on_mode_non_bridge_fallbacks_do_not_regress(closure_compile_on):
    baseline = _load_baseline()
    expected = baseline["on_mode_totals"]["non_bridge_fallbacks_multi"]
    actual = closure_compile_on["non_bridge_fallbacks"]
    assert actual is not None, (
        "no IR produced; cannot count non-bridge fallbacks"
    )
    assert actual <= expected, (
        f"ON-mode non-bridge py_cpy_* calls regressed: {actual} > "
        f"baseline {expected}. This tracks the original dynamic CPython "
        f"surface separately from temporary CPython->pcc bridge calls."
    )


def test_on_mode_per_module_fallbacks_under_ratchet(closure_compile_on):
    baseline = _load_baseline()
    baseline_per_module = baseline["on_mode_per_module"]

    failures: list[str] = []
    for mod, expected in baseline_per_module.items():
        actual = closure_compile_on["per_module"].get(mod)
        if actual is None:
            failures.append(f"{mod}: missing from current run")
            continue
        if actual == -1:
            failures.append(f"{mod}: per-module ON codegen failed")
            continue
        if not _within_ratchet(actual, expected):
            failures.append(
                f"{mod}: {actual} vs ON baseline {expected} "
                f"(+{_RATCHET_PERCENT}%)"
            )

    for mod, actual in closure_compile_on["per_module"].items():
        if mod in baseline_per_module:
            continue
        if actual == -1:
            failures.append(f"{mod}: per-module ON codegen failed")
            continue
        if actual != 0:
            failures.append(
                f"{mod}: {actual} ON fallbacks (baseline implicitly 0); "
                f"a previously-clean ON-mode module regressed"
            )

    assert not failures, (
        "ON-mode per-module fallback regressions:\n  "
        + "\n  ".join(failures)
    )
