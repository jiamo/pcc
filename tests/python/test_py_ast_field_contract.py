"""The AST field-order contract is hand-maintained in two places. Pin both.

`pcc/py_frontend/py_ast_contract.py` pins the field order of every
`pcc.py_frontend.py_ast` node so a self-compiled unit and the unit that
references it agree on class layout. `pipeline_ast_wire.py` owns the wire-side
copy (it cannot import the contract across the module boundary in the compiled
closure), `pipeline.py` re-exports it, and `class_gen.py` reads the contract
copy.

Two hand-maintained copies of a layout contract drift, and they already had:
`SetType` reached the pipeline copy and never the contract file, and
`ValueArrayType` reached neither, so those two nodes silently fell back to
inferred field order on one path and pinned order on the other
(AUD-P2-SELF-MODULE-SPECIAL-CASES-IN-CODEGEN).

The checks here are the enforcement the mechanism never had: the copies must
be equal, the pinned order must match the real dataclass, and every AST node
must be covered.
"""

from __future__ import annotations

import dataclasses
import inspect

import pcc.py_frontend.py_ast as py_ast
import pcc.py_frontend.pipeline as pipeline
import pcc.py_frontend.pipeline_ast_wire as pipeline_ast_wire
from pcc.py_frontend.py_ast_contract import PY_AST_FIELD_NAME_OVERRIDES


def _ast_dataclasses():
    return {
        name: obj
        for name, obj in vars(py_ast).items()
        if inspect.isclass(obj)
        and dataclasses.is_dataclass(obj)
        and obj.__module__ == py_ast.__name__
    }


def test_the_two_copies_of_the_contract_are_identical():
    ours = {k: tuple(v) for k, v in PY_AST_FIELD_NAME_OVERRIDES.items()}
    theirs = {
        k: tuple(v)
        for k, v in pipeline_ast_wire._PY_AST_FIELD_NAME_OVERRIDES.items()
    }
    assert (
        pipeline._PY_AST_FIELD_NAME_OVERRIDES
        is pipeline_ast_wire._PY_AST_FIELD_NAME_OVERRIDES
    )
    only_contract = sorted(set(ours) - set(theirs))
    only_pipeline = sorted(set(theirs) - set(ours))
    differing = sorted(k for k in set(ours) & set(theirs) if ours[k] != theirs[k])
    assert not (only_contract or only_pipeline or differing), (
        "py_ast_contract.py and pipeline.py disagree about AST field order:\n"
        f"  only in py_ast_contract: {only_contract}\n"
        f"  only in pipeline:        {only_pipeline}\n"
        f"  differing order:         {differing}"
    )


def test_layout_and_wire_tables_use_the_same_semantic_fields():
    assert not hasattr(pipeline_ast_wire, "_PY_AST_WIRE_FIELD_NAME_OVERRIDES")
    for field_names in PY_AST_FIELD_NAME_OVERRIDES.values():
        assert "kind_id" not in field_names


def test_every_pinned_order_matches_the_real_dataclass():
    classes = _ast_dataclasses()
    mismatches = []
    for name, pinned in PY_AST_FIELD_NAME_OVERRIDES.items():
        cls = classes.get(name)
        if cls is None:
            mismatches.append(f"{name}: pinned but no such dataclass in py_ast")
            continue
        actual = tuple(f.name for f in dataclasses.fields(cls))
        if actual != tuple(pinned):
            mismatches.append(f"{name}: dataclass {actual}, pinned {tuple(pinned)}")
    assert not mismatches, "\n  ".join(["pinned field order is wrong:"] + mismatches)


def test_every_ast_node_is_pinned():
    """An unpinned node takes the inferred path on one side and the pinned path
    on the other — which is exactly how SetType and ValueArrayType drifted."""
    unpinned = sorted(set(_ast_dataclasses()) - set(PY_AST_FIELD_NAME_OVERRIDES))
    assert not unpinned, (
        "these py_ast nodes have no pinned field order; add them to "
        "py_ast_contract.py AND pipeline.py's copy:\n  " + ", ".join(unpinned)
    )
