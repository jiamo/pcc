"""Parallel multi-module frontend scheduling behind the pipeline facade."""

from __future__ import annotations

import os
import json
import subprocess
import tempfile
from typing import Optional

from . import module_action_dag as _module_action_dag
from .pipeline_frontend_workers import (
    SOURCE_WORKER_AUTO_SAFE_JOBS as _SOURCE_WORKER_AUTO_SAFE_JOBS,
    split_codegen_chunks_by_source_size as _split_codegen_chunks_by_source_size,
)


_MODULE_IR_ARTIFACT_SCHEMA = "pcc.python-module-ir-action.v1"


def _module_ir_action(state, module_name: str):
    return _module_action_dag.Action(
        module=str(module_name),
        stage="module-ir",
        key=_module_action_dag.action_key(state, str(module_name), "module-ir"),
        reason="content-addressed-reuse",
    )


def _encode_module_ir_artifact(
    module_name: str,
    ir_text: str,
    needs_libpython: bool,
    needs_native_exports: bool,
    ir_bytes_before_passes: int,
) -> bytes:
    body = str(ir_text).encode("utf-8")
    header = {
        "body_bytes": len(body),
        "ir_bytes_before_passes": int(ir_bytes_before_passes),
        "module": str(module_name),
        "needs_libpython": bool(needs_libpython),
        "needs_native_extension_exports": bool(needs_native_exports),
        "schema": _MODULE_IR_ARTIFACT_SCHEMA,
    }
    return (
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
        + body
    )


def _decode_module_ir_artifact(payload: bytes, module_name: str):
    try:
        header_raw, body = payload.split(b"\n", 1)
        header = json.loads(header_raw.decode("utf-8"))
        if not isinstance(header, dict) or set(header) != {
            "body_bytes",
            "ir_bytes_before_passes",
            "module",
            "needs_libpython",
            "needs_native_extension_exports",
            "schema",
        }:
            return None
        if (
            header.get("schema") != _MODULE_IR_ARTIFACT_SCHEMA
            or header.get("module") != str(module_name)
            or type(header.get("needs_libpython")) is not bool
            or type(header.get("needs_native_extension_exports")) is not bool
            or int(header.get("body_bytes", -1)) != len(body)
        ):
            return None
        ir_size = int(header.get("ir_bytes_before_passes", -1))
        if ir_size < 1 or not body:
            return None
        ir_text = body.decode("utf-8")
        if not ir_text:
            return None
        return (
            ir_text,
            bool(header["needs_libpython"]),
            bool(header["needs_native_extension_exports"]),
            ir_size,
        )
    except (UnicodeDecodeError, ValueError, TypeError):
        return None


def _load_noop_action_result(action_cache_plan, src_paths, module_names):
    """Load a graph whose source/global identities are exactly unchanged."""

    if action_cache_plan is None:
        return None
    action_root = str(action_cache_plan.get("action_root", ""))
    if not action_root:
        return None
    try:
        state = _module_action_dag.load_graph_state_file(action_root)
        if state is None:
            return None
        if (
            state.compiler_digest
            != str(action_cache_plan.get("compiler_digest", ""))
            or state.runtime_abi_digest
            != str(action_cache_plan.get("runtime_abi_digest", ""))
            or state.target != str(action_cache_plan.get("target", ""))
            or state.options_digest
            != str(action_cache_plan.get("options_digest", ""))
        ):
            return None
        modules = state.module_map()
        if set(modules) != set(str(name) for name in module_names):
            return None
        for src_path, module_name in zip(src_paths, module_names):
            module = modules.get(str(module_name))
            if (
                module is None
                or module.source_digest
                != _module_action_dag.source_digest(str(src_path))
            ):
                return None

        module_ir_texts = []
        any_needs_libpython = False
        any_needs_native = False
        total_ir_bytes = 0
        libpython_modules: list[str] = []
        for module_name in module_names:
            action = _module_ir_action(state, str(module_name))
            payload = _module_action_dag.load_action_artifact(action_root, action)
            if payload is None:
                return None
            decoded = _decode_module_ir_artifact(payload, str(module_name))
            if decoded is None:
                return None
            ir_text, needs_libpython, needs_native, ir_size = decoded
            module_ir_texts.append((str(module_name), ir_text))
            total_ir_bytes += int(ir_size)
            if needs_libpython:
                any_needs_libpython = True
                libpython_modules.append(str(module_name))
            if needs_native:
                any_needs_native = True
        return (
            module_ir_texts,
            any_needs_libpython,
            any_needs_native,
            total_ir_bytes,
            libpython_modules,
        )
    except Exception:
        return None


