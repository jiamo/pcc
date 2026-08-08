"""Declarative contracts for self-host-sensitive codegen modules."""

from pathlib import Path

from pcc.py_frontend.codegen import layer1_support
from pcc.py_frontend.codegen.self_module_contracts import (
    IR_SCAFFOLD_CONTRACT,
    L1_CODEGEN_HOST_ATTR_CONTRACT,
    PY_AST_FIELD_ORDER_CONTRACT,
    module_for_class_symbol_contract,
    module_has_contract,
)


ROOT = Path(__file__).resolve().parents[2]


def test_self_module_capabilities_are_declared_as_registry_data():
    assert module_has_contract(
        "pcc.py_frontend.codegen.runtime_abi",
        IR_SCAFFOLD_CONTRACT,
    )
    assert module_has_contract(
        "pcc.py_frontend.codegen.layer1",
        L1_CODEGEN_HOST_ATTR_CONTRACT,
    )
    assert module_has_contract(
        "pcc.py_frontend.py_ast",
        PY_AST_FIELD_ORDER_CONTRACT,
    )
    assert not module_has_contract("third_party.module", IR_SCAFFOLD_CONTRACT)


def test_extern_class_symbol_resolves_through_the_same_contract_registry():
    assert module_for_class_symbol_contract(
        ".class.pcc_py_frontend_py_ast.IntType",
        PY_AST_FIELD_ORDER_CONTRACT,
    ) == "pcc.py_frontend.py_ast"
    assert module_for_class_symbol_contract(
        ".class.unrelated_IntType",
        PY_AST_FIELD_ORDER_CONTRACT,
    ) is None


def test_codegen_sites_request_capabilities_instead_of_naming_owners():
    class_gen = (ROOT / "pcc/py_frontend/codegen/class_gen.py").read_text()
    scaffold = (
        ROOT / "pcc/py_frontend/codegen/ir_scaffold_lowering.py"
    ).read_text()
    assert "module_has_contract(" in class_gen
    assert "module_for_class_symbol_contract(" in class_gen
    assert '== "pcc.py_frontend.py_ast"' not in class_gen
    assert "module_has_contract(" in scaffold
    assert " in IR_SCAFFOLD_FORCED_MODULES" not in scaffold

    codegen_root = ROOT / "pcc/py_frontend/codegen"
    direct_source_guards = []
    for path in codegen_root.glob("*.py"):
        if 'module.name == "pcc.' in path.read_text():
            direct_source_guards.append(path.name)
    assert direct_source_guards == []


def test_default_native_exports_use_the_single_module_registry():
    for module_name in layer1_support._PCC_FRONTEND_STATIC_NATIVE_MODULES:
        assert layer1_support._default_native_module_exports(module_name) is (
            layer1_support._PCC_FRONTEND_STATIC_NATIVE_EXPORTS
        )
    assert layer1_support._default_native_module_exports("unknown.module") is None
    source = (ROOT / "pcc/py_frontend/codegen/layer1_support.py").read_text()
    function_source = source.split("def _default_native_module_exports", 1)[1]
    function_source = function_source.split("\ndef ", 1)[0]
    assert 'module_name == "pcc.' not in function_source
