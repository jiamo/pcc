"""Correlated Value Propagation — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/CorrelatedValuePropagation.cpp``
  implements :cpp:class:`llvm::CorrelatedValuePropagationPass`. It
  uses the LazyValueInfo (LVI) analysis to walk the dominator tree
  and exploit per-edge facts established by conditional branches.

Subset implemented here: when a block is reached through an edge of
the form ``br i1 %cond, label %then, label %else`` where ``%cond``
was computed as ``icmp eq i32 %x, C`` (or ``ne``), every dominance-
reachable use of ``%x`` in the matching branch is replaced with
``C``. This mirrors the core "equality propagation on predicates"
simplification LVI gives upstream, with two narrowings:

- only integer ``eq`` / ``ne`` predicates are supported,
- only direct-dominance cases (no walk back through phi merges).

Full LVI additionally tracks ranges; we don't yet.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG, compute_dominator_tree
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_ICMP_EQ_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*icmp\s+(?P<pred>eq|ne)\s+
    (?P<ty>i\d+)\s+(?P<lhs>[^,]+?)\s*,\s*(?P<rhs>.+?)\s*$
    """,
    re.VERBOSE,
)

_COND_BR_RE = re.compile(
    r"""
    ^\s*br\s+i1\s+%(?P<cond>[\w\.]+)\s*,\s*
    label\s+%(?P<t>[\w\.]+)\s*,\s*label\s+%(?P<f>[\w\.]+)\s*$
    """,
    re.VERBOSE,
)


class CorrelatedValuePropagationPass(ModulePass):
    name = "pcc-correlated-propagation"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = _cvp_module(module)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def _cvp_module(module: llvm.ModuleRef) -> tuple[str, bool]:
    ir_text = str(module)
    any_changed = False

    # For each function: collect compare-with-constant facts, walk
    # dominator tree, substitute in dominated blocks on the
    # appropriate edge.
    for fn in module.functions:
        if fn.is_declaration:
            continue
        facts = _collect_eq_facts(fn)
        if not facts:
            continue
        dom = compute_dominator_tree(fn)
        new_text, changed = _apply_facts(ir_text, fn.name, facts, dom)
        if changed:
            any_changed = True
            ir_text = new_text

    return ir_text, any_changed


def _collect_eq_facts(fn) -> list[tuple[str, str, str, str, str]]:
    """Find ``br i1 %c, T, F`` where %c = icmp eq/ne %x, C.

    Returns a list of ``(branch_block, cond_name, pred, var_name, const_value)``.
    """
    compares: dict[str, tuple[str, str, str]] = {}  # res → (pred, var, const)
    for block in fn.blocks:
        for inst in block.instructions:
            text = str(inst).strip()
            m = _ICMP_EQ_RE.match(text)
            if not m:
                continue
            lhs, rhs = m.group("lhs").strip(), m.group("rhs").strip()
            # Canonicalize so constant is on RHS.
            var, const_val = None, None
            if lhs.startswith("%") and _is_int_const(rhs):
                var = lhs[1:]
                const_val = rhs
            elif rhs.startswith("%") and _is_int_const(lhs):
                var = rhs[1:]
                const_val = lhs
            if var is not None and const_val is not None:
                compares[m.group("res")] = (m.group("pred"), var, const_val)

    cfg = CFG.of_function(fn)
    facts: list[tuple[str, str, str, str, str]] = []
    for block in fn.blocks:
        block_name = block.name or ""
        term = None
        for inst in block.instructions:
            term = inst
        if term is None:
            continue
        m = _COND_BR_RE.match(str(term).strip())
        if not m:
            continue
        cond = m.group("cond")
        if cond not in compares:
            continue
        pred, var, const_val = compares[cond]
        for target, fact_pred in (
            (m.group("t"), pred),
            (m.group("f"), _negate(pred)),
        ):
            target_preds = tuple(cfg.predecessors.get(target, ()))
            if len(target_preds) != 1 or target_preds[0] != block_name:
                continue
            facts.append((block_name, target, fact_pred, var, const_val))
    return facts


def _is_int_const(tok: str) -> bool:
    tok = tok.strip()
    if tok in ("true", "false"):
        return True
    try:
        int(tok)
        return True
    except ValueError:
        return False


def _negate(pred: str) -> str:
    return {"eq": "ne", "ne": "eq"}.get(pred, pred)


def _apply_facts(
    ir_text: str,
    fn_name: str,
    facts: list[tuple[str, str, str, str, str]],
    dom,
) -> tuple[str, bool]:
    """Replace %var uses inside blocks dominated by the matching edge.

    For ``eq`` on the true-branch target: every use of %var in blocks
    dominated by the target becomes the constant.
    ``ne`` on its true-branch target → no substitution (we don't yet
    compute a negated range).
    """
    eq_subs: dict[str, str] = {}  # var → const value to substitute
    eq_scope: dict[str, set[str]] = {}  # var → set of dominated blocks

    for branch_block, target_block, pred, var, const_val in facts:
        if pred != "eq":
            continue
        # Blocks dominated by `target_block`.
        dominated = {
            b for b in dom.all_blocks() if dom.dominates(target_block, b)
        }
        # Narrowing: the substitution is only safe if the substitution
        # target dominates all uses — we conservatively mark the scope
        # as the dominated set, and later substitute only uses whose
        # defining block is in that set. We approximate: treat all
        # blocks in dominated set as the safe-substitute region.
        if var in eq_subs and eq_subs[var] != const_val:
            # Conflict — bail out for this var.
            continue
        eq_subs[var] = const_val
        eq_scope.setdefault(var, set()).update(dominated)

    if not eq_subs:
        return ir_text, False

    # Substitute textually, but only within the target function and
    # only in lines that belong to dominated blocks.
    new_text = _substitute_per_block(ir_text, fn_name, eq_subs, eq_scope)
    return new_text, new_text != ir_text


def _substitute_per_block(
    ir_text: str,
    fn_name: str,
    eq_subs: dict[str, str],
    eq_scope: dict[str, set[str]],
) -> str:
    lines = ir_text.splitlines(keepends=True)
    out: list[str] = []
    current_fn: str | None = None
    current_block: str | None = None
    label_re = re.compile(r"^([\w\.]+):\s*(?:;.*)?$")
    define_re = re.compile(r"^define\s+[^@]*@([\w\.]+)")

    for line in lines:
        m = define_re.match(line)
        if m:
            current_fn = m.group(1)
            current_block = "entry"
            out.append(line)
            continue
        if line.strip() == "}":
            current_fn = None
            current_block = None
            out.append(line)
            continue
        lm = label_re.match(line.strip())
        if lm:
            current_block = lm.group(1)
            out.append(line)
            continue

        if current_fn == fn_name and current_block is not None:
            new_line = line
            for var, const in eq_subs.items():
                if current_block in eq_scope.get(var, set()):
                    new_line = re.sub(
                        r"%" + re.escape(var) + r"\b",
                        const,
                        new_line,
                    )
            out.append(new_line)
            continue

        out.append(line)
    return "".join(out)