def build_shared_exports(
    tmp: str,
    src_paths,
    module_names,
    chunks,
    worker_prefix,
    *,
    entry_module: str,
    sibling_inits,
    libpython_mode: str,
    ir_scaffold_mode: str,
    verbose: bool,
    max_parallel: int,
    ast_dir: str = "",
    profile: Optional[dict] = None,
    write_manifest,
    shell_quote_arg,
    worker_arg: str,
    worker_env_prefix,
    join_strings,
    run_worker_commands,
    profiled_gc_collect,
    read_native_exports_wire,
    read_reexport_edges_wire,
    merge_reexport_edges,
    repair_default_global_owners,
    merge_mixin_stack_methods,
    merge_codegen_methods,
    apply_function_object_uses,
    read_ast_wire,
    annotate_vthread_effects,
    derived_class_map,
    write_native_exports_wire,
    profile_counter,
    pipeline_error,
) -> str:
    export_dir = os.path.join(tmp, "exports")
    subprocess.run(["mkdir", "-p", export_dir], check=True)
    result_paths: list[str] = []
    commands: list[str] = []
    for worker_index, chunk in enumerate(chunks):
        manifest_path = os.path.join(
            tmp,
            "export_" + str(worker_index) + ".manifest",
        )
        result_path = os.path.join(
            tmp,
            "export_" + str(worker_index) + ".tsv",
        )
        write_manifest(
            manifest_path,
            result_path,
            export_dir,
            "",
            ast_dir,
            src_paths,
            module_names,
            chunk,
            entry_module=entry_module,
            sibling_inits=sibling_inits,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            verbose=verbose,
            job_kind="export",
        )
        result_paths.append(result_path)
        command_parts = [shell_quote_arg(part) for part in worker_prefix]
        command_parts.append(shell_quote_arg(worker_arg))
        command_parts.append(shell_quote_arg(manifest_path))
        commands.append(
            worker_env_prefix() + " " + join_strings(command_parts, " ")
        )

    run_worker_commands(commands, max_parallel=max_parallel)
    # An export worker that produced no result file is a hard failure, not a
    # cache miss.  The caller wraps this whole region in `except Exception` to
    # fall back when the ACTION CACHE is unusable, so a bare FileNotFoundError
    # from the loop below was swallowed there: stage2 reported zero IR modules
    # and the first visible symptom was the linker saying it had no inputs.
    missing = []
    for result_path in result_paths:
        if not os.path.exists(result_path):
            missing.append(os.path.basename(result_path))
    if missing:
        raise pipeline_error(
            "frontend export worker produced no result for "
            + join_strings(missing, ", ")
        )
    profiled_gc_collect(
        profile,
        "multi_frontend_export_worker_collect",
        allocations_owned_by_current_process=False,
    )

    native_exports = {}
    reexport_edges = []
    function_object_uses = []
    export_worker_sum_ms = 0
    export_worker_max_ms = 0
    for result_path in result_paths:
        with open(result_path, "r", encoding="utf-8") as stream:
            result_text = stream.read()
        for raw_line in result_text.splitlines():
            parts = raw_line.split("\t")
            if not parts:
                continue
            if parts[0] == "ERR":
                message = parts[1] if len(parts) > 1 else "worker error"
                raise pipeline_error(message)
            if parts[0] != "EXPORT" or len(parts) < 3:
                raise pipeline_error("invalid frontend export worker result")
            shard_exports, _derived, shard_object_uses = read_native_exports_wire(
                parts[1],
                include_function_object_uses=True,
            )
            for module_name, exports in shard_exports.items():
                native_exports[module_name] = exports
            function_object_uses.extend(shard_object_uses)
            reexport_edges.extend(read_reexport_edges_wire(parts[2]))
            if len(parts) >= 4:
                try:
                    worker_ms = int(parts[3])
                except ValueError:
                    worker_ms = 0
                export_worker_sum_ms += worker_ms
                if worker_ms > export_worker_max_ms:
                    export_worker_max_ms = worker_ms

    merge_reexport_edges(module_names, native_exports, reexport_edges)
    repair_default_global_owners(native_exports)
    merge_mixin_stack_methods(native_exports)
    merge_codegen_methods(native_exports)
    apply_function_object_uses(native_exports, function_object_uses)

    # Export workers initially see only their own shard.  Recompute the
    # virtual-thread effect closure after the public re-export graph has
    # converged, otherwise a caller in another shard can retain the ordinary
    # function ABI while its leaf already exposes the resumable generator ABI.
    # Consume the AST sidecars produced by those same native workers: reparsing
    # here would introduce a second frontend owner and, for pcc1, risk crossing
    # back into a host/CPython-only path.
    if not ast_dir:
        raise pipeline_error(
            "parallel frontend may_park closure requires worker AST wire"
        )
    parsed_modules = []
    for index in range(len(module_names)):
        ast_path = os.path.join(ast_dir, "module_" + str(index) + ".json")
        parsed_modules.append(read_ast_wire(ast_path))
    annotate_vthread_effects(parsed_modules, module_names, native_exports)

    class_map = derived_class_map(native_exports)
    exports_path = os.path.join(tmp, "native_exports.json")
    write_native_exports_wire(exports_path, native_exports, class_map)
    profile_counter(
        profile,
        "multi_frontend_export_worker_sum_ms",
        export_worker_sum_ms,
    )
    profile_counter(
        profile,
        "multi_frontend_export_worker_max_ms",
        export_worker_max_ms,
    )
    return exports_path


