"""Focused contracts for closed-world export metadata extraction."""
from __future__ import annotations

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_exports
from pcc.py_frontend import type_infer
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
        "_read_native_exports_wire_for_module",
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


def test_indexed_native_export_wire_materializes_only_the_required_closure(
    tmp_path,
):
    function = {
        "kind": "function",
        "param_types": (),
        "return_ty": ("dyn",),
        "call_sig": (),
    }
    exports = {
        "root": {
            "entry": dict(function),
            "remote": {
                "kind": "module_global",
                "owning_module": "metadata.owner",
                "export_name": "remote",
                "value_ty": ("dyn",),
            },
        },
        "dep": {"dep": dict(function)},
        "leaf": {"leaf": dict(function)},
        "metadata.owner": {"remote": dict(function)},
        "unique.owner": {
            "Solo": {
                "kind": "class",
                "class_name": "Solo",
                "owning_module": "unique.owner",
                "base_names": (),
                "field_names": (),
                "field_types": (),
            }
        },
        "dup.a": {
            "Shared": {
                "kind": "class",
                "class_name": "Shared",
                "owning_module": "dup.a",
                "base_names": (),
                "field_names": (),
                "field_types": (),
            }
        },
        "dup.b": {
            "Shared": {
                "kind": "class",
                "class_name": "Shared",
                "owning_module": "dup.b",
                "base_names": (),
                "field_names": (),
                "field_types": (),
            }
        },
        "unrelated": {"unused": dict(function)},
    }
    dependencies = {
        "root": ("dep",),
        "dep": ("leaf",),
    }
    path = tmp_path / "native-exports.indexed"
    pipeline_exports._write_native_exports_wire(
        path,
        exports,
        {},
        module_dependencies=dependencies,
        unique_class_preload_index=(
            type_infer.build_unique_external_class_preload_index(exports)
        ),
    )

    full, _derived = pipeline_exports._read_native_exports_wire(path)
    assert full == exports
    assert pipeline_exports._read_native_exports_wire_raw_modules(path) == (
        pipeline_exports._native_export_to_wire(exports)
    )
    selected, _derived, unique_preload, indexed = (
        pipeline_exports._read_native_exports_wire_for_module(path, "root")
    )

    assert indexed is True
    assert tuple(selected) == (
        "root",
        "dep",
        "leaf",
        "metadata.owner",
    )
    assert selected == {name: exports[name] for name in selected}
    expected_preload = type_infer.build_unique_external_class_preload(
        {name: value for name, value in exports.items() if name != "root"}
    )
    dropped = set(unique_preload["drop_keys"])
    actual_by_key = {
        key: unique_preload["types"][type_id]
        for key, type_id in unique_preload["base_keys"]
        if key not in dropped
    }
    for key, type_id in unique_preload["set_keys"]:
        actual_by_key[key] = unique_preload["types"][type_id]
    expected_by_key = {
        key: expected_preload["types"][type_id]
        for key, type_id in expected_preload["keys"]
    }
    assert actual_by_key == expected_by_key
    assert "Solo" in actual_by_key
    assert "unrelated" not in selected
    assert "unique.owner" not in selected
    assert "dup.a" not in selected
    assert "dup.b" not in selected


def test_indexed_native_export_wire_rejects_stale_dependency(tmp_path):
    path = tmp_path / "native-exports.indexed"
    pipeline_exports._write_native_exports_wire(
        path,
        {"root": {}},
        {},
        module_dependencies={"root": ()},
        unique_class_preload_index=(
            type_infer.build_unique_external_class_preload_index({"root": {}})
        ),
    )
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace('P\t{"root": []}', 'P\t{"root": ["missing"]}'),
        encoding="utf-8",
    )

    try:
        pipeline_exports._read_native_exports_wire_for_module(path, "root")
    except Exception as exc:
        assert "dependency" in str(exc)
    else:
        raise AssertionError("stale indexed dependency was accepted")


def test_indexed_contextual_host_surface_is_lazy_and_root_scoped(tmp_path):
    class_info = {
        "kind": "class",
        "class_name": "Host",
        "owning_module": "host.module",
        "base_names": (),
        "field_names": (),
        "field_types": (),
    }
    exports = {
        "ordinary": {},
        "contextual": {},
        "host.module": {"Host": class_info, "unused": {"kind": "constant"}},
    }
    path = tmp_path / "native-exports.indexed"
    pipeline_exports._write_native_exports_wire(
        path,
        exports,
        {},
        module_dependencies={name: () for name in exports},
        unique_class_preload_index=(
            type_infer.build_unique_external_class_preload_index(exports)
        ),
        contextual_modules=("contextual",),
        contextual_host_exports={"host.module": {"Host": class_info}},
    )

    ordinary, _derived, _preload, indexed = (
        pipeline_exports._read_native_exports_wire_for_module(path, "ordinary")
    )
    assert indexed is True
    assert tuple(ordinary) == ("ordinary",)

    contextual, _derived, _preload, indexed = (
        pipeline_exports._read_native_exports_wire_for_module(path, "contextual")
    )
    assert indexed is True
    assert tuple(contextual) == ("contextual", "host.module")
    assert contextual["host.module"] == {"Host": class_info}
