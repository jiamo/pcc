from __future__ import annotations

import shlex
from pathlib import Path

from pcc.py_frontend.pipeline_self_backend_emit import emit_objects_many_in_process
from pcc.py_frontend.pipeline_self_backend_emit import run_emit_worker_pool


MANIFEST_VERSION = "pcc.self_backend.emit_batch.v1"


def _worker_items(tmp_path: Path, count: int):
    return [
        (
            str(tmp_path / f"result-{index}"),
            str(tmp_path / f"object-{index}"),
            str(tmp_path / f"input-{index}.ll"),
        )
        for index in range(count)
    ]


def _manifest_items(command: str):
    manifest = Path(shlex.split(command)[-1])
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert lines[0] == MANIFEST_VERSION
    payload = lines[1:]
    assert len(payload) % 4 == 0
    return [payload[index : index + 4] for index in range(0, len(payload), 4)]


def test_oversized_emit_items_get_one_fresh_process_each(tmp_path: Path) -> None:
    command_batches = []

    def record_commands(commands, max_parallel=None):
        command_batches.append((list(commands), max_parallel))

    process_count = run_emit_worker_pool(
        ["/tmp/pcc1"],
        _worker_items(tmp_path, 5),
        "",
        str(tmp_path),
        "oversized",
        1,
        batch_max_items=4,
        manifest_version=MANIFEST_VERSION,
        item_bytes=[13_000_000, 11_000_000, 6_000_000, 5_000_000, 4_000_000],
        worker_arg="--pcc-self-backend-emit-batch-worker",
        small_int_decimal=str,
        shell_quote_arg=shlex.quote,
        run_worker_commands=record_commands,
    )

    assert process_count == 5
    commands, max_parallel = command_batches[0]
    assert max_parallel == 1
    assert len(commands) == 5
    assert all(len(_manifest_items(command)) == 1 for command in commands)


def test_safe_emit_batches_are_balanced_by_input_bytes(tmp_path: Path) -> None:
    command_batches = []

    def record_commands(commands, max_parallel=None):
        command_batches.append((list(commands), max_parallel))

    items = _worker_items(tmp_path, 5)
    sizes = [10, 9, 8, 1, 1]
    size_by_input = {item[2]: size for item, size in zip(items, sizes)}
    process_count = run_emit_worker_pool(
        ["/tmp/pcc1"],
        items,
        "",
        str(tmp_path),
        "safe",
        3,
        batch_max_items=2,
        manifest_version=MANIFEST_VERSION,
        item_bytes=sizes,
        worker_arg="--pcc-self-backend-emit-batch-worker",
        small_int_decimal=str,
        shell_quote_arg=shlex.quote,
        run_worker_commands=record_commands,
    )

    assert process_count == 3
    commands, max_parallel = command_batches[0]
    assert max_parallel == 3
    loads = []
    for command in commands:
        loads.append(
            sum(size_by_input[item[0]] for item in _manifest_items(command))
        )
    assert sorted(loads) == [9, 10, 10]


def test_native_emitter_materializes_text_for_the_worker_owner(
    tmp_path: Path,
) -> None:
    ir_text = 'target triple = "arm64-apple-darwin23.6.0"\n'
    observed_paths = []

    def run_pool(
        _prefix,
        worker_items,
        _cc,
        _tmp_dir,
        _label,
        _jobs,
        _fresh,
        **_kwargs,
    ):
        for result_path, object_path, ir_path in worker_items:
            observed_paths.append(ir_path)
            assert Path(ir_path).read_text(encoding="utf-8") == ir_text
            Path(object_path).write_text("asm\n", encoding="utf-8")
            Path(result_path).write_text(
                "self-aarch64-darwin-v0\n" + object_path + "\noff\n",
                encoding="utf-8",
            )
        return len(worker_items)

    results = emit_objects_many_in_process(
        [ir_text],
        str(tmp_path),
        "cc",
        split_large_modules=False,
        profile=None,
        internal_link=True,
        parse_target_triple=lambda _text: "arm64-apple-darwin23.6.0",
        host_target_triple=lambda: "arm64-apple-darwin23.6.0",
        target_supported=lambda _triple: True,
        native_worker_executable=lambda: "/tmp/pcc1",
        split_large_ir_modules=lambda texts: texts,
        source_workers_worthwhile=lambda _texts: False,
        worker_command_prefix_for_frontend=lambda: [],
        split_threshold_bytes=lambda: 2_000_000,
        split_shard_bytes=lambda: 1_000_000,
        jobs_for_ir_texts=lambda _texts, **_kwargs: 1,
        profile_counter=lambda *_args: None,
        profiled_gc_collect=lambda *_args, **_kwargs: None,
        profile_begin=lambda _profile: 0,
        profile_end=lambda *_args: None,
        run_worker_commands=lambda *_args, **_kwargs: None,
        small_int_decimal=str,
        shell_quote_arg=shlex.quote,
        split_worker_arg="--split-worker",
        plan_cache=lambda items, *_args: [("", "off") for _item in items],
        jobs=lambda _count: 1,
        jobs_for_input_sizes=lambda _sizes, **_kwargs: 1,
        jobs_env="PCC_SELF_BACKEND_JOBS",
        run_emit_worker_pool=run_pool,
        publish_cache=lambda *_args: True,
        maintain_cache=lambda *_args: None,
        emit_in_process=lambda _text: (_ for _ in ()).throw(
            AssertionError("native path must use the worker adapter")
        ),
        join_strings=lambda values, sep: sep.join(values),
    )

    expected_path = str(tmp_path / "self_backend_module_0.ll")
    assert observed_paths == [expected_path]
    assert Path(expected_path).read_text(encoding="utf-8") == ir_text
    assert results == [
        ("self-aarch64-darwin-v0", str(tmp_path / "self_backend_native_0.s"))
    ]
