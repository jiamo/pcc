"""tiered.py — local-only content-hash block directory for immutable blocks.

The research doc's reclamation idea, distilled from vLLM prefix caching and the
Mooncake Store roadmap: for **immutable, restartable** blocks (frozen metadata
tables, deduplicated serialized objects, cacheable subgraphs), reuse can be
content-addressed. A block's identity for *reuse* is its content hash; if the
directory entry is invalidated or missing, there must be an **explicit
recompute/fallback** path — the "recompute on get failure" contract, never an
inconsistent state.

This slice is **local-only** (single process, in-memory dict). It does NOT
implement Mooncake, RDMA, cross-instance sharing, or SSD spill. It models:

* content-hash stability (same bytes -> same hash, deterministic);
* touch / refcount on reuse (mirrors substrate + vLLM touch discipline);
* invalidation (entry removed; a later get must recompute);
* recompute path (a supplied factory is called on miss, result re-registered).

CLAIM BOUNDARY: local-only, in-memory, immutable-blocks-only reuse oracle. No
distributed store, no network, no persistence, no GPU.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


class DirectoryError(RuntimeError):
    """Raised on an invalid directory operation (e.g. hashing a mutable block)."""


def content_hash(data: bytes) -> str:
    """Deterministic content hash of a block's bytes.

    Stable across runs and processes: pure SHA-256 of the exact bytes, hex
    digest. This is the reuse identity for immutable blocks.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise DirectoryError("content_hash requires bytes-like data")
    return hashlib.sha256(bytes(data)).hexdigest()


@dataclass
class TieredEntry:
    """A directory entry for one immutable, content-addressed block."""

    digest: str
    size: int
    refcount: int = 0
    touches: int = 0
    #: Opaque handle to the resident payload. In a real system this points at a
    #: page / buffer; here it is whatever the producer registered (bytes/obj).
    payload: object = None

    def touch(self) -> None:
        self.refcount += 1
        self.touches += 1


@dataclass
class _Stats:
    hits: int = 0
    misses: int = 0
    recomputes: int = 0
    invalidations: int = 0
    registrations: int = 0


class BlockDirectory:
    """Local, content-addressed directory for immutable restartable blocks.

    Keyed by content hash. ``get_or_recompute`` is the central contract: a hit
    touches and returns the cached entry; a miss (never registered OR
    invalidated) invokes the recompute factory, re-registers, and returns the
    fresh entry — so a caller can never observe a dangling/invalid block.
    """

    def __init__(self) -> None:
        self._entries: Dict[str, TieredEntry] = {}
        # Keys that were explicitly invalidated (so a get is a *forced* miss
        # even if a stale producer hands back the same digest without re-reg).
        self._invalidated: set = set()
        self.stats = _Stats()

    # -- registration -------------------------------------------------------

    def register(self, data: bytes, payload: object = None) -> TieredEntry:
        """Register (or reuse) an immutable block by its content bytes.

        If the same content is already present and not invalidated, this is a
        reuse touch. Otherwise a new entry is created. Returns the live entry.
        """
        digest = content_hash(data)
        self._invalidated.discard(digest)
        entry = self._entries.get(digest)
        if entry is None:
            entry = TieredEntry(
                digest=digest,
                size=len(bytes(data)),
                payload=payload if payload is not None else bytes(data),
            )
            self._entries[digest] = entry
            self.stats.registrations += 1
        entry.touch()
        return entry

    # -- lookup -------------------------------------------------------------

    def contains(self, digest: str) -> bool:
        return digest in self._entries and digest not in self._invalidated

    def get(self, digest: str) -> Optional[TieredEntry]:
        """Return the entry for a digest, or None on miss/invalidation.

        A hit does NOT touch — use ``get_or_recompute`` for the reuse path.
        """
        if digest in self._invalidated or digest not in self._entries:
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        return self._entries[digest]

    def get_or_recompute(
        self, data: bytes, recompute: Callable[[], object]
    ) -> TieredEntry:
        """Central reuse+recompute contract for a block identified by ``data``.

        * Hit (present, not invalidated): touch and return — no recompute.
        * Miss (absent or invalidated): call ``recompute()``, register the
          result under the content hash, and return the fresh entry.

        This guarantees the "recompute on get failure" invariant: the caller
        always gets a valid, resident entry, never a stale/None one.
        """
        digest = content_hash(data)
        if digest in self._entries and digest not in self._invalidated:
            self.stats.hits += 1
            entry = self._entries[digest]
            entry.touch()
            return entry
        # Miss -> recompute + re-register.
        self.stats.misses += 1
        self.stats.recomputes += 1
        payload = recompute()
        return self.register(data, payload=payload)

    # -- invalidation / release --------------------------------------------

    def invalidate(self, digest: str) -> bool:
        """Invalidate an entry's reuse identity. Returns True if it existed.

        The entry is removed and the digest marked invalidated, so a subsequent
        ``get`` misses and ``get_or_recompute`` recomputes. Mirrors the
        substrate's "metadata invalidation at eviction time".
        """
        existed = digest in self._entries
        self._entries.pop(digest, None)
        self._invalidated.add(digest)
        if existed:
            self.stats.invalidations += 1
        return existed

    def release(self, digest: str) -> None:
        """Drop one reference to an entry; remove it when refcount hits 0."""
        entry = self._entries.get(digest)
        if entry is None:
            raise DirectoryError(f"cannot release unknown block {digest!r}")
        if entry.refcount <= 0:
            raise DirectoryError(f"refcount underflow releasing block {digest!r}")
        entry.refcount -= 1
        if entry.refcount == 0:
            self._entries.pop(digest, None)

    def as_dict(self) -> Dict[str, object]:
        return {
            "entries": len(self._entries),
            "invalidated": len(self._invalidated),
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "recomputes": self.stats.recomputes,
            "invalidations": self.stats.invalidations,
            "registrations": self.stats.registrations,
        }
