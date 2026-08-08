"""Explicit configuration boundary for the strict two-Mac transport gate.

Nothing in this module discovers hosts or opens sockets.  Both ranks must be
given the same JSON cluster file, their own rank, and the same >=256-bit PSK.
The PSK is passed to the TCP owner's fresh-nonce challenge-response admission;
traffic remains unencrypted and is not labeled TLS/mTLS secure.
"""
from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from . import transport


class MultiHostConfigError(ValueError):
    """The strict multi-host environment is absent or malformed."""


@dataclass(frozen=True)
class MultiHostConfig:
    manifest: transport.ClusterManifest
    rank: int
    admission_key: bytes = field(repr=False)
    connect_timeout_s: float = 30.0
    io_timeout_s: float = 10.0

    def owner_options(self) -> dict[str, object]:
        return {
            "allow_remote": True,
            "admission_key": self.admission_key,
            "connect_timeout_s": self.connect_timeout_s,
            "io_timeout_s": self.io_timeout_s,
        }


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise MultiHostConfigError(f"{name} is required for the strict two-Mac gate")
    return value


def _endpoint_host(endpoint: str) -> str:
    if endpoint.startswith("["):
        end = endpoint.find("]")
        if end < 0 or end + 1 >= len(endpoint) or endpoint[end + 1] != ":":
            raise MultiHostConfigError(f"invalid bracketed endpoint {endpoint!r}")
        return endpoint[1:end]
    try:
        host, _ = endpoint.rsplit(":", 1)
    except ValueError:
        raise MultiHostConfigError(
            f"multi-host endpoint must be host:port, got {endpoint!r}"
        ) from None
    return host


def _require_distinct_remote_hosts(manifest: transport.ClusterManifest) -> None:
    hosts: list[str] = []
    for node in manifest.nodes:
        host = _endpoint_host(node.host).lower()
        if host == "localhost" or host.endswith(".localhost"):
            raise MultiHostConfigError("strict two-Mac endpoints cannot be localhost")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if address.is_loopback or address.is_unspecified:
                raise MultiHostConfigError(
                    "strict two-Mac endpoints must be non-loopback advertised addresses"
                )
        hosts.append(host)
    if len(set(hosts)) != 2:
        raise MultiHostConfigError(
            "strict two-Mac config requires two distinct advertised hosts"
        )


def _positive_timeout(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise MultiHostConfigError(f"{name} must be a number, got {raw!r}") from None
    if not (0.0 < value <= 120.0):
        raise MultiHostConfigError(f"{name} must be in (0, 120], got {value}")
    return value


def load_multi_host_config(
    env: Mapping[str, str] | None = None,
) -> MultiHostConfig:
    source = os.environ if env is None else env
    config_path = Path(_required(source, "PCC_DIST_CLUSTER_CONFIG"))
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MultiHostConfigError(
            f"cannot read PCC_DIST_CLUSTER_CONFIG {config_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise MultiHostConfigError("cluster config must be a JSON object")
    cluster_id = raw.get("cluster_id")
    nodes = raw.get("nodes")
    if not isinstance(cluster_id, str) or not cluster_id:
        raise MultiHostConfigError("cluster config requires a non-empty cluster_id")
    if not isinstance(nodes, list) or len(nodes) != 2:
        raise MultiHostConfigError("strict gate requires exactly two node entries")
    normalized_nodes: list[dict[str, object]] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise MultiHostConfigError("each cluster node must be a JSON object")
        normalized = dict(node)
        normalized.setdefault("transport", "tcp-ring")
        normalized_nodes.append(normalized)
    try:
        manifest = transport.build_manifest(cluster_id, normalized_nodes)
    except (TypeError, ValueError, transport.ManifestError) as exc:
        raise MultiHostConfigError(f"invalid cluster manifest: {exc}") from exc
    _require_distinct_remote_hosts(manifest)

    rank_text = _required(source, "PCC_DIST_RANK")
    try:
        rank = int(rank_text)
    except ValueError:
        raise MultiHostConfigError(
            f"PCC_DIST_RANK must be an integer, got {rank_text!r}"
        ) from None
    if not (0 <= rank < manifest.world_size):
        raise MultiHostConfigError(
            f"PCC_DIST_RANK {rank} is outside world_size {manifest.world_size}"
        )

    key_hex = _required(source, "PCC_DIST_ADMISSION_KEY_HEX")
    try:
        admission_key = bytes.fromhex(key_hex)
    except ValueError:
        raise MultiHostConfigError(
            "PCC_DIST_ADMISSION_KEY_HEX must be hexadecimal"
        ) from None
    if len(admission_key) < 32:
        raise MultiHostConfigError(
            "PCC_DIST_ADMISSION_KEY_HEX must encode at least 256 bits"
        )
    return MultiHostConfig(
        manifest=manifest,
        rank=rank,
        admission_key=admission_key,
        connect_timeout_s=_positive_timeout(
            source, "PCC_DIST_CONNECT_TIMEOUT_S", 30.0
        ),
        io_timeout_s=_positive_timeout(source, "PCC_DIST_IO_TIMEOUT_S", 10.0),
    )
