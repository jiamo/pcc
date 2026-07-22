from __future__ import annotations

import json
import os
import subprocess

import llvmlite.binding as llvm
import pytest

from pcc.ir_passes.parity import normalize_ir
from pcc.passes.llvm_text_pipeline import find_opt_binary, run_pipeline
from pcc.py_frontend import ir_pass_pipeline, pipeline


def test_python_frontend_jobs_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("PCC_PY_FRONTEND_JOBS", raising=False)
    monkeypatch.delenv("PCC_OUTER_PARALLELISM", raising=False)
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)

    assert pipeline._python_frontend_jobs(111) == 10


def test_profiled_gc_collect_records_current_process_reclamation(monkeypatch):
    profile = {}
    monkeypatch.setattr(pipeline.gc, "collect", lambda: 7)

    assert pipeline._profiled_gc_collect(profile, "current_process") == 7
    assert profile["counters"]["current_process_objects"] == 7
    assert profile["counters"]["current_process_skipped"] == 0
    assert profile["phase_totals_ms"]["current_process"] >= 0


def test_profiled_gc_collect_skips_subprocess_reclamation_boundary(monkeypatch):
    profile = {}

    def unexpected_collect():
        raise AssertionError("worker exit owns reclamation")

    monkeypatch.setattr(pipeline.gc, "collect", unexpected_collect)

    assert (
        pipeline._profiled_gc_collect(
            profile,
            "worker_boundary",
            allocations_owned_by_current_process=False,
        )
        == 0
    )
    assert profile["counters"]["worker_boundary_objects"] == 0
    assert profile["counters"]["worker_boundary_skipped"] == 1
    assert profile["phase_totals_ms"]["worker_boundary"] >= 0


def test_python_frontend_package_graph_auto_budget_caps_retained_heap(monkeypatch):
    monkeypatch.delenv("PCC_PY_FRONTEND_JOBS", raising=False)
    monkeypatch.delenv("PCC_OUTER_PARALLELISM", raising=False)
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(
        pipeline,
        "_package_site_package_root_for_src",
        lambda path: "/site/demo" if path.startswith("/site/") else None,
    )

    assert pipeline._python_frontend_jobs_for_sources(["/repo/main.py"] * 111) == 10
    assert (
        pipeline._python_frontend_jobs_for_sources(
            ["/repo/main.py"] * 110 + ["/site/demo/__init__.py"]
        )
        == 1
    )


def test_python_frontend_package_graph_explicit_jobs_remain_authoritative(monkeypatch):
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "5")
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(
        pipeline,
        "_package_site_package_root_for_src",
        lambda _path: "/site/demo",
    )

    assert pipeline._python_frontend_jobs_for_sources(["/site/demo/mod.py"] * 111) == 5


def test_python_frontend_package_graph_caps_outer_auto_budget(monkeypatch):
    monkeypatch.delenv("PCC_PY_FRONTEND_JOBS", raising=False)
    monkeypatch.setenv("PCC_OUTER_PARALLELISM", "6")
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(
        pipeline,
        "_package_site_package_root_for_src",
        lambda _path: "/site/demo",
    )

    assert pipeline._python_frontend_jobs_for_sources(["/site/demo/mod.py"] * 111) == 1


def test_python_frontend_jobs_env_can_force_serial(monkeypatch):
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "0")
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)

    assert pipeline._python_frontend_jobs(111) == 1


def test_self_backend_jobs_defaults_to_conservative_pool(monkeypatch):
    monkeypatch.delenv("PCC_SELF_BACKEND_JOBS", raising=False)
    monkeypatch.delenv("PCC_OUTER_PARALLELISM", raising=False)
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)

    assert pipeline._self_backend_jobs(111) == 2
    assert pipeline._self_backend_jobs(1) == 1


def test_self_backend_large_ir_defaults_bound_native_worker_memory(monkeypatch):
    monkeypatch.delenv("PCC_SELF_BACKEND_SPLIT_THRESHOLD_BYTES", raising=False)
    monkeypatch.delenv("PCC_SELF_BACKEND_SPLIT_SHARD_BYTES", raising=False)

    assert pipeline._self_backend_split_threshold_bytes() == 2_000_000
    assert pipeline._self_backend_split_shard_bytes() == 1_000_000


def test_self_backend_large_ir_memory_budget_remains_overridable(monkeypatch):
    monkeypatch.setenv("PCC_SELF_BACKEND_SPLIT_THRESHOLD_BYTES", "3000000")
    monkeypatch.setenv("PCC_SELF_BACKEND_SPLIT_SHARD_BYTES", "1500000")

    assert pipeline._self_backend_split_threshold_bytes() == 3_000_000
    assert pipeline._self_backend_split_shard_bytes() == 1_500_000


def test_compiled_self_backend_large_ir_defaults_to_one_worker(monkeypatch):
    monkeypatch.delenv("PCC_SELF_BACKEND_JOBS", raising=False)
    monkeypatch.delenv("PCC_OUTER_PARALLELISM", raising=False)
    monkeypatch.delenv("PCC_SELF_BACKEND_SPLIT_THRESHOLD_BYTES", raising=False)
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)

    assert (
        pipeline._self_backend_jobs_for_ir_texts(
            ["x" * 2_000_000, "small"], native_worker=True
        )
        == 1
    )
    assert (
        pipeline._self_backend_jobs_for_ir_texts(
            ["x" * 1_999_999, "small"], native_worker=True
        )
        == 2
    )
    assert (
        pipeline._self_backend_jobs_for_ir_texts(
            ["x" * 2_000_000, "small"], native_worker=False
        )
        == 2
    )


def test_compiled_self_backend_explicit_jobs_override_large_ir_cap(monkeypatch):
    monkeypatch.setenv("PCC_SELF_BACKEND_JOBS", "2")

    assert (
        pipeline._self_backend_jobs_for_ir_texts(
            ["x" * 2_000_000, "small"], native_worker=True
        )
        == 2
    )


def test_nested_parallelism_shares_cpu_budget_across_outer_workers(monkeypatch):
    monkeypatch.delenv("PCC_PY_FRONTEND_JOBS", raising=False)
    monkeypatch.delenv("PCC_PYTHON_IR_PASS_JOBS", raising=False)
    monkeypatch.delenv("PCC_SELF_BACKEND_JOBS", raising=False)
    monkeypatch.setenv("PCC_OUTER_PARALLELISM", "6")
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)

    assert pipeline._python_frontend_jobs(111) == 2
    assert pipeline._python_ir_pass_jobs(111) == 2
    assert pipeline._self_backend_jobs(111) == 2


