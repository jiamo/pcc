"""Throughput measurement contract gate (DIST-P1-THROUGHPUT-SCALING).

Asserts the CONTRACT — fixed labeled vectors, warmup/sample floors, p50<=p95,
positive effective bandwidth, and fail-closed process-isolated labeling — never
machine-dependent absolute speeds. The measured vectors run through explicit
spawned-process TCP-ring owners and are labeled ``localhost-multiprocess``;
they prove process scaling, not multi-host hardware scaling.
"""
from __future__ import annotations

import multiprocessing
import os
import socket
import time

import pytest

from pcc.dist import transport
from pcc.dist.perf_contract import (
    MODE_PROCESS_ISOLATED,
    SCHEMA,
    PerfContractError,
    build_perf_record,
    build_vector_entry,
)


def _free_loopback_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _manifest_for_ports(ports: list[int]):
    return transport.build_manifest(
        "perf-contract-local",
        [
            {
                "rank": rank,
                "host": f"127.0.0.1:{port}",
                "transport": "tcp-ring",
            }
            for rank, port in enumerate(ports)
        ],
    )


def _perf_ring_worker(manifest, rank, start, results, payload_bytes, rounds):
    try:
        owner = transport.select_owner(
            "tcp-ring",
            manifest,
            rank,
            connect_timeout_s=5.0,
            io_timeout_s=5.0,
        )
        payload = bytes(payload_bytes)
        if not start.wait(5.0):
            raise RuntimeError("start event timed out")
        latencies = []
        with owner:
            for _ in range(rounds):
                begin = time.perf_counter()
                owner.send(payload)
                received = owner.recv()
                latencies.append(time.perf_counter() - begin)
                assert len(received) == payload_bytes
        results.put(("ok", rank, os.getpid(), latencies))
    except BaseException as exc:  # propagate as data; worker is a subprocess
        results.put(("error", rank, type(exc).__name__, str(exc)))


def _measure_ring_round_latencies(world: int, payload_bytes: int, rounds: int):
    manifest = _manifest_for_ports(_free_loopback_ports(world))
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    workers = [
        ctx.Process(
            target=_perf_ring_worker,
            args=(manifest, rank, start, results, payload_bytes, rounds),
        )
        for rank in range(world)
    ]
    for worker in workers:
        worker.start()
    start.set()
    collected = [results.get(timeout=30.0) for _ in range(world)]
    for worker in workers:
        worker.join(timeout=30.0)
    errors = [item for item in collected if item[0] != "ok"]
    assert not errors, errors
    rank0 = next(item for item in collected if item[1] == 0)
    summaries = [
        {
            "rank": item[1],
            "pid": item[2],
            "world_size": world,
            "isolation": "spawn",
            "strict_gate_passed": True,
        }
        for item in collected
    ]
    return rank0[3], summaries


def test_contract_vectors_are_fixed_and_labeled():
    warmup = 3
    samples = 10
    vectors = []
    process_summaries = []
    for world_size in (2, 4):
        for payload_bytes in (4096, 65536):
            latencies, summaries_for_world = _measure_ring_round_latencies(
                world=world_size,
                payload_bytes=payload_bytes,
                rounds=warmup + samples,
            )
            vectors.append(
                build_vector_entry(
                    payload_bytes=payload_bytes,
                    world_size=world_size,
                    warmup_rounds=warmup,
                    sample_latencies_s=latencies[warmup:],
                )
            )
            if payload_bytes == 4096:
                process_summaries.extend(summaries_for_world)
    record = build_perf_record(
        mode=MODE_PROCESS_ISOLATED,
        vectors=vectors,
        process_summaries=process_summaries,
    )
    assert record["schema"] == SCHEMA
    assert record["mode"] == MODE_PROCESS_ISOLATED
    for entry in record["vectors"]:  # type: ignore[union-attr]
        assert entry["warmup_rounds"] >= 3
        assert entry["samples"] >= 10
        assert 0 < entry["p50_latency_s"] <= entry["p95_latency_s"]
        assert entry["effective_bandwidth_bytes_per_s"] > 0


def test_process_isolated_label_is_fail_closed_without_complete_rank_summaries():
    vector = build_vector_entry(
        payload_bytes=4096,
        world_size=2,
        warmup_rounds=3,
        sample_latencies_s=[0.001] * 10,
    )
    with pytest.raises(PerfContractError, match="spawned summaries"):
        build_perf_record(mode=MODE_PROCESS_ISOLATED, vectors=[vector])
    with pytest.raises(PerfContractError, match="spawned summaries"):
        build_perf_record(
            mode=MODE_PROCESS_ISOLATED,
            vectors=[vector],
            process_summaries=[
                {
                    "rank": 0,
                    "pid": 100,
                    "world_size": 2,
                    "isolation": "spawn",
                    "strict_gate_passed": True,
                },
                {
                    "rank": 1,
                    "pid": 100,
                    "world_size": 2,
                    "isolation": "spawn",
                    "strict_gate_passed": True,
                },
            ],
        )
    record = build_perf_record(
        mode=MODE_PROCESS_ISOLATED,
        vectors=[vector],
        process_summaries=[
            {
                "rank": 0,
                "pid": 100,
                "world_size": 2,
                "isolation": "spawn",
                "strict_gate_passed": True,
            },
            {
                "rank": 1,
                "pid": 101,
                "world_size": 2,
                "isolation": "spawn",
                "strict_gate_passed": True,
            },
        ],
    )
    assert record["mode"] == MODE_PROCESS_ISOLATED


def test_contract_floors_are_enforced():
    with pytest.raises(PerfContractError, match="warmup"):
        build_vector_entry(
            payload_bytes=4096,
            world_size=2,
            warmup_rounds=1,
            sample_latencies_s=[0.001] * 10,
        )
    with pytest.raises(PerfContractError, match="sample count"):
        build_vector_entry(
            payload_bytes=4096,
            world_size=2,
            warmup_rounds=3,
            sample_latencies_s=[0.001] * 5,
        )
    with pytest.raises(PerfContractError, match="unknown mode"):
        build_perf_record(mode="benchmarketing", vectors=[{}])
