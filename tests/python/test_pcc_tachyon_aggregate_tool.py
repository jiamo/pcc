from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.pcc_tachyon_aggregate import (
    TachyonAggregateError,
    aggregate,
    load_embedded_data,
)


def _write_profile(path: Path, *, foo: int, bar: int) -> None:
    strings = ["root", "file.py", "mod", "foo", "bar"]
    data = {
        "name": 0,
        "value": foo + bar,
        "self": 0,
        "children": [
            {
                "filename": 1,
                "module": 2,
                "funcname": 3,
                "lineno": 10,
                "value": foo,
                "self": foo,
                "children": [],
                "opcodes": {"164": foo},
            },
            {
                "filename": 1,
                "module": 2,
                "funcname": 4,
                "lineno": 20,
                "value": bar,
                "self": bar,
                "children": [],
                "opcodes": {"83": bar},
            },
        ],
        "stats": {
            "duration_sec": 1.0,
            "sample_rate": 1000.0,
            "error_rate": 0.0,
        },
        "strings": strings,
        "opcode_mapping": {
            "names": {"83": "LOAD_FAST", "164": "CALL_PY_EXACT_ARGS"}
        },
    }
    path.write_text(
        "<html>\nconst EMBEDDED_DATA = " + json.dumps(data) + ";\n</html>\n",
        encoding="utf-8",
    )


def test_aggregate_sums_worker_self_samples_and_opcodes(tmp_path: Path):
    first = tmp_path / "stage1_1.html"
    second = tmp_path / "stage1_2.html"
    _write_profile(first, foo=3, bar=1)
    _write_profile(second, foo=2, bar=4)

    result = aggregate([first, second], top=10)

    assert result["profile_count"] == 2
    assert result["total_root_samples"] == 10
    assert result["total_self_samples"] == 10
    assert result["top_self_functions"][:2] == [
        {
            "samples": 5,
            "percent": 50.0,
            "filename": "file.py",
            "lineno": 10,
            "function": "foo",
            "module": "mod",
        },
        {
            "samples": 5,
            "percent": 50.0,
            "filename": "file.py",
            "lineno": 20,
            "function": "bar",
            "module": "mod",
        },
    ]
    assert result["top_frame_opcodes"][:2] == [
        {"samples": 5, "percent": 50.0, "name": "CALL_PY_EXACT_ARGS"},
        {"samples": 5, "percent": 50.0, "name": "LOAD_FAST"},
    ]


def test_load_rejects_html_without_embedded_profile(tmp_path: Path):
    path = tmp_path / "empty.html"
    path.write_text("<html></html>\n", encoding="utf-8")

    with pytest.raises(TachyonAggregateError, match="missing EMBEDDED_DATA"):
        load_embedded_data(path)