def test_explicit_inner_jobs_override_outer_parallelism_budget(monkeypatch):
    monkeypatch.setenv("PCC_OUTER_PARALLELISM", "6")
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "5")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_JOBS", "4")
    monkeypatch.setenv("PCC_SELF_BACKEND_JOBS", "3")
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)

    assert pipeline._python_frontend_jobs(111) == 5
    assert pipeline._python_ir_pass_jobs(111) == 4
    assert pipeline._self_backend_jobs(111) == 3


def test_python_frontend_worker_timing_is_opt_in(monkeypatch):
    monkeypatch.delenv("PCC_PY_FRONTEND_WORKER_TIMING", raising=False)

    assert pipeline._python_frontend_worker_timing_enabled() is False
    assert pipeline._python_frontend_worker_env_prefix() == "PCC_PY_FRONTEND_JOBS=1"

    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")

    assert pipeline._python_frontend_worker_timing_enabled() is True
    assert pipeline._python_frontend_worker_env_prefix() == (
        "PCC_PY_FRONTEND_JOBS=1 PCC_PY_FRONTEND_WORKER_TIMING=1"
    )


def test_python_frontend_native_workers_keep_one_module_per_process(tmp_path):
    native_worker = tmp_path / "pcc1"

    assert (
        pipeline._python_frontend_codegen_chunk_count(111, 10, [str(native_worker)])
        == 111
    )


def test_python_frontend_single_native_worker_still_isolates_each_module(tmp_path):
    native_worker = tmp_path / "pcc1"

    assert (
        pipeline._python_frontend_codegen_chunk_count(111, 1, [str(native_worker)])
        == 111
    )


def test_python_frontend_single_source_worker_keeps_one_chunk():
    assert (
        pipeline._python_frontend_codegen_chunk_count(
            111,
            1,
            ["python", "-m", "pcc"],
        )
        == 1
    )


def test_python_frontend_source_workers_keep_one_chunk_per_worker():
    assert (
        pipeline._python_frontend_codegen_chunk_count(
            111,
            10,
            ["python", "-m", "pcc"],
        )
        == 10
    )


def test_python_frontend_worker_executable_skips_text_console_script(
    monkeypatch, tmp_path
):
    console_script = tmp_path / "pcc"
    console_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(pipeline.sys, "argv", [str(console_script)])
    monkeypatch.setattr(pipeline.sys, "executable", "")

    assert pipeline._python_frontend_worker_executable() == ""


