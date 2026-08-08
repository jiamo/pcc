"""D-P0-DIST-KV-BRIDGE: local metadata-only KV block manager oracle.

Models vLLM-style paged-KV block bookkeeping as pure metadata:

    * a sequence of token ids is chunked into fixed-size blocks
    * each block gets a DETERMINISTIC prefix hash = hash(parent_hash + tokens)
      so shared prefixes map to the same block id (prefix caching)
    * blocks carry a refcount; a block is evictable only when refcount == 0
    * pin/unpin add an independent hold that blocks eviction regardless of refs
    * eviction policy is "refcount==0, then longest-common-prefix-first, then
      LRU": we evict the block whose token prefix is least likely to be reused
      (shortest depth) and, among equals, least-recently-used
    * handles serialize/deserialize round-trip stably
    * invalidation drops a block and its descendants (children depend on parent)

There is NO GPU, NO serving, NO real cache memory. A block stores only its
token ids and bookkeeping. No vLLM serving or GPU cache movement is claimed.

Standalone-importable: ``import pcc.dist.kv``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Sequence


class KVError(Exception):
    """Raised for bad block sizes, refcount underflow, or unknown handles."""


_ROOT_HASH = "root"


def _block_hash(parent_hash: str, tokens: Sequence[int]) -> str:
    """Deterministic content hash chaining the parent prefix and this block's tokens."""
    payload = json.dumps([parent_hash, list(int(t) for t in tokens)], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class KVBlockHandle:
    """An opaque, serializable handle to a cached block.

    ``block_hash`` is the deterministic prefix hash (the identity). ``depth`` is
    the block's position in the prefix chain (0 = first block of a sequence).
    """

    block_hash: str
    depth: int
    num_tokens: int

    def serialize(self) -> str:
        return f"kvblk:{self.block_hash}:{self.depth}:{self.num_tokens}"

    @classmethod
    def deserialize(cls, blob: str) -> "KVBlockHandle":
        if not blob.startswith("kvblk:"):
            raise KVError(f"not a KVBlockHandle: {blob!r}")
        try:
            _, h, depth, ntok = blob.split(":", 3)
            return cls(h, int(depth), int(ntok))
        except (ValueError, TypeError) as exc:
            raise KVError(f"malformed KVBlockHandle: {blob!r}") from exc


@dataclass
class _Block:
    block_hash: str
    parent_hash: str
    depth: int
    tokens: tuple[int, ...]
    refcount: int = 0
    pins: int = 0
    last_access: int = 0
    children: set[str] = field(default_factory=set)

    @property
    def evictable(self) -> bool:
        return self.refcount == 0 and self.pins == 0

    def handle(self) -> KVBlockHandle:
        return KVBlockHandle(self.block_hash, self.depth, len(self.tokens))


class BlockManager:
    """Metadata-only KV block manager with deterministic prefix caching."""

    def __init__(self, block_tokens: int = 4, capacity: int | None = None) -> None:
        if block_tokens <= 0:
            raise KVError(f"block_tokens must be positive, got {block_tokens}")
        if capacity is not None and capacity <= 0:
            raise KVError(f"capacity must be positive, got {capacity}")
        self.block_tokens = block_tokens
        self.capacity = capacity
        self._blocks: dict[str, _Block] = {}
        self._clock = 0

    # -- internal helpers --------------------------------------------------
    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def _chunk(self, tokens: Sequence[int]) -> list[tuple[int, ...]]:
        toks = tuple(int(t) for t in tokens)
        return [toks[i:i + self.block_tokens] for i in range(0, len(toks), self.block_tokens)]

    # -- public API --------------------------------------------------------
    def num_blocks(self) -> int:
        return len(self._blocks)

    def has_block(self, block_hash: str) -> bool:
        return block_hash in self._blocks

    def allocate(self, tokens: Sequence[int]) -> list[KVBlockHandle]:
        """Allocate/reuse blocks for a token sequence; increments refcounts.

        Blocks with an identical prefix are shared (same hash -> same block,
        refcount bumped). Returns one handle per block in order.
        """
        chunks = self._chunk(tokens)
        if not chunks:
            raise KVError("cannot allocate an empty token sequence")
        handles: list[KVBlockHandle] = []
        parent = _ROOT_HASH
        for depth, chunk in enumerate(chunks):
            h = _block_hash(parent, chunk)
            block = self._blocks.get(h)
            if block is None:
                self._maybe_evict(reserve=1)
                block = _Block(h, parent, depth, chunk)
                self._blocks[h] = block
                if parent != _ROOT_HASH and parent in self._blocks:
                    self._blocks[parent].children.add(h)
            block.refcount += 1
            block.last_access = self._tick()
            handles.append(block.handle())
            parent = h
        return handles

    def release(self, handle: KVBlockHandle) -> None:
        block = self._require(handle.block_hash)
        if block.refcount <= 0:
            raise KVError(f"refcount underflow releasing block {handle.block_hash}")
        block.refcount -= 1

    def refcount(self, handle: KVBlockHandle) -> int:
        return self._require(handle.block_hash).refcount

    def pin(self, handle: KVBlockHandle) -> None:
        self._require(handle.block_hash).pins += 1

    def unpin(self, handle: KVBlockHandle) -> None:
        block = self._require(handle.block_hash)
        if block.pins <= 0:
            raise KVError(f"pin underflow unpinning block {handle.block_hash}")
        block.pins -= 1

    def is_pinned(self, handle: KVBlockHandle) -> bool:
        return self._require(handle.block_hash).pins > 0

    def touch(self, handle: KVBlockHandle) -> None:
        self._require(handle.block_hash).last_access = self._tick()

    def invalidate(self, handle: KVBlockHandle) -> list[str]:
        """Drop a block and all descendants (children depend on the parent prefix).

        Returns the hashes removed. Raises if any removed block is pinned.
        """
        root = self._require(handle.block_hash)
        removed: list[str] = []
        stack = [root]
        seen: set[str] = set()
        while stack:
            b = stack.pop()
            if b.block_hash in seen:
                continue
            seen.add(b.block_hash)
            if b.pins > 0:
                raise KVError(f"cannot invalidate pinned block {b.block_hash}")
            stack.extend(self._blocks[c] for c in b.children if c in self._blocks)
        for h in seen:
            parent = self._blocks[h].parent_hash
            if parent in self._blocks:
                self._blocks[parent].children.discard(h)
            del self._blocks[h]
            removed.append(h)
        return removed

    def evict_one(self) -> str | None:
        """Evict a single victim by policy; return its hash or None if none evictable.

        Policy: among evictable blocks (refcount==0 and unpinned), prefer the
        block least likely to be reused as a shared prefix — the shallowest
        depth (a leaf far from a shared root) — and break ties by least-recent
        access (LRU). We also never evict a block that still has live children.
        """
        candidates = [
            b for b in self._blocks.values()
            if b.evictable and not any(c in self._blocks for c in b.children)
        ]
        if not candidates:
            return None
        # Longest-prefix-FIRST retention => evict the SHORTEST prefix depth
        # first; ties broken by oldest access (smallest last_access).
        victim = min(candidates, key=lambda b: (b.depth, b.last_access))
        parent = victim.parent_hash
        if parent in self._blocks:
            self._blocks[parent].children.discard(victim.block_hash)
        del self._blocks[victim.block_hash]
        return victim.block_hash

    def _maybe_evict(self, reserve: int) -> None:
        if self.capacity is None:
            return
        while len(self._blocks) + reserve > self.capacity:
            if self.evict_one() is None:
                raise KVError(
                    f"cache full (capacity {self.capacity}) and no evictable block available"
                )

    def _require(self, block_hash: str) -> _Block:
        block = self._blocks.get(block_hash)
        if block is None:
            raise KVError(f"unknown block {block_hash!r}")
        return block

    # -- serialization -----------------------------------------------------
    def serialize(self) -> str:
        payload = {
            "block_tokens": self.block_tokens,
            "capacity": self.capacity,
            "clock": self._clock,
            "blocks": [
                {
                    "block_hash": b.block_hash,
                    "parent_hash": b.parent_hash,
                    "depth": b.depth,
                    "tokens": list(b.tokens),
                    "refcount": b.refcount,
                    "pins": b.pins,
                    "last_access": b.last_access,
                    "children": sorted(b.children),
                }
                for b in sorted(self._blocks.values(), key=lambda x: (x.depth, x.block_hash))
            ],
        }
        return json.dumps(payload, sort_keys=True)

    @classmethod
    def deserialize(cls, blob: str) -> "BlockManager":
        data = json.loads(blob)
        mgr = cls(block_tokens=int(data["block_tokens"]), capacity=data["capacity"])
        mgr._clock = int(data.get("clock", 0))
        for b in data["blocks"]:
            block = _Block(
                block_hash=str(b["block_hash"]),
                parent_hash=str(b["parent_hash"]),
                depth=int(b["depth"]),
                tokens=tuple(int(t) for t in b["tokens"]),
                refcount=int(b["refcount"]),
                pins=int(b["pins"]),
                last_access=int(b["last_access"]),
                children=set(b.get("children", [])),
            )
            mgr._blocks[block.block_hash] = block
        return mgr
