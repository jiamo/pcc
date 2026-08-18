"""Sliding-window + RSS-admission contract for the deferred codegen lanes.

Replaces the wave scheduler that let a width-4 small lane aggregate past the
8 GiB breaker (Stage2 v8, evidence 005): widths become caps, the next launch
is admitted only under live aggregate-RSS headroom, and a pressure ladder
suspends the youngest workers rather than letting concurrent growth cross
the budget.  Progress is guaranteed: at least one worker always runs.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "run_pcc_deferred_link.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "pcc_deferred_link_window_test", TOOL
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_worker(tmp_path: Path, *, sleep_s: float = 0.3) -> Path:
    """Worker recording live concurrency; fails when the manifest says so."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    script = tmp_path / "worker.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'run="{run_dir}"\n'
        'touch "$run/p$$"\n'
        f'ls "$run" | wc -l >> "{tmp_path}/counts.log"\n'
        f"sleep {sleep_s}\n"
        'rm -f "$run/p$$"\n'
        'case "$2" in *bad*) exit 2;; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _manifests(tmp_path: Path, names: list[str]) -> list[Path]:
    out = []
    for name in names:
        path = tmp_path / f"{name}.manifest"
        path.write_text("stub\n", encoding="utf-8")
        out.append(path)
    return out


def _max_count(tmp_path: Path) -> int:
    log = tmp_path / "counts.log"
    if not log.exists():
        return 0
    values = [int(line) for line in log.read_text().split() if line.isdigit()]
    return max(values) if values else 0


def test_window_caps_width_and_completes(tmp_path: Path, monkeypatch):
    tool = _load_tool()
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    worker = _fake_worker(tmp_path)
    manifests = _manifests(tmp_path, [f"m{i}" for i in range(6)])
    stats = tool._run_codegen_batches(
        worker, manifests, width=3, assembly_only=True, lane="test"
    )
    assert stats["launched"] == 6
    assert 1 <= _max_count(tmp_path) <= 3


def test_window_fails_closed_and_stops_launching(tmp_path: Path, monkeypatch):
    tool = _load_tool()
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    worker = _fake_worker(tmp_path, sleep_s=0.1)
    names = ["m0", "bad1"] + [f"m{i}" for i in range(2, 8)]
    manifests = _manifests(tmp_path, names)
    with pytest.raises(tool.DeferredLinkError, match="test .*rc=2"):
        tool._run_codegen_batches(
            worker, manifests, width=2, assembly_only=True, lane="test"
        )
    # The failure is detected while early items run; the tail is never
    # launched (in-flight items may still finish).
    launched = len((tmp_path / "counts.log").read_text().split())
    assert launched < len(manifests)


def test_pressure_blocks_admission_but_never_deadlocks(
    tmp_path: Path, monkeypatch
):
    tool = _load_tool()
    worker = _fake_worker(tmp_path, sleep_s=0.1)
    manifests = _manifests(tmp_path, [f"m{i}" for i in range(4)])
    # Live RSS always reads far above any budget: only the progress
    # guarantee may admit, so the lane degrades to serial and still finishes.
    monkeypatch.setattr(
        tool, "_live_rss_by_pid", lambda pids: {pid: 1 << 60 for pid in pids}
    )
    stats = tool._run_codegen_batches(
        worker, manifests, width=3, assembly_only=True, lane="test"
    )
    assert stats["launched"] == 4
    assert _max_count(tmp_path) == 1


def test_pressure_ladder_suspends_then_resumes(tmp_path: Path, monkeypatch):
    tool = _load_tool()
    worker = _fake_worker(tmp_path, sleep_s=0.4)
    manifests = _manifests(tmp_path, [f"m{i}" for i in range(3)])
    calls = {"n": 0}

    def scripted(pids):
        calls["n"] += 1
        # Low pressure until the window fills, then a burst above the
        # ceiling, then low again so suspended workers resume.
        if calls["n"] < 4:
            return {pid: 0 for pid in pids}
        if calls["n"] < 8:
            return {pid: 1 << 60 for pid in pids}
        return {pid: 0 for pid in pids}

    monkeypatch.setattr(tool, "_live_rss_by_pid", scripted)
    stats = tool._run_codegen_batches(
        worker, manifests, width=3, assembly_only=True, lane="test",
        floors=[1 << 30] * 3,
    )
    assert stats["launched"] == 3
    # Three fresh 1 GiB floors fit the 7 GiB ceiling, so all three are
    # running when the burst arrives; the ladder must stop every runnable
    # worker but the oldest in that same poll, not one per poll.
    assert stats["suspensions"] == 2
    assert stats["resumes"] >= 1


