"""Focused contracts for closed-world re-export and object-use helpers."""
from __future__ import annotations

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_closed_world


def test_pipeline_closed_world_helper_facade_is_thin():
    for name in (
        "_resolve_ast_import_from_module",
        "_closed_world_star_export_items",
        "_merge_closed_world_reexport_edges",
        "_repair_closed_world_default_global_owners",
        "_mark_closed_world_function_object_exports",
        "_closed_world_function_object_exports",
        "_write_reexport_edges_wire",
        "_read_reexport_edges_wire",
        "_closed_world_shallow_lift_module",
    ):
        assert getattr(pipeline, name) is getattr(pipeline_closed_world, name)


def test_reexport_edges_converge_transitively_without_copying_metadata():
    function = {
        "kind": "function",
        "owning_module": "pkg.source",
        "symbol": "user_pkg_source_run",
    }
    exports = {
        "pkg.source": {"run": function, "_private": {"kind": "global"}},
        "pkg.middle": {},
        "pkg.api": {},
    }
    edges = (
        ("pkg.middle", "pkg.source", "run", "renamed", False),
        ("pkg.api", "pkg.middle", "renamed", "run", False),
    )
    pipeline_closed_world._merge_closed_world_reexport_edges(
        ("pkg.source", "pkg.middle", "pkg.api"), exports, edges
    )
    assert exports["pkg.middle"]["renamed"] is function
    assert exports["pkg.api"]["run"] is function


def test_function_object_use_marks_only_the_requested_owner_export():
    exports = {
        "pkg.mod": {
            "called": {"kind": "function", "owning_module": "pkg.mod"},
            "direct": {"kind": "function", "owning_module": "pkg.mod"},
            "foreign": {"kind": "function", "owning_module": "pkg.other"},
        }
    }
    pipeline_closed_world._apply_closed_world_function_object_uses(
        exports, (("pkg.mod", "called"), ("pkg.mod", "foreign"))
    )
    assert pipeline_closed_world._closed_world_function_object_exports(
        exports, "pkg.mod"
    ) == {"called": True}


def test_reexport_edge_wire_roundtrip_preserves_order(tmp_path):
    edges = (
        ("pkg.api", "pkg.impl", "run", "run", False),
        ("pkg.api", "pkg.extra", "*", "", True),
    )
    path = tmp_path / "reexports.json"
    pipeline_closed_world._write_reexport_edges_wire(path, edges)
    assert pipeline_closed_world._read_reexport_edges_wire(path) == edges

