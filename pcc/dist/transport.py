"""D-P0-DIST-TRANSPORT: transport registry + signed-manifest oracle (local-only).

The research note is explicit that pcc must NOT copy TVM RPC's trusted-network
model: the default Apple-Silicon path is a TCP ring, with QUIC and
Thunderbolt-RDMA/JACCL as later optional modes, all gated behind capability
checks. This module lands the default *shells* without opening a socket.  The
separate explicit ``select_owner('tcp-ring', ...)`` route owns a
localhost-only multi-process ring; it is never selected by ``probe`` or
``open_channel``:

    * a transport registry mapping mode name -> :class:`TransportSpec`
    * ``probe(mode)`` returning ``AVAILABLE`` only for ``insecure-dev``
      (a local, in-process channel) and ``SKIPPED_WITH_REASON`` for every
      network mode (``bonjour``, ``tcp-ring``, ``quic``, ``jaccl-rdma``)
    * a signed-cluster-manifest parser/validator oracle: it checks structure,
      required fields, rank uniqueness/coverage, and a *toy deterministic*
      signature (HMAC-SHA256 over the canonical body). The signature is a
      structural oracle for round-trip and tamper-detection tests — it is NOT a
      real PKI / mTLS admission control, which is a later gate.

Standalone-importable: ``import pcc.dist.transport``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .results import (
    CapabilityResult,
    DistUnavailableError,
    available,
    skipped,
)


class ManifestError(Exception):
    """Raised when a cluster manifest is malformed, incomplete, or unsigned/tampered."""


@dataclass(frozen=True)
class TransportSpec:
    """Static description of a transport mode.

    ``available`` is False for every real-network mode in this slice; the
    ``reason`` explains why (mode-labeled). ``secure`` marks whether the mode,
    *when implemented*, would carry authenticated/encrypted admission — used to
    document (not enforce) the security boundary.
    """

    name: str
    available: bool
    secure: bool
    reason: str
    kind: str  # "local" | "socket" | "rdma"


# The registry. insecure-dev is the only AVAILABLE mode and it is a local,
# in-process loopback with NO authentication (hence secure=False, and callers
# must never treat it as a real cluster transport).
_REGISTRY: dict[str, TransportSpec] = {
    "insecure-dev": TransportSpec(
        "insecure-dev",
        available=True,
        secure=False,
        reason="local in-process loopback; no sockets, no authentication, dev-only",
        kind="local",
    ),
    "bonjour": TransportSpec(
        "bonjour",
        available=False,
        secure=True,
        reason="Bonjour/Network.framework discovery not implemented in this local-only slice",
        kind="socket",
    ),
    "tcp-ring": TransportSpec(
        "tcp-ring",
        available=False,
        secure=False,
        reason=(
            "TCP ring is disabled in the default local oracle; explicitly select "
            "the localhost-only owner to open sockets"
        ),
        kind="socket",
    ),
    "quic": TransportSpec(
        "quic",
        available=False,
        secure=True,
        reason="QUIC/Network.framework transport not implemented in this local-only slice",
        kind="socket",
    ),
    "jaccl-rdma": TransportSpec(
        "jaccl-rdma",
        available=False,
        secure=True,
        reason=(
            "JACCL Thunderbolt-RDMA not implemented; also requires fully-connected "
            "topology and macOS Recovery RDMA enablement even when landed"
        ),
        kind="rdma",
    ),
}


def registered_modes() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def spec_of(mode: str) -> TransportSpec:
    if mode not in _REGISTRY:
        raise ManifestError(f"unknown transport mode {mode!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[mode]


def probe(mode: str) -> CapabilityResult:
    """Probe one transport mode. Only ``insecure-dev`` reports AVAILABLE."""
    spec = spec_of(mode)
    if spec.available:
        return available(f"transport[{mode}]", kind=spec.kind, secure=spec.secure)
    return skipped(f"transport[{mode}]", spec.reason, kind=spec.kind, secure=spec.secure)


def probe_all() -> tuple[CapabilityResult, ...]:
    return tuple(probe(m) for m in _REGISTRY)


def open_channel(mode: str) -> str:
    """Open a transport. Only ``insecure-dev`` succeeds (returns a channel tag).

    Any network mode raises :class:`~pcc.dist.results.DistUnavailableError` so a
    caller cannot accidentally proceed as if a socket were connected.
    """
    result = probe(mode)
    result.raise_if_unavailable()
    return f"local-channel:{mode}"


def select_owner(
    requested_backend: str,
    manifest: "ClusterManifest",
    rank: int,
    **options: Any,
):
    """Select an explicit transport owner without ambient fallback.

    The default registry remains a local-only capability oracle.  Selecting
    ``tcp-ring`` is a separate opt-in action.  Its default remains localhost;
    the caller must explicitly pass ``allow_remote=True`` plus an admission
    key for non-loopback endpoints.  No other backend is substituted if the
    request cannot be honored.
    """
    if requested_backend != "tcp-ring":
        if requested_backend in _REGISTRY:
            result = probe(requested_backend)
            raise DistUnavailableError(result.capability, result.reason)
        raise ManifestError(
            f"unknown transport owner {requested_backend!r}; "
            "implemented owners: ['tcp-ring']"
        )
    from .tcp_transport import TCPRingOwner

    return TCPRingOwner(manifest, rank, **options)


def select_collective_owner(
    requested_backend: str,
    manifest: "ClusterManifest",
    rank: int,
    **options: Any,
):
    """Select a transport-backed collective owner with no backend fallback."""
    owner = select_owner(requested_backend, manifest, rank, **options)
    from .transport_collective import TCPCollectiveOwner

    return TCPCollectiveOwner(owner)


# --------------------------------------------------------------------------
# Signed cluster manifest oracle
# --------------------------------------------------------------------------
_MANIFEST_VERSION = 1
_REQUIRED_NODE_KEYS = frozenset({"rank", "host", "transport"})


@dataclass(frozen=True)
class ClusterNode:
    rank: int
    host: str
    transport: str

    def to_dict(self) -> dict[str, Any]:
        return {"rank": self.rank, "host": self.host, "transport": self.transport}


@dataclass(frozen=True)
class ClusterManifest:
    """A parsed, validated cluster manifest (identity/topology metadata only)."""

    cluster_id: str
    world_size: int
    nodes: tuple[ClusterNode, ...]
    version: int = _MANIFEST_VERSION

    def canonical_body(self) -> str:
        """Deterministic, signature-stable serialization of the manifest body."""
        payload = {
            "version": self.version,
            "cluster_id": self.cluster_id,
            "world_size": self.world_size,
            "nodes": [n.to_dict() for n in sorted(self.nodes, key=lambda x: x.rank)],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _validate_structure(cluster_id: str, world_size: int, nodes: Sequence[ClusterNode]) -> None:
    if not cluster_id:
        raise ManifestError("cluster_id must be non-empty")
    if world_size <= 0:
        raise ManifestError(f"world_size must be positive, got {world_size}")
    if len(nodes) != world_size:
        raise ManifestError(
            f"manifest declares world_size {world_size} but has {len(nodes)} nodes"
        )
    ranks = sorted(n.rank for n in nodes)
    if ranks != list(range(world_size)):
        raise ManifestError(
            f"node ranks must cover 0..{world_size - 1} exactly once, got {ranks}"
        )
    for node in nodes:
        if node.transport not in _REGISTRY:
            raise ManifestError(
                f"node rank {node.rank} names unknown transport {node.transport!r}"
            )
        if not node.host:
            raise ManifestError(f"node rank {node.rank} has empty host")


def build_manifest(
    cluster_id: str,
    nodes: Sequence[Mapping[str, Any] | ClusterNode],
) -> ClusterManifest:
    """Validate node metadata and build a :class:`ClusterManifest` (unsigned)."""
    parsed: list[ClusterNode] = []
    for n in nodes:
        if isinstance(n, ClusterNode):
            parsed.append(n)
            continue
        missing = _REQUIRED_NODE_KEYS - set(n)
        if missing:
            raise ManifestError(f"node missing keys {sorted(missing)}: {dict(n)}")
        parsed.append(ClusterNode(int(n["rank"]), str(n["host"]), str(n["transport"])))
    _validate_structure(cluster_id, len(parsed), parsed)
    return ClusterManifest(cluster_id, len(parsed), tuple(parsed))


def sign_manifest(manifest: ClusterManifest, key: bytes) -> str:
    """Produce a signed manifest blob: ``<sig-hex>.<base-json>``.

    NOTE: this HMAC is a *structural signature oracle* for round-trip and
    tamper-detection tests only. It is NOT cluster admission control; real
    TLS/mTLS admission is a later gate (D-P0-DIST-TRANSPORT follow-up).
    """
    body = manifest.canonical_body()
    sig = hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{sig}.{body}"


def parse_signed_manifest(blob: str, key: bytes) -> ClusterManifest:
    """Verify signature and parse a signed manifest blob.

    Raises :class:`ManifestError` if the blob is malformed, the signature does
    not match ``key`` (tampered body or wrong key), or the structure is invalid.
    """
    if "." not in blob:
        raise ManifestError("signed manifest must be '<sig>.<body>'")
    sig, _, body = blob.partition(".")
    expected = hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ManifestError("manifest signature mismatch (tampered body or wrong key)")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest body is not valid JSON: {exc}") from exc
    if payload.get("version") != _MANIFEST_VERSION:
        raise ManifestError(
            f"unsupported manifest version {payload.get('version')!r}; expected {_MANIFEST_VERSION}"
        )
    nodes = [
        ClusterNode(int(n["rank"]), str(n["host"]), str(n["transport"]))
        for n in payload.get("nodes", [])
    ]
    manifest = build_manifest(str(payload.get("cluster_id", "")), nodes)
    if manifest.world_size != int(payload.get("world_size", -1)):
        raise ManifestError("world_size disagrees with node count after parse")
    return manifest


def require_transport(mode: str) -> None:
    """Hard-require an available transport; raise for any network mode."""
    probe(mode).raise_if_unavailable()
