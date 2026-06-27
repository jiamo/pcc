from __future__ import annotations

import json
import subprocess
import sys

from scripts import pcc_sample_aggregate as aggregate


def test_sample_aggregate_parses_top_stack_section_and_categories():
    text = """
Call graph:
    999 ignored_call_graph_entry

    Sort by top of stack:
        38,727 __wait4
        101 __wait4_nocancel
        8,383 pcc_gc_object_index_insert (in typed_tagged_mul) + 12
        4,500 pcc_gc_load_ptr (in typed_tagged_mul) + 4
        10,192 user_py_obj_ops_compare__cmp_threeway
    3,548 user_py_class__strs_eq
    user_py_gc_backend__init_config 2,145

Binary Images:
    ignored trailer
"""

    summary = aggregate.summarize_counts(aggregate.parse_sample_text(text))

    assert summary["total_self"] == 67596
    assert summary["non_wait_self"] == 28768
    assert summary["categories"]["wait"] == 38828
    assert summary["categories"]["gc_index"] == 8383
    assert summary["categories"]["gc_read_barrier"] == 4500
    assert summary["categories"]["gc_other"] == 2145
    assert summary["categories"]["compare_sort"] == 10192
    assert summary["categories"]["class_lookup"] == 3548
    assert not any(
        entry["symbol"] == "ignored_call_graph_entry"
        for entry in summary["symbols"]
    )


def test_sample_aggregate_cli_json_reads_file(tmp_path):
    sample_file = tmp_path / "worker.sample"
    sample_file.write_text(
        """
Sort by top of stack:
  7 pcc_gc_ptr_index_upsert
  3 malloc_zone_malloc
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/pcc_sample_aggregate.py",
            "--json",
            str(sample_file),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["total_self"] == 10
    assert summary["categories"]["gc_index"] == 7
    assert summary["categories"]["allocator"] == 3
    assert summary["inputs"] == [str(sample_file)]
