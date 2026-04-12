"""LowerConstantIntrinsics — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LowerConstantIntrinsics.cpp``
  lowers ``llvm.is.constant.*`` queries to boolean constants.

Subset implemented here (labelled ``subset``):

- Fold direct calls to ``llvm.is.constant.*``.
- Return ``true`` when the queried operand is an immediate constant.
- Return ``false`` when the queried operand is an SSA value.
- Drop now-unused ``declare`` lines for the intrinsic.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import Function, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_IS_CONSTANT_CALL_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<res>%[\w\.]+)\s*=\s*)?
    (?:(?:tail|musttail|notail)\s+)?
    call\s+i1\s+@llvm\.is\.constant\.[^(\s]+\((?P<arg>.*)\)\s*$
    """,
    re.VERBOSE,
)
_IS_CONSTANT_DECLARE_RE = re.compile(
    r"^\s*declare\s+i1\s+@llvm\.is\.constant\.[^(\s]+\([^)]*\)\s*$"
)


class LowerConstantIntrinsicsPass(ModulePass):
    name = "pcc-lower-constant-intrinsics"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = lower_constant_intrinsics_text(str(module))
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def lower_constant_intrinsics_text(ir_text: str) -> tuple[str, bool]:
    mut = MutableModule.parse(ir_text)
    changed = False
    for fn in mut.functions:
        if _rewrite_in_function(fn):
            changed = True
    if not changed:
        return ir_text, False
    mut.declarations = [
        line for line in mut.declarations
        if not _IS_CONSTANT_DECLARE_RE.match(line.strip())
    ]
    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _rewrite_in_function(fn: Function) -> bool:
    changed = False
    for block in fn.blocks:
        idx = 0
        while idx < len(block.instructions):
            inst = block.instructions[idx]
            m = _IS_CONSTANT_CALL_RE.match(inst.text.strip())
            if m is None:
                idx += 1
                continue
            result = m.group("res")
            arg = _first_arg_value(m.group("arg"))
            replacement = "true" if _is_immediate_constant(arg) else "false"
            del block.instructions[idx]
            if result is not None:
                _replace_value_uses(fn, result, replacement)
            changed = True
    return changed


def _first_arg_value(arg_text: str) -> str:
    parts = arg_text.strip().split()
    return parts[-1] if parts else arg_text.strip()


def _is_immediate_constant(token: str) -> bool:
    tok = token.strip()
    if tok.startswith("%"):
        return False
    return True


def _replace_value_uses(fn: Function, old: str, new: str) -> None:
    pat = re.compile(re.escape(old) + r"(?![\w\.])")
    for block in fn.blocks:
        for inst in block.instructions:
            inst.text = pat.sub(new, inst.text)
