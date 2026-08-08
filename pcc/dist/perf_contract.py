"""Collective throughput measurement contract (DIST-P1-THROUGHPUT-SCALING).

Defines HOW dist throughput/scaling numbers may be produced and labeled — a
correctness pass is never a performance claim, and a process-isolated label is
impossible to mint without complete spawned-rank gate summaries.

The record shape is machine-readable and mode-labeled:

  schema: pcc-dist-collective-perf-v1
  mode:   localhost-single-host | localhost-multiprocess (fail-closed below)
  vectors: one entry per (payload_bytes, world_size) with warmup/sample
           counts, p50/p95 wall latency, and effective ring-allreduce
           bandwidth — machine-dependent VALUES are never asserted by gates;
           gates assert shape, labels, and internal consistency only.
"""
from __future__ import annotations

from typing import Sequence

SCHEMA = "pcc-dist-collective-perf-v1"
MODE_LOCALHOST = "localhost-single-host"
MODE_PROCESS_ISOLATED = "localhost-multiprocess"

MIN_WARMUP_ROUNDS = 3
MIN_SAMPLE_ROUNDS = 10


class PerfContractError(ValueError):
    """A measurement record violated the throughput contract."""


def ring_allreduce_effective_bandwidth(
    payload_bytes: int, world_size: int, latency_s: float
) -> float:
    """Ring-allreduce effective bandwidth for one collective round.

    Standard ring model: each rank sends/receives 2*(world-1)/world of the
    payload; effective bandwidth normalizes the measured wall latency by that
    traffic so numbers are comparable across world sizes.
    """
    if payload_bytes <= 0 or world_size < 2 or latency_s <= 0:
        raise PerfContractError(
            "bandwidth needs payload_bytes>0, world_size>=2, latency>0"
        )
    traffic = payload_bytes * 2 * (world_size - 1) / world_size
    return traffic / latency_s


def build_vector_entry(
    *,
    payload_bytes: int,
    world_size: int,
    warmup_rounds: int,
    sample_latencies_s: Sequence[float],
) -> dict[str, object]:
    """Validate one (payload, world) measurement into a contract entry."""
    if warmup_rounds < MIN_WARMUP_ROUNDS:
        raise PerfContractError(
            f"warmup_rounds {warmup_rounds} < required {MIN_WARMUP_ROUNDS}"
        )
    samples = [float(s) for s in sample_latencies_s]
    if len(samples) < MIN_SAMPLE_ROUNDS:
        raise PerfContractError(
            f"sample count {len(samples)} < required {MIN_SAMPLE_ROUNDS}"
        )
    if any(s <= 0 for s in samples):
        raise PerfContractError("latency samples must be positive")
    ordered = sorted(samples)
    p50 = ordered[len(ordered) // 2]
    p95 = ordered[min(len(ordered) - 1, (len(ordered) * 95) // 100)]
    if p50 > p95:
        raise PerfContractError("p50 must be <= p95")
    return {
        "payload_bytes": int(payload_bytes),
        "world_size": int(world_size),
        "warmup_rounds": int(warmup_rounds),
        "samples": len(samples),
        "p50_latency_s": p50,
        "p95_latency_s": p95,
        "effective_bandwidth_bytes_per_s": ring_allreduce_effective_bandwidth(
            payload_bytes, world_size, p50
        ),
    }


def build_perf_record(
    *,
    mode: str,
    vectors: Sequence[dict[str, object]],
    process_summaries: Sequence[dict[str, object]] = (),
) -> dict[str, object]:
    """Assemble a labeled record; process isolation is fail-closed.

    A ``localhost-multiprocess`` record requires one passing ``spawn``
    summary for every rank in every declared world size, with distinct OS
    process IDs. A caller cannot relabel in-process or incomplete samples.
    """
    if mode not in (MODE_LOCALHOST, MODE_PROCESS_ISOLATED):
        raise PerfContractError(f"unknown mode label: {mode!r}")
    if not vectors:
        raise PerfContractError("a perf record needs at least one vector")
    if mode == MODE_PROCESS_ISOLATED:
        required_worlds = {int(vector.get("world_size", 0)) for vector in vectors}
        if any(world < 2 for world in required_worlds):
            raise PerfContractError(
                "process-isolated vectors require world_size >= 2"
            )
        for world_size in required_worlds:
            accepted = [
                summary
                for summary in process_summaries
                if int(summary.get("world_size", 0)) == world_size
                and summary.get("strict_gate_passed") is True
                and summary.get("isolation") == "spawn"
            ]
            ranks = {int(summary.get("rank", -1)) for summary in accepted}
            pids = {int(summary.get("pid", 0)) for summary in accepted}
            if ranks != set(range(world_size)) or len(pids) != world_size or 0 in pids:
                raise PerfContractError(
                    "process-isolated label requires passing spawned summaries "
                    f"for every rank with distinct pids at world_size {world_size}"
                )
    return {
        "schema": SCHEMA,
        "mode": mode,
        "vectors": list(vectors),
    }


__all__ = [
    "SCHEMA",
    "MODE_LOCALHOST",
    "MODE_PROCESS_ISOLATED",
    "MIN_WARMUP_ROUNDS",
    "MIN_SAMPLE_ROUNDS",
    "PerfContractError",
    "ring_allreduce_effective_bandwidth",
    "build_vector_entry",
    "build_perf_record",
]
