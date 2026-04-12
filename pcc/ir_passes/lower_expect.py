"""LowerExpectIntrinsic — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LowerExpectIntrinsic.cpp``
  lowers ``llvm.expect`` intrinsics into ordinary SSA values plus
  branch/switch/select profile metadata.

Subset implemented here (labelled ``subset``):

- Strip direct ``llvm.expect.*`` and
  ``llvm.expect.with.probability.*`` calls.
- Replace all SSA uses of the intrinsic result with the first
  intrinsic operand.
- Drop now-unused ``declare`` lines for those intrinsics.

We do not currently recreate ``!prof`` metadata, so this pass matches
the structural SSA rewrite but not the full metadata payload.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import Function, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_EXPECT_CALL_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<result>%[\w\.]+)\s*=\s*)?
    (?:(?:tail|musttail|notail)\s+)?
    call\s+(?P<ret>.+?)\s+
    @llvm\.expect(?:\.with\.probability)?\.[^(\s]+\(
    (?P<args>.*)
    \)\s*$
    """,
    re.VERBOSE,
)
_EXPECT_DECLARE_RE = re.compile(
    r"^\s*declare\s+.+?@llvm\.expect(?:\.with\.probability)?\.[^(\s]+\([^)]*\)\s*$"
)


class LowerExpectPass(ModulePass):
    name = "pcc-lower-expect"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = lower_expect_text(str(module))
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def lower_expect_text(ir_text: str) -> tuple[str, bool]:
    mut = MutableModule.parse(ir_text)
    any_changed = False
    for fn in mut.functions:
        if _lower_expect_in_function(fn):
            any_changed = True
    if not any_changed:
        return ir_text, False
    _drop_unused_expect_declares(mut)
    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _lower_expect_in_function(fn: Function) -> bool:
    changed = False
    for block in fn.blocks:
        idx = 0
        while idx < len(block.instructions):
            inst = block.instructions[idx]
            m = _EXPECT_CALL_RE.match(inst.text.strip())
            if m is None:
                idx += 1
                continue
            replacement = _first_arg_value(m.group("args"))
            if replacement is None:
                idx += 1
                continue
            result = m.group("result")
            del block.instructions[idx]
            if result is not None:
                _replace_value_uses(fn, result.lstrip("%"), replacement)
            changed = True
    return changed


def _first_arg_value(args_text: str) -> str | None:
    parts = [part.strip() for part in args_text.split(",")]
    if not parts:
        return None
    first = parts[0]
    tokens = first.split()
    if len(tokens) < 2:
        return None
    return tokens[-1]


def _replace_value_uses(fn: Function, old: str, new: str) -> None:
    pat = re.compile(r"%" + re.escape(old) + r"(?![\w\.])")
    for block in fn.blocks:
        for inst in block.instructions:
            inst.text = pat.sub(new, inst.text)


def _drop_unused_expect_declares(mut: MutableModule) -> None:
    live_text = "".join(fn.serialize() for fn in mut.functions)
    kept: list[str] = []
    for line in mut.declarations:
        if not _EXPECT_DECLARE_RE.match(line.strip()):
            kept.append(line)
            continue
        name_m = re.search(r"@([\w\.\$]+)", line)
        if name_m is None:
            kept.append(line)
            continue
        name = f"@{name_m.group(1)}"
        if re.search(re.escape(name) + r"\b", live_text):
            kept.append(line)
    mut.declarations = kept