def test_closed_world_shallow_lift_preserves_class_keywords(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(
        "class Base:\n"
        "    pass\n"
        "\n"
        "class Child(Base, total=False):\n"
        "    pass\n",
        encoding="utf-8",
    )

    parsed_modules, _native_exports, _derived = pipeline.build_closed_world_context(
        [str(src)],
        ["pkg.mod"],
        lift_indices=[],
        merge_exports=False,
    )

    child = parsed_modules[0].body[1]
    assert child.keywords[0][0] == "total"


def test_native_export_wire_preserves_expression_defaults(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(
        "import numpy as np\n"
        "\n"
        "def f(dtype=int, axis=-1, keepdims=np._NoValue):\n"
        "    pass\n",
        encoding="utf-8",
    )
    _parsed_modules, native_exports, derived = pipeline.build_closed_world_context(
        [str(src)],
        ["pkg.mod"],
        lift_indices=[],
        merge_exports=False,
    )
    path = tmp_path / "exports.json"

    pipeline._write_native_exports_wire(str(path), native_exports, derived)
    restored_exports, _restored_derived = pipeline._read_native_exports_wire(str(path))

    export = restored_exports["pkg.mod"]["f"]
    param_types = export["param_types"]
    assert isinstance(param_types, tuple)
    assert param_types[0] == ("dyn",)

    sig = export["call_sig"]
    assert isinstance(sig, tuple)
    assert sig[0]["has_default"] is True
    assert sig[0]["default"].ident == "int"
    assert sig[1]["has_default"] is True
    assert sig[1]["default"].op == "-"
    assert sig[2]["has_default"] is True
    assert sig[2]["default"].name == "_NoValue"


_DEAD_ADD_IR = """
define i32 @main() {
entry:
  %dead = add i32 1, 2
  ret i32 0
}
"""

_RUNTIME_CALL_IR = """
; ModuleID = "probe"
target triple = "unknown-unknown-unknown"

declare ptr @py_int_from_i64(i64)
declare void @py_print(ptr)

define i32 @main() {
entry:
  %v = call ptr @py_int_from_i64(i64 1)
  call void @py_print(ptr %v)
  ret i32 0
}
"""

_GLOBAL_STRING_BRANCH_IR = """
; ModuleID = "probe"
target triple = "unknown-unknown-unknown"

@.pystr.0 = internal constant [2 x i8] c"x\\00"

declare ptr @py_str_from_cstr(ptr)
declare void @py_print(ptr)

define i32 @main() {
entry:
  br i1 true, label %then, label %else
then:
  %p = getelementptr inbounds [2 x i8], ptr @.pystr.0, i32 0, i32 0
  %v = call ptr @py_str_from_cstr(ptr %p)
  call void @py_print(ptr %v)
  ret i32 0
else:
  ret i32 1
}
"""

_SIBLING_CALL_BRANCH_IR = """
; ModuleID = "probe"
target triple = "unknown-unknown-unknown"

define ptr @helper(ptr %x) {
entry:
  ret ptr %x
}

define i32 @main(ptr %arg) {
entry:
  br i1 true, label %then, label %else
then:
  %v = call ptr @helper(ptr %arg)
  ret i32 0
else:
  ret i32 1
}
"""

_INTERNAL_SIBLING_CALL_BRANCH_IR = """
; ModuleID = "probe"
target triple = "unknown-unknown-unknown"

define internal ptr @helper(ptr %x) {
entry:
  ret ptr %x
}

define i32 @main(ptr %arg) {
entry:
  br i1 true, label %then, label %else
then:
  %v = call ptr @helper(ptr %arg)
  ret i32 0
else:
  ret i32 1
}
"""

_OPT_DEFAULT_PIPELINE_IR = """
; ModuleID = "probe"
target triple = "unknown-unknown-unknown"

define i32 @main() {
entry:
  %p = alloca i32
  store i32 1, ptr %p
  %v = load i32, ptr %p
  %dead = add i32 %v, 0
  ret i32 %dead
}
"""


def test_python_ir_pass_pipeline_off_is_noop(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")

    out = pipeline._apply_python_ir_pass_pipeline(
        _DEAD_ADD_IR,
        module_name="probe",
    )

    assert out == _DEAD_ADD_IR


def test_python_ir_pass_pipeline_default_runs_registered_passes(monkeypatch):
    monkeypatch.delenv("PCC_PYTHON_IR_PASSES", raising=False)

    out = pipeline._apply_python_ir_pass_pipeline(
        _DEAD_ADD_IR,
        module_name="probe",
    )

    # The default preset is the "fast" preset = ("mem2reg", "sroa").
    # mem2reg promotes allocas to SSA; sroa breaks up aggregates.
    # Neither eliminates dead arithmetic (``%dead = add i32 %v, 0``
    # stays because it's still used in ``ret i32 %dead``).  But the
    # ``alloca``/``store``/``load`` triple should be gone — that's the
    # actual signal that the registered passes ran.  DCE is in the
    # "quick" preset, not "default"/"fast".
    assert "alloca" not in out
    assert "store i32" not in out
    assert "load i32" not in out


def test_python_ir_pass_names_stay_list_for_bootstrap_joining():
    pass_names = pipeline._resolve_python_ir_pass_names("default")

    assert isinstance(pass_names, list)
    assert pipeline._join_strings(pass_names, ",") == "mem2reg,sroa"


def test_host_python_prefers_repo_venv(tmp_path, monkeypatch):
    monkeypatch.delenv("PCC_HOST_PYTHON", raising=False)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    host_py = venv_bin / "python3"
    host_py.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert pipeline._host_python_command() == str(host_py)


def test_module_name_from_package_main(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    main = pkg / "__main__.py"
    main.write_text("print(1)\n", encoding="utf-8")

    assert pipeline._module_name_from_src(str(main)) == "pkg.__main__"


def test_python_ir_pass_pipeline_failure_is_not_empty_success(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "not_a_real_pass")

    with pytest.raises(pipeline.PyPipelineError):
        pipeline._apply_python_ir_pass_pipeline(
            _DEAD_ADD_IR,
            module_name="probe",
        )


def test_python_ir_pass_pipeline_runs_registered_ir_pass(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "dce")

    out = pipeline._apply_python_ir_pass_pipeline(
        _DEAD_ADD_IR,
        module_name="probe",
    )

    assert "%dead" not in out


def test_python_ir_pass_pipeline_on_expands_to_fast_default(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "on")

    assert pipeline._resolve_python_ir_pass_names() == [
        "mem2reg",
        "sroa",
    ]


def test_python_ir_pass_pipeline_all_stays_explicit_all(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "all")

    assert pipeline._resolve_python_ir_pass_names() == ["all"]


def test_python_ir_pass_pipeline_all_preset_runs_registered_passes():
    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _DEAD_ADD_IR,
        pass_names=("all",),
        module_name="probe",
    )

    assert "%dead" not in out


def test_python_ir_pass_memory_transport_all_uses_llvm_default_o2(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")

    names = ir_pass_pipeline._expand_pass_names(
        ("all",),
        len(_DEAD_ADD_IR),
        transport="memory",
    )

    assert names == ("default<O2>",)


def test_python_ir_pass_memory_transport_keeps_llvm_default_on_large_modules(
    monkeypatch,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_LARGE_MODULE_BYTES", "1")

    names = ir_pass_pipeline._expand_pass_names(
        ("all",),
        len(_DEAD_ADD_IR),
        transport="memory",
    )

    assert names == ("default<O2>",)


def test_python_ir_pass_memory_transport_matches_opt_default_o2(monkeypatch):
    opt = find_opt_binary()
    if opt is None:
        pytest.fail("matching LLVM opt binary is not available")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")

    memory_out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _OPT_DEFAULT_PIPELINE_IR,
        pass_names=("all",),
        module_name="probe",
    )
    opt_out = run_pipeline(opt, "default<O2>", _OPT_DEFAULT_PIPELINE_IR)

    assert normalize_ir(memory_out) == normalize_ir(opt_out)


def test_python_ir_pass_memory_transport_canonicalizes_default_os(monkeypatch):
    opt = find_opt_binary()
    if opt is None:
        pytest.fail("matching LLVM opt binary is not available")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")

    memory_out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _OPT_DEFAULT_PIPELINE_IR,
        pass_names=("default<os>",),
        module_name="probe",
    )
    opt_out = run_pipeline(opt, "default<Os>", _OPT_DEFAULT_PIPELINE_IR)

    assert normalize_ir(memory_out) == normalize_ir(opt_out)


def test_python_ir_pass_memory_transport_runs_default_fast(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_CACHE", "off")

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _OPT_DEFAULT_PIPELINE_IR,
        pass_names=("default",),
        module_name="probe",
    )

    assert "%p = alloca" not in out
    llvm.parse_assembly(out).verify()


def test_python_ir_pass_memory_transport_skips_parse_error(monkeypatch, capsys):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_CACHE", "off")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY", "1")

    invalid_ir = """
define i64 @main() {
entry:
  %flag = icmp eq i64 1, 1
  %bad = or ptr null, %flag
  ret i64 0
}
"""

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        invalid_ir,
        pass_names=("default",),
        module_name="probe",
    )

    assert out == invalid_ir
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert any(
        record.get("event") == "pass-batch"
        and record.get("status") == "skip_parse_error"
        for record in records
    )


def test_python_ir_pass_default_fast_auto_selects_memory_transport(
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("PCC_PYTHON_IR_PASS_TRANSPORT", raising=False)
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_CACHE", "off")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY", "1")

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _OPT_DEFAULT_PIPELINE_IR,
        pass_names=("default",),
        module_name="probe",
    )

    assert "%p = alloca" not in out
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert records[0]["transport"] == "memory"
    assert {
        (record.get("pass"), record.get("status"), record.get("transport"))
        for record in records
        if record.get("event") == "pass"
    } == {
        ("mem2reg", "run", "memory"),
        ("sroa", "run", "memory"),
    }
    llvm.parse_assembly(out).verify()


def test_python_ir_pass_explicit_text_transport_overrides_default_fast(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "text")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY", "1")

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _OPT_DEFAULT_PIPELINE_IR,
        pass_names=("default",),
        module_name="probe",
    )

    # Default = "fast" preset = (mem2reg, sroa). Confirms the alloca
    # was promoted; ``%dead = add %v, 0`` stays because DCE isn't in
    # this preset (DCE is in "quick").
    assert "%p = alloca" not in out
    assert "store i32" not in out
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert records[0]["transport"] == "text"


