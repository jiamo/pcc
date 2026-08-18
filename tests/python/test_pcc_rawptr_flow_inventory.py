"""Contract for scripts/pcc_rawptr_flow_inventory.py.

The inventory is a read-only AST walk that names every place a raw C pointer
(an unsafe intrinsic result, a ``c_ptr`` extern result, a raw-returning local
function) flows into an object-shaped position.  Each sink kind is pinned
here on a fixture, together with the sanctioned consumers that must not be
reported, so the inventory the frontend type work relies on cannot drift.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "scripts" / "pcc_rawptr_flow_inventory.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("pcc_rawptr_flow_inventory", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("pcc_rawptr_flow_inventory", module)
    spec.loader.exec_module(module)
    return module


_FIXTURE = textwrap.dedent(
    '''
    from pcc.extern import c_int64, c_ptr, extern
    from pcc.unsafe import int_to_ptr, load_i64, malloc, ptr_add, ptr_eq, ptr_is_null, ptr_to_int

    _buffer_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)
    _count_bytes = extern("py_bytes_len", (c_ptr,), c_int64)

    MODULE_RAW = malloc(64)


    def make_raw(size: int):
        return malloc(size)


    def consume(value):
        return value


    def store_sites(holder, table):
        raw = make_raw(16)
        holder.slot = raw
        table[0] = raw
        return 0


    def call_sites(items: list):
        raw = ptr_add(malloc(8), 8)
        consume(raw)
        items.append(raw)
        wrapped = _buffer_new(raw, 8)
        return wrapped


    def compare_and_truth():
        raw = int_to_ptr(4096)
        if raw == 0:
            return 1
        if raw:
            return 2
        if not raw and ptr_is_null(raw) == 0:
            return 3
        flag = raw or 0
        return ptr_to_int(raw) + len(str(flag))


    def mixed_lane(count: int):
        cursor = malloc(8)
        cursor = count
        return load_i64(int_to_ptr(cursor), 0)


    def safe_uses(buffer):
        raw = malloc(16)
        if ptr_is_null(raw) != 0:
            return -1
        other = ptr_add(raw, 8)
        if ptr_eq(raw, other) != 0:
            return -2
        total = ptr_to_int(other) - ptr_to_int(raw)
        total = total + _count_bytes(buffer)
        return total + load_i64(raw, 0)


    def literal_sites():
        raw = malloc(8)
        pair = (raw, 1)
        return pair
    '''
).lstrip()


def _fixture_report():
    tool = _load_tool()
    return tool, tool.analyze_source(_FIXTURE, "rawptr_fixture.py", "rawptr_fixture")


def _sites(report, kind):
    return sorted((site.function, site.detail) for site in report.sites if site.kind == kind)


def test_pointer_intrinsics_are_derived_from_the_frontend_table():
    tool = _load_tool()
    names = tool.pointer_intrinsic_names()
    assert {"malloc", "ptr_add", "int_to_ptr", "load_ptr", "cstr", "global_addr"} <= names
    assert "ptr_to_int" not in names and "load_i64" not in names and "abi_constant" not in names


def test_every_sink_kind_is_reported_on_the_fixture():
    _, report = _fixture_report()
    counts = report.counts()
    assert _sites(report, "store") == [
        ("<module>", "module global MODULE_RAW"),
        ("store_sites", "attr"),
        ("store_sites", "subscript"),
    ]
    assert _sites(report, "arg") == [
        ("call_sites", ".append arg0"),
        ("call_sites", "consume arg0"),
    ]
    assert _sites(report, "return") == [
        ("call_sites", "raw return"),
        ("make_raw", "raw return"),
    ]
    assert _sites(report, "compare") == [("compare_and_truth", "==")]
    assert _sites(report, "truth") == [
        ("compare_and_truth", "condition"),
        ("compare_and_truth", "not"),
        ("compare_and_truth", "or"),
    ]
    assert _sites(report, "mixed") == [("mixed_lane", "cursor")]
    assert _sites(report, "literal") == [("literal_sites", "TupleLit")] or counts["literal"] == 1


def test_sanctioned_raw_consumers_and_extern_parameters_are_not_sinks():
    _, report = _fixture_report()
    safe = [site for site in report.sites if site.function == "safe_uses"]
    assert safe == []
    # ``_buffer_new(raw, 8)`` passes the pointer to a c_ptr extern parameter and
    # returns a raw pointer that ``call_sites`` then returns: one return sink,
    # no argument sink for the extern call itself.
    call_site_args = [site.detail for site in report.sites if site.function == "call_sites" and site.kind == "arg"]
    assert "_buffer_new arg0" not in call_site_args


def test_cli_writes_a_json_receipt(tmp_path):
    tool = _load_tool()
    fixture = tmp_path / "rawptr_fixture.py"
    fixture.write_text(_FIXTURE, encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    assert tool.main([str(fixture), "--json", str(receipt), "--sites", "3"]) == 0
    import json

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["schema"] == "pcc.rawptr-flow-inventory.v3"
    assert payload["pointer_lane_modules"] == []
    assert payload["totals"]["store"] == 3
    assert payload["modules"][0]["module"] == "rawptr_fixture"
    assert payload["parse_failures"] == []


_MARKER_FIXTURE = textwrap.dedent(
    '''
    from pcc.extern import c_int64, c_obj, c_ptr, c_rawptr, extern
    from pcc.unsafe import load_i8

    _utf8 = extern("py_str_utf8", (c_ptr,), c_rawptr)
    _bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_obj)


    def keep(value):
        return value


    def raw_result(text):
        address = _utf8(text)
        keep(address)
        return address


    def object_result(text):
        payload = _bytes_new(_utf8(text), 3)
        keep(payload)
        return payload
    '''
).lstrip()


def test_c_rawptr_returns_are_raw_and_c_obj_returns_are_objects_regardless_of_policy():
    tool = _load_tool()
    for policy in ("raw", "object"):
        report = tool.analyze_source(_MARKER_FIXTURE, "markers.py", "markers", policy)
        assert _sites(report, "arg") == [("raw_result", "keep arg0")], policy
        assert _sites(report, "return") == [("raw_result", "raw return")], policy
        assert report.extern_c_ptr_symbols == []
        assert not report.pointer_lane


def test_pointer_lane_modules_are_flagged_and_excluded_from_totals(tmp_path):
    tool = _load_tool()
    for directive in ("__pcc_freestanding__", "__pcc_runtime_port__"):
        report = tool.analyze_source(directive + " = True\n" + _FIXTURE, "port.py", "port")
        assert report.pointer_lane
    port = tmp_path / "port_fixture.py"
    port.write_text("__pcc_runtime_port__ = True\n" + _FIXTURE, encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    assert tool.main([str(port), "--json", str(receipt)]) == 0
    import json

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["pointer_lane_modules"] == ["port_fixture"]
    assert payload["modules"] == []
    assert sum(payload["totals"].values()) == 0
