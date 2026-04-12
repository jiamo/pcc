"""Dead Loop Deletion — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LoopDeletion.cpp``
  implements :cpp:class:`llvm::LoopDeletionPass`. It deletes a loop
  when:

  1. Every block in the loop has no side-effects (no stores, no
     calls, no volatile loads, no atomic ops).
  2. Every value live-out of the loop (used in an exit-block phi)
     is available without executing any iteration — i.e. the phi
     incoming from the preheader suffices.

  When both hold, the preheader's branch to the header can be
  replaced with a branch to the exit, and the loop blocks become
  dead (simplifycfg will DCE them later).

Subset here: we require the loop to be "trivially-dead" — no
side-effects AND no exit phi references any loop-internal value.
Exit-phi incoming from inside the loop is hard to replace without
rewriting the phi, so we skip loops where that happens.

Upstream additionally handles "infinite loops proved side-effect
free under -fno-finite-loops" (deferred here).
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import compute_dominator_tree
from .loop_info import Loop, compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_SIDE_EFFECT_OPCODES = {
    "store", "call", "invoke", "atomicrmw", "cmpxchg", "fence",
}


class LoopDeletionPass(ModulePass):
    name = "pcc-loop-deletion"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = _run_loop_deletion(module)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def _run_loop_deletion(module: llvm.ModuleRef) -> tuple[str, bool]:
    ir_text = str(module)
    any_changed = False
    for fn in module.functions:
        if fn.is_declaration:
            continue
        info = compute_loop_info(fn)
        if not info.loops():
            continue
        # Process innermost loops first (safer when nested).
        loops = sorted(info.loops(), key=lambda l: -l.depth())
        for loop in loops:
            if not _loop_is_dead(loop, fn):
                continue
            new_text, changed = _delete_loop(ir_text, fn, loop)
            if changed:
                try:
                    # Validate before committing.
                    llvm.parse_assembly(new_text).verify()
                    ir_text = new_text
                    any_changed = True
                    # Refresh module for subsequent loop iterations.
                    module = llvm.parse_assembly(ir_text)
                except RuntimeError:
                    continue
    return ir_text, any_changed


def _loop_is_dead(loop: Loop, fn: llvm.ValueRef) -> bool:
    """Return True when the loop can be safely removed."""
    # 1. No side-effects in any body block.
    for block in fn.blocks:
        if block.name not in loop.blocks:
            continue
        for inst in block.instructions:
            if inst.opcode in _SIDE_EFFECT_OPCODES:
                return False
    # 2. Exit-block phis do not reference loop-internal values.
    loop_internal: set[str] = set()
    for block in fn.blocks:
        if block.name not in loop.blocks:
            continue
        for inst in block.instructions:
            name_m = re.match(r"^\s*%([\w\.]+)\s*=", str(inst))
            if name_m:
                loop_internal.add(name_m.group(1))

    from .dominator_tree import CFG
    cfg = CFG.of_function(fn)
    for exit_block in loop.exit_blocks(cfg):
        for block in fn.blocks:
            if block.name != exit_block:
                continue
            for inst in block.instructions:
                text = str(inst).strip()
                if not text.startswith("%") or " = phi " not in text:
                    continue
                # Check each incoming value %x: if x is loop-internal, abort.
                for m in re.finditer(r"\[\s*%([\w\.]+)\s*,", text):
                    if m.group(1) in loop_internal:
                        return False
    # 3. Loop must have exactly one preheader (single predecessor to
    # the header from outside the loop). Otherwise rewriting is
    # ambiguous.
    preds_of_header = cfg.predecessors.get(loop.header, ())
    external_preds = [p for p in preds_of_header if p not in loop.blocks]
    if len(external_preds) != 1:
        return False
    # 4. Loop must have exactly one exit block.
    exits = loop.exit_blocks(cfg)
    if len(exits) != 1:
        return False
    return True


def _delete_loop(
    ir_text: str, fn: llvm.ValueRef, loop: Loop
) -> tuple[str, bool]:
    """Replace the preheader's branch to header with branch to exit."""
    from .dominator_tree import CFG
    cfg = CFG.of_function(fn)
    preds = cfg.predecessors.get(loop.header, ())
    external_preds = [p for p in preds if p not in loop.blocks]
    if len(external_preds) != 1:
        return ir_text, False
    preheader = external_preds[0]
    exits = loop.exit_blocks(cfg)
    if len(exits) != 1:
        return ir_text, False
    exit_block = exits[0]

    lines = ir_text.splitlines(keepends=True)
    current_fn: str | None = None
    current_block: str | None = None
    out: list[str] = []
    define_re = re.compile(r"^define\s+[^@]*@([\w\.]+)")
    label_re = re.compile(r"^([\w\.]+):\s*(?:;.*)?$")

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

        if (current_fn == fn.name
            and current_block == preheader
            and re.match(r"^\s*br\b", line)):
            # Rewrite this br to go to exit_block.
            new_line = re.sub(
                r"label\s+%" + re.escape(loop.header) + r"\b",
                f"label %{exit_block}",
                line,
            )
            # If the br is conditional and both targets were the header,
            # collapse to unconditional — unlikely for a well-formed
            # preheader, so just emit what we have.
            if new_line != line:
                # If now both labels are the same, make unconditional.
                cond_match = re.match(
                    r"^(\s*)br\s+i1\s+[^,]+,\s*label\s+%([\w\.]+)\s*,\s*label\s+%([\w\.]+)\s*$",
                    new_line.rstrip("\n"),
                )
                if cond_match and cond_match.group(2) == cond_match.group(3):
                    new_line = (
                        f"{cond_match.group(1)}br label %{cond_match.group(2)}\n"
                    )
                out.append(new_line)
                continue

        out.append(line)

    new_ir = "".join(out)
    # Also fix the exit-block phi: any incoming from a loop-latch
    # should be replaced with an incoming from the preheader carrying
    # the phi's preheader value (since the loop never executes now).
    new_ir = _fix_exit_phis(new_ir, fn.name, loop, preheader, exit_block, cfg)
    # The header/body become dead; their phis still reference the
    # preheader but that edge no longer exists. Remove the preheader
    # entry from the header's phis so the (now unreachable) IR still
    # verifies.
    new_ir = _strip_phi_incoming(
        new_ir, fn.name, loop.header, preheader
    )
    return new_ir, new_ir != ir_text


