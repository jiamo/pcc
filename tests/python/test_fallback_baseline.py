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

import ast
import inspect
import json
import os
import re
import sys
import textwrap
from pathlib import Path

import pytest


# Both closure fixtures compile the complete stage1 source closure.  Keep all
# assertions on one xdist worker so each module-scoped snapshot is produced
# once instead of once per worker.
pytestmark = pytest.mark.xdist_group(name="fallback_baseline")

_REPO_ROOT = Path(__file__).absolute().parents[2]
_BASELINE_JSON = _REPO_ROOT / "tests" / "fallback_baseline.json"

# Allow a small ratchet for noise. Anything beyond this fails the gate.
# Path A's job is to push numbers DOWN; this just blocks creep upward.
_RATCHET_PERCENT = 5.0
_BRIDGE_CPY_SYMBOLS = frozenset(
    {
        "py_cpy_to_pcc_obj",
        "py_cpy_to_pcc_str",
    }
)


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


def _classify_py_cpy_calls(ir_text: str) -> dict[str, int]:
    """Split semantic fallback actions from conversion/refcount plumbing.

    The strict multi-file gate still counts every ``py_cpy_*`` call and must
    remain zero.  Independent module probes lack cross-module export context;
    there, the useful monotone signal is the number of dynamic actions, not
    how thoroughly each action cleans up owned references on error paths.
    """
    from scripts.probe_fallback_categories import classify_py_cpy_symbol

    symbols = re.findall(r"\bcall [^\n]*@(py_cpy_[a-z0-9_]+)", ir_text)
    actions = 0
    plumbing = 0
    for symbol in symbols:
        classification, _kind = classify_py_cpy_symbol(symbol)
        if classification == "action":
            actions += 1
        else:
            plumbing += 1
    return {"total": len(symbols), "actions": actions, "plumbing": plumbing}


def _split_py_cpy_calls(ir_text: str) -> dict[str, int]:
    symbols = re.findall(r"\bcall [^\n]*@(py_cpy_[a-z0-9_]+)", ir_text)
    bridge = sum(1 for sym in symbols if sym in _BRIDGE_CPY_SYMBOLS)
    total = len(symbols)
    return {
        "total": total,
        "bridge": bridge,
        "non_bridge": total - bridge,
    }


def _per_module_action_failures(
    actual_by_module: dict[str, int],
    expected_by_module: dict[str, int],
    *,
    contextual_modules: set[str],
    label: str,
) -> list[str]:
    """Return semantic-action ratchet failures for standalone probes.

    ``-1`` means that a module needs sibling/contextual exports and therefore
    cannot be compiled as an isolated source file.  The separate
    ``per_module_pass`` gate owns that diagnostic population.  Treating the
    same sentinel as an action regression conflates unsupported standalone
    shape with a newly emitted CPython operation.
    """
    failures: list[str] = []
    for mod, expected in expected_by_module.items():
        if mod in contextual_modules:
            continue
        actual = actual_by_module.get(mod)
        if actual is None:
            failures.append(f"{mod}: missing from current run")
            continue
        if actual == -1:
            continue
        if not _within_ratchet(actual, expected):
            failures.append(
                f"{mod}: {actual} vs {label}baseline {expected} "
                f"(+{_RATCHET_PERCENT}%)"
            )

    for mod, actual in actual_by_module.items():
        if mod in contextual_modules or mod in expected_by_module:
            continue
        if actual == -1:
            continue
        if actual != 0:
            failures.append(
                f"{mod}: {actual} {label}fallbacks (baseline implicitly 0); "
                f"a previously-clean {label}module regressed"
            )
    return failures


def test_standalone_fallback_metric_counts_actions_not_ownership_plumbing():
    ir_text = "\n".join(
        (
            "  %m = call ptr @py_cpy_import(ptr null)",
            "  call void @py_cpy_decref(ptr %m)",
            "  %h = call ptr @py_cpy_handle_get(ptr null)",
        )
    )

    assert _classify_py_cpy_calls(ir_text) == {
        "total": 3,
        "actions": 1,
        "plumbing": 2,
    }


