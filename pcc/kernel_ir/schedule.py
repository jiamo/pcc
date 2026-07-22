"""Replayable owner-neutral schedules for PCC Kernel IR.

Kernel IR describes what a device kernel means.  This module describes a
checked transformation of that payload before the TIRx freeze.  Schedule plans
are content-addressed and bind to the exact input Kernel IR, target, function,
and previous binding so that replay against stale payload IR fails closed.

The first finite schedule instruction is Metal thread binding.  Tiling,
layouts, software pipelines, and autotuning require their own typed
instructions; they must not be smuggled through an unvalidated attributes map.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from pcc.kernel_ir.ir import KernelModule, validate_kernel

KERNEL_SCHEDULE_SCHEMA = "pcc-kernel-schedule-v1"
METAL_MAX_THREADS_PER_THREADGROUP = 1024


class KernelScheduleError(ValueError):
    """A schedule plan or replay violated the checked schedule contract."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _target_kind(target: str) -> str:
    normalized = str(target).strip().lower()
    if not normalized:
        raise KernelScheduleError("schedule target must not be empty")
    return normalized.split(":", 1)[0]


def kernel_ir_sha256(module: KernelModule) -> str:
    """Return the canonical semantic Kernel IR digest used by schedule guards."""
    validate_kernel(module)
    return _sha256(module.to_dict())


@dataclass(frozen=True)
class BindThreads:
    """Bind one kernel function to a Metal threadgroup width.

    ``expected_threads`` is an explicit replay guard, not documentation.  A
    changed payload must produce a diagnostic rather than silently applying a
    previously chosen schedule to a different launch shape.
    """

    function: str
    expected_threads: int
    threads: int

    def __post_init__(self) -> None:
        if not isinstance(self.function, str) or not self.function:
            raise KernelScheduleError("thread binding requires a function name")
        for label, value in (
            ("expected thread count", self.expected_threads),
            ("thread count", self.threads),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise KernelScheduleError(f"{label} must be an integer")
        if self.expected_threads < 0:
            raise KernelScheduleError("expected thread count must be non-negative")
        if not 1 <= self.threads <= METAL_MAX_THREADS_PER_THREADGROUP:
            raise KernelScheduleError(
                "Metal thread count must be in "
                f"[1, {METAL_MAX_THREADS_PER_THREADGROUP}], got {self.threads}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "bind_threads",
            "function": self.function,
            "expected_threads": self.expected_threads,
            "threads": self.threads,
        }


@dataclass(frozen=True)
class KernelSchedule:
    """A versioned transformation plan bound to one semantic Kernel IR input."""

    target: str
    input_kernel_ir_sha256: str
    steps: tuple[BindThreads, ...]
    schema: str = KERNEL_SCHEDULE_SCHEMA

    def __post_init__(self) -> None:
        target = _target_kind(self.target)
        if target != "metal":
            raise KernelScheduleError(
                f"schedule v1 supports only target 'metal', got {target!r}"
            )
        if self.target != target:
            raise KernelScheduleError(
                f"schedule target must be canonical {target!r}, got {self.target!r}"
            )
        if self.schema != KERNEL_SCHEDULE_SCHEMA:
            raise KernelScheduleError(
                f"unsupported schedule schema {self.schema!r}; "
                f"expected {KERNEL_SCHEDULE_SCHEMA!r}"
            )
        if len(self.input_kernel_ir_sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in self.input_kernel_ir_sha256
        ):
            raise KernelScheduleError(
                "schedule input_kernel_ir_sha256 must be a lowercase SHA-256 digest"
            )
        if not isinstance(self.steps, tuple) or not self.steps:
            raise KernelScheduleError("schedule must contain at least one step")
        functions: set[str] = set()
        for step in self.steps:
            if not isinstance(step, BindThreads):
                raise KernelScheduleError(
                    f"schedule v1 does not support step {type(step).__name__!r}"
                )
            if step.function in functions:
                raise KernelScheduleError(
                    f"duplicate schedule selector for function {step.function!r}"
                )
            functions.add(step.function)

    @classmethod
    def for_module(
        cls,
        module: KernelModule,
        *,
        target: str,
        steps: tuple[BindThreads, ...],
    ) -> KernelSchedule:
        return cls(
            target=_target_kind(target),
            input_kernel_ir_sha256=kernel_ir_sha256(module),
            steps=steps,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "target": self.target,
            "input_kernel_ir_sha256": self.input_kernel_ir_sha256,
            "steps": [step.to_dict() for step in self.steps],
        }

    @property
    def sha256(self) -> str:
        return _sha256(self.to_dict())


@dataclass(frozen=True)
class AppliedKernelSchedule:
    """The validated payload and deterministic trace produced by one replay."""

    module: KernelModule
    schedule_sha256: str
    input_kernel_ir_sha256: str
    output_kernel_ir_sha256: str
    trace: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_sha256": self.schedule_sha256,
            "input_kernel_ir_sha256": self.input_kernel_ir_sha256,
            "output_kernel_ir_sha256": self.output_kernel_ir_sha256,
            "trace": [dict(record) for record in self.trace],
        }


def apply_kernel_schedule(
    module: KernelModule,
    schedule: KernelSchedule,
    *,
    target: str,
) -> AppliedKernelSchedule:
    """Replay *schedule* on *module* or fail before TIRx freeze."""
    validate_kernel(module)
    target_kind = _target_kind(target)
    if target_kind != schedule.target:
        raise KernelScheduleError(
            f"schedule target {schedule.target!r} does not match requested "
            f"target {target_kind!r}"
        )

    input_digest = kernel_ir_sha256(module)
    if input_digest != schedule.input_kernel_ir_sha256:
        raise KernelScheduleError(
            "schedule input Kernel IR digest is stale: "
            f"expected {schedule.input_kernel_ir_sha256}, got {input_digest}"
        )

    funcs = list(module.funcs)
    trace: list[dict[str, Any]] = []
    for step in schedule.steps:
        matches = [
            index for index, func in enumerate(funcs) if func.name == step.function
        ]
        if len(matches) != 1:
            raise KernelScheduleError(
                f"schedule function selector {step.function!r} matched "
                f"{len(matches)} functions"
            )
        index = matches[0]
        func = funcs[index]
        if func.threads != step.expected_threads:
            raise KernelScheduleError(
                f"schedule expected function {step.function!r} to have "
                f"{step.expected_threads} threads, got {func.threads}"
            )
        funcs[index] = replace(func, threads=step.threads)
        trace.append(
            {
                "kind": "bind_threads",
                "function": step.function,
                "before": func.threads,
                "after": step.threads,
            }
        )

    scheduled = replace(module, funcs=tuple(funcs))
    validate_kernel(scheduled)
    return AppliedKernelSchedule(
        module=scheduled,
        schedule_sha256=schedule.sha256,
        input_kernel_ir_sha256=input_digest,
        output_kernel_ir_sha256=kernel_ir_sha256(scheduled),
        trace=tuple(trace),
    )


__all__ = [
    "KERNEL_SCHEDULE_SCHEMA",
    "METAL_MAX_THREADS_PER_THREADGROUP",
    "AppliedKernelSchedule",
    "BindThreads",
    "KernelSchedule",
    "KernelScheduleError",
    "apply_kernel_schedule",
    "kernel_ir_sha256",
]
