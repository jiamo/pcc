"""Self-backend single/batch/split worker protocol implementations."""

from __future__ import annotations

import gc
import os
import subprocess
import sys
from typing import Optional

from .pipeline_paths import join_strings


class SelfBackendEmitError(RuntimeError):
    """A self-backend emitter worker contract was invalid."""


def debug_dump_ir_texts(ir_texts: list[str]) -> None:
    dump_dir = str(os.environ.get("PCC_DEBUG_SELF_IR_DUMP_DIR", "") or "").strip()
    if not dump_dir:
        return
    for index, ir_text in enumerate(ir_texts):
        path = str(os.path.join(dump_dir, f"self_backend_input_{index}.ll"))
        try:
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(str(ir_text))
        except OSError:
            return


def emit_in_process(
    ir_text: str,
    *,
    parse_target_triple,
    host_target_triple,
    target_supported,
    emit_asm,
) -> Optional[tuple[str, str]]:
    triple = parse_target_triple(ir_text)
    if triple == "unknown-unknown-unknown":
        triple = host_target_triple()
    if not target_supported(triple):
        return None
    return "self-aarch64-darwin-v0", emit_asm(ir_text, False)


def emit_via_host_python(
    ir_text: str,
    tmp_dir: str,
    index: int,
    *,
    emit_native,
    normalize_ir,
    host_python_command,
    host_code: str,
    pcc_source_root,
) -> tuple[str, str]:
    native_result = emit_native(ir_text)
    if native_result is not None:
        return native_result
    ir_path = str(os.path.join(tmp_dir, f"self_backend_input_{index}.ll"))
    with open(ir_path, "w", encoding="utf-8") as stream:
        stream.write(normalize_ir(ir_text))
    try:
        output = str(
            subprocess.check_output(
                [
                    host_python_command(),
                    "-c",
                    host_code,
                    pcc_source_root(),
                    ir_path,
                ],
                text=True,
            )
        )
    except Exception as exc:
        raise SelfBackendEmitError(
            f"self backend native emission failed: {exc}"
        ) from exc
    lines = output.splitlines()
    if not lines:
        raise SelfBackendEmitError(
            "self backend native emission failed: host emitter produced no output"
        )
    return lines[0], "\n".join(lines[1:])


def run_emit_worker(
    ir_path: str,
    result_path: str,
    obj_path: str = "",
    cc: str = "",
    *,
    normalize_ir,
    emit_native,
) -> int:
    try:
        with open(ir_path, "r", encoding="utf-8") as stream:
            ir_text = normalize_ir(stream.read())
        native_result = emit_native(ir_text)
        if native_result is None:
            raise SelfBackendEmitError(
                "native emitter does not support the module target"
            )
        target_id, asm_text = native_result
        result_payload = asm_text
        if obj_path:
            asm_path = obj_path if not cc else result_path + ".s"
            with open(asm_path, "w", encoding="utf-8") as stream:
                stream.write(asm_text)
            if cc:
                subprocess.run([cc, "-c", asm_path, "-o", obj_path], check=True)
            result_payload = obj_path
        with open(result_path, "w", encoding="utf-8") as stream:
            stream.write(target_id + "\n")
            stream.write(result_payload)
        return 0
    except Exception as exc:
        sys.stderr.write("self backend emit worker failed: " + str(exc) + "\n")
        return 1


def run_emit_batch_worker(
    manifest_path: str,
    *,
    manifest_version: str,
    emit_worker,
) -> int:
    try:
        with open(manifest_path, "r", encoding="utf-8") as stream:
            lines = stream.read().splitlines()
        if not lines or lines[0] != manifest_version:
            raise SelfBackendEmitError(
                "self backend emit batch has an invalid manifest"
            )
        payload = lines[1:]
        if not payload or len(payload) % 4 != 0:
            raise SelfBackendEmitError(
                "self backend emit batch has invalid item fields"
            )
        index = 0
        while index < len(payload):
            status = emit_worker(
                payload[index],
                payload[index + 1],
                payload[index + 2],
                payload[index + 3],
            )
            if status != 0:
                return status
            index += 4
        return 0
    except Exception as exc:
        sys.stderr.write(
            "self backend emit batch worker failed: " + str(exc) + "\n"
        )
        return 1