def test_standalone_fallback_metric_rejects_unknown_bridge_symbols():
    with pytest.raises(ValueError, match="unclassified py_cpy symbol"):
        _classify_py_cpy_calls("  call void @py_cpy_new_unreviewed_edge()")


def test_standalone_action_ratchet_defers_codegen_failures_to_pass_count():
    failures = _per_module_action_failures(
        {"needs.context": -1, "clean": 0, "regressed": 2},
        {"needs.context": 0, "clean": 0, "regressed": 0},
        contextual_modules=set(),
        label="",
    )

    assert failures == [
        "regressed: 2 vs baseline 0 (+5.0%)",
    ]


def _contextual_policy_modules(module_names) -> set[str]:
    from pcc.py_frontend.pipeline import contextual_per_module_modules

    out: set[str] = set()
    for mod in contextual_per_module_modules(module_names):
        out.add(mod)
    return out


def _check_contextual_per_module(
    actual_by_module: dict[str, int],
    expected_by_module: dict[str, int],
    *,
    contextual_modules: set[str],
    label: str,
    enforce_ratchet: bool,
    require_zero: bool,
) -> None:
    from pcc.py_frontend.pipeline import contextual_host_for_module

    failures: list[str] = []
    for mod in sorted(contextual_modules):
        if not contextual_host_for_module(mod):
            failures.append(f"{mod}: contextual probe has no host contract")
        actual = actual_by_module.get(mod)
        if actual is None:
            failures.append(f"{mod}: missing from contextual run")
            continue
        if actual == -1:
            failures.append(f"{mod}: contextual codegen failed")
            continue
        if require_zero and actual != 0:
            failures.append(f"{mod}: contextual fallback count {actual}; expected 0")
    if not enforce_ratchet:
        assert (
            not failures
        ), f"{label} contextual per-module fallback regressions:\n  " + "\n  ".join(
            failures
        )
        return
    for mod in sorted(contextual_modules):
        if mod not in expected_by_module:
            failures.append(f"{mod}: missing contextual baseline entry")
    for mod in sorted(expected_by_module):
        if mod not in contextual_modules:
            failures.append(f"{mod}: stale contextual baseline entry")
    for mod, expected in expected_by_module.items():
        actual = actual_by_module.get(mod)
        if actual is None:
            continue
        if actual == -1:
            continue
        if expected == 0:
            if actual != 0:
                failures.append(
                    f"{mod}: contextual fallback count {actual}; expected 0"
                )
            continue
        if not _within_ratchet(actual, expected):
            failures.append(
                f"{mod}: contextual fallback count {actual} vs "
                f"baseline {expected} (+{_RATCHET_PERCENT}%)"
            )
    assert (
        not failures
    ), f"{label} contextual per-module fallback regressions:\n  " + "\n  ".join(
        failures
    )


def _contextual_per_module_counts(srcs, mods, *, ir_scaffold_mode: str):
    from pcc.py_frontend.pipeline import (
        compile_contextual_per_module_fallback_counts,
    )

    baseline = _load_baseline()
    contextual_modules = _contextual_policy_modules(mods)
    if not contextual_modules:
        return {}
    return compile_contextual_per_module_fallback_counts(
        srcs,
        mods,
        contextual_modules,
        ir_scaffold_mode=ir_scaffold_mode,
        strict_no_libpython=(ir_scaffold_mode == "on"),
    )


