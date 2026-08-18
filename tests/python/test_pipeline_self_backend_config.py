"""Focused ownership contracts for self-backend pipeline configuration."""

from __future__ import annotations

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_self_backend_config as config


def test_pipeline_self_backend_config_facade_has_one_owner():
    assert pipeline._self_backend_jobs is config.jobs
    assert pipeline._self_backend_jobs_for_ir_texts is config.jobs_for_ir_texts
    assert pipeline._self_backend_jobs_for_input_sizes is config.jobs_for_input_sizes
    assert pipeline._self_backend_skip_ll_temp is config.skip_ll_temp
    assert (
        pipeline._self_backend_split_large_modules_enabled
        is config.split_large_modules_enabled
    )
    assert pipeline._self_backend_split_threshold_bytes is config.split_threshold_bytes
    assert pipeline._self_backend_split_shard_bytes is config.split_shard_bytes
    assert pipeline._split_self_backend_large_ir_modules is config.split_large_ir_modules


def test_native_large_inputs_are_capped_not_serialized(monkeypatch):
    """A large input bounds the lane; it no longer collapses the whole batch.

    The old rule returned 1 as soon as ANY input crossed the split threshold,
    so one large module serialized every object in the batch -- measured at
    434 s in a cold stage1 emit phase against a 35 s baseline.  The cap is kept
    so a host with little memory, or a future regression that makes a single
    module expensive again, is still protected; only the collapse to 1 is gone.
    """
    monkeypatch.delenv(config.SELF_BACKEND_JOBS_ENV, raising=False)
    monkeypatch.delenv(config.SELF_BACKEND_SPLIT_THRESHOLD_BYTES_ENV, raising=False)

    many_inputs = [2_000_000] + [1] * 63
    unbounded = config.jobs_for_input_sizes(many_inputs, native_worker=False)
    bounded = config.jobs_for_input_sizes(many_inputs, native_worker=True)

    assert bounded <= config.COMPILED_NATIVE_SAFE_JOBS
    assert bounded >= 1
    # The cap must actually bind on a batch wide enough to want more workers,
    # otherwise this test would pass with the cap removed entirely.
    if unbounded > config.COMPILED_NATIVE_SAFE_JOBS:
        assert bounded == config.COMPILED_NATIVE_SAFE_JOBS
        assert bounded < unbounded

    # An explicit job count still wins over the cap.
    monkeypatch.setenv(config.SELF_BACKEND_JOBS_ENV, "2")
    assert config.jobs_for_input_sizes(
        [2_000_000, 1],
        native_worker=True,
    ) == 2


def test_compiled_native_small_inputs_do_not_inherit_stage1_width(monkeypatch):
    monkeypatch.delenv(config.SELF_BACKEND_JOBS_ENV, raising=False)
    monkeypatch.setattr(config, "parallel_cpu_budget", lambda: 12)

    assert config.jobs_for_input_sizes([1] * 20, native_worker=False) == 8
    assert config.jobs_for_input_sizes([1] * 20, native_worker=True) == 2