def _item_byte_sizes(sized_items):
    """Return just the byte sizes from ``(input_bytes, worker_item)`` pairs.

    A comprehension here would bind a second `input_bytes` in this module, and
    the self-hosted frontend plans that name as an exact-int local elsewhere in
    the same function -- the two storage forms cannot coexist, and stage1 fails
    with "planned exact-int local does not have object storage: input_bytes".
    Keeping the unpack inside its own function sidesteps the name entirely.
    """
    sizes = []
    for sized_item in sized_items:
        sizes.append(sized_item[0])
    return sizes


def _pack_batches(worker_items, batch_max_items: int, item_bytes):
    """Group worker items into batches balanced by input BYTES, not count.

    A batch is emitted serially by one worker, so a batch's cost is the sum of
    its inputs and the pool's wall time is the heaviest batch.  Packing purely
    by item count put the four largest shards of a stage2 emit into one batch:

        batch 0   27 + 12 + 10 + 5 MB = 54 MB   <- decided the whole phase
        batch 1    4 +  4 +  4 + 2 MB = 14 MB
        batch 2    2 +  1 +  1      MB =  4 MB

    Two of the three workers finished early and idled while the first ground
    through 54 MB serially.  (The same batch is what a previous cold bootstrap
    was killed in.)  Longest-processing-time-first — take the largest item and
    put it in whichever batch is currently lightest — evens that out; the item
    count cap is kept so a batch never grows an unbounded manifest.

    ``item_bytes`` may be None when sizes are unknown, in which case this
    degrades to the original count-based chunking.
    """
    items = list(worker_items)
    if not items:
        return []
    if not item_bytes or len(item_bytes) != len(items):
        batches = []
        batch = []
        for item in items:
            batch.append(item)
            if len(batch) >= batch_max_items:
                batches.append(batch)
                batch = []
        if batch:
            batches.append(batch)
        return batches

    order = sorted(range(len(items)), key=lambda i: -int(item_bytes[i]))
    n_batches = (len(items) + batch_max_items - 1) // batch_max_items
    batches = []
    loads = []
    for _ in range(n_batches):
        batches.append([])
        loads.append(0)
    for index in order:
        target = -1
        for slot in range(n_batches):
            if len(batches[slot]) >= batch_max_items:
                continue
            if target < 0 or loads[slot] < loads[target]:
                target = slot
        if target < 0:
            target = 0
            batches.append([])
            loads.append(0)
            target = len(batches) - 1
        batches[target].append(items[index])
        loads[target] += int(item_bytes[index])
    return [batch for batch in batches if batch]


def run_emit_worker_pool(
    worker_command_prefix: list[str],
    worker_items: list[tuple[str, str, str]],
    cc: str,
    tmp_dir: str,
    batch_label: str,
    max_parallel: int,
    *,
    batch_max_items: int,
    manifest_version: str,
    item_bytes=None,
    worker_arg: str,
    small_int_decimal,
    shell_quote_arg,
    run_worker_commands,
) -> int:
    if not worker_items:
        return 0
    max_parallel = max(1, min(int(max_parallel), len(worker_items)))
    batches = _pack_batches(worker_items, batch_max_items, None)  # BISECT: packing off
    commands: list[str] = []
    for batch_index, batch in enumerate(batches):
        manifest_path = str(
            os.path.join(
                tmp_dir,
                "self_backend_emit_"
                + batch_label
                + "_"
                + small_int_decimal(batch_index)
                + ".manifest",
            )
        )
        with open(manifest_path, "w", encoding="utf-8") as stream:
            stream.write(manifest_version + "\n")
            for result_path, obj_path, ir_path in batch:
                for field in (ir_path, result_path, obj_path, cc):
                    if "\n" in field or "\r" in field:
                        raise SelfBackendEmitError(
                            "self backend emit batch field contains a newline"
                        )
                    stream.write(field + "\n")
        command_parts = [shell_quote_arg(part) for part in worker_command_prefix]
        command_parts.extend(
            [shell_quote_arg(worker_arg), shell_quote_arg(manifest_path)]
        )
        commands.append(join_strings(command_parts, " "))
    run_worker_commands(
        commands,
        max_parallel=min(max_parallel, len(commands)),
    )
    return len(commands)