def test_python_ir_pass_memory_transport_strict_no_libpython_skips_cpy_refs(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_STRICT_NO_LIBPYTHON", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY", "1")

    ir_text = """
declare ptr @py_cpy_import(ptr)

@.mod = internal constant [9 x i8] c"builtins\\00"

define i32 @main() {
entry:
  %p = getelementptr inbounds [9 x i8], ptr @.mod, i32 0, i32 0
  %m = call ptr @py_cpy_import(ptr %p)
  ret i32 0
}
""".strip()

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        ir_text,
        pass_names=("default",),
        module_name="probe",
    )

    assert out == ir_text
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    statuses = {
        (record.get("pass"), record.get("status"))
        for record in records
        if record.get("event") == "pass"
    }
    assert ("mem2reg", "skip_cpy_ref") in statuses


def test_python_ir_pass_memory_transport_strict_no_libpython_allows_cpy_decls(
    monkeypatch,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_STRICT_NO_LIBPYTHON", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_CACHE", "off")
    ir_text = _OPT_DEFAULT_PIPELINE_IR.replace(
        "define i32 @main()",
        "declare ptr @py_cpy_import(ptr)\n\n" "define i32 @main()",
    )

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        ir_text,
        pass_names=("default",),
        module_name="probe",
    )

    assert "%p = alloca" not in out
    llvm.parse_assembly(out).verify()


def test_python_ir_pass_memory_transport_uses_content_cache(tmp_path, monkeypatch):
    from pcc.llvm_capi import binding as llvm_capi_binding

    cache_dir = tmp_path / "ir-pass-cache"
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_CACHE_DIR", str(cache_dir))
    calls = []
    real_run_passes_on_ir = llvm_capi_binding.run_passes_on_ir

    def counted_run_passes_on_ir(ir_text, passes, *args, **kwargs):
        calls.append(passes)
        return real_run_passes_on_ir(ir_text, passes, *args, **kwargs)

    monkeypatch.setattr(
        llvm_capi_binding,
        "run_passes_on_ir",
        counted_run_passes_on_ir,
    )
    first = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _OPT_DEFAULT_PIPELINE_IR,
        pass_names=("all",),
        module_name="probe",
    )
    assert calls == ["default<O2>"]

    def fail_run_passes_on_ir(*_args, **_kwargs):
        raise AssertionError("second identical memory pipeline should hit cache")

    monkeypatch.setattr(
        llvm_capi_binding,
        "run_passes_on_ir",
        fail_run_passes_on_ir,
    )
    second = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _OPT_DEFAULT_PIPELINE_IR,
        pass_names=("all",),
        module_name="probe",
    )

    assert second == first


def test_python_ir_pass_memory_transport_cache_can_be_disabled(tmp_path, monkeypatch):
    from pcc.llvm_capi import binding as llvm_capi_binding

    cache_dir = tmp_path / "ir-pass-cache"
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_CACHE_DIR", str(cache_dir))
    ir_pass_pipeline.run_python_ir_pass_pipeline(
        _OPT_DEFAULT_PIPELINE_IR,
        pass_names=("all",),
        module_name="probe",
    )

    calls = []

    def fake_run_passes_on_ir(ir_text, _passes, *_args, **_kwargs):
        calls.append(True)
        return ir_text

    monkeypatch.setenv("PCC_PYTHON_IR_PASS_CACHE", "off")
    monkeypatch.setattr(
        llvm_capi_binding,
        "run_passes_on_ir",
        fake_run_passes_on_ir,
    )
    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _OPT_DEFAULT_PIPELINE_IR,
        pass_names=("all",),
        module_name="probe",
    )

    assert calls == [True]
    assert out == _OPT_DEFAULT_PIPELINE_IR


def test_python_ir_pass_memory_transport_rejects_bad_transport():
    with pytest.raises(ir_pass_pipeline.PythonIRPassError, match="TRANSPORT"):
        ir_pass_pipeline.resolve_python_ir_pass_transport("socket")


def test_python_ir_pass_pipeline_runs_loop_unroll():
    ir = """
define i32 @main(i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
  %s = phi i32 [ 0, %entry ], [ %s2, %loop ]
  %s2 = add i32 %s, %i
  %inc = add i32 %i, 1
  %cmp = icmp slt i32 %inc, 3
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %s2
}
"""

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        ir,
        pass_names=("loop-unroll",),
        module_name="probe",
    )

    assert "phi i32" not in out
    assert "ret i32 3" in out


def test_python_ir_pass_pipeline_runs_conservative_dse():
    ir = """
define i32 @main() {
entry:
  %p = alloca i32
  store i32 1, ptr %p
  store i32 2, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}
"""

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        ir,
        pass_names=("dse",),
        module_name="probe",
    )

    assert "store i32 1, ptr %p" not in out
    assert "store i32 2, ptr %p" in out


def test_python_ir_pass_pipeline_sets_bootstrap_licm_budget(monkeypatch):
    monkeypatch.delenv("PCC_LICM_LOOP_BUDGET", raising=False)

    ir_pass_pipeline.run_python_ir_pass_pipeline(
        _DEAD_ADD_IR,
        pass_names=("dce",),
        module_name="probe",
    )

    assert os.environ["PCC_LICM_LOOP_BUDGET"] == "8"


def test_large_module_budget_skips_textual_pass(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_LARGE_MODULE_BYTES", "1")

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _GLOBAL_STRING_BRANCH_IR,
        pass_names=("simplifycfg",),
        module_name="probe",
    )

    assert "br i1 true" in out
    assert "ret i32 0" in out
    llvm.parse_assembly(out).verify()


def test_all_skipped_large_module_does_not_parse_ir(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_LARGE_MODULE_BYTES", "1")

    def fail_parse(_text):
        raise AssertionError("skipped-only pipeline should not parse IR")

    monkeypatch.setattr(llvm, "parse_assembly", fail_parse)

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _GLOBAL_STRING_BRANCH_IR,
        pass_names=("simplifycfg",),
        module_name="probe",
    )

    assert out == _GLOBAL_STRING_BRANCH_IR


