"""Loop Rotate — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LoopRotation.cpp``
  converts while-style loops into rotated do-while style loops.

Subset implemented here (labelled ``subset``):

- The loop must be a 2-block counting loop:
  - unique preheader,
  - header with exactly one phi, one ``icmp``, and one conditional
    branch to ``body`` / ``exit``,
  - body with exactly one side-effect-free arithmetic instruction
    followed by ``br label %header``,
  - the body's arithmetic result is the header phi's backedge value.
- The exit block may return the header phi value directly.

Transform:

1. Fold the body arithmetic into the header.
2. Rewrite the header phi's backedge to come from ``%header`` instead
   of ``%body``.
3. Rewrite the header branch so the true edge loops back to ``%header``.
4. Remove the old body block.
5. If the exit directly returns the header phi, materialize the
   upstream-style LCSSA phi in the exit.

Wider loop rotation (precheck insertion, multi-block bodies, dedicated
exit canonicalization) remains deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG
from .ir_mutator import Instruction, MutableModule
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_PHI_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*phi\s+(?P<ty>.+?)\s+"
    r"\[\s*(?P<init>[^,\]]+?)\s*,\s*%(?P<pre>[\w\.\$]+)\s*\]\s*,\s*"
    r"\[\s*(?P<back>[^,\]]+?)\s*,\s*%(?P<latch>[\w\.\$]+)\s*\]\s*$"
)
_ICMP_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*icmp\s+(?P<pred>\w+)\s+"
    r"(?P<ty>\S+)\s+(?P<lhs>[^,]+?)\s*,\s*(?P<rhs>.+?)\s*$"
)
_COND_BR_RE = re.compile(
    r"^\s*br\s+i1\s+%(?P<cond>[\w\.]+)\s*,\s*label\s+%(?P<t>[\w\.\$]+)\s*,\s*label\s+%(?P<f>[\w\.\$]+)\s*$"
)
_UNCOND_BR_RE = re.compile(r"^\s*br\s+label\s+%(?P<target>[\w\.\$]+)\s*$")
_RET_RE = re.compile(r"^\s*ret\s+(?P<ty>.+?)\s+(?P<val>.+?)\s*$")

_SAFE_BODY_OPS = {"add", "sub", "mul", "and", "or", "xor"}


class LoopRotatePass(ModulePass):
    name = "pcc-loop-rotate"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = loop_rotate_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def loop_rotate_module(ir_text: str) -> tuple[str, bool]:
    binding_mod = llvm.parse_assembly(ir_text)
    binding_mod.verify()

    for fn in binding_mod.functions:
        if fn.is_declaration:
            continue
        cfg = CFG.of_function(fn)
        info = compute_loop_info(fn)
        for loop in info.loops():
            candidate = _find_candidate(fn, loop, cfg)
            if candidate is None:
                continue
            new_text = _rotate_function(ir_text, fn.name, candidate)
            try:
                llvm.parse_assembly(new_text).verify()
            except RuntimeError:
                continue
            return new_text, True
    return ir_text, False


def _find_candidate(fn, loop, cfg):
    if len(loop.blocks) != 2 or len(loop.latches) != 1:
        return None
    header = loop.header
    body = loop.latches[0]
    if body == header:
        return None
    ext_preds = [p for p in cfg.predecessors.get(header, ()) if p not in loop.blocks]
    if len(ext_preds) != 1:
        return None
    preheader = ext_preds[0]
    exits = loop.exit_blocks(cfg)
    if len(exits) != 1:
        return None
    exit_block = exits[0]
    if tuple(cfg.successors.get(body, ())) != (header,):
        return None

    header_block = next((b for b in fn.blocks if b.name == header), None)
    body_block = next((b for b in fn.blocks if b.name == body), None)
    if header_block is None or body_block is None:
        return None

    hinsts = list(header_block.instructions)
    if len(hinsts) != 3:
        return None
    phi_m = _PHI_RE.match(str(hinsts[0]).strip())
    icmp_m = _ICMP_RE.match(str(hinsts[1]).strip())
    br_m = _COND_BR_RE.match(str(hinsts[2]).strip())
    if not (phi_m and icmp_m and br_m):
        return None
    if phi_m.group("pre") != preheader or phi_m.group("latch") != body:
        return None
    if br_m.group("cond") != icmp_m.group("res"):
        return None
    if {br_m.group("t"), br_m.group("f")} != {body, exit_block}:
        return None
    if br_m.group("t") != body:
        return None

    b_insts = list(body_block.instructions)
    if len(b_insts) != 2:
        return None
    body_inst = b_insts[0]
    term_m = _UNCOND_BR_RE.match(str(b_insts[1]).strip())
    if term_m is None or term_m.group("target") != header:
        return None
    if body_inst.opcode not in _SAFE_BODY_OPS:
        return None
    if body_inst.name != phi_m.group("back").lstrip("%"):
        return None
    # Keep the subset narrow: compare must read the header phi directly.
    phi_name = phi_m.group("name")
    if icmp_m.group("lhs").strip() != f"%{phi_name}":
        return None

    return {
        "header": header,
        "body": body,
        "exit": exit_block,
        "preheader": preheader,
        "phi": phi_m.groupdict(),
        "icmp": icmp_m.groupdict(),
    }


def _rotate_function(ir_text: str, fn_name: str, candidate: dict) -> str:
    module = MutableModule.parse(ir_text)
    fn = module.function(fn_name)
    if fn is None:
        return ir_text

    header = fn.block(candidate["header"])
    body = fn.block(candidate["body"])
    exit_block = fn.block(candidate["exit"])
    if header is None or body is None or exit_block is None:
        return ir_text

    phi = candidate["phi"]
    phi_name = phi["name"]
    phi_ty = phi["ty"]

    # Rewrite header phi backedge from %body to %header.
    header.instructions[0].text = re.sub(
        r"\[\s*" + re.escape(phi["back"]) + r"\s*,\s*%" + re.escape(candidate["body"]) + r"\s*\]",
        f"[ {phi['back']}, %{candidate['header']} ]",
        header.instructions[0].text,
    )
    header.instructions[0] = Instruction.from_text(header.instructions[0].text)

    # Keep the compare where it is, splice the body arithmetic before the branch,
    # and loop back to %header on the true edge.
    body_arith = Instruction.from_text(body.instructions[0].text)
    new_branch_text = re.sub(
        r"label\s+%" + re.escape(candidate["body"]) + r"\b",
        f"label %{candidate['header']}",
        header.instructions[-1].text,
    )
    header.instructions = [
        header.instructions[0],
        header.instructions[1],
        body_arith,
        Instruction.from_text(new_branch_text),
    ]

    # Remove the old body block.
    fn.blocks = [b for b in fn.blocks if b.name != candidate["body"]]

    # If the exit directly returns the loop-carried phi, materialize an LCSSA phi.
    if exit_block.instructions:
        ret = exit_block.instructions[-1]
        ret_m = _RET_RE.match(ret.text.strip())
        if ret_m and ret_m.group("val").strip() == f"%{phi_name}":
            lcssa_name = f"{phi_name}.lcssa"
            exit_block.instructions = [
                Instruction.from_text(
                    f"  %{lcssa_name} = phi {phi_ty} [ %{phi_name}, %{candidate['header']} ]\n"
                ),
                Instruction.from_text(
                    f"  ret {ret_m.group('ty').strip()} %{lcssa_name}\n"
                ),
            ]

    return module.serialize()
