"""CPU-only oracle tests for pcc.gpu_gc.tiered.

Content-hash stability, hit/miss accounting, invalidation forcing a recompute,
the recompute-on-get-failure contract, and refcount-based release. Local-only,
in-memory, immutable-blocks-only. No distributed store, no GPU.
"""
from __future__ import annotations

import pytest

from pcc.gpu_gc.tiered import (
    BlockDirectory,
    DirectoryError,
    content_hash,
)


def test_content_hash_is_stable_and_deterministic():
    assert content_hash(b"hello") == content_hash(b"hello")
    assert content_hash(b"hello") != content_hash(b"world")
    # bytearray / memoryview accepted and equal to bytes
    assert content_hash(bytearray(b"x")) == content_hash(b"x")
    assert content_hash(memoryview(b"x")) == content_hash(b"x")


def test_content_hash_rejects_non_bytes():
    with pytest.raises(DirectoryError):
        content_hash("not-bytes")  # type: ignore[arg-type]


def test_register_creates_entry_and_touches():
    d = BlockDirectory()
    e = d.register(b"abc", payload="P")
    assert e.digest == content_hash(b"abc")
    assert e.size == 3
    assert e.refcount == 1 and e.touches == 1
    assert d.contains(e.digest)


def test_register_same_content_is_reuse_touch():
    d = BlockDirectory()
    e1 = d.register(b"abc")
    e2 = d.register(b"abc")
    assert e1 is e2
    assert e2.refcount == 2 and e2.touches == 2
    assert d.stats.registrations == 1


def test_get_hit_and_miss_accounting():
    d = BlockDirectory()
    d.register(b"abc")
    h = content_hash(b"abc")
    assert d.get(h) is not None
    assert d.stats.hits == 1
    assert d.get(content_hash(b"zzz")) is None
    assert d.stats.misses == 1


def test_invalidation_forces_miss_and_recompute():
    d = BlockDirectory()
    d.register(b"abc", payload="OLD")
    h = content_hash(b"abc")
    assert d.invalidate(h) is True
    assert not d.contains(h)
    calls = []

    def recompute():
        calls.append(1)
        return "NEW"

    e = d.get_or_recompute(b"abc", recompute)
    assert calls == [1]
    assert e.payload == "NEW"
    assert d.stats.recomputes == 1
    assert d.stats.invalidations == 1


def test_get_or_recompute_hit_does_not_recompute():
    d = BlockDirectory()
    d.register(b"abc", payload="P")
    calls = []
    e = d.get_or_recompute(b"abc", lambda: calls.append(1) or "X")
    assert calls == []
    assert e.payload == "P"
    assert d.stats.recomputes == 0


def test_recompute_on_first_miss():
    d = BlockDirectory()
    calls = []
    e = d.get_or_recompute(b"fresh", lambda: calls.append(1) or "COMPUTED")
    assert calls == [1]
    assert e.payload == "COMPUTED"
    assert d.contains(content_hash(b"fresh"))


def test_release_drops_entry_at_zero_refcount():
    d = BlockDirectory()
    e = d.register(b"abc")
    d.register(b"abc")  # refcount 2
    h = e.digest
    d.release(h)
    assert d.contains(h)  # still 1 ref
    d.release(h)
    assert not d.contains(h)  # dropped
    with pytest.raises(DirectoryError, match="cannot release unknown block"):
        d.release(h)


def test_release_rejects_unknown_digest():
    d = BlockDirectory()
    with pytest.raises(DirectoryError, match="cannot release unknown block"):
        d.release("missing")


def test_recompute_reregisters_under_same_hash_after_invalidation():
    """After invalidate + recompute, the digest is live again and a subsequent
    identical register is a reuse (invalidation cleared)."""
    d = BlockDirectory()
    d.register(b"blk")
    h = content_hash(b"blk")
    d.invalidate(h)
    d.get_or_recompute(b"blk", lambda: "R")
    assert d.contains(h)
    # A later register of the same content reuses (not a fresh registration).
    regs_before = d.stats.registrations
    d.register(b"blk")
    assert d.stats.registrations == regs_before


def test_as_dict_reports_counters():
    d = BlockDirectory()
    d.register(b"a")
    d.register(b"b")
    d.invalidate(content_hash(b"a"))
    snap = d.as_dict()
    assert snap["entries"] == 1
    assert snap["registrations"] == 2
    assert snap["invalidations"] == 1