def test_python_ir_pass_telemetry_reports_skips_and_runs(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_LARGE_MODULE_BYTES", "1")

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _GLOBAL_STRING_BRANCH_IR,
        pass_names=("simplifycfg", "dce"),
        module_name="probe",
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    records = [json.loads(line) for line in captured.err.splitlines()]
    assert records[0]["event"] == "start"
    assert records[0]["module"] == "probe"
    assert any(
        record.get("event") == "pass"
        and record.get("pass") == "simplifycfg"
        and record.get("status") == "skip_large"
        for record in records
    )
    assert any(
        record.get("event") == "pass"
        and record.get("pass") == "dce"
        and record.get("status") == "run"
        and "elapsed_ms" in record
        and record.get("ir_bytes_before", 0) > 0
        for record in records
    )
    assert records[-1]["event"] == "end"
    llvm.parse_assembly(out).verify()


def test_python_ir_pass_telemetry_can_write_jsonl_file(
    tmp_path,
    monkeypatch,
    capsys,
):
    telemetry = tmp_path / "passes.jsonl"
    monkeypatch.setenv(
        "PCC_PYTHON_IR_PASS_TELEMETRY_PATH",
        str(telemetry),
    )

    ir_pass_pipeline.run_python_ir_pass_pipeline(
        _DEAD_ADD_IR,
        pass_names=("dce",),
        module_name="probe",
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    records = [
        json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["event"] == "start"
    assert records[1]["pass"] == "dce"
    assert records[1]["status"] == "run"
    assert records[-1]["event"] == "end"


def test_medium_module_budget_skips_costly_textual_passes(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_MEDIUM_MODULE_BYTES", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_LARGE_MODULE_BYTES", "0")

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _GLOBAL_STRING_BRANCH_IR,
        pass_names=("mldst-motion", "dce"),
        module_name="probe",
    )

    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert any(
        record.get("event") == "pass"
        and record.get("pass") == "mldst-motion"
        and record.get("status") == "skip_medium_cost"
        for record in records
    )
    assert any(
        record.get("event") == "pass"
        and record.get("pass") == "dce"
        and record.get("status") == "run"
        for record in records
    )
    llvm.parse_assembly(out).verify()


def test_large_module_fast_default_keeps_mem2reg_sroa(monkeypatch, capsys):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_LARGE_MODULE_BYTES", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_HUGE_MODULE_BYTES", "0")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "text")

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _RUNTIME_CALL_IR,
        pass_names=("default",),
        module_name="probe",
    )

    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    statuses = {
        (record.get("pass"), record.get("status"))
        for record in records
        if record.get("event") == "pass"
    }
    assert ("mem2reg", "run") in statuses
    assert ("sroa", "run") in statuses
    assert ("mem2reg", "skip_large") not in statuses
    assert ("sroa", "skip_large") not in statuses
    llvm.parse_assembly(out).verify()


def test_huge_module_default_skips_fast_preset_without_parse(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_HUGE_MODULE_BYTES", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "text")

    def fail_parse(_text):
        raise AssertionError("huge skipped-only pipeline should not parse IR")

    monkeypatch.setattr(llvm, "parse_assembly", fail_parse)

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _RUNTIME_CALL_IR,
        pass_names=("default",),
        module_name="probe",
    )

    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    statuses = {
        (record.get("pass"), record.get("status"))
        for record in records
        if record.get("event") == "pass"
    }
    assert ("mem2reg", "skip_huge") in statuses
    assert ("mem2reg", "skip_huge") in statuses
    assert ("sroa", "skip_huge") in statuses
    assert out == _RUNTIME_CALL_IR


def test_huge_module_default_memory_transport_skips_fast_preset_without_parse(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_HUGE_MODULE_BYTES", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")

    from pcc.llvm_capi import binding as llvm_capi_binding

    def fail_run_passes_on_ir(*_args, **_kwargs):
        raise AssertionError("huge skipped-only memory pipeline should not run LLVM")

    monkeypatch.setattr(llvm_capi_binding, "run_passes_on_ir", fail_run_passes_on_ir)

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _RUNTIME_CALL_IR,
        pass_names=("default",),
        module_name="probe",
    )

    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    statuses = {
        (record.get("pass"), record.get("status"))
        for record in records
        if record.get("event") == "pass"
    }
    assert ("mem2reg", "skip_huge") in statuses
    assert ("mem2reg", "skip_huge") in statuses
    assert ("sroa", "skip_huge") in statuses
    assert out == _RUNTIME_CALL_IR


def test_large_module_all_preset_uses_self_host_safe_subset(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_LARGE_MODULE_BYTES", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_HUGE_MODULE_BYTES", "0")

    out = ir_pass_pipeline.run_python_ir_pass_pipeline(
        _GLOBAL_STRING_BRANCH_IR,
        pass_names=("all",),
        module_name="probe",
    )

    assert "br i1 true" in out
    assert "ret i32 0" in out
    llvm.parse_assembly(out).verify()


@pytest.mark.parametrize("pass_name", ["loop-instsimplify", "loop-simplifycfg"])
def test_function_local_loop_passes_keep_module_declarations(monkeypatch, pass_name):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", pass_name)

    out = pipeline._apply_python_ir_pass_pipeline(
        _RUNTIME_CALL_IR,
        module_name="probe",
    )

    assert "@py_int_from_i64" in out
    llvm.parse_assembly(out).verify()


def test_simplifycfg_local_cleanup_keeps_global_context(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "simplifycfg")

    out = pipeline._apply_python_ir_pass_pipeline(
        _GLOBAL_STRING_BRANCH_IR,
        module_name="probe",
    )

    assert "@.pystr.0" in out
    assert "br i1 true" not in out
    llvm.parse_assembly(out).verify()


def test_simplifycfg_local_cleanup_declares_sibling_functions(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "simplifycfg")

    out = pipeline._apply_python_ir_pass_pipeline(
        _SIBLING_CALL_BRANCH_IR,
        module_name="probe",
    )

    assert "@helper" in out
    assert "br i1 true" not in out
    llvm.parse_assembly(out).verify()


def test_simplifycfg_local_cleanup_strips_internal_from_declarations(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "simplifycfg")

    out = pipeline._apply_python_ir_pass_pipeline(
        _INTERNAL_SIBLING_CALL_BRANCH_IR,
        module_name="probe",
    )

    assert "declare internal ptr @helper" not in out
    assert "@helper" in out
    assert "br i1 true" not in out
    llvm.parse_assembly(out).verify()


def test_python_ir_pass_pipeline_many_runs_one_batch(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "simplifycfg")

    out = pipeline._apply_python_ir_pass_pipeline_many(
        [
            ("first", _GLOBAL_STRING_BRANCH_IR),
            ("second", _SIBLING_CALL_BRANCH_IR),
        ],
    )

    assert [name for name, _text in out] == ["first", "second"]
    for _name, ir_text in out:
        assert "br i1 true" not in ir_text
        llvm.parse_assembly(ir_text).verify()


