"""Late scalar cleanups: DivRemPairs + ConstantMerge + misc.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/DivRemPairs.cpp``
  combines adjacent ``sdiv``/``srem`` (or ``udiv``/``urem``) on the
  same operand pair into a single divmod-style computation. The
  concrete rewrite:

      %q = sdiv a, b
      %r = srem a, b     →     %r = sub a, %q*b         (sdiv kept,
                                                         srem dropped)

  Upstream emits the combined form ``@llvm.smul.fix`` or leaves the
  pattern for the target to lower; we emit the explicit sub form,
  which is always correct and lets InstCombine / target lowering do
  the rest.

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/ConstantMerge.cpp``
  merges identical unnamed globals (``@0 = constant ...`` shapes),
  combining call-site string literals and similar duplicates.

Subset implemented here (labelled ``subset``):

- ``div-rem``: pair up ``sdiv`` + ``srem`` (or ``udiv`` + ``urem``)
  instructions that share operands within the same basic block,
  rewriting the rem as ``sub a, q*b``. The sdiv stays.
- ``constmerge``: identify unnamed constants that have identical
  initializers and replace all references to the duplicates with
  references to a chosen representative.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class LateScalarPass(ModulePass):
    name = "pcc-late-scalar"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        t1, c1 = constmerge_text(ir_text)
        t2, c2 = divrem_pairs_text(t1)
        if not (c1 or c2):
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(t2).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = t2
        return PreservedAnalyses.none()


# ---------------------------------------------------------------------------
# DivRemPairs
# ---------------------------------------------------------------------------


_DIV_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<name>[\w\.]+)\s*=\s*"
    r"(?P<op>sdiv|udiv)\s+(?P<ty>i\d+)\s+"
    r"(?P<a>[^,]+?)\s*,\s*(?P<b>.+?)\s*$"
)
_REM_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<name>[\w\.]+)\s*=\s*"
    r"(?P<op>srem|urem)\s+(?P<ty>i\d+)\s+"
    r"(?P<a>[^,]+?)\s*,\s*(?P<b>.+?)\s*$"
)


def divrem_pairs_text(ir_text: str) -> tuple[str, bool]:
    lines = ir_text.splitlines(keepends=True)
    block_start = 0
    changed = False

    i = 0
    out: list[str] = []
    divs_in_block: list[tuple[int, dict]] = []
    label_re = re.compile(r"^\s*[\w\.]+:\s*(?:;.*)?$")

    def flush_block(start: int, end: int):
        nonlocal changed
        # Pair divs with rems (same a, b, same-sign op) in [start, end).
        div_map: dict[tuple[str, str, str], tuple[int, str]] = {}
        for idx in range(start, end):
            m = _DIV_RE.match(lines[idx].rstrip("\n"))
            if m:
                k = (m.group("ty"), m.group("a").strip(), m.group("b").strip())
                div_map[k] = (idx, m.group("name"))
        for idx in range(start, end):
            m = _REM_RE.match(lines[idx].rstrip("\n"))
            if not m:
                continue
            k = (m.group("ty"), m.group("a").strip(), m.group("b").strip())
            div_info = div_map.get(k)
            if div_info is None:
                continue
            if not _compatible_signs(m.group("op"), _DIV_RE.match(lines[div_info[0]].rstrip("\n")).group("op")):
                continue
            div_idx, div_name = div_info
            # Rewrite rem as: %name_mul = mul ty %div_name, b ;
            # %name = sub ty a, %name_mul
            rem_name = m.group("name")
            ty = m.group("ty")
            a = m.group("a").strip()
            b = m.group("b").strip()
            indent = m.group("indent")
            mul_name = f"{rem_name}.mul"
            new_line = (
                f"{indent}%{mul_name} = mul {ty} %{div_name}, {b}\n"
                f"{indent}%{rem_name} = sub {ty} {a}, %{mul_name}\n"
            )
            lines[idx] = new_line
            changed = True

    for idx, line in enumerate(lines):
        if idx == 0 or label_re.match(line) or line.strip().startswith("define"):
            if idx > block_start:
                flush_block(block_start, idx)
            block_start = idx + 1
    flush_block(block_start, len(lines))

    return "".join(lines), changed


def _compatible_signs(rem_op: str, div_op: str) -> bool:
    return (rem_op.startswith("s") and div_op.startswith("s")) or (
        rem_op.startswith("u") and div_op.startswith("u")
    )


# ---------------------------------------------------------------------------
# ConstantMerge
# ---------------------------------------------------------------------------


_UNNAMED_GLOBAL_RE = re.compile(
    r"^\s*@(?P<name>\.?[\w\.]+)\s*=\s*"
    r"(?P<linkage>private\s+|internal\s+|)"
    r"(?P<unnamed>unnamed_addr\s+|)"
    r"(?P<mut>constant\s+|global\s+)"
    r"(?P<init>.+)$"
)


def constmerge_text(ir_text: str) -> tuple[str, bool]:
    """Merge unnamed-addr constants with identical initializers."""
    lines = ir_text.splitlines(keepends=True)
    # Group: (linkage, init) → list of (line_idx, name)
    groups: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for idx, line in enumerate(lines):
        m = _UNNAMED_GLOBAL_RE.match(line.rstrip("\n"))
        if not m:
            continue
        # Require either `private`/`internal` linkage AND unnamed_addr,
        # and must be `constant` (not `global`).
        if not m.group("linkage"):
            continue
        if not m.group("unnamed"):
            continue
        if "constant" not in m.group("mut"):
            continue
        key = (m.group("linkage").strip(), m.group("init").strip())
        groups.setdefault(key, []).append((idx, m.group("name")))

    replacements: dict[str, str] = {}
    dead_lines: set[int] = set()
    for group_members in groups.values():
        if len(group_members) < 2:
            continue
        # Keep the first, drop the rest.
        keeper_idx, keeper_name = group_members[0]
        for dup_idx, dup_name in group_members[1:]:
            replacements[dup_name] = keeper_name
            dead_lines.add(dup_idx)

    if not replacements:
        return ir_text, False

    kept = [ln for i, ln in enumerate(lines) if i not in dead_lines]
    text = "".join(kept)
    for dup, keeper in replacements.items():
        text = re.sub(
            r"@" + re.escape(dup) + r"\b", f"@{keeper}", text
        )
    return text, True