def test_pipeline_and_codegen_host_contract_do_not_drift():
    """The contextual probe and type inference must share one host table.

    A stale duplicate table lets ``pipeline.py`` identify a helper as
    contextual while ``type_infer.py`` still gives that helper a host type
    that lacks the accessed ``L1CodeGen`` member. That turns cross-module
    static host references back into ``py_cpy_getattr``/``py_cpy_call``.
    """
    from pcc.py_frontend import pipeline
    from pcc.py_frontend.codegen import host_contract

    assert (
        pipeline.l1_codegen_lowering_host_contract()
        == host_contract.l1_codegen_lowering_host_contract()
    )
    assert (
        pipeline.per_module_probe_policy("pcc.py_frontend.codegen.layer1_init")
        == host_contract.PROBE_POLICY_CONTEXTUAL_MIXIN
    )

    from pcc.py_frontend.codegen.layer1_support import (
        _default_native_module_exports,
    )

    for module_name in (
        "pcc.py_frontend.pipeline",
        "pcc.py_frontend.codegen.layer1_entrypoints",
        "pcc.cli_bootstrap",
        "pcc.cli_contract",
        "unrelated.module",
    ):
        assert pipeline._module_uses_default_native_exports(module_name) == (
            _default_native_module_exports(module_name) is not None
        )

    exports = _default_native_module_exports(
        "pcc.py_frontend.codegen.layer1_entrypoints"
    )
    assert exports is not None
    assert exports["pcc.diagnostics"]["DiagnosticSpan"]["field_names"] == (
        "file",
        "line",
        "col",
        "end_line",
        "end_col",
    )


def test_l1_codegen_host_contract_covers_constructor_state_fields():
    """Keep L1CodeGen's constructor fields in the contextual host schema."""
    from pcc.py_frontend.codegen.host_contract import L1_CODEGEN_HOST_ATTRS
    from pcc.py_frontend.codegen.layer1_init import Layer1InitMixin

    source = textwrap.dedent(inspect.getsource(Layer1InitMixin._init_l1_state))
    tree = ast.parse(source)
    assigned = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                assigned.add(target.attr)

    missing = sorted(assigned.difference(L1_CODEGEN_HOST_ATTRS))
    assert not missing


def test_l1_codegen_lambda_counters_are_initialized():
    """Fixed-layout pcc1 instances must not expose NULL lazy counters."""
    from pcc.py_frontend.codegen.layer1 import L1CodeGen
    from pcc.py_frontend.py_ast import Module

    codegen = L1CodeGen(Module(name="lambda_counter_probe", body=[]))

    assert codegen._native_lambda_func_counter == 0
    assert codegen._native_lambda_callback_counter == 0
    assert codegen._lambda_counter == []


def test_l1_codegen_scaffold_binding_tables_are_initialized():
    """Self-hosted fixed-layout scaffold slots must start as containers."""
    from pcc.py_frontend.codegen.layer1 import L1CodeGen
    from pcc.py_frontend.py_ast import Module

    codegen = L1CodeGen(Module(name="scaffold_binding_probe", body=[]))

    assert codegen._extern_bindings == {}
    assert codegen._unsafe_bindings == {}
    assert codegen._extern_decls == {}


def test_l1_codegen_class_attr_mutation_state_is_initialized():
    """pcc1 fixed-layout codegen must retain class-attr invalidation state."""
    from pcc.py_frontend.codegen.layer1 import L1CodeGen
    from pcc.py_frontend.py_ast import Module

    codegen = L1CodeGen(Module(name="class_attr_state_probe", body=[]))

    assert codegen._class_attr_runtime_state == {}
    assert codegen._class_attr_mutation_in_loop_depth == 0


def test_l1_codegen_active_handler_stack_is_initialized():
    """pcc1 fixed-layout codegen must observe handler-stack push/pop state."""
    from pcc.py_frontend.codegen.layer1 import L1CodeGen
    from pcc.py_frontend.py_ast import Module

    codegen = L1CodeGen(Module(name="active_handler_stack_probe", body=[]))

    assert codegen._active_handler_excs == []


