"""A tiny local-rewrite IR pass used as a Phase 1 smoke test.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Analysis/InstructionSimplify.cpp``
  defines the full ``InstSimplify`` logic. The identity subset we
  partially mirror here corresponds to the short-circuits in
  ``SimplifyAddInst``, ``SimplifySubInst``, ``SimplifyMulInst``,
  ``SimplifyOrInst``, ``SimplifyAndInst``, and ``SimplifyShiftInst``:

    x + 0   → x
    x - 0   → x
    x * 1   → x
    x * 0   → 0
    x | 0   → x
    x & -1  → x
    x & 0   → 0
    x <<|>>|>>u 0 → x

This is intentionally not the full ``instsimplify`` — that lands as
task #46 (Phase 3a). The pass here is a narrow subset used to prove
the IR pass runtime and parity harness work end-to-end on a real
rewrite, and to give later passes a template for how to emit a
replaced IR through the manager. Labelled ``subset`` in the status
taxonomy; not a replacement for upstream ``instsimplify``.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


# Match a simple ``%res = <op> <ty> <lhs>, <rhs>`` instruction on one line.
_BINOP_LINE_RE = re.compile(
    r"""
    ^(?P<indent>\s*)
    %(?P<result>[\w\.]+)\s*=\s*
    (?P<op>add|sub|mul|and|or|shl|lshr|ashr)
    (?P<flags>(?:\s+(?:nsw|nuw|exact))*)
    \s+(?P<ty>i\d+)\s+
    (?P<lhs>[^,\s][^,]*?)\s*,\s*
    (?P<rhs>.+?)\s*$
    """,
    re.VERBOSE,
)


class TrivialArithIdentitiesPass(ModulePass):
    """Fold a small set of arithmetic identities within each function.

    The pass serializes the module, performs a set of textual
    identity simplifications, and — if any rewrite fired — exposes
    the new IR via :attr:`rewritten_ir`. The Python-level
    :class:`~pcc.ir_passes.parity.run_pcc_ir_pass` helper uses that
    attribute to pick up the rewrite.

    Passes that can mutate in place through :mod:`llvmlite.ir`
    (built, not parsed) don't need this attribute — they just mutate
    the module and return ``PreservedAnalyses.none()``. The attribute
    is only meaningful for passes that textually round-trip through
    ``parse_assembly``, which is necessary while we don't yet have a
    mutable pcc IR layer.
    """

    name = "trivial-instsimplify"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = _simplify_ir_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        # Verify the new IR still parses — catches bugs early.
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


# ---------------------------------------------------------------------------
# Rewrite kernel
# ---------------------------------------------------------------------------


def _is_zero(token: str) -> bool:
    return token.strip() == "0"


def _is_one(token: str) -> bool:
    return token.strip() == "1"


def _is_all_ones(token: str, ty: str) -> bool:
    t = token.strip()
    if t == "-1":
        return True
    if t == "true" and ty == "i1":
        return True
    return False


def _simplify_binop(op: str, ty: str, lhs: str, rhs: str) -> str | None:
    """Return the simplified replacement token, or None if no rewrite."""
    if op == "add":
        if _is_zero(rhs):
            return lhs
        if _is_zero(lhs):
            return rhs
    elif op == "sub":
        if _is_zero(rhs):
            return lhs
    elif op == "mul":
        if _is_zero(rhs) or _is_zero(lhs):
            return "0"
        if _is_one(rhs):
            return lhs
        if _is_one(lhs):
            return rhs
    elif op == "or":
        if _is_zero(rhs):
            return lhs
        if _is_zero(lhs):
            return rhs
    elif op == "and":
        if _is_zero(rhs) or _is_zero(lhs):
            return "0"
        if _is_all_ones(rhs, ty):
            return lhs
        if _is_all_ones(lhs, ty):
            return rhs
    elif op in ("shl", "lshr", "ashr"):
        if _is_zero(rhs):
            return lhs
    return None


def _simplify_ir_text(ir_text: str) -> tuple[str, bool]:
    """Apply trivial arithmetic simplifications, return (new_ir, changed).

    Implementation note: operates on IR text with a line-level regex.
    For the identity subset covered here, each rewrite either drops a
    whole instruction (result is an alias for an operand) or replaces
    the instruction with a constant. Both shapes are safe under a
    single pass of substitution across the module, with fixed-point
    iteration to collapse chains like ``%a = add x,0; %b = add %a,0``.
    """
    replacements: dict[str, str] = {}
    kept_lines: list[str] = []
    changed = False

    for line in ir_text.splitlines(keepends=True):
        m = _BINOP_LINE_RE.match(line.rstrip("\n"))
        if m is None:
            kept_lines.append(line)
            continue
        replacement = _simplify_binop(
            m.group("op"),
            m.group("ty"),
            m.group("lhs"),
            m.group("rhs"),
        )
        if replacement is None:
            kept_lines.append(line)
            continue
        replacements[m.group("result")] = replacement
        changed = True
        # Drop the simplified instruction entirely.

    if not changed:
        return ir_text, False

    text = "".join(kept_lines)
    # Fixed-point substitution: chained identities collapse through.
    for _ in range(8):
        new_text = text
        for name, rep in replacements.items():
            new_text = re.sub(
                r"%" + re.escape(name) + r"\b", rep, new_text
            )
        if new_text == text:
            break
        text = new_text
    return text, True
