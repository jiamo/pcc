"""Focused contracts for closed-world export metadata extraction."""
from __future__ import annotations

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_exports
from pcc.py_frontend.export_meta import encode_type


def test_pipeline_export_metadata_facade_is_thin():
    for name in (
        "_export_param_types",
        "_export_returns_none",
        "_closed_world_is_node",
        "_normalise_export_annotation_text",
        "_export_call_sig",
        "_export_default_to_wire",
        "_export_default_from_wire",
        "_write_native_exports_wire",
        "_read_native_exports_wire",
        "_export_method_symbol",
    ):
        assert getattr(pipeline, name) is getattr(pipeline_exports, name)


def test_nested_annotation_text_normalises_without_driver_state():
    annotation = pipeline_exports._normalise_export_annotation_text(
        "dict[str, list[tuple[int, bytes]]]"
    )
    assert encode_type(annotation) == (
        "dict",
        ("str",),
        ("list", ("tuple", (("int", 64, True), ("bytes",)))),
    )


def test_export_default_wire_roundtrip_preserves_nested_safe_values():
    key = pipeline_exports._EXPORT_DEFAULT_WIRE_KEY
    wire = {
        key: "dict",
        "pairs": [
            (
                {key: "str", "value": "threshold"},
                {
                    key: "tuple",
                    "elems": [
                        {key: "int", "value": -21},
                        {key: "bytes", "value": [0, 127, 255]},
                    ],
                },
            )
        ],
    }
    value = pipeline_exports._export_default_from_wire(wire)
    assert pipeline_exports._export_default_to_wire(value) == wire
    assert pipeline_exports._export_default_wire_is_safe(wire)


def test_native_export_manifest_roundtrip_is_deterministic(tmp_path):
    exports = {
        "pkg.mod": {
            "f": {
                "kind": "function",
                "param_types": (("int",),),
                "return_ty": ("str",),
                "call_sig": (),
            }
        }
    }
    derived = {"pkg.Base": ("pkg.Child",)}
    uses = (("pkg.mod", "f"),)
    path = tmp_path / "native-exports.json"
    pipeline_exports._write_native_exports_wire(path, exports, derived, uses)
    assert pipeline_exports._read_native_exports_wire(
        path, include_function_object_uses=True
    ) == (exports, derived, uses)
