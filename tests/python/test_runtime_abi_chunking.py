from __future__ import annotations

import ast
from pathlib import Path

from pcc.py_frontend.codegen import runtime_abi
from pcc.py_frontend.codegen.runtime_abi import (
    FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
    RUNTIME_SIGNATURES,
)


SOURCE = (
    Path(__file__).resolve().parents[2]
    / "pcc"
    / "py_frontend"
    / "codegen"
    / "runtime_abi.py"
)


def _chunk_keys(prefix: str) -> tuple[list[str], list[int]]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    keys: list[str] = []
    sizes: list[int] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith(prefix):
            continue
        returns = [stmt for stmt in node.body if isinstance(stmt, ast.Return)]
        assert len(returns) == 1
        value = returns[0].value
        assert isinstance(value, ast.Dict)
        sizes.append(len(value.keys))
        for key in value.keys:
            assert isinstance(key, ast.Constant) and isinstance(key.value, str)
            keys.append(key.value)
    return keys, sizes


def _assemble_chunks(prefix: str) -> dict:
    parts = []
    for name, value in vars(runtime_abi).items():
        if name.startswith(prefix):
            parts.append((int(name.rsplit("_", 1)[1]), value))
    assembled = {}
    for _index, part in sorted(parts):
        assembled.update(part())
    return assembled


def test_runtime_signature_literals_are_bounded_function_chunks() -> None:
    keys, sizes = _chunk_keys("_runtime_signatures_part_")

    assert sizes
    assert max(sizes) <= 50
    assert len(keys) == len(set(keys))
    assert _assemble_chunks("_runtime_signatures_part_") == RUNTIME_SIGNATURES
    assert keys == list(RUNTIME_SIGNATURES)


def test_cross_object_signature_literals_are_bounded_function_chunks() -> None:
    keys, sizes = _chunk_keys("_cross_object_signatures_part_")

    assert sizes
    assert max(sizes) <= 50
    assembled = _assemble_chunks("_cross_object_signatures_part_")
    assert assembled == FREESTANDING_GC_CROSS_OBJECT_SIGNATURES
    assert list(assembled) == list(FREESTANDING_GC_CROSS_OBJECT_SIGNATURES)
