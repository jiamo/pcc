"""Loop Simplify + Loop Rotate — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Utils/LoopSimplify.cpp``
  implements :cpp:class:`llvm::LoopSimplifyPass`. It canonicalizes
  every loop so that:

  1. The header has exactly one predecessor from outside the loop
     (the *preheader*).
  2. The loop has exactly one backedge from a dedicated *latch*.
  3. Every exit block's predecessors are all inside the loop.

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LoopRotation.cpp``
  implements :cpp:class:`llvm::LoopRotatePass`. It converts loops
  from ``while`` form (header with cond-branch to body/exit) to
  ``do-while`` form (cond moved to the latch, often enabling LICM
  of the header check).

Subset implemented here (labelled ``subset``):

- **Preheader insertion**: when a loop's header has multiple external
  predecessors (no unique preheader), insert a new block that merges
  them all and branches to the header. Header phi incomings from
  those predecessors are rewritten to come from the new preheader.
- **Dedicated-latch insertion**: when a loop header has multiple
  backedge predecessors, insert a merge latch that collects those
  backedge values through latch-local phis and becomes the header's
  sole backedge.

Dedicated-exit normalization and loop-rotate are deferred to the full
implementation.
"""

from __future__ import annotations

import re
import llvmlite.binding as llvm

from .dominator_tree import CFG
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class LoopSimplifyPass(ModulePass):
    name = "pcc-loop-simplify"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = loop_simplify_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def loop_simplify_module(ir_text: str) -> tuple[str, bool]:
    any_change = False
    preheader_counter = 0
    latch_counter = 0
    while True:
        module = llvm.parse_assembly(ir_text)
        module.verify()
        changed_this_round = False
        for fn in module.functions:
            if fn.is_declaration:
                continue
            info = compute_loop_info(fn)
            cfg = CFG.of_function(fn)
            for loop in info.loops():
                ext = [p for p in cfg.predecessors.get(loop.header, ())
                       if p not in loop.blocks]
                if len(ext) > 1:
                    preheader_counter += 1
                    preheader_name = _fresh_block_name(
                        ir_text,
                        fn.name,
                        f"{loop.header}.preheader",
                        preheader_counter,
                    )
                    new_ir = _insert_merge_block(
                        ir_text,
                        fn.name,
                        loop.header,
                        ext,
                        preheader_name,
                    )
                    try:
                        llvm.parse_assembly(new_ir).verify()
                        ir_text = new_ir
                        any_change = True
                        changed_this_round = True
                        break
                    except RuntimeError:
                        continue
                if len(loop.latches) > 1:
                    latch_counter += 1
                    latch_name = _fresh_block_name(
                        ir_text,
                        fn.name,
                        f"{loop.header}.backedge",
                        latch_counter,
                    )
                    new_ir = _insert_merge_block(
                        ir_text,
                        fn.name,
                        loop.header,
                        loop.latches,
                        latch_name,
                    )
                    try:
                        llvm.parse_assembly(new_ir).verify()
                        ir_text = new_ir
                        any_change = True
                        changed_this_round = True
                        break
                    except RuntimeError:
                        continue
            if changed_this_round:
                break
        if not changed_this_round:
            break
    return ir_text, any_change


def _fresh_block_name(
    ir_text: str,
    fn_name: str,
    base: str,
    counter: int,
) -> str:
    names = _function_block_names(ir_text, fn_name)
    if base not in names:
        return base
    idx = counter
    while f"{base}{idx}" in names:
        idx += 1
    return f"{base}{idx}"


def _function_block_names(ir_text: str, fn_name: str) -> set[str]:
    lines = ir_text.splitlines()
    fn_define_re = re.compile(rf"^\s*define\s+[^@]*@{re.escape(fn_name)}\b")
    label_re = re.compile(r"^\s*([\w\.]+):\s*(?:;.*)?$")
    names: set[str] = set()
    in_fn = False
    for line in lines:
        if not in_fn:
            if fn_define_re.match(line):
                in_fn = True
            continue
        if line.strip() == "}":
            break
        m = label_re.match(line)
        if m:
            names.add(m.group(1))
    return names


