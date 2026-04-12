"""NewGVN — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/NewGVN.cpp``
  is LLVM's optimistic congruence-class GVN. It is substantially
  richer than :cpp:class:`llvm::GVNPass`: it processes values into
  equivalence classes using equality saturation, can move some
  value numbering through phi nodes, and propagates congruence
  through a walker that combines SCCP + GVN.

Subset implemented here (labelled ``subset``): NewGVN's *classical
core* — the dominator-aware value-numbering that finds same-op
same-operand duplicates across blocks — is already implemented in
:mod:`pcc.ir_passes.gvn`. This module delegates to that subset.
After value numbering, we also run a local simplifier pass so that
fresh equalities such as ``sub %a, %a`` or ``and %a, %a`` collapse
the same way they do downstream in upstream NewGVN pipelines. The
richer optimistic / phi-reasoning behaviour is still deferred.

Callers that want NewGVN-style precision today should compose the
classical GVN subset with SCCP / instsimplify / instcombine; for
most scalar code that combination matches upstream NewGVN's output
on simple CFG shapes.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dce import dce_module_text
from .gvn import gvn_text
from .instsimplify import simplify_module_text
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_PHI_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<name>[\w\.]+)\s*=\s*phi\s+"
    r"(?P<ty>[^ ]+)\s+(?P<rest>\[.*)$"
)
_INCOMING_RE = re.compile(
    r"\[\s*(?P<val>[^,\]]+?)\s*,\s*%(?P<block>[\w\.]+)\s*\]"
)


def _split_functions(ir_text: str) -> list[tuple[bool, str]]:
    chunks: list[tuple[bool, str]] = []
    current: list[str] = []
    in_function = False
    brace_depth = 0
    for line in ir_text.splitlines(keepends=True):
        stripped = line.lstrip()
        if not in_function and stripped.startswith("define "):
            if current:
                chunks.append((False, "".join(current)))
                current = []
            in_function = True
            brace_depth = line.count("{") - line.count("}")
            current.append(line)
            continue
        if in_function:
            current.append(line)
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                chunks.append((True, "".join(current)))
                current = []
                in_function = False
            continue
        current.append(line)
    if current:
        chunks.append((in_function, "".join(current)))
    return chunks


def _fold_redundant_phis_in_function(fn_text: str) -> tuple[str, bool]:
    lines = fn_text.splitlines(keepends=True)
    dead_lines: set[int] = set()
    replacements: dict[str, str] = {}

    for idx, line in enumerate(lines):
        m = _PHI_RE.match(line.rstrip("\n"))
        if m is None:
            continue
        incomings = [g.group("val").strip() for g in _INCOMING_RE.finditer(m.group("rest"))]
        if len(incomings) < 2:
            continue
        if any(val != incomings[0] for val in incomings[1:]):
            continue
        replacements[m.group("name")] = incomings[0]
        dead_lines.add(idx)

    if not replacements:
        return fn_text, False

    text = "".join(ln for i, ln in enumerate(lines) if i not in dead_lines)
    for _ in range(8):
        new_text = text
        for old, new in replacements.items():
            new_text = re.sub(
                r"%" + re.escape(old) + r"(?![\w\.])",
                new,
                new_text,
            )
        if new_text == text:
            break
        text = new_text
    return text, True


def newgvn_text(
    ir_text: str,
    fn_doms: dict[str, dict[str, list[str]]],
) -> tuple[str, bool]:
    current, changed = gvn_text(ir_text, fn_doms, run_dce=True)
    out: list[str] = []
    phi_changed = False
    for is_function, chunk in _split_functions(current):
        if not is_function:
            out.append(chunk)
            continue
        rewritten, fn_changed = _fold_redundant_phis_in_function(chunk)
        out.append(rewritten)
        phi_changed = phi_changed or fn_changed
    current = "".join(out)
    if changed or phi_changed:
        current, _ = simplify_module_text(current)
        current, _ = dce_module_text(current)
    return current, changed or phi_changed


class NewGVNPass(ModulePass):
    name = "pcc-newgvn"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        from .dominator_tree import compute_dominator_tree
        self.rewritten_ir = None
        ir_text = str(module)
        fn_doms: dict[str, dict[str, list[str]]] = {}
        for fn in module.functions:
            if fn.is_declaration:
                continue
            dom = compute_dominator_tree(fn)
            fn_doms[fn.name] = {
                block: dom.dominators(block) for block in dom.all_blocks()
            }
        new_text, changed = newgvn_text(ir_text, fn_doms)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()
