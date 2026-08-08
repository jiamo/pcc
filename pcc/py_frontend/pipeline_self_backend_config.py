"""Self-backend worker budgets and large-module splitting policy."""

from __future__ import annotations

import os

from .pipeline_ir_split import split_self_backend_ir_module_for_object_shards
from .pipeline_pass_config import (
    parallel_cpu_budget,
    positive_int_env,
    small_int_decimal,
)


# Emit-worker default.  This was 2 while a compiled emitter needed several GiB
# for one multi-megabyte module; with the dynamic-method-call reference leaks and
# the O(n**2) keyed sort in stack-map planning both fixed, a measured cold stage1
# peaks at 1.27 GB across ALL emit workers on a 96 GB host.  The frontend has
# always used ~10 here, and the emit lane was the only phase still 12x off its
# recorded baseline (434 s vs 35 s).  Measured cold stage1, fresh cache identity:
#
#     emit jobs   2 -> 589 s   (emit phase 434 s)
#     emit jobs   8 -> 404 s   (emit phase 272 s, peak 1.27 GB)
#     emit jobs  16 -> 406 s   (emit phase 272 s, peak 1.19 GB)
#
# 16 buys nothing over 8: at peak only 3 workers are alive at once, so past 8 the
# limit is elsewhere, not the worker budget.  Raise this only with a measurement
# that shows both a shorter emit phase and a peak the host can hold.
SELF_BACKEND_DEFAULT_JOBS = 8
SELF_BACKEND_JOBS_ENV = "PCC_SELF_BACKEND_JOBS"
# How many >=threshold modules may be emitted concurrently.  See
# jobs_for_input_sizes for the measurement behind this.
LARGE_INPUT_CONCURRENCY = 4
SELF_BACKEND_SKIP_LL_TEMP_ENV = "PCC_SELF_BACKEND_SKIP_LL_TEMP"
SELF_BACKEND_SPLIT_LARGE_MODULES_ENV = "PCC_SELF_BACKEND_SPLIT_LARGE_MODULES"
SELF_BACKEND_SPLIT_THRESHOLD_BYTES_ENV = "PCC_SELF_BACKEND_SPLIT_THRESHOLD_BYTES"
SELF_BACKEND_SPLIT_SHARD_BYTES_ENV = "PCC_SELF_BACKEND_SPLIT_SHARD_BYTES"


def jobs(n_modules: int) -> int:
    n_modules = int(n_modules)
    if n_modules <= 1:
        return 1
    raw = str(os.environ.get(SELF_BACKEND_JOBS_ENV, "") or "").strip()
    if raw:
        try:
            selected = int(raw)
        except ValueError:
            selected = 1
        return max(1, min(n_modules, selected))
    cpu_count = parallel_cpu_budget()
    return max(1, min(n_modules, cpu_count, SELF_BACKEND_DEFAULT_JOBS))


def jobs_for_ir_texts(ir_texts, *, native_worker: bool) -> int:
    return jobs_for_input_sizes(
        [len(str(ir_text)) for ir_text in ir_texts],
        native_worker=native_worker,
    )


def jobs_for_input_sizes(input_sizes, *, native_worker: bool) -> int:
    selected = jobs(len(input_sizes))
    if selected <= 1 or not native_worker:
        return selected
    raw = str(os.environ.get(SELF_BACKEND_JOBS_ENV, "") or "").strip()
    if raw:
        return selected
    threshold = split_threshold_bytes()
    large_inputs = 0
    for input_bytes in input_sizes:
        if int(input_bytes) >= threshold:
            large_inputs += 1
    if not large_inputs:
        return selected
    # Bound the lane by how many large modules can be in flight at once, not by
    # collapsing the whole batch to one worker.  The old rule returned 1 as soon
    # as ANY input crossed the threshold, so a single large module serialized all
    # 525 objects of a cold stage1 -- 434 s in the emit phase against a recorded
    # 35 s baseline.  That rule was written when one multi-megabyte module could
    # take several GiB; a measured cold stage1 now peaks at 1.27 GB across every
    # worker combined.  Keeping a cap (rather than removing it) preserves the
    # protection for a host with genuinely little memory and for a future
    # regression that makes a single module expensive again.
    return max(1, min(selected, LARGE_INPUT_CONCURRENCY))


def skip_ll_temp() -> bool:
    normalized = str(
        os.environ.get(SELF_BACKEND_SKIP_LL_TEMP_ENV, "") or ""
    ).strip().lower()
    if normalized in ("0", "false", "no", "off", "legacy"):
        return False
    if normalized in ("1", "true", "yes", "on"):
        return True
    return True


def split_large_modules_enabled() -> bool:
    normalized = str(
        os.environ.get(SELF_BACKEND_SPLIT_LARGE_MODULES_ENV, "") or ""
    ).strip().lower()
    if normalized in ("0", "false", "no", "off", "legacy"):
        return False
    return True


def split_threshold_bytes() -> int:
    return positive_int_env(
        SELF_BACKEND_SPLIT_THRESHOLD_BYTES_ENV,
        2_000_000,
    )


def split_shard_bytes() -> int:
    return positive_int_env(
        SELF_BACKEND_SPLIT_SHARD_BYTES_ENV,
        1_000_000,
    )


def split_large_ir_modules(ir_texts: list[str]) -> list[str]:
    if not split_large_modules_enabled():
        return ir_texts
    threshold = split_threshold_bytes()
    shard_bytes = split_shard_bytes()
    out: list[str] = []
    for index, text in enumerate(ir_texts):
        text = str(text)
        if len(text) < threshold:
            out.append(text)
            continue
        shards = split_self_backend_ir_module_for_object_shards(
            text,
            export_prefix="__pco" + small_int_decimal(index) + "_",
            shard_bytes=shard_bytes,
        )
        out.extend(shards)
    return out
