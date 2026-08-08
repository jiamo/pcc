"""No integer *literal* above the tagged lane may appear in the self-host closure.

A literal integer constant larger than the tagged small-int lane evaluates to
**0** in pcc-compiled code, while the same value *computed* (``(1 << 64) - 1``)
is correct. Probed directly with a pcc1-built binary:

    1 << 63                -> 9223372036854775808   correct
    (1 << 64) - 1          -> 18446744073709551615   correct
    9223372036854775808    -> 0                      WRONG
    0xFFFFFFFFFFFFFFFF     -> 0                      WRONG

This is why pcc2 could not print any integer: `_emit_int_literal_object`
masked the tagged constant with a literal ``0xFFFFFFFFFFFFFFFF``, which under
pcc2 became ``& 0``, so every int literal lowered to a NULL pointer. The mask
was redundant anyway -- an in-lane value's ``(v << 1) | 1`` is already a valid
signed i64.

The underlying frontend/runtime defect is tracked as
M5-SELFHOST-BIG-INT-LITERAL. Until it is closed, this test keeps the hazard out
of the modules that pcc compiles into itself. Write the value as an expression
(``(1 << 64) - 1``) rather than as a literal.
"""

from __future__ import annotations

import ast
from pathlib import Path

from pcc1_gate import repo_root

REPO = repo_root()

# The tagged small-int lane. A literal at or below this is lowered correctly.
_LANE_MAX = (1 << 62) - 1

# Compiled into pcc1/pcc2. Runtime ports under py_runtime/py are covered by
# their own freestanding gates and use a different literal pipeline.
_CLOSURE_DIRS = ("pcc/py_frontend", "pcc/backend")


def _over_lane_literals(path: Path) -> list[tuple[int, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover - a parse failure is another test's job
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            if abs(node.value) > _LANE_MAX:
                found.append((node.lineno, node.value))
    return found


def test_self_host_closure_has_no_over_lane_int_literals():
    offenders = []
    for directory in _CLOSURE_DIRS:
        for path in sorted((REPO / directory).rglob("*.py")):
            for lineno, value in _over_lane_literals(path):
                offenders.append(
                    f"{path.relative_to(REPO)}:{lineno}: {value} "
                    f"(> {_LANE_MAX}); write it as an expression instead"
                )
    assert not offenders, (
        "integer literals above the tagged lane evaluate to 0 in pcc-compiled "
        "code:\n  " + "\n  ".join(offenders)
    )