def _strip_phi_incoming(
    ir_text: str, fn_name: str, block_name: str, pred_name: str
) -> str:
    """Remove ``[_, %pred_name]`` incomings from every phi in block_name."""
    lines = ir_text.splitlines(keepends=True)
    current_fn: str | None = None
    current_block: str | None = None
    out: list[str] = []
    define_re = re.compile(r"^define\s+[^@]*@([\w\.]+)")
    label_re = re.compile(r"^([\w\.]+):\s*(?:;.*)?$")

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

        if (current_fn == fn_name
            and current_block == block_name
            and " = phi " in line):
            pattern = re.compile(
                r"\[\s*[^,\]]+,\s*%" + re.escape(pred_name)
                + r"[ \t]*\][ \t]*,?[ \t]*"
            )
            new_line = pattern.sub("", line)
            # Clean trailing comma if we left one behind.
            new_line = re.sub(r",[ \t]*\n", "\n", new_line)
            out.append(new_line)
            continue

        out.append(line)

    return "".join(out)


def _fix_exit_phis(
    ir_text: str,
    fn_name: str,
    loop: Loop,
    preheader: str,
    exit_block: str,
    cfg,
) -> str:
    """After deletion, the exit block may still have phi incomings from
    loop blocks that don't exist semantically anymore. Rewrite those
    to come from the preheader.
    """
    lines = ir_text.splitlines(keepends=True)
    current_fn: str | None = None
    current_block: str | None = None
    out: list[str] = []
    define_re = re.compile(r"^define\s+[^@]*@([\w\.]+)")
    label_re = re.compile(r"^([\w\.]+):\s*(?:;.*)?$")

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

        if (current_fn == fn_name
            and current_block == exit_block
            and " = phi " in line):
            # Replace any `[val, %<loop-block>]` with `[val, %preheader]`.
            new_line = line
            for loop_block in loop.blocks:
                new_line = re.sub(
                    r"(\[\s*[^,\]]+,\s*)%" + re.escape(loop_block) + r"(\s*\])",
                    r"\1%" + preheader + r"\2",
                    new_line,
                )
            out.append(new_line)
            continue

        out.append(line)

    return "".join(out)