def test_python_ir_pass_pipeline_timeout_is_bounded(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, timeout=0.5)

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "dce")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TIMEOUT", "0.5")
    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    with pytest.raises(
        pipeline.PyPipelineError,
        match=(
            "Python IR pass pipeline timed out.*probe.*0.500s.*" "passes=dce.*ir_bytes="
        ),
    ):
        pipeline._apply_python_ir_pass_pipeline(
            _DEAD_ADD_IR,
            module_name="probe",
        )

    assert calls
    assert calls[0][1]["timeout"] == 0.5


def test_python_ir_pass_pipeline_many_timeout_is_bounded(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, timeout=0.25)

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "dce")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TIMEOUT", "0.25")
    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    with pytest.raises(
        pipeline.PyPipelineError,
        match=(
            "Python IR pass batch pipeline timed out after 0.250s; "
            "modules=2 passes=dce total_bytes=.*largest=second:.*first:"
        ),
    ):
        pipeline._apply_python_ir_pass_pipeline_many(
            [
                ("first", _DEAD_ADD_IR),
                ("second", _DEAD_ADD_IR),
            ],
        )

    assert calls
    assert calls[0][1]["timeout"] == 0.25


def test_compile_python_emit_llvm_applies_python_ir_pass_pipeline(
    tmp_path,
    monkeypatch,
):
    src = tmp_path / "main.py"
    src.write_text("print(1)\n", encoding="utf-8")
    out = tmp_path / "main.ll"
    seen = []

    def fake_pipeline(
        ir_text,
        *,
        module_name,
        verbose=False,
        default_raw=None,
        strict_no_libpython=False,
    ):
        seen.append((module_name, verbose))
        return str(ir_text) + "\n; pass marker\n"

    monkeypatch.setattr(
        pipeline,
        "_apply_python_ir_pass_pipeline",
        fake_pipeline,
    )

    pipeline.compile_python(str(src), str(out), emit_llvm_only=True)

    assert seen == [("main", False)]
    assert "; pass marker" in out.read_text(encoding="utf-8")


def test_string_literals_emit_immortal_globals_not_py_str_new(tmp_path):
    src = tmp_path / "main.py"
    src.write_text('print("same")\nprint("same")\n', encoding="utf-8")
    out = tmp_path / "main.ll"

    pipeline.compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        libpython_mode="off",
    )

    text = out.read_text(encoding="utf-8")
    assert "call ptr @py_str_new" not in text
    assert text.count('c"same\\00"') == 1
    assert "i32 4, i32 1" in text


def test_compile_python_multi_batches_python_ir_pass_pipeline(
    tmp_path,
    monkeypatch,
):
    entry = tmp_path / "entry.py"
    helper = tmp_path / "helper.py"
    entry.write_text("print(1)\n", encoding="utf-8")
    helper.write_text("print(2)\n", encoding="utf-8")
    out = tmp_path / "combined.ll"
    seen = []

    def fail_per_module(*args, **kwargs):
        raise AssertionError("multi-file compile should use batch IR passes")

    def fake_pipeline_many(
        module_ir_texts,
        *,
        verbose=False,
        default_raw=None,
        strict_no_libpython=False,
    ):
        seen.append([name for name, _text in module_ir_texts])
        return [
            (name, str(text) + "\n; pass marker " + name + "\n")
            for name, text in module_ir_texts
        ]

    monkeypatch.setattr(
        pipeline,
        "_apply_python_ir_pass_pipeline",
        fail_per_module,
    )
    monkeypatch.setattr(
        pipeline,
        "_apply_python_ir_pass_pipeline_many",
        fake_pipeline_many,
        raising=False,
    )

    pipeline.compile_python_multi(
        [str(entry), str(helper)],
        str(out),
        emit_llvm_only=True,
        entry_module="entry",
        module_names=["entry", "helper"],
    )

    assert seen == [["entry", "helper"]]
    text = out.read_text(encoding="utf-8")
    assert "; pass marker entry" in text
    assert "; pass marker helper" in text