def _insert_merge_block(
    ir_text: str,
    fn_name: str,
    header: str,
    preds_to_merge: list[str],
    new_name: str,
) -> str:
    """Create a new block that merges ``preds_to_merge`` into ``header``."""
    lines = ir_text.splitlines(keepends=True)
    # 1. Find the function body.
    fn_define_re = re.compile(
        rf"^\s*define\s+[^@]*@{re.escape(fn_name)}\b"
    )
    fn_start = fn_end = -1
    for i, line in enumerate(lines):
        if fn_define_re.match(line):
            fn_start = i
        elif fn_start >= 0 and line.strip() == "}":
            fn_end = i
            break
    if fn_start < 0 or fn_end < 0:
        return ir_text

    # 2. Rewrite each merged predecessor's terminator so label %header →
    # label %new_name.
    current_block = "entry"
    label_re = re.compile(r"^\s*([\w\.]+):\s*(?:;.*)?$")
    for i in range(fn_start, fn_end + 1):
        lm = label_re.match(lines[i].rstrip("\n"))
        if lm:
            current_block = lm.group(1)
            continue
        if current_block not in preds_to_merge:
            continue
        if re.match(r"^\s*br\b", lines[i]):
            lines[i] = re.sub(
                r"label\s+%" + re.escape(header) + r"\b",
                f"label %{new_name}",
                lines[i],
            )

    # 3. Collect header phis and their merged-pred incomings; we
    # will reproduce those as phi nodes in the new preheader, then
    # coalesce the header phi down to one incoming from preheader.
    header_label_idx = None
    for i in range(fn_start, fn_end + 1):
        lm = label_re.match(lines[i].rstrip("\n"))
        if lm and lm.group(1) == header:
            header_label_idx = i
            break
    if header_label_idx is None:
        return ir_text

    phi_re = re.compile(
        r"^(?P<indent>\s*)%(?P<name>[\w\.]+)\s*=\s*phi\s+"
        r"(?P<ty>\S+)\s+(?P<rest>\[.*)$"
    )
    incoming_re = re.compile(
        r"\[\s*(?P<val>[^,\]]+?)\s*,\s*%(?P<block>[\w\.]+)\s*\]"
    )

    preheader_phi_lines: list[str] = []
    replacements: list[tuple[int, str]] = []

    for i in range(header_label_idx + 1, fn_end + 1):
        lm = label_re.match(lines[i].rstrip("\n"))
        if lm and lm.group(1) != header:
            break
        m = phi_re.match(lines[i].rstrip("\n"))
        if not m:
            continue
        incomings = list(incoming_re.finditer(m.group("rest")))
        merged_inc = [g for g in incomings if g.group("block") in preds_to_merge]
        other_inc = [g for g in incomings if g.group("block") not in preds_to_merge]
        if not merged_inc:
            continue
        merged_vals = [g.group("val") for g in merged_inc]
        if all(val == merged_vals[0] for val in merged_vals[1:]):
            merged_value = merged_vals[0]
        else:
            # Create merge-block phi that merges the redirected incomings.
            pre_phi_name = f"{m.group('name')}.ph"
            pre_phi_body = ", ".join(
                f"[ {g.group('val')}, %{g.group('block')} ]" for g in merged_inc
            )
            preheader_phi_lines.append(
                f"  %{pre_phi_name} = phi {m.group('ty')} {pre_phi_body}\n"
            )
            merged_value = f"%{pre_phi_name}"
        # Rewrite header phi: keep non-merged incomings + one new
        # incoming from the merge block with the merged phi value.
        new_incomings = (
            [f"[ {merged_value}, %{new_name} ]"]
            + [f"[ {g.group('val')}, %{g.group('block')} ]" for g in other_inc]
        )
        new_phi = (
            f"{m.group('indent')}%{m.group('name')} = phi "
            f"{m.group('ty')} {', '.join(new_incomings)}\n"
        )
        replacements.append((i, new_phi))

    for idx, new_line in replacements:
        lines[idx] = new_line

    # 4. Insert new merge block (with its phis if any) before header.
    merge_block = (
        f"{new_name}:\n"
        + "".join(preheader_phi_lines)
        + f"  br label %{header}\n"
    )
    lines.insert(header_label_idx, merge_block)

    return "".join(lines)
