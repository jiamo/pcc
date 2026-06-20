"""CPU-only oracle tests for pcc.gpu_gc.assist.

Deterministic classification (same page -> same class, table-driven), the CPU
oracle as ground truth, and provable kernel parity: a modelled GPU kernel is
only trusted when it EQUALS the oracle; otherwise the oracle wins and a fallback
is counted. No GPU is launched.
"""
from __future__ import annotations

import pytest

from pcc.gpu_gc.assist import (
    AssistClass,
    AssistOracle,
    classify_page,
)
from pcc.gpu_gc.substrate import LayoutClass, PageState, RegionKind, Substrate


def _page(sub, region, layout, slots=()):
    p = sub.allocate(region, layout)
    p.live_slots.update(slots)
    return p


def _env():
    sub = Substrate()
    old = sub.add_region(RegionKind.OLD, 32)
    return sub, old


@pytest.mark.parametrize(
    "layout,expected",
    [
        (LayoutClass.FLAT_ARRAY, AssistClass.GPU_TRACEABLE),
        (LayoutClass.OBJECT_VECTOR, AssistClass.GPU_TRACEABLE),
        (LayoutClass.RAW_PAYLOAD, AssistClass.GPU_TRACEABLE),
        (LayoutClass.IMMUTABLE, AssistClass.GPU_TRACEABLE),
        (LayoutClass.POINTER_TABLE, AssistClass.GPU_SUMMARY_ONLY),
        (LayoutClass.POINTER_GRAPH, AssistClass.CPU_ONLY),
    ],
)
def test_classification_is_deterministic_by_layout(layout, expected):
    sub, old = _env()
    p = _page(sub, old, layout)
    assert classify_page(p) is expected
    # Determinism: repeated calls agree.
    assert classify_page(p) is classify_page(p)


def test_oracle_mark_only_allocated_pages():
    sub, old = _env()
    p = _page(sub, old, LayoutClass.FLAT_ARRAY, slots={0, 1, 2})
    assert AssistOracle.mark_page(p) == {0, 1, 2}
    sub.free(p)
    assert p.state is PageState.FREE
    assert AssistOracle.mark_page(p) == set()


def test_good_kernel_matches_oracle_and_is_trusted():
    sub, old = _env()
    p = _page(sub, old, LayoutClass.FLAT_ARRAY, slots={3, 5, 7})
    orc = AssistOracle()
    good = lambda pg: set(pg.live_slots)
    assert orc.check_kernel_parity(p, good) is True
    assert orc.assisted_mark(p, good) == {3, 5, 7}
    assert orc.telemetry.gpu_dispatched == 1
    assert orc.telemetry.parity_mismatches == 0
    assert orc.telemetry.cpu_fallbacks == 0


def test_bad_kernel_falls_back_to_oracle_and_counts_mismatch():
    sub, old = _env()
    p = _page(sub, old, LayoutClass.FLAT_ARRAY, slots={1, 2})
    orc = AssistOracle()
    bad = lambda pg: {42}
    assert orc.check_kernel_parity(p, bad) is False
    assert orc.assisted_mark(p, bad) == {1, 2}  # oracle wins
    assert orc.telemetry.parity_mismatches == 1
    assert orc.telemetry.cpu_fallbacks == 1


def test_missing_kernel_falls_back_and_counts_unavailable():
    sub, old = _env()
    p = _page(sub, old, LayoutClass.OBJECT_VECTOR, slots={9})
    orc = AssistOracle()
    assert orc.assisted_mark(p, None) == {9}
    assert orc.telemetry.kernel_unavailable == 1
    assert orc.telemetry.cpu_fallbacks == 1
    assert orc.telemetry.gpu_dispatched == 0


def test_cpu_only_pages_never_dispatched():
    sub, old = _env()
    p = _page(sub, old, LayoutClass.POINTER_GRAPH, slots={4})
    orc = AssistOracle()
    # Even given a good kernel, CPU_ONLY pages are not offloaded.
    good = lambda pg: set(pg.live_slots)
    assert orc.assisted_mark(p, good) == {4}
    assert orc.telemetry.gpu_dispatched == 0
    assert orc.telemetry.classified[AssistClass.CPU_ONLY] == 1


def test_summary_only_pages_are_dispatch_eligible():
    sub, old = _env()
    p = _page(sub, old, LayoutClass.POINTER_TABLE, slots={0})
    orc = AssistOracle()
    good = lambda pg: set(pg.live_slots)
    assert orc.assisted_mark(p, good) == {0}
    assert orc.telemetry.gpu_dispatched == 1
    assert orc.telemetry.classified[AssistClass.GPU_SUMMARY_ONLY] == 1


def test_telemetry_as_dict_shape():
    sub, old = _env()
    p = _page(sub, old, LayoutClass.FLAT_ARRAY, slots={0})
    orc = AssistOracle()
    orc.assisted_mark(p, None)
    d = orc.telemetry.as_dict()
    assert d["kernel_unavailable"] == 1
    assert d["classified_gpu_traceable"] == 1
    assert "parity_mismatches" in d


def test_parity_over_many_pages_aggregate_counts():
    sub, old = _env()
    orc = AssistOracle()
    good = lambda pg: set(pg.live_slots)
    bad = lambda pg: {999}
    for i in range(5):
        _page(sub, old, LayoutClass.FLAT_ARRAY, slots={i})
    pages = [pg for pg in sub.pages()]
    for pg in pages[:3]:
        orc.assisted_mark(pg, good)
    for pg in pages[3:]:
        orc.assisted_mark(pg, bad)
    assert orc.telemetry.gpu_dispatched == 5
    assert orc.telemetry.parity_mismatches == 2
    assert orc.telemetry.cpu_fallbacks == 2