def test_pipeline_contextual_cross_module_exports_stay_clean():
    """Focused regression for stale frontend closure export metadata.

    ``pipeline.py`` is now a facade over many sibling owner modules.  A raw
    standalone probe intentionally lacks those siblings' export context and
    is tracked only by the action/plumbing diagnostic ratchet.  The production
    multi-file path supplies the contextual host contract; that exact mode
    must resolve every cross-module helper without a CPython bridge.
    """
    import importlib.util as _imputil

    from pcc.py_frontend.pipeline import (
        compile_contextual_per_module_fallback_counts,
    )

    spec = _imputil.spec_from_file_location(
        "_probe_stage1_pipeline_context",
        str(_REPO_ROOT / "scripts" / "probe_stage1_closure.py"),
    )
    probe_mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(probe_mod)
    srcs, mods = probe_mod._tightened_closure(
        str(_REPO_ROOT / "pcc" / "__main__.py")
    )
    target = "pcc.py_frontend.pipeline"
    counts = compile_contextual_per_module_fallback_counts(
        srcs,
        mods,
        {target},
        ir_scaffold_mode="on",
        strict_no_libpython=True,
    )

    assert counts[target] == 0


def test_pipeline_subprocess_run_kwargs_resolve_without_cpython_bridge():
    """Keep statement-only ``subprocess.run`` on the native process ABI.

    The full per-module ratchet caught this only after compiling all of
    ``pipeline.py``.  Preserve the exact ingredients that regressed in a small
    canary: an imported-module alias, a starred argv tail, a computed boolean
    keyword, and the timeout helper.  These calls intentionally discard
    ``CompletedProcess``; they must lower through ``py_subprocess_run*`` and
    never materialize ``py_cpy_import/getattr/call_kw``.
    """
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.type_infer import infer_module
    from pcc.py_frontend.codegen.layer1 import L1CodeGen

    source = textwrap.dedent(
        """
        import subprocess as process

        def invoke(make_cmd: list[str], verbose: bool, seconds: int) -> None:
            process.run(
                ["sh", "-c", "true", *make_cmd],
                check=True,
                capture_output=not verbose,
            )
            process.run(make_cmd, check=True, timeout=seconds)
        """
    )
    module_name = "pcc.py_frontend.pipeline"
    typed = infer_module(parse_and_lift(source, "subprocess_probe.py", module_name))
    ir_text = str(
        L1CodeGen(
            typed,
            emit_cpy_main_exitcode=False,
            ir_scaffold_mode="on",
        ).generate(typed)
    )

    assert "@py_subprocess_run(" in ir_text
    assert "@py_subprocess_run_timeout(" in ir_text
    assert _count_py_cpy_calls(ir_text) == 0


