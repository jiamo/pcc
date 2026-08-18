"""Focused ownership contracts for frontend worker execution and scheduling."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_frontend_parallel as parallel
from pcc.py_frontend import pipeline_frontend_workers as worker_policy
from pcc.py_frontend import pipeline_frontend_worker_execution as worker_execution
from pcc.py_frontend import module_action_dag


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _action_state(source_a: str = "a-v1"):
    summary = module_action_dag.PublicSummary.create(exports=("stable",))
    return module_action_dag.GraphState.create(
        compiler_digest=_digest("compiler"),
        runtime_abi_digest=_digest("runtime"),
        target="darwin:arm64",
        options_digest=_digest("options"),
        modules=(
            module_action_dag.ModuleState.create(
                "a", _digest(source_a), (), summary
            ),
            module_action_dag.ModuleState.create(
                "b", _digest("b-v1"), (), summary
            ),
        ),
    )


def _module_action(state, name: str):
    return module_action_dag.Action(
        module=name,
        stage="module-ir",
        key=module_action_dag.action_key(state, name, "module-ir"),
        reason="test",
    )


def _action_cache_plan(action_root, state):
    """Mirror the identity-bearing plan produced by the real cache owner."""

    return {
        "action_root": str(action_root),
        "compiler_digest": state.compiler_digest,
        "runtime_abi_digest": state.runtime_abi_digest,
        "target": state.target,
        "options_digest": state.options_digest,
    }


def test_worker_command_runner_has_one_owner():
    assert pipeline._run_python_frontend_worker_commands is worker_policy.run_worker_commands


def test_action_dependencies_keep_from_import_submodules_and_ir_provider():
    pipeline_path = Path(pipeline.__file__).resolve()
    pipeline_dependencies = pipeline._python_frontend_action_dependencies(
        str(pipeline_path),
        "pcc.py_frontend.pipeline",
        (
            "pcc.py_frontend.pipeline",
            "pcc.py_frontend.pipeline_paths",
            "pcc.py_frontend.type_infer",
        ),
    )
    assert pipeline_dependencies == (
        "pcc.py_frontend.pipeline_paths",
        "pcc.py_frontend.type_infer",
    )

    method_path = pipeline_path.with_name("codegen") / (
        "method_call_expression_lowering.py"
    )
    method_dependencies = pipeline._python_frontend_action_dependencies(
        str(method_path),
        "pcc.py_frontend.codegen.method_call_expression_lowering",
        (
            "pcc.py_frontend.codegen.method_call_expression_lowering",
            "pcc.py_frontend.codegen.errors",
            "pcc.py_frontend.codegen.freestanding_abi_constants",
            "pcc.py_frontend.codegen.marshal",
            "pcc.py_frontend.py_ast",
            "pcc.llvm_capi.ir",
        ),
    )
    assert method_dependencies == (
        "pcc.llvm_capi.ir",
        "pcc.py_frontend.codegen.errors",
        "pcc.py_frontend.codegen.freestanding_abi_constants",
        "pcc.py_frontend.codegen.marshal",
        "pcc.py_frontend.py_ast",
    )


def test_summary_worker_parallelism_uses_host_width_and_native_memory_guard(
    monkeypatch,
):
    monkeypatch.delenv("PCC_PY_FRONTEND_SUMMARY_JOBS", raising=False)
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    assert parallel._summary_worker_parallelism(10, ["python", "-m", "pcc"]) == 10
    assert parallel._summary_worker_parallelism(10, ["pcc1"]) == 2
    assert parallel._summary_worker_parallelism(1, ["python", "-m", "pcc"]) == 1

    gib = 1024**3
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(8 * gib))
    assert parallel._summary_worker_parallelism(10, ["pcc1"]) == 2
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(16 * gib))
    assert parallel._summary_worker_parallelism(7, ["pcc1"]) == 7

    monkeypatch.setenv("PCC_PY_FRONTEND_SUMMARY_JOBS", "7")
    assert parallel._summary_worker_parallelism(10, ["pcc1"]) == 7
    monkeypatch.setenv("PCC_PY_FRONTEND_SUMMARY_JOBS", "99")
    assert parallel._summary_worker_parallelism(10, ["pcc1"]) == 10
    for invalid in ("0", "-3", "auto", "invalid"):
        monkeypatch.setenv("PCC_PY_FRONTEND_SUMMARY_JOBS", invalid)
        assert parallel._summary_worker_parallelism(10, ["pcc1"]) == 1


def test_summary_worker_owns_exactly_one_ast_and_publishes_one_wire(tmp_path):
    ast_dir = tmp_path / "ast"
    summary_dir = tmp_path / "summaries"
    ast_dir.mkdir()
    summary_dir.mkdir()
    exports_path = tmp_path / "exports.json"
    result_path = tmp_path / "result.tsv"
    exports_path.write_text("exports", encoding="utf-8")
    ast_path = ast_dir / "module_1.json"
    ast_path.write_text("ast", encoding="utf-8")
    calls = []
    manifest = {
        "assigned_indices": [1],
        "module_names": ["unused", "pkg.leaf"],
        "ast_dir": str(ast_dir),
        "exports_path": str(exports_path),
        "ir_dir": str(summary_dir),
        "result_path": str(result_path),
    }

    def read_exports(path):
        assert path == str(exports_path)
        return {"pkg.leaf": {}}, {}

    def read_ast(path):
        assert path == str(ast_path)
        calls.append(path)
        return "parsed-ast"

    def build_summary(ast_module, module_name, exports):
        assert (ast_module, module_name, exports) == (
            "parsed-ast",
            "pkg.leaf",
            {"pkg.leaf": {}},
        )
        return {"module_name": module_name}

    def write_summary(path, summary):
        assert summary == {"module_name": "pkg.leaf"}
        Path(path).write_text("wire", encoding="utf-8")

    assert worker_execution.run_summary_worker(
        manifest,
        read_native_exports_wire=read_exports,
        read_ast_wire=read_ast,
        build_effect_summary=build_summary,
        write_effect_summary=write_summary,
    ) == 0
    assert calls == [str(ast_path)]
    assert result_path.read_text(encoding="utf-8") == (
        "SUMMARY\t1\tpkg.leaf\t"
        + str(summary_dir / "summary_1.wire")
        + "\n"
    )


def test_summary_worker_rejects_multi_module_ownership(tmp_path):
    manifest = {
        "assigned_indices": [0, 1],
        "module_names": ["a", "b"],
    }

    try:
        worker_execution.run_summary_worker(
            manifest,
            read_native_exports_wire=lambda _path: ({}, {}),
            read_ast_wire=lambda _path: None,
            build_effect_summary=lambda *_args: {},
            write_effect_summary=lambda *_args: None,
        )
    except ValueError as exc:
        assert "exactly one module" in str(exc)
    else:
        raise AssertionError("summary worker accepted multiple AST owners")


def test_parallel_cache_hit_skips_frontend_worker_execution():
    expected = ([("entry", "ir")], False, False, 2, [])
    called = []

    result = parallel.compile_parallel(
        ["entry.py"],
        ["entry"],
        jobs=1,
        entry_module="entry",
        sibling_inits=(),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: ["pcc1"],
        compile_uncached=lambda *_args, **_kwargs: called.append("uncached"),
        plan_cache=lambda *_args, **_kwargs: "plan",
        load_cache=lambda plan, names: expected,
        acquire_cache=lambda _plan: False,
        wait_cache=lambda *_args: None,
        publish_cache=lambda *_args: False,
        release_cache=lambda _plan: called.append("release"),
        host_python_command=lambda: "python3",
        source_root=lambda: "/source",
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda *_args: None,
    )

    assert result == expected
    assert called == []


def test_parallel_worker_failure_is_fail_closed_before_result_parse(tmp_path):
    class ExpectedError(RuntimeError):
        pass

    try:
        parallel.compile_parallel_uncached(
            [str(tmp_path / "entry.py")],
            ["entry"],
            jobs=1,
            entry_module="entry",
            sibling_inits=(),
            libpython_mode="off",
            ir_scaffold_mode="on",
            verbose=False,
            can_spawn_worker=lambda: True,
            worker_command_prefix=lambda: ["pcc1"],
            chunk_count_for_workers=lambda *_args: 1,
            codegen_chunks=lambda *_args: [[0]],
            ast_wire_enabled=lambda: False,
            build_shared_exports_callback=lambda *_args, **_kwargs: "exports.json",
            write_manifest=lambda *_args, **_kwargs: None,
            shell_quote_arg=str,
            worker_arg="--worker",
            worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
            join_strings=lambda values, sep: sep.join(values),
            run_worker_commands=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                subprocess.CalledProcessError(1, "worker")
            ),
            profiled_gc_collect=lambda *_args, **_kwargs: None,
            read_worker_ir=lambda *_args: "",
            profile_begin=lambda _profile: 0,
            profile_end=lambda *_args: None,
            profile_counter=lambda *_args: None,
            pipeline_error=ExpectedError,
        )
    except ExpectedError as exc:
        assert "parallel frontend codegen worker failed" in str(exc)
    else:
        raise AssertionError("worker failure must fail closed")


def test_source_workers_separate_export_and_short_codegen_shards(tmp_path):
    src_paths = []
    module_names = []
    for index in range(4):
        source = tmp_path / ("m" + str(index) + ".py")
        body = "x = " + str(index) + "\n"
        if index == 0:
            body += "#" * 220_000
        source.write_text(body, encoding="utf-8")
        src_paths.append(str(source))
        module_names.append("m" + str(index))

    chunk_counts = []
    export_chunks = []
    manifests = []
    command_batches = []

    def chunks(_paths, count):
        chunk_counts.append(count)
        if count == 2:
            return [[0, 1], [2, 3]]
        assert count == 4
        return [[0], [1], [2], [3]]

    def build_exports(_tmp, _srcs, _names, chunks_arg, *_args, **_kwargs):
        export_chunks.extend(chunks_arg)
        return str(tmp_path / "exports.json")

    def write_manifest(
        manifest_path,
        result_path,
        ir_dir,
        _exports_path,
        _ast_dir,
        _srcs,
        names,
        assigned_indices,
        **_kwargs,
    ):
        manifests.append(
            (
                manifest_path,
                result_path,
                ir_dir,
                names,
                list(assigned_indices),
            )
        )

    def run_commands(commands, *, max_parallel):
        command_batches.append((len(commands), max_parallel))
        for manifest_path, result_path, ir_dir, names, indices in manifests:
            if not any(manifest_path in command for command in commands):
                continue
            assert len(indices) == 1
            index = indices[0]
            ir_path = tmp_path / ("module_" + str(index) + ".ll")
            ir_text = "; " + names[index] + "\n"
            ir_path.write_text(ir_text, encoding="utf-8")
            with open(result_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "OK\t"
                    + str(index)
                    + "\t"
                    + names[index]
                    + "\t0\t0\t"
                    + str(len(ir_text))
                    + "\t"
                    + str(ir_path)
                    + "\n"
                )

    result = parallel.compile_parallel_uncached(
        src_paths,
        module_names,
        jobs=2,
        entry_module="m0",
        sibling_inits=(),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: ["python3"],
        chunk_count_for_workers=lambda *_args: 4,
        codegen_chunks=chunks,
        ast_wire_enabled=lambda: False,
        build_shared_exports_callback=build_exports,
        write_manifest=write_manifest,
        shell_quote_arg=str,
        worker_arg="--worker",
        worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
        join_strings=lambda values, sep: sep.join(values),
        run_worker_commands=run_commands,
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        read_worker_ir=lambda path, _name: open(path, encoding="utf-8").read(),
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda *_args: None,
        pipeline_error=RuntimeError,
    )

    assert chunk_counts == [4, 2]
    assert export_chunks == [[0, 1], [2, 3]]
    assert [entry[4] for entry in manifests] == [[0], [1], [2], [3]]
    # Host CPython workers are memory-cheap: auto mode keeps the full chunked
    # width with no oversized split.  The split + safe-lane clamp is the
    # compiled pcc1 contract, covered by the native lane test below.
    assert command_batches == [(4, 2)]
    assert [name for name, _ir in result[0]] == module_names


def test_native_auto_frontend_bounds_export_and_codegen_lanes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_NATIVE_OBJECT", "1")
    src_paths = []
    module_names = []
    for index, size in enumerate((220_000, 20, 30, 40)):
        source = tmp_path / ("native" + str(index) + ".py")
        source.write_text("x" * size, encoding="utf-8")
        src_paths.append(str(source))
        module_names.append("native" + str(index))

    export_plan = {}
    command_batches = []
    worker_commands = []
    manifests = []
    counters = {}

    def build_exports(_tmp, _srcs, _names, chunks_arg, *_args, **kwargs):
        export_plan["chunks"] = [list(chunk) for chunk in chunks_arg]
        export_plan["oversized"] = kwargs["oversized_chunk_count"]
        export_plan["safe_jobs"] = kwargs["safe_parallel"]
        return str(tmp_path / "exports.json")

    def write_manifest(
        manifest_path,
        result_path,
        ir_dir,
        _exports_path,
        _ast_dir,
        _srcs,
        names,
        assigned_indices,
        **_kwargs,
    ):
        manifests.append(
            (manifest_path, result_path, ir_dir, names, list(assigned_indices))
        )

    def run_commands(commands, *, max_parallel):
        command_batches.append((len(commands), max_parallel))
        worker_commands.extend(commands)
        for manifest_path, result_path, ir_dir, names, indices in manifests:
            if not any(manifest_path in command for command in commands):
                continue
            index = indices[0]
            ir_path = tmp_path / ("native_module_" + str(index) + ".ll")
            ir_text = "; " + names[index] + "\n"
            ir_path.write_text(ir_text, encoding="utf-8")
            with open(result_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "OK\t"
                    + str(index)
                    + "\t"
                    + names[index]
                    + "\t0\t0\t"
                    + str(len(ir_text))
                    + "\t"
                    + str(ir_path)
                    + "\n"
                )

    result = parallel.compile_parallel_uncached(
        src_paths,
        module_names,
        jobs=10,
        entry_module="native0",
        sibling_inits=(),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: ["pcc1"],
        chunk_count_for_workers=lambda *_args: 4,
        codegen_chunks=lambda _paths, _count: [[0], [1], [2], [3]],
        ast_wire_enabled=lambda: False,
        build_shared_exports_callback=build_exports,
        write_manifest=write_manifest,
        shell_quote_arg=str,
        worker_arg="--worker",
        worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
        join_strings=lambda values, sep: sep.join(values),
        run_worker_commands=run_commands,
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        read_worker_ir=lambda path, _name: open(path, encoding="utf-8").read(),
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda _profile, name, value: counters.__setitem__(
            name, value
        ),
        pipeline_error=RuntimeError,
    )

    assert export_plan == {
        "chunks": [[0], [1], [2], [3]],
        "oversized": 1,
        "safe_jobs": 2,
    }
    assert command_batches == [(1, 1), (3, 2)]
    assert "PCC_DIRECT_INDEXED_NATIVE_OBJECT=0" in worker_commands[0]
    assert all(
        "PCC_DIRECT_INDEXED_NATIVE_OBJECT=0" not in command
        for command in worker_commands[1:]
    )
    assert counters["multi_frontend_worker_requested_concurrency"] == 10
    assert counters["multi_frontend_worker_concurrency"] == 2
    assert [name for name, _ir in result[0]] == module_names


def test_native_in_process_codegen_reuses_coordinator_and_forces_asm(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PCC_PY_FRONTEND_IN_PROCESS_CODEGEN", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_NATIVE_OBJECT", "1")
    src_paths = []
    module_names = ["large", "small"]
    for index, size in enumerate((220_000, 20)):
        source = tmp_path / ("inproc" + str(index) + ".py")
        source.write_text("x" * size, encoding="utf-8")
        src_paths.append(str(source))
    manifests = {}
    in_process_calls = []

    def write_manifest(
        manifest_path,
        result_path,
        ir_dir,
        _exports_path,
        _ast_dir,
        _srcs,
        names,
        assigned_indices,
        **_kwargs,
    ):
        manifests[manifest_path] = (
            result_path,
            ir_dir,
            names,
            list(assigned_indices),
        )

    def run_in_process(manifest_path):
        assert os.environ["PCC_DIRECT_INDEXED_NATIVE_OBJECT"] == "0"
        in_process_calls.append(manifest_path)
        result_path, ir_dir, names, indices = manifests[manifest_path]
        index = indices[0]
        ir_path = Path(ir_dir) / ("module_" + str(index) + ".ll")
        ir_path.write_text("", encoding="utf-8")
        assembly_path = Path(ir_dir) / ("module_" + str(index) + ".direct.s")
        assembly_path.write_text(".text\n", encoding="utf-8")
        Path(result_path).write_text(
            "OK\t"
            + str(index)
            + "\t"
            + names[index]
            + "\t0\t0\t0\t"
            + str(ir_path)
            + "\tASM\t"
            + str(assembly_path)
            + "\n",
            encoding="utf-8",
        )
        return 0

    result = parallel.compile_parallel_uncached(
        src_paths,
        module_names,
        jobs=10,
        entry_module="large",
        sibling_inits=("small",),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: ["pcc1"],
        chunk_count_for_workers=lambda *_args: 2,
        codegen_chunks=lambda *_args: [[0], [1]],
        ast_wire_enabled=lambda: False,
        build_shared_exports_callback=lambda *_args, **_kwargs: "exports.json",
        write_manifest=write_manifest,
        shell_quote_arg=str,
        worker_arg="--worker",
        worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
        join_strings=lambda values, sep: sep.join(values),
        run_worker_commands=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("in-process codegen must not launch worker commands")
        ),
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        read_worker_ir=lambda *_args: (_ for _ in ()).throw(
            AssertionError("direct artifact must not read worker IR")
        ),
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda *_args: None,
        pipeline_error=RuntimeError,
        run_worker_manifest_in_process=run_in_process,
    )

    assert len(in_process_calls) == 2
    assert os.environ["PCC_DIRECT_INDEXED_NATIVE_OBJECT"] == "1"
    assert [kind for _name, kind, _path in result[7]] == ["ASM", "ASM"]


def test_native_codegen_checkpoint_persists_sidecars_and_singleton_manifests(
    tmp_path,
    monkeypatch,
):
    plan = tmp_path / "codegen.plan"
    output = tmp_path / "pcc2"
    runtime = tmp_path / "runtime.a"
    artifacts = tmp_path / "artifacts"
    runtime.write_bytes(b"runtime")
    artifacts.mkdir()
    monkeypatch.setenv("PCC_DEFER_FRONTEND_CODEGEN_PLAN", str(plan))
    monkeypatch.setenv("PCC_DEFER_FRONTEND_OUTPUT", str(output))
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(runtime))
    sources = []
    for index, size in enumerate((220_000, 20)):
        source = tmp_path / ("checkpoint" + str(index) + ".py")
        source.write_text("x" * size, encoding="utf-8")
        sources.append(str(source))

    def build_exports(tmp, _srcs, _names, _chunks, *_args, **kwargs):
        ast_dir = Path(kwargs["ast_dir"])
        ast_dir.mkdir(parents=True, exist_ok=True)
        for index in range(2):
            (ast_dir / ("module_" + str(index) + ".json")).write_text(
                "{}", encoding="utf-8"
            )
        exports = Path(tmp) / "native_exports.json"
        exports.write_text("{}", encoding="utf-8")
        return str(exports)

    result = parallel.compile_parallel_uncached(
        sources,
        ["large", "small"],
        jobs=10,
        entry_module="large",
        sibling_inits=("small",),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        artifact_dir=str(artifacts),
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: [str(tmp_path / "pcc1")],
        chunk_count_for_workers=lambda *_args: 2,
        codegen_chunks=lambda *_args: [[0], [1]],
        ast_wire_enabled=lambda: False,
        build_shared_exports_callback=build_exports,
        write_manifest=worker_policy.write_worker_manifest,
        shell_quote_arg=str,
        worker_arg="--worker",
        worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
        join_strings=lambda values, sep: sep.join(values),
        run_worker_commands=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("deferred codegen must stop before worker launch")
        ),
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        read_worker_ir=lambda *_args: "",
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda *_args: None,
        pipeline_error=RuntimeError,
    )

    assert result == ("PCC_DEFERRED_FRONTEND_CODEGEN",)
    lines = plan.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "pcc.frontend-codegen-plan.v2"
    assert lines[7:11] == ["2", "1", "2", "2"]
    assert lines[11] == "pidx-pco-v1"
    persisted = [Path(path) for path in lines[12:]]
    assert all(path.is_file() for path in persisted)
    manifest = worker_policy.read_worker_manifest(str(persisted[0]))
    assert Path(manifest["ast_dir"]).is_dir()
    assert Path(manifest["exports_path"]).is_file()


def test_parallel_frontend_reads_worker_ir_into_ordered_text_results(tmp_path):
    manifests = []
    reads = []

    def write_manifest(
        _manifest_path,
        result_path,
        ir_dir,
        _exports_path,
        _ast_dir,
        _src_paths,
        names,
        assigned_indices,
        **_kwargs,
    ):
        manifests.append((result_path, ir_dir, names, list(assigned_indices)))

    def run_commands(_commands, **_kwargs):
        for result_path, ir_dir, names, indices in manifests:
            with open(result_path, "w", encoding="utf-8") as result_stream:
                for index in indices:
                    ir_path = Path(ir_dir) / ("module_" + str(index) + ".ll")
                    ir_text = "; " + names[index] + "\n"
                    ir_path.write_text(ir_text, encoding="utf-8")
                    result_stream.write(
                        "OK\t"
                        + str(index)
                        + "\t"
                        + names[index]
                        + ("\t1\t0\t4\t" if index == 0 else "\t0\t1\t4\t")
                        + str(ir_path)
                        + "\n"
                    )

    def read_worker_ir(path, module_name):
        reads.append((module_name, path))
        return Path(path).read_text(encoding="utf-8")

    result = parallel.compile_parallel_uncached(
        [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
        ["a", "b"],
        jobs=2,
        entry_module="a",
        sibling_inits=("b",),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: ["pcc1"],
        chunk_count_for_workers=lambda *_args: 2,
        codegen_chunks=lambda *_args: [[1], [0]],
        ast_wire_enabled=lambda: False,
        build_shared_exports_callback=lambda *_args, **_kwargs: "exports.json",
        write_manifest=write_manifest,
        shell_quote_arg=str,
        worker_arg="--worker",
        worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
        join_strings=lambda values, sep: sep.join(values),
        run_worker_commands=run_commands,
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        read_worker_ir=read_worker_ir,
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda *_args: None,
        pipeline_error=RuntimeError,
        auto_source_lanes=False,
    )

    assert result == (
        [("a", "; a\n"), ("b", "; b\n")],
        True,
        True,
        8,
        ["a"],
    )
    assert [name for name, _path in reads] == ["b", "a"]
    assert all(not Path(path).exists() for _name, path in reads)


def test_parallel_frontend_returns_ordered_native_object_paths_without_reading(
    tmp_path,
):
    manifests = []

    def write_manifest(
        _manifest_path,
        result_path,
        ir_dir,
        _exports_path,
        _ast_dir,
        _src_paths,
        names,
        assigned_indices,
        **_kwargs,
    ):
        manifests.append((result_path, ir_dir, names, list(assigned_indices)))

    def run_commands(_commands, **_kwargs):
        for result_path, ir_dir, names, indices in manifests:
            with open(result_path, "w", encoding="utf-8") as result_stream:
                for index in indices:
                    ir_path = Path(ir_dir) / ("module_" + str(index) + ".ll")
                    ir_path.write_text("", encoding="utf-8")
                    native_path = Path(ir_dir) / (
                        "module_" + str(index) + ".direct.pco"
                    )
                    native_path.write_bytes(("pco-" + names[index]).encode())
                    result_stream.write(
                        "OK\t"
                        + str(index)
                        + "\t"
                        + names[index]
                        + "\t0\t0\t0\t"
                        + str(ir_path)
                        + "\tPCO\t"
                        + str(native_path)
                        + "\n"
                    )

    result = parallel.compile_parallel_uncached(
        [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
        ["a", "b"],
        jobs=2,
        entry_module="a",
        sibling_inits=("b",),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: ["pcc1"],
        chunk_count_for_workers=lambda *_args: 2,
        codegen_chunks=lambda *_args: [[1], [0]],
        ast_wire_enabled=lambda: False,
        build_shared_exports_callback=lambda *_args, **_kwargs: "exports.json",
        write_manifest=write_manifest,
        shell_quote_arg=str,
        worker_arg="--worker",
        worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
        join_strings=lambda values, sep: sep.join(values),
        run_worker_commands=run_commands,
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        read_worker_ir=lambda *_args: (_ for _ in ()).throw(
            AssertionError("native-object result must not read worker IR")
        ),
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda *_args: None,
        pipeline_error=RuntimeError,
        auto_source_lanes=False,
    )

    assert result[:5] == ([('a', ''), ('b', '')], False, False, 0, [])
    assert result[5] is None
    assert [name for name, _path in result[6]] == ["a", "b"]
    assert all(Path(path).suffix == ".pco" for _name, path in result[6])


def test_parallel_frontend_preserves_mixed_artifact_module_order(tmp_path):
    manifests = []

    def write_manifest(
        _manifest_path,
        result_path,
        ir_dir,
        _exports_path,
        _ast_dir,
        _src_paths,
        names,
        assigned_indices,
        **_kwargs,
    ):
        manifests.append((result_path, ir_dir, names, list(assigned_indices)))

    def run_commands(_commands, **_kwargs):
        for result_path, ir_dir, names, indices in manifests:
            index = indices[0]
            ir_path = Path(ir_dir) / ("module_" + str(index) + ".ll")
            ir_path.write_text("", encoding="utf-8")
            if index == 0:
                artifact = Path(ir_dir) / "module_0.direct.s"
                artifact.write_text(".text\n", encoding="utf-8")
                marker = "ASM"
            else:
                artifact = Path(ir_dir) / "module_1.direct.pco"
                artifact.write_bytes(b"pco")
                marker = "PCO"
            with open(result_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "OK\t"
                    + str(index)
                    + "\t"
                    + names[index]
                    + "\t0\t0\t0\t"
                    + str(ir_path)
                    + "\t"
                    + marker
                    + "\t"
                    + str(artifact)
                    + "\n"
                )

    result = parallel.compile_parallel_uncached(
        [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
        ["a", "b"],
        jobs=2,
        entry_module="a",
        sibling_inits=("b",),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: ["pcc1"],
        chunk_count_for_workers=lambda *_args: 2,
        codegen_chunks=lambda *_args: [[1], [0]],
        ast_wire_enabled=lambda: False,
        build_shared_exports_callback=lambda *_args, **_kwargs: "exports.json",
        write_manifest=write_manifest,
        shell_quote_arg=str,
        worker_arg="--worker",
        worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
        join_strings=lambda values, sep: sep.join(values),
        run_worker_commands=run_commands,
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        read_worker_ir=lambda *_args: (_ for _ in ()).throw(
            AssertionError("mixed direct artifacts must not read worker IR")
        ),
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda *_args: None,
        pipeline_error=RuntimeError,
        auto_source_lanes=False,
    )

    assert [(name, kind) for name, kind, _path in result[7]] == [
        ("a", "ASM"),
        ("b", "PCO"),
    ]


def test_direct_pco_worker_releases_frontend_and_authoring_graphs_in_order():
    source = Path(worker_execution.__file__).read_text(encoding="utf-8")

    generate = source.index("generated_module = codegen.generate(typed_module)")
    conditional_render = source.index(
        'ir_text = str(generated_module) if render_ir_text else ""',
        generate,
    )
    structured_emit = source.index(
        "emit_aarch64_darwin_indexed_transport(",
        conditional_render,
    )
    structured_mode = source.index(
        "structured_instructions=(",
        structured_emit,
    )
    release_frontend = source.index("parsed_modules[index] = None")
    assemble_lines = source.index(
        "sections, undefined = assemble_lines(",
        release_frontend,
    )
    release_transport = source.index("del direct_transport", assemble_lines)
    assemble = source.index("sections, undefined = assemble_file(direct_asm)")
    release_assembly = source.index('direct_asm = ""', assemble)
    encode_sections = source.index(
        "encoded = encode_native_object_from_sections(",
        assemble,
    )
    release_sections = source.index("del sections", encode_sections)
    write_encoded = source.index("stream.write(encoded)", release_sections)

    assert (
        generate
        < conditional_render
        < structured_emit
        < structured_mode
        < release_frontend
    )
    assert release_frontend < assemble_lines < release_transport < assemble
    assert assemble < release_assembly < encode_sections
    assert encode_sections < release_sections < write_encoded


def test_source_worker_lane_failure_stops_safe_work_and_override_is_authoritative(
    tmp_path,
):
    large = tmp_path / "large.py"
    small = tmp_path / "small.py"
    large.write_text("#" * 220_000, encoding="utf-8")
    small.write_text("x = 1\n", encoding="utf-8")

    def run_case(auto_source_lanes, worker_prefix=("python3",)):
        calls = []

        def fail_first(commands, *, max_parallel):
            calls.append((len(commands), max_parallel))
            raise subprocess.CalledProcessError(1, commands)

        try:
            parallel.compile_parallel_uncached(
                [str(large), str(small)],
                ["large", "small"],
                jobs=2,
                entry_module="large",
                sibling_inits=(),
                libpython_mode="off",
                ir_scaffold_mode="on",
                verbose=False,
                can_spawn_worker=lambda: True,
                worker_command_prefix=lambda: list(worker_prefix),
                chunk_count_for_workers=lambda *_args: 2,
                codegen_chunks=lambda *_args: [[0], [1]],
                ast_wire_enabled=lambda: False,
                build_shared_exports_callback=(
                    lambda *_args, **_kwargs: str(tmp_path / "exports.json")
                ),
                write_manifest=lambda *_args, **_kwargs: None,
                shell_quote_arg=str,
                worker_arg="--worker",
                worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
                join_strings=lambda values, sep: sep.join(values),
                run_worker_commands=fail_first,
                profiled_gc_collect=lambda *_args, **_kwargs: None,
                read_worker_ir=lambda *_args: "",
                profile_begin=lambda _profile: 0,
                profile_end=lambda *_args: None,
                profile_counter=lambda *_args: None,
                pipeline_error=RuntimeError,
                auto_source_lanes=auto_source_lanes,
            )
        except RuntimeError as exc:
            assert "parallel frontend codegen worker failed" in str(exc)
        else:
            raise AssertionError("worker failure must fail closed")
        return calls

    # Compiled native workers keep the oversized/safe split: the failing
    # oversized lane stops the run before any safe worker launches.
    assert run_case(True, worker_prefix=("pcc1",)) == [(1, 1)]
    # Host CPython workers are not split in auto mode, and a numeric override
    # stays authoritative for every executor.
    assert run_case(True) == [(2, 2)]
    assert run_case(False) == [(2, 2)]


def test_parallel_action_cache_noop_emits_zero_frontend_workers(tmp_path):
    state = _action_state()
    action_root = tmp_path / "actions"
    (tmp_path / "a.py").write_text("a-v1", encoding="utf-8")
    (tmp_path / "b.py").write_text("b-v1", encoding="utf-8")
    expected = []
    for name, ir_text in (("a", "; a\n"), ("b", "; b\n")):
        action = _module_action(state, name)
        artifact = parallel._encode_module_ir_artifact(
            name,
            ir_text,
            False,
            False,
            len(ir_text),
        )
        assert module_action_dag.publish_action_artifact(
            str(action_root), action, artifact
        )
        expected.append((name, ir_text))
    assert module_action_dag.publish_graph_state_file(str(action_root), state)
    command_batches = []

    result = parallel.compile_parallel_uncached(
        [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
        ["a", "b"],
        jobs=2,
        entry_module="a",
        sibling_inits=("b",),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        action_cache_plan=_action_cache_plan(action_root, state),
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: ["pcc1"],
        chunk_count_for_workers=lambda *_args: 2,
        codegen_chunks=lambda *_args: [[0], [1]],
        ast_wire_enabled=lambda: False,
        build_shared_exports_callback=lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(AssertionError("no-op action graph must not run export workers")),
        write_manifest=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit must not emit a codegen manifest")
        ),
        shell_quote_arg=str,
        worker_arg="--worker",
        worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
        join_strings=lambda values, sep: sep.join(values),
        run_worker_commands=lambda commands, **_kwargs: command_batches.append(commands),
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        read_worker_ir=lambda *_args: (_ for _ in ()).throw(
            AssertionError("cache hit must not read worker IR")
        ),
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda *_args: None,
        pipeline_error=RuntimeError,
        build_action_state=lambda *_args: state,
    )

    assert result == (expected, False, False, len("; a\n") + len("; b\n"), [])
    assert command_batches == []


def test_parallel_action_cache_private_edit_compiles_only_changed_module(tmp_path):
    previous = _action_state()
    current = _action_state("a-private-edit")
    action_root = tmp_path / "actions"
    (tmp_path / "a.py").write_text("a-private-edit", encoding="utf-8")
    (tmp_path / "b.py").write_text("b-v1", encoding="utf-8")
    cached_b = "; cached b\n"
    b_action = _module_action(current, "b")
    assert module_action_dag.publish_action_artifact(
        str(action_root),
        b_action,
        parallel._encode_module_ir_artifact(
            "b", cached_b, False, False, len(cached_b)
        ),
    )
    assert module_action_dag.publish_graph_state_file(str(action_root), previous)
    manifests = []

    def write_manifest(
        _path,
        result_path,
        ir_dir,
        _exports_path,
        _ast_dir,
        _src_paths,
        module_names,
        assigned_indices,
        **_kwargs,
    ):
        manifests.append((result_path, ir_dir, module_names, list(assigned_indices)))

    def run_commands(commands, **_kwargs):
        assert len(commands) == 1
        result_path, ir_dir, names, indices = manifests[0]
        assert indices == [0]
        ir_path = str(tmp_path / "compiled-a.ll")
        with open(ir_path, "w", encoding="utf-8") as stream:
            stream.write("; compiled a\n")
        with open(result_path, "w", encoding="utf-8") as stream:
            stream.write(
                "OK\t0\t"
                + names[0]
                + "\t0\t0\t13\t"
                + ir_path
                + "\n"
            )

    result = parallel.compile_parallel_uncached(
        [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
        ["a", "b"],
        jobs=2,
        entry_module="a",
        sibling_inits=("b",),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=False,
        action_cache_plan=_action_cache_plan(action_root, current),
        can_spawn_worker=lambda: True,
        worker_command_prefix=lambda: ["pcc1"],
        chunk_count_for_workers=lambda *_args: 2,
        codegen_chunks=lambda *_args: [[0], [1]],
        ast_wire_enabled=lambda: False,
        build_shared_exports_callback=lambda *_args, **_kwargs: "exports.json",
        write_manifest=write_manifest,
        shell_quote_arg=str,
        worker_arg="--worker",
        worker_env_prefix=lambda: "PCC_PY_FRONTEND_JOBS=1",
        join_strings=lambda values, sep: sep.join(values),
        run_worker_commands=run_commands,
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        read_worker_ir=lambda path, _name: open(path, encoding="utf-8").read(),
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        profile_counter=lambda *_args: None,
        pipeline_error=RuntimeError,
        build_action_state=lambda *_args: current,
    )

    assert manifests[0][3] == [0]
    assert result[0] == [("a", "; compiled a\n"), ("b", cached_b)]
    assert result[3] == 13 + len(cached_b)
    assert module_action_dag.load_graph_state_file(str(action_root)) == current