def test_compile_python_multi_strict_no_libpython_fails_after_first_fallback_module(
    tmp_path,
    monkeypatch,
):
    import pytest

    from pcc.py_frontend import type_infer
    from pcc.py_frontend.codegen import layer1

    entry = tmp_path / "entry.py"
    helper = tmp_path / "helper.py"
    entry.write_text("print(1)\n", encoding="utf-8")
    helper.write_text("print(2)\n", encoding="utf-8")
    out = tmp_path / "combined.ll"
    generated = []

    class FakeCodeGen:
        def __init__(self, typed_mod, allow_libpython, ir_scaffold_mode):
            self._native_module_exports = {}

        def generate(self, typed_mod):
            generated.append(typed_mod)
            if len(generated) == 1:
                return (
                    "declare ptr @py_cpy_import(ptr)\n\n"
                    "define void @user_entry_main() {\n"
                    "  %m = call ptr @py_cpy_import(ptr null)\n"
                    "  ret void\n"
                    "}\n"
                )
            raise AssertionError("later modules should not be generated")

    def fake_build_closed_world_context(src_paths, module_names, profile):
        return list(module_names), {}, {}

    def fake_infer_module(ast_mod, **_kwargs):
        return ast_mod

    def fail_pipeline_many(*_args, **_kwargs):
        raise AssertionError(
            "strict no-libpython fallback should fail before IR passes"
        )

    monkeypatch.setattr(
        pipeline,
        "_collect_multi_source_relative_closure",
        lambda srcs, mods, recursive_stdlib=False: (list(srcs), list(mods)),
    )
    monkeypatch.setattr(
        pipeline,
        "_filter_ir_scaffold_closure",
        lambda srcs, mods, ir_scaffold_mode=None: (list(srcs), list(mods)),
    )
    monkeypatch.setattr(
        pipeline,
        "_validate_package_site_no_libpython_abi",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(pipeline, "_order_module_inits", lambda *_args: [])
    monkeypatch.setattr(
        pipeline,
        "build_closed_world_context",
        fake_build_closed_world_context,
    )
    monkeypatch.setattr(
        pipeline,
        "_module_imports_pcc_native_extension",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        pipeline,
        "_contextual_host_params_for_module",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        pipeline,
        "_module_uses_default_native_exports",
        lambda _mod_name: False,
    )
    monkeypatch.setattr(
        pipeline,
        "_apply_python_ir_pass_pipeline_many",
        fail_pipeline_many,
    )
    monkeypatch.setattr(type_infer, "infer_module", fake_infer_module)
    monkeypatch.setattr(layer1, "L1CodeGen", FakeCodeGen)

    with pytest.raises(pipeline.PyPipelineError) as excinfo:
        pipeline.compile_python_multi(
            [str(entry), str(helper)],
            str(out),
            emit_llvm_only=True,
            entry_module="entry",
            module_names=["entry", "helper"],
            libpython_mode="off",
        )

    message = str(excinfo.value)
    assert "requires libpython fallback for multi-file compile" in message
    assert "module entry generated IR still calls py_cpy_* helpers" in message
    assert len(generated) == 1
    assert getattr(generated[0], "name", "") == "entry"


def test_compile_python_multi_reuses_export_pass_ast(tmp_path, monkeypatch):
    from pcc.parse import py_lift

    entry = tmp_path / "entry.py"
    helper = tmp_path / "helper.py"
    entry.write_text("from .helper import value\nprint(value())\n", encoding="utf-8")
    helper.write_text("def value() -> int:\n    return 3\n", encoding="utf-8")
    out = tmp_path / "combined.ll"
    calls = []
    real_parse_and_lift = py_lift.parse_and_lift

    def counted_parse_and_lift(source, filename, module_name):
        calls.append(module_name)
        return real_parse_and_lift(source, filename, module_name)

    monkeypatch.setattr(py_lift, "parse_and_lift", counted_parse_and_lift)

    pipeline.compile_python_multi(
        [str(entry), str(helper)],
        str(out),
        emit_llvm_only=True,
        backend="self",
        module_names=["pkg.entry", "pkg.helper"],
        entry_module="pkg.entry",
    )

    assert calls == []


def test_parallel_frontend_codegen_uses_shared_export_context(tmp_path, monkeypatch):
    entry = tmp_path / "entry.py"
    helper = tmp_path / "helper.py"
    entry.write_text("def main() -> int:\n    return 0\n\nmain()\n", encoding="utf-8")
    helper.write_text("def value() -> int:\n    return 3\n", encoding="utf-8")
    out = tmp_path / "program.out"
    context_lift_indices = []
    seen_exports = []
    export_manifests = []
    codegen_manifests = []

    def fake_build_closed_world_context(
        src_paths,
        module_names,
        profile=None,
        lift_indices=None,
        merge_exports=True,
    ):
        context_lift_indices.append(tuple(lift_indices or ()))
        return [None for _src in src_paths], {"entry": {}, "helper": {}}, {}

    def fake_run_worker_commands(commands, max_parallel=None):
        for command in commands:
            manifest_path = command.split()[-1]
            manifest = pipeline._read_python_frontend_worker_manifest(manifest_path)
            if manifest["job_kind"] == "export":
                export_manifests.append(manifest)
                exports_path = os.path.join(
                    manifest["ir_dir"],
                    f"exports_{len(export_manifests)}.json",
                )
                edges_path = os.path.join(
                    manifest["ir_dir"],
                    f"edges_{len(export_manifests)}.json",
                )
                pipeline._write_native_exports_wire(
                    exports_path,
                    {"entry": {}, "helper": {}},
                    {},
                )
                pipeline._write_reexport_edges_wire(edges_path, ())
                with open(manifest["result_path"], "w", encoding="utf-8") as f:
                    f.write("EXPORT\t" + exports_path + "\t" + edges_path + "\n")
                continue
            codegen_manifests.append(manifest)
            assert manifest["exports_path"]
            native_exports, derived_class_map = pipeline._read_native_exports_wire(
                manifest["exports_path"]
            )
            seen_exports.append((native_exports, derived_class_map))
            result_lines = []
            for index in manifest["assigned_indices"]:
                mod_name = manifest["module_names"][index]
                ir_path = os.path.join(manifest["ir_dir"], f"module_{index}.ll")
                with open(ir_path, "w", encoding="utf-8") as f:
                    f.write(f"; module {mod_name}\n")
                result_lines.append(
                    "OK\t"
                    + str(index)
                    + "\t"
                    + mod_name
                    + "\t0\t0\t"
                    + str(len(mod_name))
                    + "\t"
                    + ir_path
                )
            with open(manifest["result_path"], "w", encoding="utf-8") as f:
                for line in result_lines:
                    f.write(line + "\n")

    monkeypatch.setattr(pipeline, "_python_frontend_jobs", lambda _n: 2)
    monkeypatch.setattr(pipeline, "_can_spawn_python_frontend_worker", lambda: True)
    monkeypatch.setattr(
        pipeline,
        "_python_frontend_worker_command_prefix",
        lambda: ["/tmp/pcc-fake-worker"],
    )
    monkeypatch.setattr(
        pipeline,
        "build_closed_world_context",
        fake_build_closed_world_context,
    )
    monkeypatch.setattr(
        pipeline,
        "_run_python_frontend_worker_commands",
        fake_run_worker_commands,
    )
    monkeypatch.setattr(
        pipeline,
        "_apply_python_ir_pass_pipeline_many",
        lambda module_ir_texts, **_kwargs: module_ir_texts,
    )
    monkeypatch.setattr(
        pipeline,
        "_ensure_runtime",
        lambda verbose, *, needs_libpython=False: "/tmp/libpy_runtime.a",
    )
    monkeypatch.setattr(
        pipeline,
        "_link_with_self_backend_ir_texts",
        lambda ir_texts, out_path, runtime_archive, verbose, **_kwargs: out.write_text(
            "linked", encoding="utf-8"
        ),
    )

    pipeline.compile_python_multi(
        [str(entry), str(helper)],
        str(out),
        backend="self",
        module_names=["entry", "helper"],
        entry_module="entry",
    )

    assert context_lift_indices == []
    assert export_manifests
    assert codegen_manifests
    assert seen_exports
    assert all(exports == {"entry": {}, "helper": {}} for exports, _ in seen_exports)
    assert out.read_text(encoding="utf-8") == "linked"


def test_self_backend_native_compile_defaults_python_ir_passes_off(
    tmp_path,
    monkeypatch,
):
    entry = tmp_path / "entry.py"
    helper = tmp_path / "helper.py"
    entry.write_text("from .helper import value\nprint(value())\n", encoding="utf-8")
    helper.write_text("def value() -> int:\n    return 3\n", encoding="utf-8")
    out = tmp_path / "program.out"
    defaults = []

    def fake_pipeline_many(
        module_ir_texts,
        *,
        verbose=False,
        default_raw=None,
        strict_no_libpython=False,
    ):
        defaults.append(default_raw)
        return [(name, str(text)) for name, text in module_ir_texts]

    monkeypatch.delenv("PCC_PYTHON_IR_PASSES", raising=False)
    monkeypatch.setattr(
        pipeline,
        "_apply_python_ir_pass_pipeline_many",
        fake_pipeline_many,
        raising=False,
    )
    monkeypatch.setattr(
        pipeline,
        "_ensure_runtime",
        lambda verbose, *, needs_libpython=False: "/tmp/libpy_runtime.a",
    )
    monkeypatch.setattr(
        pipeline,
        "_link_native",
        lambda ll_paths, out_path, runtime_archive, verbose, *, backend, needs_libpython=False: out.write_text(
            "linked", encoding="utf-8"
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_link_with_self_backend_ir_texts",
        lambda ir_texts, out_path, runtime_archive, verbose, *, needs_libpython=False, needs_native_extension_exports=False, profile=None: out.write_text(
            "linked", encoding="utf-8"
        ),
    )

    pipeline.compile_python_multi(
        [str(entry), str(helper)],
        str(out),
        backend="self",
        module_names=["pkg.entry", "pkg.helper"],
        entry_module="pkg.entry",
    )

    assert defaults == ["off"]


def test_self_backend_emit_llvm_defaults_python_ir_passes_off(
    tmp_path,
    monkeypatch,
):
    entry = tmp_path / "entry.py"
    helper = tmp_path / "helper.py"
    entry.write_text("from .helper import value\nprint(value())\n", encoding="utf-8")
    helper.write_text("def value() -> int:\n    return 3\n", encoding="utf-8")
    out = tmp_path / "combined.ll"
    defaults = []

    def fake_pipeline_many(
        module_ir_texts,
        *,
        verbose=False,
        default_raw=None,
        strict_no_libpython=False,
    ):
        defaults.append(default_raw)
        return [(name, str(text)) for name, text in module_ir_texts]

    monkeypatch.delenv("PCC_PYTHON_IR_PASSES", raising=False)
    monkeypatch.setattr(
        pipeline,
        "_apply_python_ir_pass_pipeline_many",
        fake_pipeline_many,
        raising=False,
    )

    pipeline.compile_python_multi(
        [str(entry), str(helper)],
        str(out),
        emit_llvm_only=True,
        backend="self",
        module_names=["pkg.entry", "pkg.helper"],
        entry_module="pkg.entry",
    )

    assert defaults == ["off"]
    assert out.exists()


def test_explicit_python_ir_pass_env_overrides_self_backend_default(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "dce")

    assert pipeline._resolve_python_ir_pass_names(default_raw="off") == ["dce"]


def test_default_fast_parent_transport_policy_selects_memory(monkeypatch):
    monkeypatch.delenv("PCC_PYTHON_IR_PASS_TRANSPORT", raising=False)

    assert (
        pipeline._default_python_ir_pass_transport(
            pipeline._resolve_python_ir_pass_names("default"),
            None,
        )
        == "memory"
    )


def test_self_backend_explicit_default_parent_transport_policy_selects_memory(
    monkeypatch,
):
    monkeypatch.delenv("PCC_PYTHON_IR_PASS_TRANSPORT", raising=False)
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "default")

    assert (
        pipeline._default_python_ir_pass_transport(
            pipeline._resolve_python_ir_pass_names("default", default_raw="off"),
            "off",
        )
        == "memory"
    )


def test_explicit_transport_overrides_parent_transport_policy(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")

    assert (
        pipeline._default_python_ir_pass_transport(
            pipeline._resolve_python_ir_pass_names("default", default_raw="off"),
            "off",
        )
        is None
    )


def test_memory_pass_shards_namespace_internal_symbols(monkeypatch):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@shared = internal constant [4 x i8] c"one\\00"

define internal ptr @helper() {
entry:
  ret ptr @shared
}

define ptr @entry() {
entry:
  %p = call ptr @helper()
  ret ptr %p
}
""".strip()

    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_SPLIT_THRESHOLD_BYTES", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_SPLIT_SHARD_BYTES", "80")

    shards = pipeline._split_large_modules_for_python_ir_passes(
        [("pkg.mod", ir_text)],
        ["all"],
    )

    assert len(shards) >= 3
    joined = "\n".join(text for _name, text in shards)
    assert "@__pcp0_shared = constant [4 x i8]" in joined
    assert "define ptr @__pcp0_helper()" in joined
    assert "call ptr @__pcp0_helper()" in joined
    assert "ret ptr @__pcp0_shared" in joined
    assert "@shared = constant" not in joined
    assert "declare internal" not in joined
    assert "define internal" not in joined
    for _name, text in shards:
        llvm.parse_assembly(text).verify()


def test_memory_pass_shards_keep_distinct_internal_symbols_per_module(monkeypatch):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

@shared = internal global i64 0

define i64 @entry() {
entry:
  %v = load i64, ptr @shared
  ret i64 %v
}

define void @touch() {
entry:
  store i64 1, ptr @shared
  ret void
}
""".strip()

    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_SPLIT_THRESHOLD_BYTES", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_SPLIT_SHARD_BYTES", "80")

    shards = pipeline._split_large_modules_for_python_ir_passes(
        [("pkg.a", ir_text), ("pkg.b", ir_text)],
        ["all"],
    )
    joined = "\n".join(text for _name, text in shards)

    assert "@__pcp0_shared = global i64 0" in joined
    assert "@__pcp1_shared = global i64 0" in joined
    assert "@shared = global i64 0" not in joined


def test_memory_pass_shards_default_fast_pipeline(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_SPLIT_THRESHOLD_BYTES", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_SPLIT_SHARD_BYTES", "80")

    shards = pipeline._split_large_modules_for_python_ir_passes(
        [("pkg.mod", _SIBLING_CALL_BRANCH_IR)],
        pipeline._resolve_python_ir_pass_names("default"),
    )

    assert [name for name, _text in shards] == [
        "pkg.mod.__pass_shard_0",
        "pkg.mod.__pass_shard_1",
    ]
    for _name, text in shards:
        llvm.parse_assembly(text).verify()


def test_batch_pass_skip_survives_large_module_sharding(monkeypatch, tmp_path):
    telemetry_path = tmp_path / "passes.jsonl"
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TRANSPORT", "memory")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_CACHE", "off")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_TELEMETRY_PATH", str(telemetry_path))
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_SPLIT_THRESHOLD_BYTES", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_SPLIT_SHARD_BYTES", "80")

    out = pipeline._apply_python_ir_pass_pipeline_many(
        [("pcc.py_frontend.codegen.class_gen", _SIBLING_CALL_BRANCH_IR)],
        default_raw="default",
    )

    assert [name for name, _text in out] == [
        "pcc.py_frontend.codegen.class_gen.__pass_shard_0",
        "pcc.py_frontend.codegen.class_gen.__pass_shard_1",
    ]
    assert (
        not telemetry_path.exists() or telemetry_path.read_text(encoding="utf-8") == ""
    )