def test_capi_export_anchor_nm_fallback_keeps_native_stdout_contract(
    tmp_path: Path,
    monkeypatch,
):
    """The nm fallback must capture output without ``CompletedProcess``."""
    from pcc.py_frontend import pipeline

    archive = tmp_path / "libpy_runtime.a"
    observed = {}

    def fake_check_output(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return (
            "00000000 T PyLong_FromLong\n"
            "00000010 T _PyObject_Call\n"
            "00000020 T helper\n"
            "         U PyErr_SetString\n"
            "00000000 T PyLong_FromLong\n"
        )

    monkeypatch.setattr(pipeline.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(pipeline, "_host_python_command", lambda: "/host/python")
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("nm fallback used subprocess.run"),
    )

    assert pipeline._capi_export_anchor_symbols(str(archive)) == [
        "PyLong_FromLong",
        "_PyObject_Call",
    ]
    assert observed["command"][:2] == ["/host/python", "-c"]
    assert "timeout=120" in observed["command"][2]
    assert observed["command"][3] == str(archive)
    assert observed["kwargs"] == {"text": True}


def test_cli_bootstrap_package_schema_static_imports_stay_native():
    """Keep the standalone bootstrap CLI independent of libpython bridges.

    Package behavior helpers live in ``pcc.package_schema`` and are compiled
    into the closed-world bootstrap normally.  The independent per-module
    ratchet still needs their static signatures; otherwise the imported calls
    and every operation on their results regress to ``py_cpy_*``.
    """
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.type_infer import infer_module
    from pcc.py_frontend.codegen.layer1 import L1CodeGen

    src = _REPO_ROOT / "pcc" / "cli_bootstrap.py"
    with open(src, "r", encoding="utf-8") as f:
        source = f.read()
    ast_mod = parse_and_lift(source, str(src), "pcc.cli_bootstrap")
    typed = infer_module(ast_mod)
    codegen = L1CodeGen(
        typed,
        emit_cpy_main_exitcode=False,
        ir_scaffold_mode="on",
    )
    ir_text = str(codegen.generate(typed))
    assert _count_py_cpy_calls(ir_text) == 0


def test_layer1_constants_cross_module_static_imports_stay_native():
    """Focused regression for ``layer1.py`` static table imports.

    ``layer1.py`` intentionally keeps the literal policy tables in
    ``layer1_constants.py`` and re-exports them as class-local attributes on
    ``L1CodeGen``. Raw single-module codegen must still see those imports as
    native module-global slots; otherwise the split silently reintroduces
    ``py_cpy_import`` / ``py_cpy_getattr`` for static tables.
    """
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.type_infer import infer_module
    from pcc.py_frontend.codegen.layer1 import L1CodeGen

    src = _REPO_ROOT / "pcc" / "py_frontend" / "codegen" / "layer1.py"
    with open(src, "r", encoding="utf-8") as f:
        source = f.read()
    ast_mod = parse_and_lift(
        source,
        str(src),
        "pcc.py_frontend.codegen.layer1",
    )
    typed = infer_module(ast_mod)
    codegen = L1CodeGen(
        typed,
        emit_cpy_main_exitcode=False,
        ir_scaffold_mode="on",
    )
    ir_text = str(codegen.generate(typed))
    layer1_constant_fallbacks = [
        line
        for line in ir_text.splitlines()
        if "layer1_constants" in line and "@py_cpy_" in line
    ]
    assert layer1_constant_fallbacks == []
    assert re.search(
        r"store ptr @\.pystr\.obj\.\d+, ptr "
        r"@\.classattr\.pcc_py_frontend_codegen_layer1\.L1CodeGen\."
        r"_IR_RUNTIME_COMPAT_MODULE",
        ir_text,
    )


def test_native_module_constant_bindings_are_contextual_host_state():
    """Keep imported literals on the real ``L1CodeGen`` host object.

    Contextual mixins use this table to carry ``from sibling import CONST``
    bindings from import lowering to Name lowering.  If it is absent from the
    closed-world host contract, compiled-stage ``setattr`` can bind the table
    to an unrelated field and module-top class assignments observe ``None``.
    """
    from pcc.py_frontend.codegen.host_contract import L1_CODEGEN_HOST_ATTRS
    from pcc.py_frontend.codegen.layer1 import L1CodeGen

    assert "_native_module_constant_bindings" in L1_CODEGEN_HOST_ATTRS
    assert hasattr(L1CodeGen, "_init_l1_state")


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
    per_module_actions: dict[str, int] = {}
    per_module_plumbing: dict[str, int] = {}
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
            classified = _classify_py_cpy_calls(ir_text)
            per_module[mod] = classified["total"]
            per_module_actions[mod] = classified["actions"]
            per_module_plumbing[mod] = classified["plumbing"]
            per_module_ok += 1
        except Exception:
            per_module[mod] = -1
            per_module_actions[mod] = -1
            per_module_plumbing[mod] = -1
    # The multi-file path doesn't take an ir_scaffold_mode kw today;
    # set the env var the pipeline reads so multi-file inherits the
    # mode for the duration of the call.
    saved = os.environ.get("PCC_IR_SCAFFOLD")
    saved_passes = os.environ.get("PCC_PYTHON_IR_PASSES")
    if ir_scaffold_mode == "on":
        os.environ["PCC_IR_SCAFFOLD"] = "on"
    else:
        os.environ.pop("PCC_IR_SCAFFOLD", None)
    # This gate counts frontend-emitted ``py_cpy_*`` calls.  The Python IR
    # pass subprocess can dominate or hang on very large closure IR and is
    # covered by separate IR-pass tests, so keep the fallback ratchet focused
    # on raw codegen output.
    os.environ["PCC_PYTHON_IR_PASSES"] = "off"
    try:
        multi_ok, ir_text, _ = probe_mod._try_full_multi_compile(srcs, mods)
    finally:
        if saved is None:
            os.environ.pop("PCC_IR_SCAFFOLD", None)
        else:
            os.environ["PCC_IR_SCAFFOLD"] = saved
        if saved_passes is None:
            os.environ.pop("PCC_PYTHON_IR_PASSES", None)
        else:
            os.environ["PCC_PYTHON_IR_PASSES"] = saved_passes
    split = (
        _split_py_cpy_calls(ir_text)
        if multi_ok
        else {"total": None, "bridge": None, "non_bridge": None}
    )
    return {
        "per_module_ok": per_module_ok,
        "per_module": per_module,
        "per_module_actions": per_module_actions,
        "per_module_plumbing": per_module_plumbing,
        "contextual_per_module": _contextual_per_module_counts(
            srcs,
            mods,
            ir_scaffold_mode=ir_scaffold_mode,
        ),
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
    assert closure_compile[
        "multi_ok"
    ], "multi-file compile regressed; closure no longer assembles"


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
    """No module's semantic fallback actions grow past the ratchet.

    Ownership/conversion plumbing remains visible in ``closure_compile`` but
    is not a dynamic Python idiom.  The strict multi-file tests below still
    require every action and plumbing call together to remain exactly zero.
    """
    baseline = _load_baseline()
    baseline_per_module = baseline["per_module_actions"]
    contextual_modules = _contextual_policy_modules(
        closure_compile["per_module"].keys()
    )

    failures = _per_module_action_failures(
        closure_compile["per_module_actions"],
        baseline_per_module,
        contextual_modules=contextual_modules,
        label="",
    )

    assert not failures, "per-module fallback regressions:\n  " + "\n  ".join(failures)


def test_contextual_per_module_fallbacks_under_ratchet(closure_compile):
    baseline = _load_baseline()
    contextual_modules = _contextual_policy_modules(
        closure_compile["per_module"].keys()
    )
    _check_contextual_per_module(
        closure_compile["contextual_per_module"],
        baseline.get("contextual_per_module", {}),
        contextual_modules=contextual_modules,
        label="OFF-mode",
        enforce_ratchet=False,
        require_zero=False,
    )


def test_contextual_for_target_domain_join_cleanup_names_compile():
    """Cleanup loops must not shadow CPython-backed builder temporaries.

    In contextual OFF mode, calls through the host IR builder intentionally
    produce CPython-domain values.  Reusing that temporary's name as a later
    pcc-list loop target asks one stack slot to carry two incompatible object
    domains.  These four helpers only iterate separate cleanup collections, so
    their loop targets must have distinct source bindings.  Strict ON mode
    remains the production zero-fallback contract.
    """
    import importlib.util as _imputil

    from pcc.py_frontend.pipeline import (
        compile_contextual_per_module_fallback_counts,
    )

    spec = _imputil.spec_from_file_location(
        "_probe_contextual_cleanup_target_names",
        str(_REPO_ROOT / "scripts" / "probe_stage1_closure.py"),
    )
    probe_mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(probe_mod)
    srcs, mods = probe_mod._tightened_closure(
        str(_REPO_ROOT / "pcc" / "__main__.py")
    )
    targets = {
        "pcc.py_frontend.codegen.lambda_callback_lowering",
        "pcc.py_frontend.codegen.lambda_helpers_lowering",
        "pcc.py_frontend.codegen.native_virtual_thread",
        "pcc.py_frontend.codegen.numeric_builtin_lowering",
    }

    off_counts = compile_contextual_per_module_fallback_counts(
        srcs,
        mods,
        targets,
        ir_scaffold_mode="off",
        strict_no_libpython=False,
    )
    on_counts = compile_contextual_per_module_fallback_counts(
        srcs,
        mods,
        targets,
        ir_scaffold_mode="on",
        strict_no_libpython=True,
    )

    assert set(off_counts) == targets
    assert all(count >= 0 for count in off_counts.values()), off_counts
    assert on_counts == {module_name: 0 for module_name in targets}


def test_contextual_valueclass_arity_projection_remains_native():
    """The dynamic struct-arity path must stay inside the strict closure."""
    import importlib.util as _imputil

    from pcc.py_frontend.pipeline import (
        compile_contextual_per_module_fallback_counts,
    )

    spec = _imputil.spec_from_file_location(
        "_probe_contextual_valueclass_arity",
        str(_REPO_ROOT / "scripts" / "probe_stage1_closure.py"),
    )
    probe_mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(probe_mod)
    srcs, mods = probe_mod._tightened_closure(
        str(_REPO_ROOT / "pcc" / "__main__.py")
    )
    targets = {
        "pcc.py_frontend.codegen.class_gen",
        "pcc.py_frontend.codegen.type_abi_lowering",
    }

    counts = compile_contextual_per_module_fallback_counts(
        srcs,
        mods,
        targets,
        ir_scaffold_mode="on",
        strict_no_libpython=True,
    )

    assert counts == {module_name: 0 for module_name in targets}


def test_contextual_frontend_type_tag_aliases_remain_native():
    """Generated compiler tag aliases must not add contextual fallbacks."""
    import importlib.util as _imputil

    from pcc.py_frontend.pipeline import (
        compile_contextual_per_module_fallback_counts,
    )

    spec = _imputil.spec_from_file_location(
        "_probe_contextual_frontend_type_tags",
        str(_REPO_ROOT / "scripts" / "probe_stage1_closure.py"),
    )
    probe_mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(probe_mod)
    srcs, mods = probe_mod._tightened_closure(
        str(_REPO_ROOT / "pcc" / "__main__.py")
    )
    targets = {
        "pcc.py_frontend.codegen.compare_membership_lowering",
        "pcc.py_frontend.codegen.dict_lowering",
        "pcc.py_frontend.codegen.guarded_loop_lowering",
        "pcc.py_frontend.codegen.isinstance_lowering",
        "pcc.py_frontend.codegen.list_method_lowering",
        "pcc.py_frontend.codegen.method_call_expression_lowering",
        "pcc.py_frontend.codegen.method_call_lowering",
        "pcc.py_frontend.codegen.name_lowering",
        "pcc.py_frontend.codegen.native_virtual_thread",
        "pcc.py_frontend.codegen.numeric_builtin_lowering",
        "pcc.py_frontend.codegen.set_lowering",
        "pcc.py_frontend.codegen.stmt_misc_lowering",
        "pcc.py_frontend.codegen.string_method_lowering",
    }

    counts = compile_contextual_per_module_fallback_counts(
        srcs,
        mods,
        targets,
        ir_scaffold_mode="on",
        strict_no_libpython=True,
    )

    assert counts == {module_name: 0 for module_name in targets}


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
    assert actual is not None, "no IR produced; cannot count non-bridge fallbacks"
    assert actual <= expected, (
        f"ON-mode non-bridge py_cpy_* calls regressed: {actual} > "
        f"baseline {expected}. This tracks the original dynamic CPython "
        f"surface separately from temporary CPython->pcc bridge calls."
    )


def test_on_mode_per_module_fallbacks_under_ratchet(closure_compile_on):
    baseline = _load_baseline()
    baseline_per_module = baseline["on_mode_per_module_actions"]
    contextual_modules = _contextual_policy_modules(
        closure_compile_on["per_module"].keys()
    )

    failures = _per_module_action_failures(
        closure_compile_on["per_module_actions"],
        baseline_per_module,
        contextual_modules=contextual_modules,
        label="ON ",
    )

    assert not failures, "ON-mode per-module fallback regressions:\n  " + "\n  ".join(
        failures
    )


def test_on_mode_contextual_per_module_fallbacks_under_ratchet(
    closure_compile_on,
):
    baseline = _load_baseline()
    contextual_modules = _contextual_policy_modules(
        closure_compile_on["per_module"].keys()
    )
    _check_contextual_per_module(
        closure_compile_on["contextual_per_module"],
        baseline.get("on_mode_contextual_per_module", {}),
        contextual_modules=contextual_modules,
        label="ON-mode",
        enforce_ratchet=False,
        require_zero=True,
    )


def test_on_mode_isinstance_helper_contextual_fallback_zero(
    closure_compile_on,
):
    """Regression for extracted layer1 helpers.

    ``isinstance_lowering`` is a contextual-host helper module: raw
    single-file inference sees a ``host`` parameter, but the real
    self-host path passes an ``L1CodeGen`` instance. The contextual
    probe must therefore keep ``host.builder`` scaffold calls and
    ``host.class_lowering`` dispatch native.
    """
    actual = closure_compile_on["contextual_per_module"].get(
        "pcc.py_frontend.codegen.isinstance_lowering"
    )
    assert actual == 0


def test_on_mode_assignment_statement_contextual_fallback_zero():
    """Keep AST-use walking inside the self-host-safe helper surface.

    ``assignment_statement_lowering`` is compiled as a contextual L1CodeGen
    mixin. Its literal dispatch analysis must not call host-only dataclasses
    reflection helpers and reintroduce ``py_cpy_*`` into the strict closure.
    """
    import importlib.util as _imputil

    from pcc.py_frontend.pipeline import (
        compile_contextual_per_module_fallback_counts,
    )

    spec = _imputil.spec_from_file_location(
        "_probe_stage1_closure_assignment",
        str(_REPO_ROOT / "scripts" / "probe_stage1_closure.py"),
    )
    probe_mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(probe_mod)
    entry = str(_REPO_ROOT / "pcc" / "__main__.py")
    srcs, mods = probe_mod._tightened_closure(entry)
    target = "pcc.py_frontend.codegen.assignment_statement_lowering"

    counts = compile_contextual_per_module_fallback_counts(
        srcs,
        mods,
        {target},
        ir_scaffold_mode="on",
    )

    assert counts[target] == 0


def test_marshal_raw_per_module_fallbacks_stay_under_ratchet():
    """Keep the legacy scaffold-off marshal helper under its hard ratchet."""
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.type_infer import infer_module
    from pcc.py_frontend.codegen.layer1 import L1CodeGen

    target = "pcc.py_frontend.codegen.marshal"
    src = _REPO_ROOT / "pcc" / "py_frontend" / "codegen" / "marshal.py"
    source = src.read_text(encoding="utf-8")
    typed = infer_module(parse_and_lift(source, str(src), target))
    codegen = L1CodeGen(
        typed,
        emit_cpy_main_exitcode=False,
        ir_scaffold_mode="off",
    )
    actual = _classify_py_cpy_calls(str(codegen.generate(typed)))["actions"]
    expected = _load_baseline()["per_module_actions"][target]

    assert _within_ratchet(
        actual, expected
    ), f"{target}: {actual} vs baseline {expected} (+{_RATCHET_PERCENT}%)"


def test_on_mode_user_function_low_ir_helpers_contextual_fallback_zero(
    closure_compile_on,
):
    """Regression for LowIR helper return types in stage1 codegen.

    ``user_function_lowering`` builds LowIR values recursively.  If those
    helpers lose the ``LowValue`` result type, reads such as ``operand.ty``
    become dynamic attribute comparisons and reintroduce ``py_cpy_*`` calls
    into pcc1's strict no-libpython closure.
    """
    actual = closure_compile_on["contextual_per_module"].get(
        "pcc.py_frontend.codegen.user_function_lowering"
    )
    assert actual == 0
