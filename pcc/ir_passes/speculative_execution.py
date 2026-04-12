"""SpeculativeExecution — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/SpeculativeExecution.cpp``
  hoists speculatable instructions out of conditional successors into
  their predecessor, based on a cost model that estimates whether
  speculative execution is cheaper than the branch misprediction.

Subset implemented here (labelled ``subset``):

- One very narrow shape: a block whose terminator is
  ``br i1 %c, label %t, label %f`` where one of ``t``/``f`` is a
  block consisting of exactly one speculatable instruction followed
  by an unconditional branch to a join block.
- The speculatable instruction must use only function arguments and
  literal constants as operands, so there is no ordering hazard when
  we move it into the predecessor.
- Supported opcodes: ``add``, ``sub``, ``mul``, ``and``, ``or``,
  ``xor``, ``shl``, ``lshr``, ``ashr``, ``icmp``, ``select``,
  ``zext``, ``sext``, ``trunc``.
- The full upstream cost model, loop-aware hoisting, and multi-block
  speculation windows are deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import BasicBlock, Function, Instruction, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_COND_BR_RE = re.compile(
    r"^\s*br\s+i1\s+(?P<cond>[^,]+),\s*"
    r"label\s+%(?P<t>[\w\.\$]+)\s*,\s*label\s+%(?P<f>[\w\.\$]+)\s*$"
)
_UNCOND_BR_RE = re.compile(r"^\s*br\s+label\s+%(?P<dest>[\w\.\$]+)\s*$")

# Speculatable binary op: `%v = OP TY A, B`.
_BINOP_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*
    (?P<op>add|sub|mul|and|or|xor|shl|lshr|ashr)\b
    (?P<flags>(?:\s+nsw|\s+nuw|\s+exact)*)
    \s+(?P<ty>\S+)\s+
    (?P<lhs>[^,]+?)\s*,\s*
    (?P<rhs>\S+)\s*$
    """,
    re.VERBOSE,
)
_ICMP_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*
    icmp\s+(?P<pred>\w+)\s+
    (?P<ty>\S+)\s+
    (?P<lhs>[^,]+?)\s*,\s*
    (?P<rhs>\S+)\s*$
    """,
    re.VERBOSE,
)
_SELECT_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*
    select\s+i1\s+(?P<cond>[^,]+?)\s*,\s*
    (?P<tty>\S+)\s+(?P<tval>[^,]+?)\s*,\s*
    (?P<fty>\S+)\s+(?P<fval>\S+)\s*$
    """,
    re.VERBOSE,
)
_CAST_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*
    (?P<op>zext|sext|trunc)\s+
    (?P<fromty>\S+)\s+(?P<val>[^,\s]+)\s+
    to\s+(?P<toty>\S+)\s*$
    """,
    re.VERBOSE,
)


class SpeculativeExecutionIRPass(ModulePass):
    """Hoist a single speculatable instruction out of a conditional successor."""

    name = "pcc-speculative-execution"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = speculative_execution_text(str(module))
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def speculative_execution_text(ir_text: str) -> tuple[str, bool]:
    mut = MutableModule.parse(ir_text)
    any_changed = False
    for fn in mut.functions:
        while True:
            if not _try_hoist_one(fn):
                break
            any_changed = True
    if not any_changed:
        return ir_text, False
    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _try_hoist_one(fn: Function) -> bool:
    arg_names = {arg.name for arg in fn.args}
    block_map = {b.name: b for b in fn.blocks}

    for pred in fn.blocks:
        if not pred.instructions:
            continue
        term = pred.instructions[-1]
        m = _COND_BR_RE.match(term.text.rstrip("\n"))
        if m is None:
            continue
        pred_defs = {
            inst.result_name
            for inst in pred.instructions[:-1]
            if inst.result_name
        }

        for succ_label in (m.group("t"), m.group("f")):
            succ = block_map.get(succ_label)
            if succ is None or succ is pred:
                continue
            hoist = _pick_hoistable_instruction(succ, arg_names, pred_defs)
            if hoist is None:
                continue
            inst, result_name = hoist
            if _any_use_outside_join(fn, succ_label, result_name, block_map):
                continue

            # Re-home the instruction: remove from successor, insert
            # immediately before the conditional branch in the
            # predecessor.
            succ.instructions.remove(inst)
            pred.instructions.insert(len(pred.instructions) - 1, inst)
            return True
    return False


def _pick_hoistable_instruction(
    succ: BasicBlock,
    arg_names: set[str],
    pred_defs: set[str],
) -> tuple[Instruction, str] | None:
    if len(succ.instructions) != 2:
        return None
    body = succ.instructions[0]
    term = succ.instructions[1]
    if _UNCOND_BR_RE.match(term.text.rstrip("\n")) is None:
        return None

    text = body.text.rstrip("\n")
    for regex, operand_picker in (
        (_BINOP_RE, lambda mm: (mm.group("lhs"), mm.group("rhs"))),
        (_ICMP_RE, lambda mm: (mm.group("lhs"), mm.group("rhs"))),
        (
            _SELECT_RE,
            lambda mm: (mm.group("cond"), mm.group("tval"), mm.group("fval")),
        ),
        (_CAST_RE, lambda mm: (mm.group("val"),)),
    ):
        mm = regex.match(text)
        if mm is None:
            continue
        operands = operand_picker(mm)
        if not all(
            _operand_is_safe_to_move(op, arg_names, pred_defs) for op in operands
        ):
            return None
        if not body.result_name:
            return None
        return body, body.result_name
    return None


def _operand_is_safe_to_move(
    operand: str,
    arg_names: set[str],
    pred_defs: set[str],
) -> bool:
    """Operand is safe iff it's a constant, function argument, or
    value already defined in the predecessor block.

    This avoids the full dominance check; it still captures the common
    case of hoisting a computation whose inputs flow only from
    arguments / constants / predecessor-local SSA values.
    """
    token = operand.strip()
    if not token:
        return False
    if not token.startswith("%"):
        # Constants like ``3``, ``-1``, ``true``, ``@glob``, etc.
        return True
    name = token[1:]
    return name in arg_names or name in pred_defs


def _any_use_outside_join(
    fn: Function,
    succ_label: str,
    name: str,
    block_map: dict[str, BasicBlock],
) -> bool:
    """Return True if ``%name`` is used outside the successor block's
    unique join target (i.e., somewhere that requires the definition to
    happen conditionally)."""
    succ = block_map[succ_label]
    term = succ.instructions[-1]
    m = _UNCOND_BR_RE.match(term.text.rstrip("\n"))
    if m is None:
        return True
    join_label = m.group("dest")

    pat = re.compile(r"%" + re.escape(name) + r"(?![\w\.])")
    for block in fn.blocks:
        if block.name == succ_label:
            continue
        for inst in block.instructions:
            if pat.search(inst.text) is None:
                continue
            if block.name != join_label:
                return True
            # In the join block, only phi uses are acceptable — and
            # hoisting preserves the incoming edge labelling, so the
            # phi stays valid.
            if inst.opcode != "phi":
                return True
    return False
