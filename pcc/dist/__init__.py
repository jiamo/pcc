"""pcc.dist — local-only distributed-runtime metadata oracles (P0 first slice).

This package is the **metadata / single-process** first slice of the pcc
distributed-runtime cluster (goal rows ``D-P0-DIST-SESSION``,
``D-P0-DIST-TRANSPORT``, ``D-P0-DIST-COLLECTIVE``, ``D-P0-DIST-SHARDING``,
``D-P0-DIST-KV-BRIDGE``). It is derived from
``docs/refs_docs/deep-research/deep-research-distribute.md`` (TVM Disco-style
``Session``/``DRef``/device-mesh boundaries, Apple-Silicon transport reality,
vLLM paged-KV block management).

Claim boundary (read this before citing this package):

    This package models identity, ownership, placement, deterministic
    collective semantics, sharding schedules, and KV-block bookkeeping in a
    SINGLE PROCESS, on CPU, with NO sockets. The separate explicit
    ``transport.select_owner('tcp-ring', ...)`` route provides only a bounded
    localhost multi-process ring and its five deterministic collective
    operations. An explicit ``allow_remote=True`` plus PSK route implements
    authenticated (not encrypted) multi-host admission, but that optional
    path is outside the completion proof. The strict execution gate uses
    independent localhost processes. Neither route justifies a multi-host,
    QUIC, RDMA, TLS/mTLS, framework-training, tensor, or serving claim.
    Default networking probes stay explicitly unavailable via
    ``SKIPPED_WITH_REASON`` rather than silently selecting an owner.

Each module is importable standalone (``import pcc.dist.session`` etc.) and
this ``__init__`` deliberately re-exports the public names WITHOUT triggering
``pcc/__init__.py`` to import ``pcc.dist``. Nothing in the wider pcc package
imports ``pcc.dist``; it is opt-in.
"""
from __future__ import annotations

from . import (
    collective,
    kv,
    multi_host,
    session,
    sharding,
    tensor_kv_execution,
    transport,
)

__all__ = [
    "session",
    "transport",
    "collective",
    "sharding",
    "kv",
    "multi_host",
    "tensor_kv_execution",
]
