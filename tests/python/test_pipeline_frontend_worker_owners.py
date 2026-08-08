"""Focused ownership contracts for frontend worker execution and scheduling."""

from __future__ import annotations

import hashlib
import subprocess

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_frontend_parallel as parallel
from pcc.py_frontend import pipeline_frontend_workers as worker_policy
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
            body += "#" * 80_000
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
    assert command_batches == [(1, 1), (3, 2)]
    assert [name for name, _ir in result[0]] == module_names


def test_source_worker_lane_failure_stops_safe_work_and_override_is_authoritative(
    tmp_path,
):
    large = tmp_path / "large.py"
    small = tmp_path / "small.py"
    large.write_text("#" * 80_000, encoding="utf-8")
    small.write_text("x = 1\n", encoding="utf-8")

    def run_case(auto_source_lanes):
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
                worker_command_prefix=lambda: ["python3"],
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

    assert run_case(True) == [(1, 1)]
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
