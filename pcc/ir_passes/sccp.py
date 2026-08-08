"""Sparse Conditional Constant Propagation (SCCP) — IR-level subset.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/SCCP.cpp``
  and the per-function logic in
  ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Utils/SCCPSolver.cpp``.
  :cpp:class:`llvm::SCCPSolver` runs a two-worklist fixed-point
  algorithm over a sparse lattice per SSA value plus an edge
  executability bit per CFG edge. Constant branch directions kill
  unreachable edges, which in turn kills incoming phi operands and
  refines the lattice further.

Staged subset implemented here:

- integer binary ops + icmp + select fold via
  :mod:`constant_lattice`,
- simple constant propagation at the instruction level (no
  edge-executability pruning yet, which means we don't delete
  unreachable blocks; that's a follow-up tied to simplifycfg),
- seed: every constant operand starts as constant, every argument
  starts as overdefined.

Labelled ``subset`` — Phase 4a proper SCCP will extend this.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .constant_lattice import (
    LatticeValue,
    evaluate_binary,
    evaluate_compare,
    meet,
)
from .manager import AnalysisManager, ModulePass, PreservedAnalyses
from .ssa_utils import build_def_use_index


_BINOP_OPS = {
    "add", "sub", "mul", "udiv", "sdiv", "urem", "srem",
    "and", "or", "xor", "shl", "lshr", "ashr",
}


class SCCPPass(ModulePass):
    name = "pcc-sccp"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = sccp_module_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def sccp_module_text(ir_text: str) -> tuple[str, bool]:
    current = ir_text
    any_change = False
    for _ in range(16):
        next_text, changed = _one_sccp_round(current)
        if not changed:
            break
        any_change = True
        current = next_text
    return current, any_change


_ASSIGN_HEAD_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<name>[\w\.]+)\s*=\s*(?P<rest>.+)\s*$"
)
_BINOP_RE = re.compile(
    r"^(?P<op>\w+)(?P<flags>(?:\s+(?:nsw|nuw|exact))*)\s+(?P<ty>i\d+)\s+(?P<lhs>[^,]+?)\s*,\s*(?P<rhs>.+?)\s*$"
)
_ICMP_RE = re.compile(
    r"^icmp\s+(?P<pred>\w+)\s+(?P<ty>i\d+)\s+(?P<lhs>[^,]+?)\s*,\s*(?P<rhs>.+?)\s*$"
)
_SELECT_RE = re.compile(
    r"^select\s+i1\s+(?P<cond>[^,]+?)\s*,\s*(?P<ty1>[\w]+)\s+(?P<tval>[^,]+?)\s*,\s*(?P<ty2>[\w]+)\s+(?P<fval>.+?)\s*$"
)


def _parse_constant(token: str, ty: str) -> LatticeValue | None:
    m = re.match(r"i(\d+)", ty)
    width = int(m.group(1)) if m else 32
    t = token.strip()
    if t == "true":
        return LatticeValue.const(1, width)
    if t == "false":
        return LatticeValue.const(0, width)
    try:
        return LatticeValue.const(int(t), width)
    except ValueError:
        return None


def _one_sccp_round(ir_text: str) -> tuple[str, bool]:
    # Parse module and build def-use index.
    module = llvm.parse_assembly(ir_text)
    module.verify()
    index = build_def_use_index(module)

    # Lattice table, indexed by SSA name.
    lattice: dict[str, LatticeValue] = {}

    # Seed: every argument starts as overdefined; constants are const.
    # SSA names that don't correspond to an instruction (e.g. function
    # args) default to overdefined.
    def get(tok: str, ty: str) -> LatticeValue:
        tok = tok.strip()
        if tok.startswith("%"):
            return lattice.get(tok[1:], LatticeValue.overdefined())
        lv = _parse_constant(tok, ty)
        return lv if lv is not None else LatticeValue.overdefined()

    # Iterate over instructions in module order, updating lattice.
    worklist = list(index.records_by_name.items())
    changed_lattice = True
    while changed_lattice:
        changed_lattice = False
        for name, rec in worklist:
            if name.startswith("__inst_"):
                continue
            text = rec.text
            m = _ASSIGN_HEAD_RE.match(text)
            if not m:
                continue
            rest = m.group("rest")
            new_val = LatticeValue.overdefined()
            bm = _BINOP_RE.match(rest)
            if bm and bm.group("op") in _BINOP_OPS:
                lv = get(bm.group("lhs"), bm.group("ty"))
                rv = get(bm.group("rhs"), bm.group("ty"))
                new_val = evaluate_binary(
                    bm.group("op"),
                    lv,
                    rv,
                    bm.group("flags").split(),
                )
            else:
                im = _ICMP_RE.match(rest)
                if im:
                    lv = get(im.group("lhs"), im.group("ty"))
                    rv = get(im.group("rhs"), im.group("ty"))
                    new_val = evaluate_compare(im.group("pred"), lv, rv)
                else:
                    sm = _SELECT_RE.match(rest)
                    if sm:
                        cond = get(sm.group("cond"), "i1")
                        tv = get(sm.group("tval"), sm.group("ty1"))
                        fv = get(sm.group("fval"), sm.group("ty2"))
                        if cond.is_constant():
                            new_val = tv if cond.constant != 0 else fv
                        else:
                            new_val = meet(tv, fv)

            old = lattice.get(name, LatticeValue.top())
            # Be monotone: only allow transitions top → constant →
            # overdefined. Never go backwards.
            if old.is_overdefined():
                continue
            if new_val.is_top():
                continue
            # If old was constant and new is a different constant, drop
            # to overdefined.
            if old.is_constant() and new_val.is_constant():
                if old.constant != new_val.constant:
                    new_val = LatticeValue.overdefined()
                else:
                    continue
            lattice[name] = new_val
            changed_lattice = True

    # Apply substitutions — replace each constant-valued SSA use.
    replacements: dict[str, str] = {}
    for name, val in lattice.items():
        if not val.is_constant():
            continue
        w = val.bit_width or 32
        if w == 1:
            replacements[name] = "true" if val.constant != 0 else "false"
        else:
            # Signed representation preferred for negative constants.
            c = val.constant or 0
            if c >= (1 << (w - 1)):
                c -= 1 << w
            replacements[name] = str(c)

    if not replacements:
        return ir_text, False

    # Substitute at use-sites *and* drop the defining instruction
    # (its result is a known constant now).
    new_lines: list[str] = []
    assign_re = re.compile(r"^\s*%([\w\.]+)\s*=")
    for line in ir_text.splitlines(keepends=True):
        m = assign_re.match(line)
        if m and m.group(1) in replacements:
            continue
        new_lines.append(line)

    text = "".join(new_lines)
    for _ in range(8):
        next_text = text
        for name, rep in replacements.items():
            next_text = re.sub(
                r"%" + re.escape(name) + r"\b", rep, next_text
            )
        if next_text == text:
            break
        text = next_text
    return text, True