def test_hung_worker_hits_the_deadline_and_fails_closed(
    tmp_path: Path, monkeypatch
):
    tool = _load_tool()
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    monkeypatch.setenv("PCC_DEFERRED_WORKER_TIMEOUT_S", "1")
    worker = _fake_worker(tmp_path, sleep_s=30)
    manifests = _manifests(tmp_path, ["m0"])
    import time as _time

    started = _time.monotonic()
    with pytest.raises(tool.DeferredLinkError, match="timed out"):
        tool._run_codegen_batches(
            worker, manifests, width=2, assembly_only=True, lane="test"
        )
    assert _time.monotonic() - started < 10


def test_budget_env_parsing(monkeypatch):
    tool = _load_tool()
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    assert tool._worker_tree_budget_bytes() == 8 * 1024**3
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(4 * 1024**3))
    assert tool._worker_tree_budget_bytes() == 4 * 1024**3
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", "junk")
    assert tool._worker_tree_budget_bytes() == 8 * 1024**3


def test_live_rss_reads_real_processes():
    tool = _load_tool()
    assert tool._live_rss_bytes([]) == 0
    assert tool._live_rss_bytes([os.getpid()]) > 0


def test_fresh_workers_are_charged_their_floor_not_their_live_rss(
    tmp_path: Path, monkeypatch
):
    """The 564 s breaker trip: four fresh ~2.75 GiB workers admitted at once.

    Live RSS reads zero for every worker (a fresh pcc1 process has not grown
    yet), so live-RSS admission would fill the whole width.  With 3 GiB floors
    under the default 8 GiB budget (7 GiB soft ceiling) only two may run:
    3 + 3 = 6 fits, a third would charge 9.
    """
    tool = _load_tool()
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    monkeypatch.setattr(tool, "_live_rss_by_pid", lambda pids: {p: 0 for p in pids})
    worker = _fake_worker(tmp_path, sleep_s=0.3)
    manifests = _manifests(tmp_path, [f"m{i}" for i in range(4)])
    stats = tool._run_codegen_batches(
        worker, manifests, width=4, assembly_only=True, lane="test",
        floors=[3 << 30] * 4,
    )
    assert stats["launched"] == 4
    assert _max_count(tmp_path) <= 2
    assert stats["admission_denied"] >= 1
    assert stats["peak_charged_bytes"] == 6 << 30


def test_small_floors_never_deny_the_full_width(tmp_path: Path, monkeypatch):
    tool = _load_tool()
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    monkeypatch.setattr(tool, "_live_rss_by_pid", lambda pids: {p: 0 for p in pids})
    worker = _fake_worker(tmp_path, sleep_s=0.3)
    manifests = _manifests(tmp_path, [f"m{i}" for i in range(4)])
    stats = tool._run_codegen_batches(
        worker, manifests, width=4, assembly_only=True, lane="test",
        floors=[1 << 30] * 4,
    )
    assert stats["launched"] == 4
    assert stats["admission_denied"] == 0
    assert _max_count(tmp_path) <= 4


def test_floors_must_align_with_manifests(tmp_path: Path):
    tool = _load_tool()
    worker = _fake_worker(tmp_path)
    manifests = _manifests(tmp_path, ["m0", "m1"])
    with pytest.raises(tool.DeferredLinkError, match="floors must align"):
        tool._run_codegen_batches(
            worker, manifests, width=2, assembly_only=True, lane="test",
            floors=[1 << 30],
        )


def test_admission_skips_a_blocked_large_item_to_fill_with_a_later_small_one():
    tool = _load_tool()
    gib = 1 << 30
    assert tool._first_admissible_pending_offset(
        [1, 2, 3],
        [0, 6 * gib, 1 * gib, 2 * gib],
        5 * gib,
        7 * gib,
    ) == 1
    assert tool._first_admissible_pending_offset(
        [1, 3],
        [0, 6 * gib, 1 * gib, 3 * gib],
        5 * gib,
        7 * gib,
    ) == -1


