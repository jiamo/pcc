"""Constraint Elimination — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/ConstraintElimination.cpp``
  implements :cpp:class:`llvm::ConstraintEliminationPass`. It
  maintains a system of linear inequalities over the dominator tree
  and uses it to prove redundant ``icmp`` instructions.

Subset implemented here (labelled ``subset``):

- For each branch ``br i1 %c, T, F`` where ``%c = icmp PRED i32 %x, CONST``,
  inside blocks dominated by T, every later ``icmp PRED2 i32 %x, CONST2``
  that the established fact already proves (true or false) is folded to
  a constant. Example:

      if (x < 10) {
        if (x < 20) { ... }     // always true
        if (x >= 10) { ... }    // always false
      }

  We fold ``x < 20`` and ``x >= 10`` on the then side.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import compute_dominator_tree
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_ICMP_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*icmp\s+"
    r"(?P<pred>eq|ne|ult|ule|ugt|uge|slt|sle|sgt|sge)\s+"
    r"(?P<ty>i\d+)\s+(?P<lhs>[^,]+?)\s*,\s*(?P<rhs>.+?)\s*$"
)
_COND_BR_RE = re.compile(
    r"^\s*br\s+i1\s+%(?P<cond>[\w\.]+)\s*,\s*label\s+%(?P<t>[\w\.]+)"
    r"\s*,\s*label\s+%(?P<f>[\w\.]+)\s*$"
)


class ConstraintEliminationPass(ModulePass):
    name = "pcc-constraint-elimination"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = _run(module)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def _run(module: llvm.ModuleRef) -> tuple[str, bool]:
    ir_text = str(module)
    any_changed = False
    for fn in module.functions:
        if fn.is_declaration:
            continue
        facts = _collect_scope_facts(fn)
        if not facts:
            continue
        new_text, changed = _apply_facts(ir_text, fn.name, fn, facts)
        if changed:
            ir_text = new_text
            any_changed = True
    return ir_text, any_changed


def _collect_scope_facts(fn):
    """Return per-block list of facts that hold in that block's scope.

    A fact has shape (var_name, pred, const_val), interpreted as:
    ``%var pred const`` is guaranteed true throughout the block.
    """
    # Map icmp result → (var, pred, const).
    compares: dict[str, tuple[str, str, int]] = {}
    for block in fn.blocks:
        for inst in block.instructions:
            m = _ICMP_RE.match(str(inst).strip())
            if not m:
                continue
            lhs = m.group("lhs").strip()
            rhs = m.group("rhs").strip()
            var = const = None
            if lhs.startswith("%") and _is_int(rhs):
                var, const = lhs[1:], int(rhs)
            elif rhs.startswith("%") and _is_int(lhs):
                var, const = rhs[1:], int(lhs)
                # Canonicalize: swap predicate for swapped operand order.
                compares[m.group("res")] = (
                    var, _swap_pred(m.group("pred")), const
                )
                continue
            if var is not None and const is not None:
                compares[m.group("res")] = (var, m.group("pred"), const)

    # Per block: scan terminator for conditional-branch facts.
    dom = compute_dominator_tree(fn)
    per_block: dict[str, list[tuple[str, str, int]]] = {
        b: [] for b in dom.all_blocks()
    }

    for block in fn.blocks:
        bname = block.name
        term = None
        for inst in block.instructions:
            term = inst
        if term is None:
            continue
        m = _COND_BR_RE.match(str(term).strip())
        if not m:
            continue
        if m.group("cond") not in compares:
            continue
        var, pred, const = compares[m.group("cond")]
        # Add fact to all blocks dominated by the true-target,
        # and the negated fact to the false-target.
        for target, fact_pred in (
            (m.group("t"), pred),
            (m.group("f"), _negate_pred(pred)),
        ):
            for b in dom.all_blocks():
                if dom.dominates(target, b):
                    per_block[b].append((var, fact_pred, const))

    return per_block


def _is_int(tok: str) -> bool:
    try:
        int(tok)
        return True
    except ValueError:
        return False


def _swap_pred(pred: str) -> str:
    return {
        "eq": "eq", "ne": "ne",
        "slt": "sgt", "sle": "sge", "sgt": "slt", "sge": "sle",
        "ult": "ugt", "ule": "uge", "ugt": "ult", "uge": "ule",
    }[pred]


def _negate_pred(pred: str) -> str:
    return {
        "eq": "ne", "ne": "eq",
        "slt": "sge", "sle": "sgt", "sgt": "sle", "sge": "slt",
        "ult": "uge", "ule": "ugt", "ugt": "ule", "uge": "ult",
    }[pred]


def _eval_with_fact(
    pred_query: str,
    const_query: int,
    pred_known: str,
    const_known: int,
    signed: bool,
) -> bool | None:
    """Given %x `pred_known` const_known is true, can we prove
    %x `pred_query` const_query? Return True/False or None if unknown.

    Uses a simplified interval analysis.
    """
    # Compute the interval [lo, hi] implied by the known fact.
    if signed:
        lo, hi = -(2**31), (2**31) - 1
    else:
        lo, hi = 0, (2**32) - 1

    def narrow(lo, hi, pred, c):
        if pred == "eq": return (c, c)
        if pred == "ne": return (lo, hi)  # can't narrow without splitting
        if pred in ("slt", "ult"): return (lo, min(hi, c - 1))
        if pred in ("sle", "ule"): return (lo, min(hi, c))
        if pred in ("sgt", "ugt"): return (max(lo, c + 1), hi)
        if pred in ("sge", "uge"): return (max(lo, c), hi)
        return (lo, hi)

    lo, hi = narrow(lo, hi, pred_known, const_known)
    if lo > hi:
        return None  # inconsistent — the true branch is unreachable

    def test(pred, c):
        if pred == "eq":
            if lo == hi == c: return True
            if c < lo or c > hi: return False
            return None
        if pred == "ne":
            if lo == hi == c: return False
            if c < lo or c > hi: return True
            return None
        if pred in ("slt", "ult"):
            if hi < c: return True
            if lo >= c: return False
            return None
        if pred in ("sle", "ule"):
            if hi <= c: return True
            if lo > c: return False
            return None
        if pred in ("sgt", "ugt"):
            if lo > c: return True
            if hi <= c: return False
            return None
        if pred in ("sge", "uge"):
            if lo >= c: return True
            if hi < c: return False
            return None
        return None

    return test(pred_query, const_query)


def _apply_facts(
    ir_text: str, fn_name: str, fn, facts: dict
) -> tuple[str, bool]:
    # Walk IR lines; for each icmp with constant, consult facts for
    # its block to see if it's determined.
    lines = ir_text.splitlines(keepends=True)
    out: list[str] = []
    current_fn: str | None = None
    current_block: str | None = None
    define_re = re.compile(r"^\s*define\s+[^@]*@([\w\.]+)")
    label_re = re.compile(r"^\s*([\w\.]+):\s*(?:;.*)?$")
    fold: dict[str, str] = {}  # %name → "true" / "false"
    dead_lines: set[int] = set()

    # Pass 1: collect foldable icmps.
    for idx, line in enumerate(lines):
        m = define_re.match(line)
        if m:
            current_fn = m.group(1)
            current_block = "entry"
            continue
        if line.strip() == "}":
            current_fn = None
            current_block = None
            continue
        lm = label_re.match(line.rstrip("\n"))
        if lm:
            current_block = lm.group(1)
            continue
        if current_fn != fn_name or current_block is None:
            continue
        mm = _ICMP_RE.match(line.rstrip("\n"))
        if not mm:
            continue
        lhs = mm.group("lhs").strip()
        rhs = mm.group("rhs").strip()
        var = const = None
        pred = mm.group("pred")
        if lhs.startswith("%") and _is_int(rhs):
            var, const = lhs[1:], int(rhs)
        elif rhs.startswith("%") and _is_int(lhs):
            var, const = rhs[1:], int(lhs)
            pred = _swap_pred(pred)
        if var is None or const is None:
            continue
        signed = pred.startswith("s") or (
            pred in ("eq", "ne")
        )
        # Consult block facts.
        for fv, fp, fc in facts.get(current_block, ()):
            if fv != var:
                continue
            res = _eval_with_fact(pred, const, fp, fc, signed)
            if res is None:
                continue
            fold[mm.group("res")] = "true" if res else "false"
            dead_lines.add(idx)
            break

    if not fold:
        return ir_text, False

    kept = [ln for i, ln in enumerate(lines) if i not in dead_lines]
    text = "".join(kept)
    for name, val in fold.items():
        text = re.sub(r"%" + re.escape(name) + r"(?![\w\.])", val, text)
    return text, True
