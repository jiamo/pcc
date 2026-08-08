"""Focused contracts for the extracted closed-world context seam."""

from __future__ import annotations


def test_pipeline_context_facade_has_single_function_owners():
    from pcc.py_frontend import pipeline
    from pcc.py_frontend import pipeline_context as context

    for name in (
        "build_closed_world_context",
        "_closed_world_derived_class_map",
        "_merge_l1_mixin_stack_methods",
        "_merge_l1_codegen_methods",
        "_contextual_host_params_for_module",
        "count_py_cpy_fallback_calls",
        "_copy_native_module_exports",
        "_module_uses_default_native_exports",
        "compile_contextual_per_module_fallback_counts",
    ):
        assert getattr(pipeline, name) is getattr(context, name)


def test_context_fallback_counter_counts_calls_not_declarations():
    from pcc.py_frontend.pipeline_context import count_py_cpy_fallback_calls

    ir = """\
declare ptr @py_cpy_import(ptr)
%one = call ptr @py_cpy_import(ptr %name)
%two = call i1 @py_cpy_truthy(ptr %one)
"""
    assert count_py_cpy_fallback_calls(ir) == 2


def test_closed_world_derived_map_only_selects_unique_owners():
    from pcc.py_frontend.pipeline_context import _closed_world_derived_class_map

    exports = {
        "left": {
            "Only": {"kind": "class", "base_names": ("Unique",)},
            "First": {"kind": "class", "base_names": ("Shared",)},
        },
        "right": {
            "Second": {"kind": "class", "base_names": ("Shared",)},
        },
    }

    assert _closed_world_derived_class_map(exports) == {
        "Unique": ("left", "Only")
    }


def test_computed_raw_int_module_global_exports_provider_storage_abi(tmp_path):
    from pcc.py_frontend.pipeline_context import build_closed_world_context

    src = tmp_path / "provider.py"
    src.write_text("FLAG = 1 << 1\n", encoding="utf-8")

    _modules, exports, _derived = build_closed_world_context(
        [str(src)],
        ["pcc.provider"],
        merge_exports=False,
    )

    exported = exports["pcc.provider"]["FLAG"]
    assert exported["kind"] == "module_global"
    assert exported["value_ty"][0] == "int"
    assert exported["box_int_abi"] is False


def test_unpacked_init_fields_keep_export_and_type_schema_order(tmp_path):
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.pipeline_context import build_closed_world_context
    from pcc.py_frontend.py_ast import ClassType
    from pcc.py_frontend.type_infer import infer_module

    src = tmp_path / "provider.py"
    src.write_text(
        "class Record:\n"
        "    def __init__(self, name: str):\n"
        "        self.name = name\n"
        "        self.left, self.right = (1, 2)\n"
        "        self.size = 3\n"
        "\n"
        "def read(record: Record):\n"
        "    return record.size\n",
        encoding="utf-8",
    )

    _modules, exports, _derived = build_closed_world_context(
        [str(src)],
        ["provider"],
        merge_exports=False,
    )
    assert exports["provider"]["Record"]["field_names"] == (
        "name",
        "left",
        "right",
        "size",
    )

    typed = infer_module(
        parse_and_lift(src.read_text(encoding="utf-8"), str(src), "provider")
    )
    read_fn = typed.body[1]
    record_ty = read_fn.args[0].annotation
    assert isinstance(record_ty, ClassType)
    assert tuple(name for name, _ty in record_ty.fields) == (
        "name",
        "left",
        "right",
        "size",
    )