def test_worker_floor_tracks_ast_size_and_caps():
    tool = _load_tool()
    gib = 1 << 30
    assert tool._worker_floor_bytes(0) == 9 * gib // 10
    # The small-band top of the capped receipt (1.9 MB AST reached 2.75+ GiB
    # while still growing) must charge more than the observed peak.
    assert tool._worker_floor_bytes(1_900_000) > int(2.75 * gib)
    assert tool._worker_floor_bytes(1_900_000) < 4 * gib
    # Two capped floors still fit the 7 GiB soft ceiling: heavy and paired
    # lanes keep their measured width of two.
    assert tool._worker_floor_bytes(13_900_000) == 7 * gib // 2
    assert 2 * tool._worker_floor_bytes(4_400_000) <= 7 * gib


def test_indexed_emit_floor_uses_exact_sidecar_bytes_and_covers_measured_peaks(
    tmp_path: Path,
):
    tool = _load_tool()
    samples = (
        ("tiny", 17_038, False, 28_327_936),
        ("small-pco-floor-boundary", 129_227, False, 166_559_744),
        ("medium-pco-envelope", 6_851_869, False, 1_042_104_320),
        ("large-pco-peak", 11_263_590, False, 1_469_612_032),
        ("py-ast", 14_911_544, False, 1_589_706_752),
        ("pipeline", 15_662_963, True, 1_391_607_808),
        ("class-gen", 34_212_312, True, 2_403_237_888),
        ("runtime-abi", 45_035_092, True, 3_285_827_584),
        ("cli-bootstrap", 60_607_363, True, 4_530_978_816),
    )
    for name, size, assembly_only, measured_peak in samples:
        sidecar = tmp_path / (name + ".pidx")
        with sidecar.open("wb") as stream:
            stream.truncate(size)
        floor = tool._indexed_emit_floor_bytes(
            sidecar,
            assembly_only=assembly_only,
        )
        assert floor >= int(measured_peak * 1.05) + 100_000_000


def test_indexed_frontend_floor_covers_the_complete_v48_boundary_samples():
    tool = _load_tool()
    samples = (
        (2_762, 638_959_616),
        (67_170, 601_374_720),
        (794_390, 751_140_864),
        (1_904_364, 913_801_216),
        (2_994_412, 1_233_305_600),
        (3_205_360, 1_258_536_960),
        (7_912_275, 2_013_659_136),
        (13_821_000, 2_910_683_136),
    )
    for ast_bytes, measured_peak in samples:
        floor = tool._indexed_frontend_floor_bytes(ast_bytes)
        assert floor >= int(measured_peak * 1.05) + 100_000_000


def test_per_worker_records_and_incremental_receipt(tmp_path: Path, monkeypatch):
    """Every worker leaves wall/peak/floor evidence, rewritten after each exit.

    A lane killed by the external breaker must still leave per-module data
    (repo rule: no long run may end with dots-only evidence)."""
    import json

    tool = _load_tool()
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    monkeypatch.setattr(
        tool, "_live_rss_by_pid", lambda pids: {p: 5 << 20 for p in pids}
    )
    worker = _fake_worker(tmp_path, sleep_s=0.2)
    manifests = _manifests(tmp_path, ["m0", "m1", "m2"])
    receipt = tmp_path / "plan.admission.json"
    stats = tool._run_codegen_batches(
        worker, manifests, width=2, assembly_only=True, lane="test",
        floors=[1 << 30] * 3, receipt_path=receipt,
        receipt_extra={"schema": "x", "completed_lanes": {"serial": {"launched": 1}}},
    )
    workers = stats["workers"]
    assert stats["elapsed_s"] >= 0.2
    assert sorted(w["manifest"] for w in workers) == ["m0.manifest", "m1.manifest", "m2.manifest"]
    assert all(w["wall_s"] >= 0.2 and w["outcome"] == 0 for w in workers)
    assert all(w["peak_rss_bytes"] == 5 << 20 and w["floor_bytes"] == 1 << 30 for w in workers)
    payload = json.loads(receipt.read_text())
    assert payload["schema"] == "x"
    assert payload["completed_lanes"] == {"serial": {"launched": 1}}
    assert payload["lane"]["lane"] == "test"
    assert len(payload["lane"]["workers"]) == 3