def compile_parallel(
    src_paths,
    module_names,
    *,
    jobs: int,
    entry_module: str,
    sibling_inits,
    libpython_mode: str,
    ir_scaffold_mode: str,
    verbose: bool,
    profile: Optional[dict] = None,
    can_spawn_worker,
    worker_command_prefix,
    compile_uncached,
    plan_cache,
    load_cache,
    acquire_cache,
    wait_cache,
    publish_cache,
    release_cache,
    host_python_command,
    source_root,
    profile_begin,
    profile_end,
    profile_counter,
):
    """Run parallel frontend codegen with a pre-pass content cache."""
    if jobs < 1 or not can_spawn_worker():
        return compile_uncached(
            src_paths,
            module_names,
            jobs=jobs,
            entry_module=entry_module,
            sibling_inits=sibling_inits,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            verbose=verbose,
            profile=profile,
        )
    worker_prefix = worker_command_prefix()
    if not worker_prefix:
        return compile_uncached(
            src_paths,
            module_names,
            jobs=jobs,
            entry_module=entry_module,
            sibling_inits=sibling_inits,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            verbose=verbose,
            profile=profile,
        )

    cache_started = profile_begin(profile)
    cache_plan = plan_cache(
        src_paths,
        module_names,
        compiler_executable=str(worker_prefix[0]),
        host_python=host_python_command(),
        entry_module=entry_module,
        sibling_inits=sibling_inits,
        libpython_mode=libpython_mode,
        ir_scaffold_mode=ir_scaffold_mode,
        source_root=source_root(),
    )
    cached_result = load_cache(cache_plan, module_names)
    profile_end(profile, "multi_frontend_ir_cache_lookup", cache_started)
    if cached_result is not None:
        profile_counter(profile, "multi_frontend_ir_cache_hits", 1)
        profile_counter(profile, "multi_frontend_ir_cache_misses", 0)
        return cached_result

    cache_owner = acquire_cache(cache_plan)
    if cache_plan is not None and not cache_owner:
        wait_started = profile_begin(profile)
        cached_result = wait_cache(cache_plan, module_names)
        profile_end(profile, "multi_frontend_ir_cache_wait", wait_started)
        if cached_result is not None:
            profile_counter(profile, "multi_frontend_ir_cache_hits", 1)
            profile_counter(profile, "multi_frontend_ir_cache_misses", 0)
            return cached_result

    profile_counter(profile, "multi_frontend_ir_cache_hits", 0)
    profile_counter(
        profile,
        "multi_frontend_ir_cache_misses",
        1 if cache_plan is not None else 0,
    )
    try:
        result = compile_uncached(
            src_paths,
            module_names,
            jobs=jobs,
            entry_module=entry_module,
            sibling_inits=sibling_inits,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            verbose=verbose,
            profile=profile,
            action_cache_plan=cache_plan,
        )
        if cache_owner and result is not None:
            publish_started = profile_begin(profile)
            published = publish_cache(cache_plan, result)
            profile_end(
                profile,
                "multi_frontend_ir_cache_publish",
                publish_started,
            )
            profile_counter(
                profile,
                "multi_frontend_ir_cache_publish_ok",
                1 if published else 0,
            )
        return result
    finally:
        if cache_owner:
            release_cache(cache_plan)


