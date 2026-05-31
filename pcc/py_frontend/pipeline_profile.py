"""Phase-level profiling helpers for the Python frontend pipeline.

This is the real profiling substrate used by CLI wrappers: it records the
phase names required by the roadmap without forcing a flag-day rewrite of
``pipeline.py``. Callers can wrap individual phase callables or the whole
``compile_python`` entry while the pipeline is gradually instrumented inside.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pcc.profile_events import ProfileRecorder

ROADMAP_PHASES = (
    "parse",
    "type_infer",
    "source_closure",
    "ir_generation",
    "ir_verify",
    "optimization_passes",
    "self_backend_lower",
    "register_allocation",
    "assembly_emit",
    "object_emit",
    "link",
    "runtime_archive",
    "stdlib_resolution",
    "subprocess",
)


@dataclass(frozen=True)
class PhaseResult:
    name: str
    value: Any


def run_profiled_phase(recorder: ProfileRecorder, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> PhaseResult:
    if name not in ROADMAP_PHASES:
        recorder.increment("unknown_phase")
    with recorder.phase(name, metadata={"argc": len(args), "kwargs": sorted(kwargs)}):
        value = fn(*args, **kwargs)
    return PhaseResult(name=name, value=value)


def seed_expected_phase_counters(recorder: ProfileRecorder) -> None:
    for name in ROADMAP_PHASES:
        recorder.increment("phase.expected." + name, 0)
