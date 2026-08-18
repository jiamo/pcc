"""Final stack-map record ordering must not depend on the arena's storage form.

`_sort_final_stack_map_records` orders four-word (pc, safepoint id, index,
exceptional offset) records by final PC then safepoint id.  On pcc1 the arena
holds native scalar storage and the heapsort runs on the raw words; under
CPython the arena keeps a list and the arena-method heapsort runs.  Both must
produce the same order, so the host oracle here pins the contract that the
pcc1 replays check byte-for-byte on emitted stack maps.
"""

from __future__ import annotations

import random

from pcc.backend import self_backend_precise_stackmaps as stackmaps
from pcc.backend.self_backend_value_arena import CompilerIntArena


def _records(arena: CompilerIntArena) -> list[tuple[int, int, int, int]]:
    count = len(arena) // 4
    return [
        tuple(arena.get_unchecked(index * 4 + word) for word in range(4))
        for index in range(count)
    ]


def _unique_rows(seed: int, count: int) -> list[tuple[int, int, int, int]]:
    rng = random.Random(seed)
    seen: set[tuple[int, int]] = set()
    rows: list[tuple[int, int, int, int]] = []
    for index in range(count):
        pc = rng.randrange(0, 48) * 4
        safepoint_id = rng.randrange(0, 1 << 20) if rng.random() < 0.7 else index
        while (pc, safepoint_id) in seen:
            safepoint_id += 1
        seen.add((pc, safepoint_id))
        rows.append((pc, safepoint_id, index, rng.randrange(-1, 1 << 20)))
    return rows


def test_sort_orders_by_pc_then_id_and_keeps_every_payload_word():
    rows = _unique_rows(20260906, 1201)
    arena = CompilerIntArena()
    for row in rows:
        arena.append4(*row)
    stackmaps._sort_final_stack_map_records(arena)
    assert _records(arena) == sorted(rows, key=lambda row: (row[0], row[1]))
    # This process is the CPython oracle: the list storage path ran here and
    # the native kernel is exercised by pcc1 replays of real modules.
    assert not arena.uses_native_storage
    assert arena.native_address() == 0


def test_sort_handles_empty_single_and_already_sorted_inputs():
    empty = CompilerIntArena()
    stackmaps._sort_final_stack_map_records(empty)
    assert _records(empty) == []

    single = CompilerIntArena()
    single.append4(16, 3, 0, -1)
    stackmaps._sort_final_stack_map_records(single)
    assert _records(single) == [(16, 3, 0, -1)]

    rows = sorted(_unique_rows(7, 257), key=lambda row: (row[0], row[1]))
    ordered = CompilerIntArena()
    for row in rows:
        ordered.append4(*row)
    stackmaps._sort_final_stack_map_records(ordered)
    assert _records(ordered) == rows


def test_native_sift_down_matches_arena_method_sift_down_on_a_fake_heap():
    """Drive the native kernel through a raw-memory stand-in on the host.

    `pcc.unsafe` loads raise under CPython, so the kernel is exercised with a
    bytearray-backed stand-in for its three raw operations and compared with
    the arena-method heapsort on identical input.
    """
    import struct

    rows = _unique_rows(99, 513)
    reference = CompilerIntArena()
    for row in rows:
        reference.append4(*row)
    stackmaps._sort_final_stack_map_records(reference)
    expected = _records(reference)

    memory = bytearray()
    for row in rows:
        memory += struct.pack("<qqqq", *row)
    address = 0x1000  # the kernel receives the arena address as an exact int

    def to_ptr(value):
        assert value == address
        return ("ptr", value)

    def load(base, offset):
        assert base == ("ptr", address)
        return struct.unpack_from("<q", memory, offset)[0]

    def store(base, offset, value):
        assert base == ("ptr", address)
        struct.pack_into("<q", memory, offset, value)

    saved = (
        stackmaps._record_int_to_ptr,
        stackmaps._record_load_i64,
        stackmaps._record_store_i64,
    )
    stackmaps._record_int_to_ptr = to_ptr
    stackmaps._record_load_i64 = load
    stackmaps._record_store_i64 = store
    try:
        count = len(rows)
        start = count // 2 - 1
        while start >= 0:
            stackmaps._sift_down_final_stack_map_records_native(address, start, count)
            start -= 1
        end = count - 1
        while end > 0:
            stackmaps._swap_final_stack_map_records_native(address, 0, end * 32)
            stackmaps._sift_down_final_stack_map_records_native(address, 0, end)
            end -= 1
    finally:
        (
            stackmaps._record_int_to_ptr,
            stackmaps._record_load_i64,
            stackmaps._record_store_i64,
        ) = saved
    native = [
        struct.unpack_from("<qqqq", memory, index * 32) for index in range(len(rows))
    ]
    assert native == expected