def compile_parallel_uncached(
    src_paths,
    module_names,
    *,
    jobs: int,
    entry_module: str,
    sibling_inits,
    libpython_mode: str,
    ir_scaffold_mode: str,
    verbose: bool,
    profile: Optional[dict] = None,
    action_cache_plan=None,
    can_spawn_worker,
    worker_command_prefix,
    chunk_count_for_workers,
    codegen_chunks,
    ast_wire_enabled,
    build_shared_exports_callback,
    write_manifest,
    shell_quote_arg,
    worker_arg: str,
    worker_env_prefix,
    join_strings,
    run_worker_commands,
    profiled_gc_collect,
    read_worker_ir,
    profile_begin,
    profile_end,
    profile_counter,
    pipeline_error,
    build_action_state=None,
    auto_source_lanes: bool = True,
):
    if jobs < 1 or not can_spawn_worker():
        return None
    worker_prefix = worker_command_prefix()
    if not worker_prefix:
        return None
    noop_result = _load_noop_action_result(
        action_cache_plan,
        src_paths,
        module_names,
    )
    if noop_result is not None:
        profile_counter(profile, "multi_frontend_action_cache_hits", len(src_paths))
        profile_counter(profile, "multi_frontend_action_cache_misses", 0)
        profile_counter(profile, "multi_frontend_action_modules_compiled", 0)
        profile_counter(profile, "multi_frontend_action_plan_actions", 0)
        return noop_result
    chunk_count = chunk_count_for_workers(len(src_paths), jobs, worker_prefix)
    chunks = codegen_chunks(src_paths, chunk_count)
    # Export workers only parse/lift and publish AST/export sidecars.  Giving
    # that phase the shorter codegen shards would repeat interpreter startup
    # and export merging without reducing retained codegen state.  Keep one
    # export shard per active slot, then consume the same sidecars from the
    # bounded, shorter-lived codegen shards below.
    export_chunks = codegen_chunks(src_paths, jobs)
    if not chunks or not export_chunks:
        return None
    profile_counter(profile, "multi_frontend_chunks", len(chunks))
    profile_counter(profile, "multi_frontend_export_chunks", len(export_chunks))
    profile_counter(profile, "multi_frontend_worker_concurrency", jobs)
    with tempfile.TemporaryDirectory(prefix="pcc_py_frontend_workers_") as tmp:
        ir_dir = os.path.join(tmp, "ir")
        subprocess.run(["mkdir", "-p", ir_dir], check=True)
        # The merged may_park fixed point is a semantic prerequisite for
        # parallel codegen, so every export worker must publish its lifted AST.
        # The same sidecars are then reused by codegen workers; no parent/host
        # reparse owns a second copy of the frontend state.  Keep recording the
        # old opt-in signal separately while making the correctness transport
        # unconditional.
        ast_wire_requested = ast_wire_enabled()
        ast_dir = os.path.join(tmp, "ast")
        subprocess.run(["mkdir", "-p", ast_dir], check=True)
        profile_counter(profile, "multi_frontend_ast_wire_enabled", 1)
        profile_counter(
            profile,
            "multi_frontend_ast_wire_requested",
            1 if ast_wire_requested else 0,
        )
        started = profile_begin(profile)
        exports_path = build_shared_exports_callback(
            tmp,
            src_paths,
            module_names,
            export_chunks,
            worker_prefix,
            entry_module=entry_module,
            sibling_inits=sibling_inits,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            verbose=verbose,
            max_parallel=jobs,
            ast_dir=ast_dir,
            profile=profile,
        )
        profile_end(profile, "multi_frontend_export_parallel", started)

        action_state = None
        action_root = ""
        action_plan = None
        module_ir_by_index: list[Optional[tuple[str, str]]] = [
            None for _source in src_paths
        ]
        module_meta_by_index: list[Optional[tuple[bool, bool, int]]] = [
            None for _source in src_paths
        ]
        action_hits = 0
        if action_cache_plan is not None and build_action_state is not None:
            try:
                action_state = build_action_state(
                    src_paths,
                    module_names,
                    exports_path,
                    action_cache_plan,
                )
                action_root = str(action_cache_plan.get("action_root", ""))
                previous_state = _module_action_dag.load_graph_state_file(
                    action_root
                )
                if action_state is not None and previous_state is not None:
                    action_plan = _module_action_dag.plan_actions(
                        previous_state,
                        action_state,
                    )
                    if not action_plan.full_rebuild:
                        for index, module_name in enumerate(module_names):
                            action = _module_ir_action(action_state, module_name)
                            artifact = _module_action_dag.load_action_artifact(
                                action_root,
                                action,
                            )
                            if artifact is None:
                                continue
                            decoded = _decode_module_ir_artifact(
                                artifact,
                                module_name,
                            )
                            if decoded is None:
                                continue
                            ir_text, needs_libpython, needs_native, ir_size = decoded
                            module_ir_by_index[index] = (module_name, ir_text)
                            module_meta_by_index[index] = (
                                needs_libpython,
                                needs_native,
                                ir_size,
                            )
                            action_hits += 1
            except Exception:
                # The action cache is an optimization boundary.  Unknown or
                # corrupt graph/export state falls back to the complete,
                # already-correct worker path and is never partially trusted.
                action_state = None
                action_root = ""
                action_plan = None
                module_ir_by_index = [None for _source in src_paths]
                module_meta_by_index = [None for _source in src_paths]
                action_hits = 0

        pending_indices = []
        for index, item in enumerate(module_ir_by_index):
            if item is None:
                pending_indices.append(index)
        pending_set = set(pending_indices)
        pending_chunks = []
        for chunk in chunks:
            pending_chunk = []
            for index in chunk:
                if index in pending_set:
                    pending_chunk.append(index)
            if pending_chunk:
                pending_chunks.append(pending_chunk)
        oversized_chunks = []
        safe_chunks = pending_chunks
        if auto_source_lanes and jobs > 1:
            oversized_chunks, safe_chunks = (
                _split_codegen_chunks_by_source_size(
                    src_paths, pending_chunks
                )
            )
        scheduled_chunks = oversized_chunks + safe_chunks
        oversized_chunk_count = len(oversized_chunks)
        safe_jobs = jobs
        if auto_source_lanes and safe_jobs > _SOURCE_WORKER_AUTO_SAFE_JOBS:
            safe_jobs = _SOURCE_WORKER_AUTO_SAFE_JOBS
        profile_counter(
            profile,
            "multi_frontend_codegen_oversized_chunks",
            oversized_chunk_count,
        )
        profile_counter(
            profile,
            "multi_frontend_codegen_safe_chunks",
            len(safe_chunks),
        )
        profile_counter(
            profile,
            "multi_frontend_codegen_oversized_jobs",
            1 if oversized_chunks else 0,
        )
        profile_counter(profile, "multi_frontend_codegen_safe_jobs", safe_jobs)
        profile_counter(profile, "multi_frontend_action_cache_hits", action_hits)
        profile_counter(
            profile,
            "multi_frontend_action_cache_misses",
            len(pending_indices),
        )
        profile_counter(
            profile,
            "multi_frontend_action_modules_compiled",
            len(pending_indices),
        )
        profile_counter(
            profile,
            "multi_frontend_action_plan_actions",
            len(action_plan.actions) if action_plan is not None else len(src_paths) * 5,
        )
        result_paths: list[str] = []
        commands: list[str] = []
        for worker_index, chunk in enumerate(scheduled_chunks):
            manifest_path = os.path.join(
                tmp,
                "worker_" + str(worker_index) + ".manifest",
            )
            result_path = os.path.join(
                tmp,
                "worker_" + str(worker_index) + ".tsv",
            )
            write_manifest(
                manifest_path,
                result_path,
                ir_dir,
                exports_path,
                ast_dir,
                src_paths,
                module_names,
                chunk,
                entry_module=entry_module,
                sibling_inits=sibling_inits,
                libpython_mode=libpython_mode,
                ir_scaffold_mode=ir_scaffold_mode,
                verbose=verbose,
            )
            result_paths.append(result_path)
            command_parts = [shell_quote_arg(part) for part in worker_prefix]
            command_parts.append(shell_quote_arg(worker_arg))
            command_parts.append(shell_quote_arg(manifest_path))
            commands.append(
                worker_env_prefix() + " " + join_strings(command_parts, " ")
            )

        try:
            started = profile_begin(profile)
            if oversized_chunk_count:
                oversized_started = profile_begin(profile)
                run_worker_commands(
                    commands[:oversized_chunk_count],
                    max_parallel=1,
                )
                profile_end(
                    profile,
                    "multi_frontend_codegen_oversized_workers",
                    oversized_started,
                )
            safe_commands = commands[oversized_chunk_count:]
            if safe_commands:
                safe_started = profile_begin(profile)
                run_worker_commands(safe_commands, max_parallel=safe_jobs)
                profile_end(
                    profile,
                    "multi_frontend_codegen_safe_workers",
                    safe_started,
                )
            profile_end(
                profile,
                "multi_frontend_codegen_worker_commands",
                started,
            )
            profiled_gc_collect(
                profile,
                "multi_frontend_codegen_worker_collect",
                allocations_owned_by_current_process=False,
            )
        except subprocess.CalledProcessError as exc:
            raise pipeline_error("parallel frontend codegen worker failed") from exc

        started = profile_begin(profile)
        worker_parse_sum_ms = 0
        worker_parse_max_ms = 0
        worker_parse_max_index = -1
        worker_infer_sum_ms = 0
        worker_infer_max_ms = 0
        worker_infer_max_index = -1
        worker_codegen_sum_ms = 0
        worker_codegen_max_ms = 0
        worker_codegen_max_index = -1
        for result_path in result_paths:
            with open(result_path, "r", encoding="utf-8") as stream:
                result_text = stream.read()
            for raw_line in result_text.splitlines():
                parts = raw_line.split("\t")
                if not parts:
                    continue
                if parts[0] == "ERR":
                    message = parts[1] if len(parts) > 1 else "worker error"
                    raise pipeline_error(message)
                if parts[0] != "OK" or len(parts) < 7:
                    raise pipeline_error("invalid frontend worker result")
                index = int(parts[1])
                module_name = parts[2]
                needs_libpython = parts[3] == "1"
                needs_native_exports = parts[4] == "1"
                try:
                    ir_bytes_before_passes = int(parts[5])
                except ValueError:
                    ir_bytes_before_passes = 0
                ir_text = read_worker_ir(parts[6], module_name)
                module_ir_by_index[index] = (module_name, ir_text)
                module_meta_by_index[index] = (
                    needs_libpython,
                    needs_native_exports,
                    ir_bytes_before_passes,
                )
                if len(parts) >= 10:
                    try:
                        parse_ms = int(parts[7])
                        infer_ms = int(parts[8])
                        codegen_ms = int(parts[9])
                    except ValueError:
                        parse_ms = 0
                        infer_ms = 0
                        codegen_ms = 0
                    worker_parse_sum_ms += parse_ms
                    worker_infer_sum_ms += infer_ms
                    worker_codegen_sum_ms += codegen_ms
                    if parse_ms > worker_parse_max_ms:
                        worker_parse_max_ms = parse_ms
                        worker_parse_max_index = index
                    if infer_ms > worker_infer_max_ms:
                        worker_infer_max_ms = infer_ms
                        worker_infer_max_index = index
                    if codegen_ms > worker_codegen_max_ms:
                        worker_codegen_max_ms = codegen_ms
                        worker_codegen_max_index = index
        module_ir_texts = []
        for index, item in enumerate(module_ir_by_index):
            if item is None:
                raise pipeline_error(
                    "parallel frontend worker missed module " + str(index)
                )
            module_ir_texts.append(item)
            if module_meta_by_index[index] is None:
                raise pipeline_error(
                    "parallel frontend worker missed module metadata " + str(index)
                )

        if action_state is not None and action_root:
            all_artifacts_published = True
            for index in pending_indices:
                module_ir_item = module_ir_by_index[index]
                module_meta_item = module_meta_by_index[index]
                if module_ir_item is None or module_meta_item is None:
                    raise pipeline_error(
                        "parallel frontend action artifact lost module "
                        + str(index)
                    )
                module_name = module_ir_item[0]
                ir_text = module_ir_item[1]
                needs_libpython = module_meta_item[0]
                needs_native = module_meta_item[1]
                ir_size = module_meta_item[2]
                action = _module_ir_action(action_state, module_name)
                artifact = _encode_module_ir_artifact(
                    module_name,
                    ir_text,
                    needs_libpython,
                    needs_native,
                    ir_size,
                )
                if not _module_action_dag.publish_action_artifact(
                    action_root,
                    action,
                    artifact,
                ):
                    all_artifacts_published = False
            if all_artifacts_published:
                _module_action_dag.publish_graph_state_file(
                    action_root,
                    action_state,
                )

        any_needs_libpython = False
        any_needs_native_extension_exports = False
        total_ir_bytes_before_passes = 0
        libpython_modules: list[str] = []
        for index, module_name in enumerate(module_names):
            module_meta_item = module_meta_by_index[index]
            if module_meta_item is None:
                raise pipeline_error(
                    "parallel frontend worker lost module metadata " + str(index)
                )
            needs_libpython = module_meta_item[0]
            needs_native = module_meta_item[1]
            ir_size = module_meta_item[2]
            total_ir_bytes_before_passes += int(ir_size)
            if needs_libpython:
                any_needs_libpython = True
                libpython_modules.append(str(module_name))
            if needs_native:
                any_needs_native_extension_exports = True
        counters = (
            ("multi_frontend_worker_parse_sum_ms", worker_parse_sum_ms),
            ("multi_frontend_worker_parse_max_ms", worker_parse_max_ms),
            ("multi_frontend_worker_parse_max_index", worker_parse_max_index),
            ("multi_frontend_worker_infer_sum_ms", worker_infer_sum_ms),
            ("multi_frontend_worker_infer_max_ms", worker_infer_max_ms),
            ("multi_frontend_worker_infer_max_index", worker_infer_max_index),
            ("multi_frontend_worker_codegen_sum_ms", worker_codegen_sum_ms),
            ("multi_frontend_worker_codegen_max_ms", worker_codegen_max_ms),
            ("multi_frontend_worker_codegen_max_index", worker_codegen_max_index),
        )
        for name, value in counters:
            profile_counter(profile, name, value)
        profile_end(profile, "multi_frontend_codegen_result_read", started)
        return (
            module_ir_texts,
            any_needs_libpython,
            any_needs_native_extension_exports,
            total_ir_bytes_before_passes,
            libpython_modules,
        )