def run_split_worker(
    ir_path: str,
    result_path: str,
    output_prefix: str,
    export_prefix: str,
    shard_bytes_text: str,
    *,
    split_ir_module,
    small_int_decimal,
) -> int:
    try:
        with open(ir_path, "r", encoding="utf-8") as stream:
            ir_text = stream.read()
        shard_bytes = int(shard_bytes_text)
        if shard_bytes <= 0:
            raise SelfBackendEmitError(
                "self backend split worker requires shard bytes"
            )
        shards = split_ir_module(
            ir_text,
            export_prefix=export_prefix,
            shard_bytes=shard_bytes,
        )
        shard_paths: list[str] = []
        for index, shard_text in enumerate(shards):
            shard_path = output_prefix + small_int_decimal(index) + ".ll"
            with open(shard_path, "w", encoding="utf-8") as stream:
                stream.write(shard_text)
            shard_paths.append(shard_path)
        with open(result_path, "w", encoding="utf-8") as stream:
            stream.write("pcc.self_backend.split.v1\n")
            for shard_path in shard_paths:
                stream.write(shard_path + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write("self backend split worker failed: " + str(exc) + "\n")
        return 1


def emit_objects_many_in_process(
    ir_texts: list[str],
    tmp_dir: str,
    cc: str,
    *,
    split_large_modules: bool,
    profile: Optional[dict],
    internal_link: bool = False,
    parse_target_triple,
    host_target_triple,
    target_supported,
    native_worker_executable,
    split_large_ir_modules,
    source_workers_worthwhile,
    worker_command_prefix_for_frontend,
    split_threshold_bytes,
    split_shard_bytes,
    jobs_for_ir_texts,
    profile_counter,
    profiled_gc_collect,
    profile_begin,
    profile_end,
    run_worker_commands,
    small_int_decimal,
    shell_quote_arg,
    split_worker_arg: str,
    plan_cache,
    jobs,
    jobs_for_input_sizes,
    jobs_env: str,
    run_emit_worker_pool,
    publish_cache,
    maintain_cache,
    emit_in_process,
    join_strings,
) -> Optional[list[tuple[str, str]]]:
    """Emit AArch64 modules as internal assembly or external Mach-O objects."""
    if not ir_texts:
        return []
    first_triple = parse_target_triple(ir_texts[0])
    if first_triple == "unknown-unknown-unknown":
        first_triple = host_target_triple()
    if not target_supported(first_triple):
        return None
    pairs: list[tuple[str, str]] = []
    native_worker = native_worker_executable()
    inputs = ir_texts
    if not native_worker and split_large_modules:
        inputs = split_large_ir_modules(ir_texts)
    if native_worker:
        worker_command_prefix = [native_worker]
    elif source_workers_worthwhile(inputs):
        worker_command_prefix = worker_command_prefix_for_frontend()
    else:
        worker_command_prefix = []
    t = profile_begin(profile)
    if worker_command_prefix:
        split_threshold = split_threshold_bytes()
        split_shard_bytes_value = split_shard_bytes()
        split_jobs = jobs_for_ir_texts(
            inputs,
            native_worker=bool(native_worker),
        )
        profile_counter(
            profile,
            "link_self_native_split_jobs",
            split_jobs,
        )
        planned_inputs: list[tuple[str, str, int]] = []
        split_worker_commands: list[str] = []
        split_module_count = 0
        for index, ir_text in enumerate(inputs):
            ir_path = str(os.path.join(tmp_dir, f"self_backend_module_{index}.ll"))
            with open(ir_path, "w", encoding="utf-8") as f:
                f.write(ir_text)
            input_bytes = len(ir_text)
            if native_worker and split_large_modules and input_bytes >= split_threshold:
                result_path = str(
                    os.path.join(tmp_dir, f"self_backend_split_{index}.result")
                )
                output_prefix = str(
                    os.path.join(tmp_dir, f"self_backend_split_{index}_shard_")
                )
                export_prefix = "__pco" + small_int_decimal(index) + "_"
                command_parts = []
                for prefix_part in worker_command_prefix:
                    command_parts.append(shell_quote_arg(prefix_part))
                command_parts.extend(
                    [
                        shell_quote_arg(split_worker_arg),
                        shell_quote_arg(ir_path),
                        shell_quote_arg(result_path),
                        shell_quote_arg(output_prefix),
                        shell_quote_arg(export_prefix),
                        shell_quote_arg(
                            small_int_decimal(split_shard_bytes_value)
                        ),
                    ]
                )
                split_worker_commands.append(join_strings(command_parts, " "))
                planned_inputs.append((result_path, output_prefix, -1))
                split_module_count += 1
            else:
                planned_inputs.append((ir_path, "", input_bytes))

        if split_worker_commands:
            profiled_gc_collect(
                profile,
                "link_self_native_pre_split_collect",
                allocations_owned_by_current_process=False,
            )
            split_t = profile_begin(profile)
            run_worker_commands(
                split_worker_commands,
                max_parallel=min(
                    2,
                    split_jobs,
                    len(split_worker_commands),
                ),
            )
            profile_end(profile, "link_self_native_split_workers", split_t)
            profiled_gc_collect(
                profile,
                "link_self_native_post_split_collect",
                allocations_owned_by_current_process=False,
            )

        worker_inputs: list[tuple[str, int]] = []
        split_shard_count = 0
        for input_path, output_prefix, input_bytes in planned_inputs:
            if input_bytes >= 0:
                worker_inputs.append((input_path, input_bytes))
                continue
            with open(input_path, "r", encoding="utf-8") as f:
                manifest_lines = f.read().splitlines()
            if not manifest_lines or manifest_lines[0] != "pcc.self_backend.split.v1":
                raise SelfBackendEmitError(
                    "self backend split worker produced an invalid manifest"
                )
            shard_paths = manifest_lines[1:]
            if not shard_paths:
                raise SelfBackendEmitError("self backend split worker produced no shards")
            for shard_path in shard_paths:
                if not shard_path.startswith(output_prefix) or not os.path.isfile(
                    shard_path
                ):
                    raise SelfBackendEmitError(
                        "self backend split worker produced an invalid shard path"
                    )
                worker_inputs.append((shard_path, os.path.getsize(shard_path)))
                split_shard_count += 1
        profile_counter(profile, "link_self_native_split_modules", split_module_count)
        profile_counter(profile, "link_self_native_split_shards", split_shard_count)

        # A native worker parsing one multi-megabyte module can retain several
        # GiB.  Keep residual post-split inputs at or above the safety threshold
        # in their own serial lane, but do not let those few inputs force every
        # sub-threshold cache miss into that lane.  The safe lane can use the
        # normal bounded self-backend worker budget after the oversized lane
        # exits, so their peak resident compiler heaps never overlap.
        emit_threshold = split_threshold_bytes()

        worker_items: list[tuple[str, str, str]] = []
        worker_input_bytes: list[int] = []
        for index, worker_input in enumerate(worker_inputs):
            ir_path, input_bytes = worker_input
            artifact_suffix = ".s" if internal_link else ".o"
            obj_path = str(os.path.join(
                tmp_dir,
                f"self_backend_native_{index}{artifact_suffix}",
            ))
            result_path = str(
                os.path.join(tmp_dir, f"self_backend_native_{index}.result")
            )
            worker_items.append((result_path, obj_path, ir_path))
            worker_input_bytes.append(input_bytes)

        cache_plan_t = profile_begin(profile)
        cache_plan = plan_cache(
            worker_items,
            "self-aarch64-darwin-v0",
            "pcc-native-asm-v1" if internal_link else cc,
            tmp_dir,
        )
        profile_end(profile, "link_self_native_object_cache_plan", cache_plan_t)

        oversized_worker_items: list[tuple[int, tuple[str, str, str]]] = []
        large_worker_items: list[tuple[int, tuple[str, str, str]]] = []
        small_worker_items: list[tuple[str, str, str]] = []
        safe_emit_input_sizes: list[int] = []
        for index, worker_item in enumerate(worker_items):
            input_bytes = worker_input_bytes[index]
            _cache_path, cache_status = cache_plan[index]
            if cache_status == "hit":
                continue
            if native_worker and input_bytes >= emit_threshold:
                insert_at = 0
                while (
                    insert_at < len(oversized_worker_items)
                    and oversized_worker_items[insert_at][0] >= input_bytes
                ):
                    insert_at += 1
                oversized_worker_items.insert(
                    insert_at,
                    (input_bytes, worker_item),
                )
            elif input_bytes >= 1_000_000:
                safe_emit_input_sizes.append(input_bytes)
                insert_at = 0
                while (
                    insert_at < len(large_worker_items)
                    and large_worker_items[insert_at][0] >= input_bytes
                ):
                    insert_at += 1
                large_worker_items.insert(insert_at, (input_bytes, worker_item))
            else:
                safe_emit_input_sizes.append(input_bytes)
                small_worker_items.append(worker_item)
        cache_miss_count = len(oversized_worker_items) + len(safe_emit_input_sizes)
        configured_emit_jobs = (
            jobs(cache_miss_count) if cache_miss_count else 0
        )
        safe_emit_jobs = (
            jobs_for_input_sizes(
                safe_emit_input_sizes,
                native_worker=bool(native_worker),
            )
            if safe_emit_input_sizes
            else 0
        )
        profile_counter(
            profile,
            "link_self_native_oversized_object_count",
            len(oversized_worker_items),
        )
        profile_counter(
            profile,
            "link_self_native_safe_object_count",
            len(large_worker_items) + len(small_worker_items),
        )
        oversized_emit_jobs = 0
        explicit_emit_jobs = str(
            os.environ.get(jobs_env, "") or ""
        ).strip()
        if oversized_worker_items:
            oversized_emit_jobs = 1
            if explicit_emit_jobs:
                oversized_emit_jobs = jobs(
                    len(oversized_worker_items)
                )
        effective_emit_jobs = max(safe_emit_jobs, oversized_emit_jobs)
        profile_counter(
            profile,
            "link_self_native_configured_jobs",
            configured_emit_jobs,
        )
        profile_counter(
            profile,
            "link_self_native_safe_emit_jobs",
            safe_emit_jobs,
        )
        profile_counter(
            profile,
            "link_self_native_emit_jobs",
            effective_emit_jobs,
        )
        profile_counter(
            profile,
            "link_self_native_oversized_emit_jobs",
            oversized_emit_jobs,
        )
        profile_counter(
            profile,
            "link_self_native_large_ir_job_cap",
            (
                1
                if split_jobs < jobs(len(inputs))
                or bool(oversized_worker_items)
                else 0
            ),
        )
        oversized_pool_processes = 0
        if oversized_worker_items:
            oversized_emit_t = profile_begin(profile)
            oversized_pool_processes = run_emit_worker_pool(
                worker_command_prefix,
                [worker_item for _input_bytes, worker_item in oversized_worker_items],
                "" if internal_link else cc,
                tmp_dir,
                "oversized",
                oversized_emit_jobs,
                item_bytes=_item_byte_sizes(oversized_worker_items),
            )
            profile_end(
                profile,
                "link_self_native_emit_oversized_workers",
                oversized_emit_t,
            )
        safe_pool_processes = 0
        safe_emit_t = profile_begin(profile)
        if large_worker_items:
            huge_items: list[tuple[str, str, str]] = []
            huge_bytes: list[int] = []
            medium_items: list[tuple[str, str, str]] = []
            medium_bytes: list[int] = []
            for input_bytes, worker_item in large_worker_items:
                if input_bytes >= 4_000_000:
                    huge_items.append(worker_item)
                    huge_bytes.append(input_bytes)
                else:
                    medium_items.append(worker_item)
                    medium_bytes.append(input_bytes)
            if huge_items:
                safe_pool_processes += run_emit_worker_pool(
                    worker_command_prefix,
                    huge_items,
                    "" if internal_link else cc,
                    tmp_dir,
                    "huge",
                    min(2, safe_emit_jobs, len(huge_items)),
                    item_bytes=huge_bytes,
                )
            if medium_items:
                safe_pool_processes += run_emit_worker_pool(
                    worker_command_prefix,
                    medium_items,
                    "" if internal_link else cc,
                    tmp_dir,
                    "medium",
                    min(8, safe_emit_jobs, len(medium_items)),
                    item_bytes=medium_bytes,
                )
        if small_worker_items:
            safe_pool_processes += run_emit_worker_pool(
                worker_command_prefix,
                small_worker_items,
                "" if internal_link else cc,
                tmp_dir,
                "small",
                min(12, safe_emit_jobs, len(small_worker_items)),
            )
        if large_worker_items or small_worker_items:
            profile_end(
                profile,
                "link_self_native_emit_safe_workers",
                safe_emit_t,
            )
        emit_pool_processes = oversized_pool_processes + safe_pool_processes
        profile_counter(
            profile,
            "link_self_native_oversized_emit_pool_processes",
            oversized_pool_processes,
        )
        profile_counter(
            profile,
            "link_self_native_safe_emit_pool_processes",
            safe_pool_processes,
        )
        profile_counter(
            profile,
            "link_self_native_emit_pool_processes",
            emit_pool_processes,
        )
        native_object_cache_hits = 0
        native_object_cache_misses = 0
        native_object_cache_disabled = 0
        for worker_index, worker_item in enumerate(worker_items):
            result_path, obj_path, _ir_path = worker_item
            with open(result_path, "r", encoding="utf-8") as f:
                worker_result = f.read()
            worker_result_lines = worker_result.splitlines()
            target_id = worker_result_lines[0] if worker_result_lines else ""
            emitted_obj_path = (
                worker_result_lines[1] if len(worker_result_lines) >= 2 else ""
            )
            if (
                not target_id
                or emitted_obj_path.strip() != obj_path
                or not os.path.isfile(obj_path)
            ):
                raise SelfBackendEmitError(
                    "self backend emit worker produced an invalid result"
                )
            cache_status = (
                worker_result_lines[2]
                if len(worker_result_lines) >= 3
                else cache_plan[worker_index][1]
            )
            if cache_status == "hit":
                native_object_cache_hits += 1
            elif cache_status == "miss":
                native_object_cache_misses += 1
            else:
                native_object_cache_disabled += 1
            pairs.append((target_id, obj_path))
        cache_publish_t = profile_begin(profile)
        cache_publish_ok = publish_cache(
            worker_items,
            cache_plan,
            tmp_dir,
        )
        if cache_publish_ok:
            protected_cache_paths: list[str] = []
            for cache_path, cache_status in cache_plan:
                if cache_path and cache_status in ("hit", "miss"):
                    protected_cache_paths.append(cache_path)
            maintain_cache(protected_cache_paths)
        profile_end(profile, "link_self_native_object_cache_publish", cache_publish_t)
        profile_counter(
            profile,
            "link_self_native_object_cache_hits",
            native_object_cache_hits,
        )
        profile_counter(
            profile,
            "link_self_native_object_cache_misses",
            native_object_cache_misses,
        )
        profile_counter(
            profile,
            "link_self_native_object_cache_disabled",
            native_object_cache_disabled,
        )
        profile_counter(
            profile,
            "link_self_native_object_cache_publish_ok",
            1 if cache_publish_ok else 0,
        )
    else:
        for object_index, ir_text in enumerate(inputs):
            asm_path = str(
                os.path.join(tmp_dir, f"self_backend_native_{object_index}.s")
            )
            obj_path = str(os.path.join(
                tmp_dir,
                "self_backend_native_"
                + small_int_decimal(object_index)
                + (".s" if internal_link else ".o"),
            ))
            native_result = emit_in_process(ir_text)
            if native_result is None:
                return None
            target_id = native_result[0]
            asm_text = native_result[1]
            with open(asm_path, "w", encoding="utf-8") as f:
                f.write(asm_text)
            if not internal_link:
                subprocess.run([cc, "-c", asm_path, "-o", obj_path], check=True)
            else:
                obj_path = asm_path
            pairs.append((target_id, obj_path))
            # Source-mode stage1 emits hundreds of shards in this process.
            if object_index % 4 == 3:
                gc.collect()
    profiled_gc_collect(
        profile,
        "link_self_emit_objects_collect",
        allocations_owned_by_current_process=not bool(worker_command_prefix),
    )
    profile_end(profile, "link_self_emit_objects_native", t)
    profile_counter(profile, "link_self_native_object_count", len(pairs))
    profile_counter(
        profile,
        "link_self_native_object_fastpath_inputs",
        len(pairs) if internal_link else 0,
    )
    return pairs


def emit_objects_many_via_host_python(
    ir_texts: list[str],
    tmp_dir: str,
    cc: str,
    *,
    split_large_modules: bool = False,
    profile: Optional[dict] = None,
    internal_link: bool = False,
    emit_in_process_many,
    profile_begin,
    profile_end,
    split_threshold_bytes,
    jobs_for_count,
    host_python_command,
    host_many_code: str,
    pcc_source_root,
    small_int_decimal,
    profile_counter,
) -> list[tuple[str, str]]:
    native_results = emit_in_process_many(
        ir_texts,
        tmp_dir,
        cc,
        split_large_modules=split_large_modules,
        profile=profile,
        internal_link=internal_link,
    )
    if native_results is not None:
        return native_results
    ir_paths = []
    t = profile_begin(profile)
    for index, ir_text in enumerate(ir_texts):
        ir_path = str(os.path.join(tmp_dir, f"self_backend_input_{index}.ll"))
        with open(ir_path, "w", encoding="utf-8") as f:
            f.write(ir_text)
        ir_paths.append(ir_path)
    profile_end(profile, "link_self_write_object_inputs", t)

    t = profile_begin(profile)
    job_count_hint = len(ir_paths)
    if split_large_modules:
        threshold = split_threshold_bytes()
        for ir_text in ir_texts:
            if len(ir_text) >= threshold:
                job_count_hint = max(job_count_hint, os.cpu_count() or 1)
                break
    jobs = jobs_for_count(job_count_hint)
    host_py = host_python_command()
    result_path = str(os.path.join(tmp_dir, "self_backend_results.tsv"))
    profile_end(profile, "link_self_prepare_object_emit", t)
    try:
        t = profile_begin(profile)
        subprocess.run(
            [
                host_py,
                "-c",
                host_many_code,
                pcc_source_root(),
                small_int_decimal(jobs),
                cc,
                "1" if split_large_modules else "0",
                result_path,
                "1" if internal_link else "0",
            ]
            + ir_paths,
            check=True,
        )
        profile_end(profile, "link_self_object_emit_subprocess", t)
    except Exception as e:
        raise SelfBackendEmitError(f"self backend native emission failed: {e}") from e
    try:
        t = profile_begin(profile)
        with open(result_path, "r", encoding="utf-8") as f:
            out = f.read()
        profile_end(profile, "link_self_read_object_results", t)
    except OSError as e:
        raise SelfBackendEmitError(f"self backend native emission failed: {e}") from e

    parsed_results: list[tuple[int, str, str]] = []
    host_emit_sum_ms = 0
    host_emit_max_ms = 0
    host_cc_sum_ms = 0
    host_cc_max_ms = 0
    host_input_bytes = 0
    host_input_max_bytes = 0
    host_object_cache_hits = 0
    host_object_cache_misses = 0
    host_object_cache_disabled = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        idx_text = parts[0]
        target_id = parts[1]
        obj_path = parts[2]
        try:
            idx = int(idx_text)
        except ValueError:
            continue
        if idx < 0:
            continue
        if len(parts) >= 6:
            try:
                emit_ms = int(parts[3])
                cc_ms = int(parts[4])
                byte_len = int(parts[5])
            except ValueError:
                emit_ms = 0
                cc_ms = 0
                byte_len = 0
            host_emit_sum_ms += emit_ms
            host_cc_sum_ms += cc_ms
            host_input_bytes += byte_len
            if byte_len > host_input_max_bytes:
                host_input_max_bytes = byte_len
            if emit_ms > host_emit_max_ms:
                host_emit_max_ms = emit_ms
            if cc_ms > host_cc_max_ms:
                host_cc_max_ms = cc_ms
        if len(parts) >= 7:
            cache_status = parts[6]
            if cache_status == "hit":
                host_object_cache_hits += 1
            elif cache_status == "miss":
                host_object_cache_misses += 1
            elif cache_status == "off":
                host_object_cache_disabled += 1
        parsed_results.append((idx, target_id, obj_path))

    if not parsed_results:
        raise SelfBackendEmitError(
            "self backend native emission failed: missing module result"
        )
    pairs: list[tuple[str, str]] = []
    for _idx, target_id, obj_path in parsed_results:
        pairs.append((target_id, obj_path))
    profile_counter(
        profile,
        "link_self_host_emit_asm_sum_ms",
        host_emit_sum_ms,
    )
    profile_counter(
        profile,
        "link_self_host_emit_asm_max_ms",
        host_emit_max_ms,
    )
    profile_counter(profile, "link_self_host_cc_sum_ms", host_cc_sum_ms)
    profile_counter(profile, "link_self_host_cc_max_ms", host_cc_max_ms)
    profile_counter(profile, "link_self_host_input_bytes", host_input_bytes)
    profile_counter(
        profile,
        "link_self_host_input_max_bytes",
        host_input_max_bytes,
    )
    profile_counter(profile, "link_self_host_object_count", len(pairs))
    profile_counter(profile, "link_self_host_jobs", jobs)
    profile_counter(
        profile,
        "link_self_host_object_cache_hits",
        host_object_cache_hits,
    )
    profile_counter(
        profile,
        "link_self_host_object_cache_misses",
        host_object_cache_misses,
    )
    profile_counter(
        profile,
        "link_self_host_object_cache_disabled",
        host_object_cache_disabled,
    )
    profile_counter(
        profile,
        "link_self_native_object_fastpath_inputs",
        len(pairs) if internal_link else 0,
    )
    return pairs

