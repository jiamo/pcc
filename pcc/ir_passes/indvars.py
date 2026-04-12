"""Induction Variable Simplification — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/IndVarSimplify.cpp``
  implements :cpp:class:`llvm::IndVarSimplifyPass`. It uses
  ScalarEvolution to canonicalize induction variables (widen them to
  the natural type, recognize isomorphic IVs, eliminate redundant
  IVs, rewrite exit conditions into a canonical form).

Subset implemented here (labelled ``subset``):

- *Redundant IV elimination*: detect two phi nodes in the same
  block that form isomorphic induction variables (same start
  constant, same step add) and replace the second IV (including
  its increment) with the first. This matches the
  ``eliminateIVComparisons``/``eliminateRedundantIVs`` win upstream
  gets in the simplest case without ScalarEvolution.

Widening, exit-condition rewriting, and cross-loop simplifications
are deferred to the full implementation.

One narrow exit-value rewrite is implemented:

- when the canonical signed loop ``for (i = 0; i < n; ++i)`` returns
  the live-out induction variable itself, rewrite the exit value to
  ``llvm.smax.iN(n, 0)``. This matches the simplest upstream
  ``IndVarSimplify`` exit canonicalization without full ScalarEvolution.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG
from .ir_mutator import Instruction, MutableModule
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_PHI_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*phi\s+(?P<ty>i\d+)\s+"
    r"(?P<rest>\[.*)$"
)
_INC_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*add\s+(?:nsw\s+|nuw\s+)*"
    r"(?P<ty>i\d+)\s+%(?P<iv>[\w\.]+)\s*,\s*(?P<step>-?\d+)\s*$"
)


class IndVarSimplifyPass(ModulePass):
    name = "pcc-indvars"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = indvars_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def indvars_text(ir_text: str) -> tuple[str, bool]:
    """Merge isomorphic IVs within each basic block."""
    # Group phis by block, then pair up IVs with matching (start, step).
    # 1. Collect phi incomings per name.
    phi_incomings: dict[str, list[tuple[str, str]]] = {}
    phi_types: dict[str, str] = {}
    incoming_re = re.compile(
        r"\[\s*(?P<val>[^,\]]+?)\s*,\s*%(?P<block>[\w\.]+)\s*\]"
    )
    for line in ir_text.splitlines():
        m = _PHI_RE.match(line.rstrip("\n"))
        if not m:
            continue
        phi_types[m.group("name")] = m.group("ty")
        phi_incomings[m.group("name")] = [
            (g.group("val").strip(), g.group("block"))
            for g in incoming_re.finditer(m.group("rest"))
        ]

    # 2. Collect inc patterns: %n.next = add ty, %phi_name, step.
    inc_map: dict[str, tuple[str, int]] = {}
    for line in ir_text.splitlines():
        m = _INC_RE.match(line.rstrip("\n"))
        if not m:
            continue
        # Only track increments whose LHS phi is known.
        if m.group("iv") not in phi_incomings:
            continue
        inc_map[m.group("name")] = (m.group("iv"), int(m.group("step")))

    # 3. For each phi, identify its "signature": (start_const, step).
    # The start_const is the incoming whose block is the preheader
    # (non-latch). We approximate: the incoming that's a literal
    # integer constant is the start. The other incoming should be
    # a %X.next that maps via inc_map to the same phi.
    signatures: dict[str, tuple[int, int, str]] = {}
    for phi_name, incomings in phi_incomings.items():
        start_val: int | None = None
        step_from_latch: int | None = None
        ty = phi_types.get(phi_name, "i32")
        for val, _blk in incomings:
            if re.fullmatch(r"-?\d+", val):
                start_val = int(val)
            elif val.startswith("%"):
                inc_name = val[1:]
                if inc_name in inc_map:
                    iv, step = inc_map[inc_name]
                    if iv == phi_name:
                        step_from_latch = step
        if start_val is not None and step_from_latch is not None:
            signatures[phi_name] = (start_val, step_from_latch, ty)

    replacements: dict[str, str] = {}
    drop_lines: set[int] = set()
    if len(signatures) >= 2:
        # 4. Group IVs by (start, step, ty). Keep one per group, replace
        # the rest with the keeper.
        groups: dict[tuple[int, int, str], list[str]] = {}
        for name, sig in signatures.items():
            groups.setdefault(sig, []).append(name)

        for sig, members in groups.items():
            if len(members) < 2:
                continue
            keeper = sorted(members)[0]
            # Find keeper's inc-name for mapping %dup.next uses.
            keeper_inc = next(
                (inc_n for inc_n, (iv, step) in inc_map.items()
                 if iv == keeper and step == sig[1]),
                None,
            )
            for dup in members:
                if dup == keeper:
                    continue
                replacements[dup] = keeper
                dup_inc = next(
                    (inc_n for inc_n, (iv, step) in inc_map.items()
                     if iv == dup and step == sig[1]),
                    None,
                )
                if dup_inc and keeper_inc:
                    replacements[dup_inc] = keeper_inc

    text = ir_text
    changed = False

    if replacements:
        # 5. Drop duplicate phi + inc lines; substitute uses.
        lines = ir_text.splitlines(keepends=True)
        for idx, line in enumerate(lines):
            m = _PHI_RE.match(line.rstrip("\n"))
            if m and m.group("name") in replacements:
                drop_lines.add(idx)
                continue
            m = _INC_RE.match(line.rstrip("\n"))
            if m and m.group("name") in replacements:
                drop_lines.add(idx)

        kept = [ln for i, ln in enumerate(lines) if i not in drop_lines]
        text = "".join(kept)
        for dup, keeper in replacements.items():
            text = re.sub(r"%" + re.escape(dup) + r"(?![\w\.])", f"%{keeper}", text)
        changed = True

    text, lcssa_changed = _insert_single_pred_exit_lcssa(text)
    text, flags_changed = _mark_simple_iv_increments_no_wrap(text)
    text, smax_changed = _rewrite_simple_iv_exit_to_smax(text)
    return text, changed or lcssa_changed or flags_changed or smax_changed


def _insert_single_pred_exit_lcssa(ir_text: str) -> tuple[str, bool]:
    module = llvm.parse_assembly(ir_text)
    module.verify()

    value_types: dict[str, dict[str, str]] = {}
    loop_blocks_by_fn: dict[str, set[str]] = {}
    exit_use_map: dict[str, dict[str, dict[str, str]]] = {}

    for fn in module.functions:
        if fn.is_declaration:
            continue
        cfg = CFG.of_function(fn)
        info = compute_loop_info(fn)
        loops = info.loops()
        if not loops:
            continue
        fn_types: dict[str, str] = {}
        for arg in fn.arguments:
            if arg.name:
                fn_types[arg.name] = str(arg.type)
        for block in fn.blocks:
            block_name = block.name or "entry"
            for inst in block.instructions:
                text = str(inst).strip()
                m = re.match(r"^%([\w\.]+)\s*=", text)
                if m:
                    fn_types[m.group(1)] = str(inst.type)
        value_types[fn.name] = fn_types
        loop_blocks_by_fn[fn.name] = set().union(*(loop.blocks for loop in loops))

        exit_uses: dict[str, dict[str, str]] = {}
        for loop in loops:
            for exit_block in loop.exit_blocks(cfg):
                preds = tuple(cfg.predecessors.get(exit_block, ()))
                if len(preds) != 1:
                    continue
                pred = preds[0]
                if pred not in loop.blocks:
                    continue
                for block in fn.blocks:
                    if (block.name or "entry") != exit_block:
                        continue
                    for inst in block.instructions:
                        text = str(inst).strip()
                        if " = phi " in text:
                            continue
                        refs = re.findall(r"%([\w\.]+)", text)
                        for ref in refs:
                            if ref in fn_types and _defined_in_loop(ref, loop.blocks, fn):
                                exit_uses.setdefault(exit_block, {})[ref] = pred
        if exit_uses:
            exit_use_map[fn.name] = exit_uses

    if not exit_use_map:
        return ir_text, False

    mut = MutableModule.parse(ir_text)
    changed = False
    for fn in mut.functions:
        fn_exit_uses = exit_use_map.get(fn.name)
        if not fn_exit_uses:
            continue
        taken_names = fn.defined_names()
        fn_types = value_types.get(fn.name, {})
        for exit_block_name, uses in fn_exit_uses.items():
            block = fn.block(exit_block_name)
            if block is None:
                continue
            rename_map: dict[str, str] = {}
            insert_at = 0
            while insert_at < len(block.instructions) and " = phi " in block.instructions[insert_at].text:
                insert_at += 1
            for name, pred in uses.items():
                if name not in fn_types:
                    continue
                phi_name = _fresh_name(f"{name}.lcssa", taken_names)
                phi_text = f"  %{phi_name} = phi {fn_types[name]} [ %{name}, %{pred} ]\n"
                block.instructions.insert(insert_at, Instruction.from_text(phi_text))
                insert_at += 1
                rename_map[name] = phi_name
            if not rename_map:
                continue
            for inst in block.instructions[insert_at:]:
                old = inst.text
                new = old
                for src, dst in rename_map.items():
                    new = re.sub(r"%" + re.escape(src) + r"(?![\w\.])", f"%{dst}", new)
                inst.text = new
            changed = True
    if not changed:
        return ir_text, False
    out = mut.serialize()
    llvm.parse_assembly(out).verify()
    return out, True


def _defined_in_loop(name: str, loop_blocks: set[str], fn: llvm.ValueRef) -> bool:
    for block in fn.blocks:
        block_name = block.name or "entry"
        if block_name not in loop_blocks:
            continue
        for inst in block.instructions:
            text = str(inst).strip()
            m = re.match(r"^%([\w\.]+)\s*=", text)
            if m and m.group(1) == name:
                return True
    return False


def _fresh_name(base: str, taken: set[str]) -> str:
    if base not in taken:
        taken.add(base)
        return base
    i = 1
    while True:
        candidate = f"{base}.{i}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
        i += 1


def _mark_simple_iv_increments_no_wrap(ir_text: str) -> tuple[str, bool]:
    lines = ir_text.splitlines(keepends=True)
    simple_ivs: set[str] = set()
    increment_names: set[str] = set()

    for idx, line in enumerate(lines):
        m = _PHI_RE.match(line.rstrip("\n"))
        if m is None:
            continue
        incomings = m.group("rest")
        if f"[ 0, %entry ]" not in incomings:
            continue
        phi_name = m.group("name")
        # Look ahead in the same block for the canonical signed compare.
        j = idx + 1
        while j < len(lines):
            stripped = lines[j].strip()
            if re.match(r"^[\w\.]+:\s*$", stripped):
                break
            if re.search(
                rf"=\s*icmp\s+slt\s+{re.escape(m.group('ty'))}\s+%{re.escape(phi_name)}\s*,\s*%[\w\.]+",
                stripped,
            ):
                simple_ivs.add(phi_name)
                break
            j += 1

    if not simple_ivs:
        return ir_text, False

    changed = False
    for idx, line in enumerate(lines):
        m = _INC_RE.match(line.rstrip("\n"))
        if m is None:
            continue
        if m.group("iv") not in simple_ivs or m.group("step") != "1":
            continue
        if "nuw" in line or "nsw" in line:
            continue
        lines[idx] = line.replace(
            f"= add {m.group('ty')}",
            f"= add nuw nsw {m.group('ty')}",
            1,
        )
        changed = True

    if not changed:
        return ir_text, False
    return "".join(lines), True


def _rewrite_simple_iv_exit_to_smax(ir_text: str) -> tuple[str, bool]:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    mut = MutableModule.parse(ir_text)
    changed = False

    for fn in module.functions:
        if fn.is_declaration:
            continue
        cfg = CFG.of_function(fn)
        info = compute_loop_info(fn)
        if not info.loops():
            continue
        fn_mut = mut.function(fn.name)
        if fn_mut is None:
            continue
        taken_names = fn_mut.defined_names()
        for loop in info.loops():
            if len(loop.latches) != 1:
                continue
            header = loop.header
            latch = loop.latches[0]
            preheaders = [
                pred_name
                for pred_name in cfg.predecessors.get(header, ())
                if pred_name not in loop.blocks
            ]
            if len(preheaders) != 1:
                continue
            preheader = preheaders[0]
            exit_blocks = loop.exit_blocks(cfg)
            if len(exit_blocks) != 1:
                continue
            exit_block = exit_blocks[0]

            header_block = next((b for b in fn.blocks if (b.name or "entry") == header), None)
            latch_block = next((b for b in fn.blocks if (b.name or "entry") == latch), None)
            if header_block is None or latch_block is None:
                continue

            phi_name = None
            phi_ty = None
            back_name = None
            limit = None
            pred = None
            start_val = None
            compare_on = None
            for inst in header_block.instructions:
                text = str(inst).strip()
                m = _PHI_RE.match(text)
                if m:
                    incomings = m.group("rest")
                    incoming_re = re.compile(r"\[\s*(?P<val>[^,\]]+?)\s*,\s*%(?P<block>[\w\.]+)\s*\]")
                    incoming_map = {
                        g.group("block"): g.group("val").strip()
                        for g in incoming_re.finditer(incomings)
                    }
                    if preheader in incoming_map and latch in incoming_map:
                        preheader_val = incoming_map[preheader]
                        if re.fullmatch(r"-?\d+", preheader_val):
                            phi_name = m.group("name")
                            phi_ty = m.group("ty")
                            start_val = int(preheader_val)
                            back_val = incoming_map[latch]
                            if back_val.startswith("%"):
                                back_name = back_val[1:]
                    continue
                m = re.match(
                    r"^%[\w\.]+\s*=\s*icmp\s+(slt|ult|eq)\s+(i\d+)\s+%([\w\.]+)\s*,\s*([^,\s]+)$",
                    text,
                )
                if m and phi_name is not None and m.group(2) == phi_ty and m.group(3) == phi_name:
                    pred = m.group(1)
                    limit = m.group(4)
                    compare_on = phi_name

            if phi_name is not None and phi_ty is not None and back_name is not None:
                for inst in latch_block.instructions:
                    text = str(inst).strip()
                    m = re.match(
                        r"^%[\w\.]+\s*=\s*icmp\s+(slt|ult|eq)\s+(i\d+)\s+%([\w\.]+)\s*,\s*([^,\s]+)$",
                        text,
                    )
                    if m and m.group(2) == phi_ty and m.group(3) == back_name:
                        pred = m.group(1)
                        limit = m.group(4)
                        compare_on = back_name

            if phi_name is None or phi_ty is None or back_name is None or limit is None or pred is None or start_val is None or compare_on is None:
                continue

            inc_ok = False
            for inst in latch_block.instructions:
                text = str(inst).strip()
                m = _INC_RE.match(text)
                if m and m.group("name") == back_name and m.group("iv") == phi_name and int(m.group("step")) == 1:
                    inc_ok = True
                    break
            if not inc_ok:
                continue

            exit_mut = fn_mut.block(exit_block)
            header_mut = fn_mut.block(header)
            latch_mut = fn_mut.block(latch)
            if exit_mut is None or header_mut is None or latch_mut is None:
                continue
            exit_lines = [inst.text.strip() for inst in exit_mut.instructions]
            if len(exit_lines) != 2:
                continue
            header_exit = (
                tuple(cfg.predecessors.get(exit_block, ())) == (header,)
                and tuple(cfg.successors.get(latch, ())) == (header,)
                and compare_on == phi_name
            )
            latch_exit = (
                tuple(cfg.predecessors.get(exit_block, ())) == (latch,)
                and tuple(cfg.successors.get(header, ())) == (latch,)
                and set(cfg.successors.get(latch, ())) == {header, exit_block}
                and compare_on == back_name
            )
            if not (header_exit or latch_exit):
                continue
            expected_liveout = phi_name if header_exit else back_name
            expected_pred = header if header_exit else latch
            if not re.fullmatch(
                rf"%[\w\.]+\s*=\s*phi\s+{re.escape(phi_ty)}\s+\[\s*%{re.escape(expected_liveout)}\s*,\s*%{re.escape(expected_pred)}\s*\]",
                exit_lines[0],
            ):
                continue
            if not re.fullmatch(r"ret\s+" + re.escape(phi_ty) + r"\s+%[\w\.]+", exit_lines[1]):
                continue

            lower_bound = start_val if header_exit else start_val + 1
            if header_exit:
                header_mut.instructions = [
                    Instruction.from_text(f"  br i1 false, label %{latch}, label %{exit_block}\n")
                ]
                latch_mut.instructions = [
                    Instruction.from_text(f"  br label %{header}\n")
                ]
            else:
                header_mut.instructions = [
                    Instruction.from_text(f"  br label %{latch}\n")
                ]
                if pred == "eq":
                    latch_mut.instructions = [
                        Instruction.from_text(f"  br i1 true, label %{exit_block}, label %{header}\n")
                    ]
                else:
                    latch_mut.instructions = [
                        Instruction.from_text(f"  br i1 false, label %{header}, label %{exit_block}\n")
                    ]
            if pred == "eq" and latch_exit:
                exit_mut.instructions = [
                    Instruction.from_text(f"  ret {phi_ty} {limit}\n"),
                ]
            elif pred == "ult" and lower_bound == 0:
                exit_mut.instructions = [
                    Instruction.from_text(f"  ret {phi_ty} {limit}\n"),
                ]
            else:
                limit_int = int(limit) if re.fullmatch(r"-?\d+", limit) else None
                if limit_int is not None and (pred != "ult" or (limit_int >= 0 and lower_bound >= 0)):
                    exit_mut.instructions = [
                        Instruction.from_text(f"  ret {phi_ty} {max(limit_int, lower_bound)}\n"),
                    ]
                    changed = True
                    continue
                intrinsic_base = "umax" if pred == "ult" else "smax"
                smax_name = _fresh_name(intrinsic_base, taken_names)
                intrinsic_decl = f"declare {phi_ty} @llvm.{intrinsic_base}.{phi_ty}({phi_ty}, {phi_ty})\n"
                if intrinsic_decl not in mut.declarations and intrinsic_decl not in mut.tail_lines:
                    mut.tail_lines.append(intrinsic_decl)
                exit_mut.instructions = [
                    Instruction.from_text(
                        f"  %{smax_name} = call {phi_ty} @llvm.{intrinsic_base}.{phi_ty}({phi_ty} {limit}, {phi_ty} {lower_bound})\n"
                    ),
                    Instruction.from_text(f"  ret {phi_ty} %{smax_name}\n"),
                ]
            changed = True

    if not changed:
        return ir_text, False
    out = mut.serialize()
    llvm.parse_assembly(out).verify()
    return out, True
