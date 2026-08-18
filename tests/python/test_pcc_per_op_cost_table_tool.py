"""Contract for scripts/pcc_per_op_cost_table.py's parsing and differencing.

No compile or run: the tool's value is the (2N - N) / N differencing that
cancels process startup, and a parser that reads ``/usr/bin/time -lp``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "pcc_per_op_cost_table.py"


def _load():
    spec = importlib.util.spec_from_file_location("pcc_per_op_cost_table_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAMPLE = """real 1.50
user 1.40
sys 0.05
      123456789  maximum resident set size
    31070072760  instructions retired
     9784522640  cycles elapsed
"""


def test_parse_time_lp_reads_instructions_wall_and_rss():
    tool = _load()
    m = tool.parse_time_lp(SAMPLE)
    assert m["real_s"] == 1.5
    assert m["instructions"] == 31070072760
    assert m["max_rss"] == 123456789


def test_per_op_differencing_cancels_fixed_cost():
    tool = _load()
    n = 1_000_000
    fixed = {"instructions": 5e9, "real_s": 0.2}
    m_n = {"instructions": fixed["instructions"] + 300 * n, "real_s": fixed["real_s"] + 100e-9 * n}
    m_2n = {"instructions": fixed["instructions"] + 300 * 2 * n, "real_s": fixed["real_s"] + 100e-9 * 2 * n}
    r = tool.per_op(m_n, m_2n, n)
    assert abs(r["instr_per_op"] - 300) < 1e-6
    assert abs(r["ns_per_op"] - 100) < 1e-3


def test_every_benchmark_is_a_typed_main_program():
    tool = _load()
    assert len(tool.BENCHMARKS) >= 15
    for name, body in tool.BENCHMARKS.items():
        assert "def main() -> None:" in body, name
        assert "N" in body, name
        assert "print(" in body, name
