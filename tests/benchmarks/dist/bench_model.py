"""P-P0-DIST-BENCH: data model for the metadata-only distributed bench harness.

This module is the *shape* of a distributed-benchmark run over the existing
``pcc.dist`` metadata oracles. It is deliberately measurement-only:

    * ``single-process`` is the ONLY mode that produces real (logical)
      measurements — and even those are latency-free op / logical-step COUNTS
      over the ``pcc.dist.collective`` oracle and the ``pcc.dist.kv``
      ``BlockManager`` surrogate. There is NO wall-clock timing, NO throughput,
      NO scaling curve, and NO speedup number anywhere in this harness.
    * every networking mode (``local-process``, ``localhost-tcp-ring``,
      ``multi-mac-tcp-ring``, ``quic``, ``jaccl-rdma``) and every upper-layer
      workload placeholder (``minimind-train-smoke``, ``vllm-kv-surrogate``)
      resolves to a ``SKIPPED_WITH_REASON`` result naming the exact missing
      capability. Nothing is silently skipped.

Claim boundary (read before citing any number this harness emits):

    A ``MEASURED`` result is a mode-labeled *logical count* (collective element
    ops, KV block/logical-step counts) produced in ONE process on CPU with NO
    sockets. It is NOT a latency, NOT a throughput, NOT a scaling result, and
    proves NOTHING about multi-process, multi-Mac, QUIC, RDMA, secure cluster
    admission, MiniMind training, or vLLM serving. Those require real hardware
    runs carrying an exact topology + security-mode label, which this harness
    does not have and therefore reports as SKIPPED_WITH_REASON.

Standalone-importable: ``import tests.benchmarks.dist.bench_model``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

# --------------------------------------------------------------------------
# Mode taxonomy
# --------------------------------------------------------------------------
# The single measurable mode. Everything else is a skip in this slice.
MODE_SINGLE_PROCESS = "single-process"

# Networking modes — all unavailable (no sockets are opened by this harness).
MODE_LOCAL_PROCESS = "local-process"
MODE_LOCALHOST_TCP_RING = "localhost-tcp-ring"
MODE_MULTI_MAC_TCP_RING = "multi-mac-tcp-ring"
MODE_QUIC = "quic"
MODE_JACCL_RDMA = "jaccl-rdma"

# Upper-layer workload placeholders — unavailable (no framework / no serving).
MODE_MINIMIND_TRAIN_SMOKE = "minimind-train-smoke"
MODE_VLLM_KV_SURROGATE = "vllm-kv-surrogate"

# The full ordered taxonomy the harness knows about.
ALL_MODES: tuple[str, ...] = (
    MODE_SINGLE_PROCESS,
    MODE_LOCAL_PROCESS,
    MODE_LOCALHOST_TCP_RING,
    MODE_MULTI_MAC_TCP_RING,
    MODE_QUIC,
    MODE_JACCL_RDMA,
    MODE_MINIMIND_TRAIN_SMOKE,
    MODE_VLLM_KV_SURROGATE,
)

# Modes that can, in this slice, actually produce logical measurements.
MEASURABLE_MODES: frozenset[str] = frozenset({MODE_SINGLE_PROCESS})

# Result status vocabulary. Kept as plain strings so a manifest round-trips
# through JSON without custom encoders (mirrors pcc.dist.results).
STATUS_MEASURED = "MEASURED"
STATUS_SKIPPED = "SKIPPED_WITH_REASON"

_VALID_STATUS = frozenset({STATUS_MEASURED, STATUS_SKIPPED})

# The exact missing-capability reason for every non-measurable mode. These are
# mode-labeled sentences (never empty) — the taxonomy's whole point is that a
# skip says *what capability is missing*, not merely "not supported".
SKIP_REASONS: dict[str, str] = {
    MODE_LOCAL_PROCESS: (
        "local-process fan-out unavailable: this harness spawns no worker "
        "processes and opens no IPC channel; only pcc.dist single-process "
        "oracles run"
    ),
    MODE_LOCALHOST_TCP_RING: (
        "localhost tcp-ring unavailable: no TCP sockets are opened; "
        "pcc.dist.transport reports tcp-ring as not implemented in this "
        "local-only slice"
    ),
    MODE_MULTI_MAC_TCP_RING: (
        "multi-mac tcp-ring unavailable: no Bonjour/Network.framework discovery "
        "and no cross-host sockets; a real multi-Mac run needs an exact "
        "topology + security-mode label this harness cannot produce"
    ),
    MODE_QUIC: (
        "quic unavailable: no QUIC/Network.framework transport is implemented; "
        "pcc.dist.transport reports quic as a later gated mode"
    ),
    MODE_JACCL_RDMA: (
        "jaccl-rdma unavailable: no Thunderbolt-RDMA backend; JACCL also "
        "requires fully-connected topology and macOS Recovery RDMA enablement "
        "even once landed"
    ),
    MODE_MINIMIND_TRAIN_SMOKE: (
        "minimind-train-smoke unavailable: no PyTorch/MLX/pcc-native tensor "
        "training and no multi-worker transport; this is a placeholder for a "
        "future real hardware training smoke run"
    ),
    MODE_VLLM_KV_SURROGATE: (
        "vllm-kv-surrogate unavailable as a serving benchmark: no GPU, no vLLM "
        "engine, no real KV cache memory; the pcc.dist.kv BlockManager only "
        "models block bookkeeping, which single-process measures as logical "
        "counts (not a serving throughput claim)"
    ),
}


class BenchError(Exception):
    """Raised for an unknown mode, invalid status, or malformed manifest blob."""


def is_measurable(mode: str) -> bool:
    return mode in MEASURABLE_MODES


def skip_reason_for(mode: str) -> str:
    """The exact missing-capability reason for a non-measurable mode."""
    if mode not in SKIP_REASONS:
        raise BenchError(
            f"no skip reason for mode {mode!r}; known skips: {sorted(SKIP_REASONS)}"
        )
    return SKIP_REASONS[mode]


@dataclass(frozen=True)
class BenchResult:
    """The outcome of running one mode of the harness.

    ``status`` is :data:`STATUS_MEASURED` or :data:`STATUS_SKIPPED`. When
    measured, ``metrics`` holds mode-labeled *logical counts* (never a
    wall-clock number); ``reason`` is empty. When skipped, ``metrics`` is empty
    and ``reason`` is a non-empty, mode-labeled sentence naming the missing
    capability.
    """

    mode: str
    status: str
    reason: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUS:
            raise BenchError(
                f"invalid status {self.status!r}; expected one of {sorted(_VALID_STATUS)}"
            )
        if self.status == STATUS_SKIPPED:
            if not self.reason.strip():
                raise BenchError(
                    f"mode {self.mode!r} is SKIPPED_WITH_REASON but reason is empty"
                )
            if self.metrics:
                raise BenchError(
                    f"skipped mode {self.mode!r} must carry no metrics, got {dict(self.metrics)}"
                )
        if self.status == STATUS_MEASURED:
            if self.reason:
                raise BenchError(
                    f"measured mode {self.mode!r} must not carry a skip reason, got {self.reason!r}"
                )
            # Guard the claim boundary: no wall-clock / throughput metric may be
            # smuggled into a MEASURED result. Only logical counts are allowed.
            banned = {"seconds", "ms", "latency", "throughput", "ops_per_sec",
                      "tokens_per_sec", "wall_clock", "speedup", "gbps"}
            leaked = banned & {str(k).lower() for k in self.metrics}
            if leaked:
                raise BenchError(
                    f"measured mode {self.mode!r} leaks a timing/throughput metric "
                    f"{sorted(leaked)}; this harness emits logical counts only"
                )

    @property
    def measured(self) -> bool:
        return self.status == STATUS_MEASURED

    @property
    def skipped(self) -> bool:
        return self.status == STATUS_SKIPPED

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "reason": self.reason,
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchResult":
        return cls(
            mode=str(data["mode"]),
            status=str(data["status"]),
            reason=str(data.get("reason", "")),
            metrics=dict(data.get("metrics", {})),
        )


def measured(mode: str, **metrics: Any) -> BenchResult:
    return BenchResult(mode, STATUS_MEASURED, metrics=metrics)


def skipped(mode: str, reason: str) -> BenchResult:
    return BenchResult(mode, STATUS_SKIPPED, reason=reason)


_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class BenchManifest:
    """A full harness run: the mode taxonomy plus one result per mode.

    Round-trips stably through JSON (``to_json`` / ``from_json``). The manifest
    is the auditable artifact: a reader can see, for every mode, whether it was
    MEASURED (with logical counts) or SKIPPED_WITH_REASON (with the exact
    missing capability).
    """

    harness: str
    results: tuple[BenchResult, ...]
    version: int = _MANIFEST_VERSION
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.harness:
            raise BenchError("manifest harness name must be non-empty")
        seen = [r.mode for r in self.results]
        if len(seen) != len(set(seen)):
            raise BenchError(f"manifest has duplicate mode results: {seen}")

    def result_for(self, mode: str) -> BenchResult:
        for r in self.results:
            if r.mode == mode:
                return r
        raise BenchError(f"no result for mode {mode!r} in manifest")

    def measured_modes(self) -> tuple[str, ...]:
        return tuple(r.mode for r in self.results if r.measured)

    def skipped_modes(self) -> tuple[str, ...]:
        return tuple(r.mode for r in self.results if r.skipped)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "harness": self.harness,
            "detail": dict(self.detail),
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchManifest":
        version = int(data.get("version", _MANIFEST_VERSION))
        if version != _MANIFEST_VERSION:
            raise BenchError(
                f"unsupported manifest version {version!r}; expected {_MANIFEST_VERSION}"
            )
        results = tuple(BenchResult.from_dict(r) for r in data.get("results", []))
        return cls(
            harness=str(data["harness"]),
            results=results,
            version=version,
            detail=dict(data.get("detail", {})),
        )

    @classmethod
    def from_json(cls, blob: str) -> "BenchManifest":
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise BenchError(f"manifest blob is not valid JSON: {exc}") from exc
        return cls.from_dict(data)
