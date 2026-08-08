"""Loop Unrolling — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LoopUnrollPass.cpp``
  implements :cpp:class:`llvm::LoopUnrollPass` (partial unroll) and
  :cpp:class:`llvm::LoopFullUnrollPass` (full unroll when trip count
  is known). Partial unrolling uses a cost model
  (``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/Analysis/TargetTransformInfo.h``).
  Full unrolling requires ScalarEvolution trip-count analysis.

Subset implemented here (labelled ``subset``):

- **Small constant full unroll**: for a canonical single-block
  self-loop with a constant trip count and step ``+1``, clone the
  block body once per iteration, substitute phi-carried values
  forward, and drop the back-edge. A local simplify/DCE cleanup then
  collapses the cloned arithmetic into the usual straight-line form.
- **Trivial multi-block trip-one linearization**: when a loop has a
  constant trip count of exactly 1 (single iteration before the exit
  condition fires), eliminate the back-edge and rewrite phi nodes to
  use their "preheader" incoming value. The loop becomes straight-line
  code.

- **Three-block trip-one full unroll**: for the canonical
  ``header -> body -> latch -> header/exit`` shape with one body block
  and one latch, clone the single iteration, redirect the latch to the
  exit with a constant-false branch, materialize narrow ``*.lcssa``
  phis in the exit, and leave one unreachable cloned tail so the CFG
  matches upstream's full-unroll shape before local cleanup.

  Pattern recognized:

      %i = phi [ C0, %entry ], [ %i.next, %latch ]
      %i.next = add i32 %i, 1
      %c = icmp slt i32 %i.next, N    ; or icmp slt i32 %i, N

  where ``N == C0 + 1`` (one iteration). Replace the latch-to-header
  edge with latch-to-exit, and substitute every phi with its
  preheader incoming.

Non-unit step, non-constant exits, and partial unrolling are deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG
from .dce import dce_module_text
from .instsimplify import simplify_module_text
from .ir_mutator import BasicBlock, Instruction, MutableModule
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses
from .integer_fold_contract import (
    FOLD_CONSTANT,
    FOLD_POISON,
    fold_llvm_integer_binary,
    signed_value,
)


_PHI_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*phi\s+(?P<ty>i\d+)\s+(?P<rest>\[.*)$"
)
_ADD_INC_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*add\s+(?:nsw\s+|nuw\s+)*"
    r"(?P<ty>i\d+)\s+%(?P<iv>[\w\.]+)\s*,\s*(?P<step>-?\d+)\s*$"
)
_BINOP_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*(?P<op>add|sub|mul)\s+"
    r"(?P<flags>(?:(?:nsw|nuw)\s+)*)(?P<ty>i\d+)\s+"
    r"(?P<lhs>[^,]+)\s*,\s*(?P<rhs>.+?)\s*$"
)
_ICMP_LIMIT_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*icmp\s+(?P<pred>slt|ult|sle|ule|eq|ne)\s+"
    r"i\d+\s+%(?P<iv>[\w\.]+)\s*,\s*(?P<limit>-?\d+|%[\w\.]+)\s*$"
)
_COND_BR_RE = re.compile(
    r"^\s*br\s+i1\s+%(?P<cond>[\w\.]+)\s*,\s*label\s+%(?P<t>[\w\.]+)\s*,\s*label\s+%(?P<f>[\w\.]+)\s*$"
)
_BR_RE = re.compile(r"^\s*br\s+label\s+%(?P<label>[\w\.]+)\s*$")
_INCOMING_RE = re.compile(
    r"\[\s*(?P<val>[^,\]]+?)\s*,\s*%(?P<block>[\w\.]+)\s*\]"
)
_ASSIGN_RE = re.compile(r"^\s*%(?P<name>[\w\.]+)\s*=")


class LoopUnrollPass(ModulePass):
    name = "pcc-loop-unroll"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = _unroll_all(module)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def _unroll_all(module: llvm.ModuleRef) -> tuple[str, bool]:
    ir_text = str(module)
    any_change = False
    for fn in module.functions:
        if fn.is_declaration:
            continue
        info = compute_loop_info(fn)
        cfg = CFG.of_function(fn)
        for loop in info.loops():
            trip_info = _constant_trip_count(fn, loop, cfg)
            if trip_info is None:
                continue
            trip_count, symbolic_limit = trip_info
            if trip_count > 1:
                if len(loop.blocks) == 1:
                    new_text, changed = _full_unroll_simple_self_loop(
                        ir_text, fn.name, loop, cfg, trip_count
                    )
                elif len(loop.blocks) == 2:
                    new_text, changed = _full_unroll_two_block_loop(
                        ir_text, fn, loop, cfg, trip_count
                    )
                    if not changed:
                        new_text, changed = _full_unroll_two_block_latch_exit_loop(
                            ir_text, fn, loop, cfg, trip_count
                        )
                elif len(loop.blocks) == 3:
                    new_text, changed = _full_unroll_three_block_loop(
                        ir_text, fn, loop, cfg, trip_count
                    )
                else:
                    changed = False
                    new_text = ir_text
            else:
                if len(loop.blocks) == 3:
                    new_text, changed = _full_unroll_trip_one_three_block_loop(
                        ir_text, fn, loop, cfg
                    )
                else:
                    new_text, changed = _linearize(ir_text, fn.name, loop, cfg)
            if changed:
                try:
                    cleaned = new_text
                    if not symbolic_limit:
                        for _ in range(4):
                            prev = cleaned
                            cleaned, _ = simplify_module_text(cleaned)
                            cleaned, _ = dce_module_text(cleaned)
                            if cleaned == prev:
                                break
                    llvm.parse_assembly(cleaned).verify()
                    ir_text = cleaned
                    any_change = True
                except RuntimeError:
                    continue
    return ir_text, any_change


def _resolve_small_const(token: str, const_values: dict[str, int]) -> int | None:
    token = token.strip()
    if token.lstrip("-").isdigit():
        return int(token)
    if token.startswith("%"):
        return const_values.get(token[1:])
    return None


def _constant_trip_count(fn, loop, cfg: CFG) -> tuple[int, bool] | None:
    """Return a small constant trip count for the supported subset."""
    start_val: int | None = None
    start_iv: str | None = None
    step: int | None = None
    limit: int | None = None
    pred: str | None = None
    cmp_iv: str | None = None
    symbolic_limit = False
    const_values: dict[str, int] = {}
    inc_of: dict[str, str] = {}
    cmp_candidates: list[tuple[str, str, int, bool]] = []

    external_preds = [
        pred_name
        for pred_name in cfg.predecessors.get(loop.header, ())
        if pred_name not in loop.blocks
    ]
    if len(external_preds) != 1:
        return None
    preheader = external_preds[0]

    self_loop = len(loop.blocks) == 1 and loop.header in loop.blocks
    header_block = None
    for block in fn.blocks:
        block_name = block.name or "entry"
        if block_name not in loop.blocks and block_name != preheader:
            continue
        if block_name == loop.header:
            header_block = block
        for inst in block.instructions:
            text = str(inst).strip()
            m = _PHI_RE.match(text)
            if m:
                for g in _INCOMING_RE.finditer(m.group("rest")):
                    if g.group("block") != preheader:
                        continue
                    val = g.group("val").strip()
                    if val.lstrip("-").isdigit() and start_val is None:
                        start_val = int(val)
                        start_iv = m.group("name")
                        break
            m = _ADD_INC_RE.match(text)
            if m:
                step = int(m.group("step"))
                inc_of[m.group("name")] = m.group("iv")
            m = _BINOP_RE.match(text)
            if m and m.group("op") in {"add", "sub"}:
                lhs = m.group("lhs").strip()
                rhs = m.group("rhs").strip()
                lhs_const = _resolve_small_const(lhs, const_values)
                rhs_const = _resolve_small_const(rhs, const_values)
                value: int | None = None
                if lhs_const is not None and rhs_const is not None:
                    width = int(m.group("ty")[1:])
                    status, raw_value = fold_llvm_integer_binary(
                        m.group("op"),
                        width,
                        lhs_const,
                        rhs_const,
                        m.group("flags"),
                    )
                    if status == FOLD_CONSTANT:
                        value = signed_value(raw_value, width)
                if value is not None:
                    const_values[m.group("name")] = value
            m = _ICMP_LIMIT_RE.match(text)
            if m:
                limit_const = _resolve_small_const(m.group("limit"), const_values)
                if limit_const is not None:
                    cmp_candidates.append((
                        m.group("pred"),
                        m.group("iv"),
                        limit_const,
                        m.group("limit").strip().startswith("%"),
                    ))

    if start_iv is not None:
        for cand_pred, cand_iv, cand_limit, cand_symbolic in cmp_candidates:
            if cand_iv == start_iv or inc_of.get(cand_iv) == start_iv:
                pred = cand_pred
                cmp_iv = cand_iv
                limit = cand_limit
                symbolic_limit = cand_symbolic
                break

    if start_val is None or step != 1 or limit is None or pred is None:
        return None

    if self_loop:
        if header_block is None:
            return None
        insts = [str(inst).strip() for inst in header_block.instructions]
        if not insts:
            return None
        br = _COND_BR_RE.match(insts[-1])
        if br is None:
            return None
        if br.group("t") != loop.header:
            return None
        if cmp_iv is None:
            return None
        if pred in ("slt", "ult"):
            trip = limit - start_val
        else:
            trip = limit - start_val + 1
        return (trip, symbolic_limit) if trip > 0 else None

    if len(loop.blocks) == 2:
        body_blocks = [block for block in loop.blocks if block != loop.header]
        if len(body_blocks) != 1:
            return None
        body = body_blocks[0]
        exit_blocks = loop.exit_blocks(cfg)
        if len(exit_blocks) != 1:
            return None
        header_block = next((b for b in fn.blocks if (b.name or "entry") == loop.header), None)
        body_block = next((b for b in fn.blocks if (b.name or "entry") == body), None)
        if header_block is None or body_block is None:
            return None
        header_insts = [str(inst).strip() for inst in header_block.instructions]
        body_insts = [str(inst).strip() for inst in body_block.instructions]
        if not header_insts or not body_insts:
            return None
        header_cond = _COND_BR_RE.match(header_insts[-1])
        header_br = _BR_RE.match(header_insts[-1])
        body_cond = _COND_BR_RE.match(body_insts[-1])
        if tuple(cfg.successors.get(body, ())) == (loop.header,):
            if header_cond is None or header_cond.group("t") != body or cmp_iv is None:
                return None
            if pred in ("slt", "ult"):
                trip = limit - start_val
            else:
                trip = limit - start_val + 1
            return (trip, symbolic_limit) if trip > 0 else None
        if (
            header_br is not None
            and header_br.group("label") == body
            and body_cond is not None
            and {body_cond.group("t"), body_cond.group("f")} == {loop.header, exit_blocks[0]}
            and cmp_iv is not None
        ):
            if pred in ("slt", "ult"):
                trip = limit - start_val
            else:
                trip = limit - start_val + 1
            return (trip, symbolic_limit) if trip > 0 else None
        return None

    if len(loop.blocks) == 3:
        header_block = next((b for b in fn.blocks if (b.name or "entry") == loop.header), None)
        if header_block is None:
            return None
        insts = [str(inst).strip() for inst in header_block.instructions]
        if not insts:
            return None
        br = _COND_BR_RE.match(insts[-1])
        if br is not None and cmp_iv is not None:
            body_blocks = [block for block in loop.blocks if block != loop.header]
            if len(body_blocks) != 2:
                return None
            exit_blocks = loop.exit_blocks(cfg)
            if len(exit_blocks) != 1:
                return None
            exit_on_true = br.group("t") == exit_blocks[0]
            if pred in ("slt", "ult"):
                trip = limit - start_val
            elif pred in ("sle", "ule"):
                trip = limit - start_val + 1
            elif pred == "eq" and exit_on_true and cmp_iv == start_iv:
                trip = limit - start_val
            elif pred == "ne" and not exit_on_true and cmp_iv == start_iv:
                trip = limit - start_val
            else:
                return None
            return (trip, symbolic_limit) if trip > 0 else None
        latch_block = next((b for b in fn.blocks if (b.name or "entry") in loop.latches), None)
        if latch_block is not None:
            latch_insts = [str(inst).strip() for inst in latch_block.instructions]
            if latch_insts:
                latch_br = _COND_BR_RE.match(latch_insts[-1])
                if latch_br is not None and cmp_iv is not None:
                    exit_blocks = loop.exit_blocks(cfg)
                    if len(exit_blocks) != 1:
                        return None
                    exit_on_true = latch_br.group("t") == exit_blocks[0]
                    if pred in ("slt", "ult"):
                        trip = limit - start_val
                    elif pred in ("sle", "ule"):
                        trip = limit - start_val + 1
                    elif (
                        pred == "eq"
                        and exit_on_true
                        and start_iv is not None
                        and (
                            cmp_iv == start_iv
                            or inc_of.get(cmp_iv) == start_iv
                        )
                    ):
                        trip = limit - start_val
                    elif (
                        pred == "ne"
                        and not exit_on_true
                        and start_iv is not None
                        and (
                            cmp_iv == start_iv
                            or inc_of.get(cmp_iv) == start_iv
                        )
                    ):
                        trip = limit - start_val
                    else:
                        return None
                    return (trip, symbolic_limit) if trip > 0 else None

    trip = limit - start_val
    return (trip, symbolic_limit) if trip == 1 else None


def _full_unroll_two_block_loop(
    ir_text: str,
    fn,
    loop,
    cfg: CFG,
    trip_count: int,
) -> tuple[str, bool]:
    if len(loop.blocks) != 2:
        return ir_text, False
    header = loop.header
    body_blocks = [block for block in loop.blocks if block != header]
    if len(body_blocks) != 1:
        return ir_text, False
    body_name = body_blocks[0]
    exit_blocks = loop.exit_blocks(cfg)
    if len(exit_blocks) != 1:
        return ir_text, False
    exit_name = exit_blocks[0]
    if tuple(cfg.successors.get(body_name, ())) != (header,):
        return ir_text, False

    outside_preds = [
        pred_name
        for pred_name in cfg.predecessors.get(header, ())
        if pred_name not in loop.blocks
    ]
    if len(outside_preds) != 1:
        return ir_text, False
    preheader = outside_preds[0]

    mut = MutableModule.parse(ir_text)
    fn_mut = mut.function(fn.name)
    if fn_mut is None:
        return ir_text, False
    header_mut = fn_mut.block(header)
    body_mut = fn_mut.block(body_name)
    exit_mut = fn_mut.block(exit_name)
    if header_mut is None or body_mut is None or exit_mut is None:
        return ir_text, False
    if any(" = phi " in inst.text for inst in exit_mut.instructions):
        return ir_text, False

    phi_info: dict[str, dict[str, str]] = {}
    header_nonphi: list[Instruction] = []
    for inst in header_mut.instructions[:-1]:
        text = inst.text.rstrip("\n")
        match = _PHI_RE.match(text)
        if match is None:
            header_nonphi.append(inst)
            continue
        incoming: dict[str, str] = {}
        for g in _INCOMING_RE.finditer(match.group("rest")):
            incoming[g.group("block")] = g.group("val").strip()
        if preheader not in incoming or body_name not in incoming:
            return ir_text, False
        phi_info[match.group("name")] = {
            "ty": match.group("ty"),
            "entry": incoming[preheader],
            "back": incoming[body_name],
        }
    if not phi_info:
        return ir_text, False

    body_term = body_mut.terminator
    if body_term is None or _BR_RE.match(body_term.text.strip()) is None:
        return ir_text, False
    body_insts = body_mut.instructions[:-1]

    header_mut.instructions = [Instruction.from_text(f"  br label %{body_name}\n")]
    value_types = _value_types_for_function(fn)

    current_values = {name: info["entry"] for name, info in phi_info.items()}
    final_values: dict[str, str] = dict(current_values)
    new_body_blocks: list[BasicBlock] = []

    def _render_body_iteration(
        iteration: int,
        incoming_values: dict[str, str],
        *,
        terminator_text: str,
        update_finals: bool,
    ) -> tuple[list[Instruction], dict[str, str], dict[str, str]]:
        iter_map = dict(incoming_values)
        cloned_insts: list[Instruction] = []
        for inst in body_insts:
            text = inst.text
            for old, new in iter_map.items():
                text = re.sub(
                    r"%" + re.escape(old) + r"(?![\w\.])",
                    new,
                    text,
                )
            if inst.result_name is not None:
                const_folded = _try_fold_const_binop(text)
                if const_folded is not None:
                    iter_map[inst.result_name] = const_folded
                    if update_finals:
                        final_values[inst.result_name] = const_folded
                    continue
                new_name = inst.result_name if iteration == 0 else f"{inst.result_name}.{iteration}"
                text = re.sub(
                    r"^(\s*)%" + re.escape(inst.result_name) + r"(\s*=)",
                    r"\1%" + new_name + r"\2",
                    text,
                )
                iter_map[inst.result_name] = f"%{new_name}"
                if update_finals:
                    final_values[inst.result_name] = f"%{new_name}"
            cloned_insts.append(Instruction.from_text(text))

        next_values = {
            name: _substitute_token(info["back"], iter_map)
            for name, info in phi_info.items()
        }
        if update_finals:
            for name, value in next_values.items():
                final_values[name] = value
        cloned_insts.append(Instruction.from_text(terminator_text))
        return cloned_insts, next_values, iter_map

    for iteration in range(trip_count):
        block_label = body_name if iteration == 0 else f"{body_name}.{iteration}"
        if iteration < trip_count - 1:
            term_text = f"  br label %{body_name}.{iteration + 1}\n"
        else:
            term_text = f"  br i1 false, label %{body_name}.{trip_count}, label %{exit_name}\n"
        cloned_insts, current_values, _ = _render_body_iteration(
            iteration,
            current_values,
            terminator_text=term_text,
            update_finals=True,
        )
        new_body_blocks.append(
            BasicBlock(
                name=block_label,
                label_line=f"{block_label}:\n",
                instructions=cloned_insts,
            )
        )

    last_body_name = new_body_blocks[-1].name
    unreachable_insts, _, _ = _render_body_iteration(
        trip_count,
        current_values,
        terminator_text="  unreachable\n",
        update_finals=False,
    )
    unreachable_block = BasicBlock(
        name=f"{body_name}.{trip_count}",
        label_line=f"{body_name}.{trip_count}:\n",
        instructions=unreachable_insts,
    )

    taken_names = fn_mut.defined_names()
    lcssa_rename: dict[str, str] = {}
    lcssa_order: list[str] = []
    for inst in exit_mut.instructions:
        if " = phi " in inst.text:
            continue
        for operand in re.findall(r"%([\w\.]+)", inst.text):
            if operand not in final_values or operand in lcssa_rename:
                continue
            if operand not in value_types:
                return ir_text, False
            lcssa_rename[operand] = _fresh_name(f"{operand}.lcssa", taken_names)
            lcssa_order.append(operand)

    new_exit_instructions: list[Instruction] = []
    for operand in lcssa_order:
        new_exit_instructions.append(
            Instruction.from_text(
                f"  %{lcssa_rename[operand]} = phi {value_types[operand]} "
                f"[ {final_values[operand]}, %{last_body_name} ]\n"
            )
        )
    for inst in exit_mut.instructions:
        text = inst.text
        for old, new in lcssa_rename.items():
            text = re.sub(
                r"%" + re.escape(old) + r"(?![\w\.])",
                f"%{new}",
                text,
            )
        new_exit_instructions.append(Instruction.from_text(text))
    exit_mut.instructions = new_exit_instructions

    new_blocks: list[BasicBlock] = []
    for block in fn_mut.blocks:
        if block.name == body_name:
            new_blocks.extend(new_body_blocks)
            new_blocks.append(unreachable_block)
            continue
        new_blocks.append(block)
    fn_mut.blocks = new_blocks

    new_text = mut.serialize()
    return new_text, new_text != ir_text


def _full_unroll_two_block_latch_exit_loop(
    ir_text: str,
    fn,
    loop,
    cfg: CFG,
    trip_count: int,
) -> tuple[str, bool]:
    if len(loop.blocks) != 2 or trip_count <= 1:
        return ir_text, False
    header = loop.header
    body_blocks = [block for block in loop.blocks if block != header]
    if len(body_blocks) != 1:
        return ir_text, False
    latch_name = body_blocks[0]
    exit_blocks = loop.exit_blocks(cfg)
    if len(exit_blocks) != 1:
        return ir_text, False
    exit_name = exit_blocks[0]

    outside_preds = [
        pred_name
        for pred_name in cfg.predecessors.get(header, ())
        if pred_name not in loop.blocks
    ]
    if len(outside_preds) != 1:
        return ir_text, False
    preheader = outside_preds[0]

    mut = MutableModule.parse(ir_text)
    fn_mut = mut.function(fn.name)
    if fn_mut is None:
        return ir_text, False
    header_mut = fn_mut.block(header)
    latch_mut = fn_mut.block(latch_name)
    exit_mut = fn_mut.block(exit_name)
    if header_mut is None or latch_mut is None or exit_mut is None:
        return ir_text, False
    if any(" = phi " in inst.text for inst in exit_mut.instructions):
        return ir_text, False
    exit_texts = [inst.text for inst in exit_mut.instructions]
    if len(exit_texts) != 1 or exit_texts[0].strip() != "ret void":
        return ir_text, False

    header_term = header_mut.terminator
    latch_term = latch_mut.terminator
    if header_term is None or latch_term is None:
        return ir_text, False
    header_br = _BR_RE.match(header_term.text.strip())
    latch_cond = _COND_BR_RE.match(latch_term.text.strip())
    if (
        header_br is None
        or header_br.group("label") != latch_name
        or latch_cond is None
        or {latch_cond.group("t"), latch_cond.group("f")} != {header, exit_name}
    ):
        return ir_text, False

    phi_info: dict[str, dict[str, str]] = {}
    header_template: list[Instruction] = []
    for inst in header_mut.instructions[:-1]:
        text = inst.text.rstrip("\n")
        match = _PHI_RE.match(text)
        if match is None:
            header_template.append(inst)
            continue
        incoming: dict[str, str] = {}
        for g in _INCOMING_RE.finditer(match.group("rest")):
            incoming[g.group("block")] = g.group("val").strip()
        if preheader not in incoming or latch_name not in incoming:
            return ir_text, False
        phi_info[match.group("name")] = {
            "entry": incoming[preheader],
            "back": incoming[latch_name],
        }
    if not phi_info:
        return ir_text, False

    latch_template = list(latch_mut.instructions[:-1])
    current_values = {name: info["entry"] for name, info in phi_info.items()}

    def _render_values(
        insts: list[Instruction],
        incoming_values: dict[str, str],
        *,
        suffix: str,
    ) -> tuple[list[Instruction], dict[str, str]]:
        value_map = dict(incoming_values)
        rendered: list[Instruction] = []
        for inst in insts:
            text = inst.text
            for old, new in value_map.items():
                text = re.sub(
                    r"%" + re.escape(old) + r"(?![\w\.])",
                    new,
                    text,
                )
            if inst.result_name is not None:
                const_folded = _try_fold_const_binop(text)
                if const_folded is not None:
                    value_map[inst.result_name] = const_folded
                    continue
                new_name = inst.result_name if not suffix else f"{inst.result_name}.{suffix}"
                text = re.sub(
                    r"^(\s*)%" + re.escape(inst.result_name) + r"(\s*=)",
                    r"\1%" + new_name + r"\2",
                    text,
                )
                value_map[inst.result_name] = f"%{new_name}"
            rendered.append(Instruction.from_text(text))
        return rendered, value_map

    header_body, after_header = _render_values(header_template, current_values, suffix="")
    header_mut.instructions = header_body + [Instruction.from_text(f"  br label %{latch_name}\n")]

    iteration_blocks: list[BasicBlock] = []
    after_latch = dict(after_header)
    for inst in latch_template:
        text = inst.text
        for old, new in after_header.items():
            text = re.sub(
                r"%" + re.escape(old) + r"(?![\w\.])",
                new,
                text,
            )
        if inst.result_name is not None:
            const_folded = _try_fold_const_binop(text)
            if const_folded is not None:
                after_latch[inst.result_name] = const_folded
            else:
                after_latch[inst.result_name] = f"%{inst.result_name}"
    current_values = {
        name: _substitute_token(info["back"], after_latch)
        for name, info in phi_info.items()
    }

    for iteration in range(1, trip_count):
        block_label = latch_name if iteration == 1 else f"{latch_name}.{iteration - 1}"
        suffix = str(iteration) if iteration > 1 else ""
        block_body, after_body = _render_values(header_template, current_values, suffix=suffix)
        next_label = f"{latch_name}.{iteration}"
        block_insts = list(block_body)
        block_insts.append(
            Instruction.from_text(
                f"  br label %{next_label}\n" if iteration < trip_count else "  ret void\n"
            )
        )
        iteration_blocks.append(
            BasicBlock(
                name=block_label,
                label_line=f"{block_label}:\n",
                instructions=block_insts,
            )
        )
        after_latch = dict(after_body)
        for inst in latch_template:
            text = inst.text
            for old, new in after_body.items():
                text = re.sub(
                    r"%" + re.escape(old) + r"(?![\w\.])",
                    new,
                    text,
                )
            if inst.result_name is not None:
                const_folded = _try_fold_const_binop(text)
                if const_folded is not None:
                    after_latch[inst.result_name] = const_folded
                else:
                    renamed = inst.result_name if not suffix else f"{inst.result_name}.{suffix}"
                    after_latch[inst.result_name] = f"%{renamed}"
        current_values = {
            name: _substitute_token(info["back"], after_latch)
            for name, info in phi_info.items()
        }

    iteration_blocks.append(
        BasicBlock(
            name=f"{latch_name}.{trip_count - 1}",
            label_line=f"{latch_name}.{trip_count - 1}:\n",
            instructions=[Instruction.from_text("  ret void\n")],
        )
    )

    new_blocks: list[BasicBlock] = []
    for block in fn_mut.blocks:
        if block.name == latch_name:
            new_blocks.extend(iteration_blocks)
            continue
        if block.name == exit_name:
            continue
        new_blocks.append(block)
    fn_mut.blocks = new_blocks

    new_text = mut.serialize()
    return new_text, new_text != ir_text


def _full_unroll_trip_one_three_block_loop(
    ir_text: str,
    fn,
    loop,
    cfg: CFG,
) -> tuple[str, bool]:
    if len(loop.blocks) != 3:
        return ir_text, False
    header = loop.header
    if len(loop.latches) != 1:
        return ir_text, False
    latch_name = loop.latches[0]
    body_blocks = [block for block in loop.blocks if block not in {header, latch_name}]
    if len(body_blocks) != 1:
        return ir_text, False
    body_name = body_blocks[0]
    exit_blocks = loop.exit_blocks(cfg)
    if len(exit_blocks) != 1:
        return ir_text, False
    exit_name = exit_blocks[0]
    if tuple(cfg.successors.get(body_name, ())) != (latch_name,):
        return ir_text, False
    if exit_name not in cfg.successors.get(latch_name, ()) and header not in cfg.successors.get(latch_name, ()):
        return ir_text, False

    outside_preds = [
        pred_name
        for pred_name in cfg.predecessors.get(header, ())
        if pred_name not in loop.blocks
    ]
    if len(outside_preds) != 1:
        return ir_text, False
    preheader = outside_preds[0]

    mut = MutableModule.parse(ir_text)
    fn_mut = mut.function(fn.name)
    if fn_mut is None:
        return ir_text, False
    header_mut = fn_mut.block(header)
    body_mut = fn_mut.block(body_name)
    latch_mut = fn_mut.block(latch_name)
    exit_mut = fn_mut.block(exit_name)
    if header_mut is None or body_mut is None or latch_mut is None or exit_mut is None:
        return ir_text, False
    if any(" = phi " in inst.text for inst in exit_mut.instructions):
        return ir_text, False

    header_term = header_mut.terminator
    latch_term = latch_mut.terminator
    if header_term is None or latch_term is None:
        return ir_text, False
    header_cond = _COND_BR_RE.match(header_term.text.strip())
    latch_cond = _COND_BR_RE.match(latch_term.text.strip())
    header_br = _BR_RE.match(header_term.text.strip())
    latch_br = _BR_RE.match(latch_term.text.strip())
    if (
        header_cond is not None
        and {header_cond.group("t"), header_cond.group("f")} == {body_name, exit_name}
        and latch_br is not None
        and latch_br.group("label") == header
    ):
        mode = "header-exit"
    elif (
        header_br is not None
        and header_br.group("label") == body_name
        and latch_cond is not None
        and {latch_cond.group("t"), latch_cond.group("f")} == {header, exit_name}
    ):
        mode = "latch-exit"
    else:
        return ir_text, False

    phi_info: dict[str, dict[str, str]] = {}
    tail_cmp_name: str | None = None
    tail_cmp_pred: str | None = None
    tail_cmp_iv: str | None = None
    tail_cmp_limit: str | None = None
    for inst in header_mut.instructions[:-1]:
        text = inst.text.rstrip("\n")
        match = _PHI_RE.match(text)
        if match is None:
            cmp_match = _ICMP_LIMIT_RE.match(text)
            if cmp_match is not None and cmp_match.group("limit").strip().startswith("%"):
                tail_cmp_name = cmp_match.group("name")
                tail_cmp_pred = cmp_match.group("pred")
                tail_cmp_iv = cmp_match.group("iv")
                tail_cmp_limit = cmp_match.group("limit").strip()
            continue
        incoming: dict[str, str] = {}
        for g in _INCOMING_RE.finditer(match.group("rest")):
            incoming[g.group("block")] = g.group("val").strip()
        if preheader not in incoming or latch_name not in incoming:
            return ir_text, False
        phi_info[match.group("name")] = {
            "ty": match.group("ty"),
            "entry": incoming[preheader],
            "back": incoming[latch_name],
        }
    if not phi_info:
        return ir_text, False

    body_term = body_mut.terminator
    if body_term is None or _BR_RE.match(body_term.text.strip()) is None:
        return ir_text, False

    value_types = _value_types_for_function(fn)
    current_values = {name: info["entry"] for name, info in phi_info.items()}

    def _render_block(
        insts: list[Instruction],
        incoming_values: dict[str, str],
        *,
        terminator_text: str,
    ) -> tuple[list[Instruction], dict[str, str], dict[str, str]]:
        value_map = dict(incoming_values)
        produced_values: dict[str, str] = {}
        rendered: list[Instruction] = []
        for inst in insts:
            text = inst.text
            for old, new in value_map.items():
                text = re.sub(
                    r"%" + re.escape(old) + r"(?![\w\.])",
                    new,
                    text,
                )
            if inst.result_name is not None:
                const_folded = _try_fold_const_binop(text)
                if const_folded is not None:
                    value_map[inst.result_name] = const_folded
                    produced_values[inst.result_name] = const_folded
                    continue
                value_map[inst.result_name] = f"%{inst.result_name}"
                produced_values[inst.result_name] = f"%{inst.result_name}"
            rendered.append(Instruction.from_text(text))
        rendered.append(Instruction.from_text(terminator_text))
        return rendered, value_map, produced_values

    body_insts, after_body, body_defs = _render_block(
        body_mut.instructions[:-1], current_values, terminator_text=f"  br label %{latch_name}\n"
    )

    if mode == "header-exit":
        latch_insts, after_latch, latch_defs = _render_block(
            latch_mut.instructions[:-1],
            after_body,
            terminator_text=f"  br i1 false, label %{body_name}.1, label %{exit_name}\n",
        )
        live_out_values: dict[str, str] = {}
        live_out_values.update(body_defs)
        live_out_values.update(latch_defs)
        for name, info in phi_info.items():
            live_out_values[name] = _substitute_token(info["back"], after_latch)
    else:
        exit_texts = [inst.text for inst in exit_mut.instructions]
        if len(exit_texts) != 1 or not exit_texts[0].lstrip().startswith("ret "):
            return ir_text, False
        live_out_values = dict(after_body)
        for name in phi_info:
            live_out_values[name] = current_values[name]
        ret_text = exit_texts[0]
        for old, new in live_out_values.items():
            ret_text = re.sub(
                r"%" + re.escape(old) + r"(?![\w\.])",
                new,
                ret_text,
            )
        latch_insts, _, _ = _render_block(
            latch_mut.instructions[:-1],
            after_body,
            terminator_text=ret_text if ret_text.endswith("\n") else ret_text + "\n",
        )

    header_mut.instructions = [Instruction.from_text(f"  br label %{body_name}\n")]
    body_mut.instructions = body_insts
    latch_mut.instructions = latch_insts

    if mode == "header-exit":
        taken_names = fn_mut.defined_names()
        lcssa_rename: dict[str, str] = {}
        lcssa_order: list[str] = []
        for inst in exit_mut.instructions:
            if " = phi " in inst.text:
                continue
            for operand in re.findall(r"%([\w\.]+)", inst.text):
                if operand not in live_out_values or operand in lcssa_rename:
                    continue
                if operand not in value_types:
                    return ir_text, False
                lcssa_rename[operand] = _fresh_name(f"{operand}.lcssa", taken_names)
                lcssa_order.append(operand)

        new_exit_instructions: list[Instruction] = []
        for operand in lcssa_order:
            new_exit_instructions.append(
                Instruction.from_text(
                    f"  %{lcssa_rename[operand]} = phi {value_types[operand]} "
                    f"[ {live_out_values[operand]}, %{latch_name} ]\n"
                )
            )
        for inst in exit_mut.instructions:
            text = inst.text
            for old, new in lcssa_rename.items():
                text = re.sub(
                    r"%" + re.escape(old) + r"(?![\w\.])",
                    f"%{new}",
                    text,
                )
            new_exit_instructions.append(Instruction.from_text(text))
        exit_mut.instructions = new_exit_instructions

        body1 = BasicBlock(
            name=f"{body_name}.1",
            label_line=f"{body_name}.1:\n",
            instructions=[Instruction.from_text(f"  br label %{latch_name}.1\n")],
        )
        latch1 = BasicBlock(
            name=f"{latch_name}.1",
            label_line=f"{latch_name}.1:\n",
            instructions=[Instruction.from_text("  unreachable\n")],
        )

        new_blocks: list[BasicBlock] = []
        for block in fn_mut.blocks:
            new_blocks.append(block)
            if block.name == latch_name:
                new_blocks.append(body1)
                new_blocks.append(latch1)
        fn_mut.blocks = new_blocks
    else:
        fn_mut.blocks = [block for block in fn_mut.blocks if block.name != exit_name]

    new_text = mut.serialize()
    return new_text, new_text != ir_text


def _full_unroll_three_block_loop(
    ir_text: str,
    fn,
    loop,
    cfg: CFG,
    trip_count: int,
) -> tuple[str, bool]:
    if len(loop.blocks) != 3 or trip_count <= 1:
        return ir_text, False
    header = loop.header
    if len(loop.latches) != 1:
        return ir_text, False
    latch_name = loop.latches[0]
    body_blocks = [block for block in loop.blocks if block not in {header, latch_name}]
    if len(body_blocks) != 1:
        return ir_text, False
    body_name = body_blocks[0]
    exit_blocks = loop.exit_blocks(cfg)
    if len(exit_blocks) != 1:
        return ir_text, False
    exit_name = exit_blocks[0]
    if tuple(cfg.successors.get(body_name, ())) != (latch_name,):
        return ir_text, False

    outside_preds = [
        pred_name
        for pred_name in cfg.predecessors.get(header, ())
        if pred_name not in loop.blocks
    ]
    if len(outside_preds) != 1:
        return ir_text, False
    preheader = outside_preds[0]

    mut = MutableModule.parse(ir_text)
    fn_mut = mut.function(fn.name)
    if fn_mut is None:
        return ir_text, False
    header_mut = fn_mut.block(header)
    body_mut = fn_mut.block(body_name)
    latch_mut = fn_mut.block(latch_name)
    exit_mut = fn_mut.block(exit_name)
    if header_mut is None or body_mut is None or latch_mut is None or exit_mut is None:
        return ir_text, False
    if any(" = phi " in inst.text for inst in exit_mut.instructions):
        return ir_text, False

    header_term = header_mut.terminator
    body_term = body_mut.terminator
    latch_term = latch_mut.terminator
    if header_term is None or body_term is None or latch_term is None:
        return ir_text, False
    header_cond = _COND_BR_RE.match(header_term.text.strip())
    header_br = _BR_RE.match(header_term.text.strip())
    latch_cond = _COND_BR_RE.match(latch_term.text.strip())
    latch_br = _BR_RE.match(latch_term.text.strip())
    header_exit_on_true = False
    if (
        header_cond is not None
        and {header_cond.group("t"), header_cond.group("f")} == {body_name, exit_name}
        and latch_br is not None
        and latch_br.group("label") == header
    ):
        mode = "header-exit"
        header_exit_on_true = header_cond.group("t") == exit_name
    elif (
        header_br is not None
        and header_br.group("label") == body_name
        and latch_cond is not None
        and {latch_cond.group("t"), latch_cond.group("f")} == {header, exit_name}
    ):
        mode = "latch-exit"
    else:
        return ir_text, False
    if _BR_RE.match(body_term.text.strip()) is None:
        return ir_text, False

    phi_info: dict[str, dict[str, str]] = {}
    tail_cmp_name: str | None = None
    tail_cmp_pred: str | None = None
    tail_cmp_iv: str | None = None
    tail_cmp_limit: str | None = None
    for inst in header_mut.instructions[:-1]:
        text = inst.text.rstrip("\n")
        match = _PHI_RE.match(text)
        if match is None:
            cmp_match = _ICMP_LIMIT_RE.match(text)
            if cmp_match is not None and cmp_match.group("limit").strip().startswith("%"):
                tail_cmp_name = cmp_match.group("name")
                tail_cmp_pred = cmp_match.group("pred")
                tail_cmp_iv = cmp_match.group("iv")
                tail_cmp_limit = cmp_match.group("limit").strip()
            continue
        incoming: dict[str, str] = {}
        for g in _INCOMING_RE.finditer(match.group("rest")):
            incoming[g.group("block")] = g.group("val").strip()
        if preheader not in incoming or latch_name not in incoming:
            return ir_text, False
        phi_info[match.group("name")] = {
            "ty": match.group("ty"),
            "entry": incoming[preheader],
            "back": incoming[latch_name],
        }
    if not phi_info:
        return ir_text, False

    value_types = _value_types_for_function(fn)
    taken_names = fn_mut.defined_names()
    current_values = {name: info["entry"] for name, info in phi_info.items()}
    final_values: dict[str, str] = dict(current_values)
    body_template = list(body_mut.instructions[:-1])
    latch_template = list(latch_mut.instructions[:-1])

    def _render_iteration_block(
        insts: list[Instruction],
        incoming_values: dict[str, str],
        *,
        suffix: str,
        terminator_text: str,
    ) -> tuple[list[Instruction], dict[str, str]]:
        value_map = dict(incoming_values)
        rendered: list[Instruction] = []
        for inst in insts:
            text = inst.text
            for old, new in value_map.items():
                text = re.sub(
                    r"%" + re.escape(old) + r"(?![\w\.])",
                    new,
                    text,
                )
            if inst.result_name is not None:
                const_folded = _try_fold_const_binop(text)
                if const_folded is not None:
                    value_map[inst.result_name] = const_folded
                    continue
                new_name = inst.result_name if not suffix else f"{inst.result_name}.{suffix}"
                text = re.sub(
                    r"^(\s*)%" + re.escape(inst.result_name) + r"(\s*=)",
                    r"\1%" + new_name + r"\2",
                    text,
                )
                value_map[inst.result_name] = f"%{new_name}"
            rendered.append(Instruction.from_text(text))
        rendered.append(Instruction.from_text(terminator_text))
        return rendered, value_map

    header_mut.instructions = [Instruction.from_text(f"  br label %{body_name}\n")]
    iteration_blocks: list[BasicBlock] = []
    last_latch_label = latch_name

    for iteration in range(trip_count):
        suffix = "" if iteration == 0 else str(iteration)
        body_label = body_name if iteration == 0 else f"{body_name}.{iteration}"
        latch_label = latch_name if iteration == 0 else f"{latch_name}.{iteration}"
        next_body_label = f"{body_name}.{iteration + 1}"

        body_insts, after_body = _render_iteration_block(
            body_template,
            current_values,
            suffix=suffix,
            terminator_text=f"  br label %{latch_label}\n",
        )
        if iteration < trip_count - 1:
            latch_term_text = f"  br label %{next_body_label}\n"
        elif mode == "header-exit":
            if (
                tail_cmp_name is not None
                and tail_cmp_pred is not None
                and tail_cmp_iv is not None
                and tail_cmp_limit is not None
                and tail_cmp_iv in phi_info
            ):
                latch_term_text = (
                    f"  br i1 %{tail_cmp_name}.{trip_count}, "
                    f"label %{next_body_label}, label %{exit_name}\n"
                )
            else:
                if header_exit_on_true:
                    latch_term_text = f"  br i1 true, label %{exit_name}, label %{next_body_label}\n"
                else:
                    latch_term_text = f"  br i1 false, label %{next_body_label}, label %{exit_name}\n"
        else:
            exit_texts = [inst.text for inst in exit_mut.instructions]
            if len(exit_texts) != 1 or not exit_texts[0].lstrip().startswith("ret "):
                return ir_text, False
            live_out_values = dict(after_body)
            for name in phi_info:
                live_out_values[name] = current_values[name]
            latch_term_text = exit_texts[0]
            for old, new in live_out_values.items():
                latch_term_text = re.sub(
                    r"%" + re.escape(old) + r"(?![\w\.])",
                    new,
                    latch_term_text,
                )
            if not latch_term_text.endswith("\n"):
                latch_term_text += "\n"
        latch_insts, after_latch = _render_iteration_block(
            latch_template,
            after_body,
            suffix=suffix,
            terminator_text=latch_term_text,
        )
        if (
            iteration == trip_count - 1
            and mode == "header-exit"
            and tail_cmp_name is not None
            and tail_cmp_pred is not None
            and tail_cmp_iv is not None
            and tail_cmp_limit is not None
            and tail_cmp_iv in phi_info
        ):
            next_phi_values = {
                name: _substitute_token(info["back"], after_latch)
                for name, info in phi_info.items()
            }
            cmp_lhs = next_phi_values.get(tail_cmp_iv, f"%{tail_cmp_iv}")
            cmp_ty = phi_info[tail_cmp_iv]["ty"]
            latch_insts.insert(
                -1,
                Instruction.from_text(
                    f"  %{tail_cmp_name}.{trip_count} = icmp {tail_cmp_pred} "
                    f"{cmp_ty} {cmp_lhs}, {tail_cmp_limit}\n"
                ),
            )

        body_block = body_mut if iteration == 0 else BasicBlock(
            name=body_label,
            label_line=f"{body_label}:\n",
            instructions=[],
        )
        body_block.instructions = body_insts
        iteration_blocks.append(body_block)

        latch_block = latch_mut if iteration == 0 else BasicBlock(
            name=latch_label,
            label_line=f"{latch_label}:\n",
            instructions=[],
        )
        latch_block.instructions = latch_insts
        iteration_blocks.append(latch_block)
        last_latch_label = latch_label

        current_values = {
            name: _substitute_token(info["back"], after_latch)
            for name, info in phi_info.items()
        }
        final_values = dict(after_latch)
        for name, value in current_values.items():
            final_values[name] = value

    if mode == "header-exit":
        dead_body_label = f"{body_name}.{trip_count}"
        dead_latch_label = f"{latch_name}.{trip_count}"
        dead_body_insts, after_dead_body = _render_iteration_block(
            body_template,
            current_values,
            suffix=str(trip_count),
            terminator_text=f"  br label %{dead_latch_label}\n",
        )
        dead_latch_insts, _ = _render_iteration_block(
            latch_template,
            after_dead_body,
            suffix=str(trip_count),
            terminator_text="  unreachable\n",
        )
        iteration_blocks.append(
            BasicBlock(
                name=dead_body_label,
                label_line=f"{dead_body_label}:\n",
                instructions=dead_body_insts,
            )
        )
        iteration_blocks.append(
            BasicBlock(
                name=dead_latch_label,
                label_line=f"{dead_latch_label}:\n",
                instructions=dead_latch_insts,
            )
        )

    if mode == "header-exit":
        lcssa_rename: dict[str, str] = {}
        lcssa_order: list[str] = []
        for inst in exit_mut.instructions:
            if " = phi " in inst.text:
                continue
            for operand in re.findall(r"%([\w\.]+)", inst.text):
                if operand not in final_values or operand in lcssa_rename:
                    continue
                if operand not in value_types:
                    return ir_text, False
                lcssa_rename[operand] = _fresh_name(f"{operand}.lcssa", taken_names)
                lcssa_order.append(operand)

        new_exit_instructions: list[Instruction] = []
        for operand in lcssa_order:
            new_exit_instructions.append(
                Instruction.from_text(
                    f"  %{lcssa_rename[operand]} = phi {value_types[operand]} "
                    f"[ {final_values[operand]}, %{last_latch_label} ]\n"
                )
            )
        for inst in exit_mut.instructions:
            text = inst.text
            for old, new in lcssa_rename.items():
                text = re.sub(
                    r"%" + re.escape(old) + r"(?![\w\.])",
                    f"%{new}",
                    text,
                )
            new_exit_instructions.append(Instruction.from_text(text))
        exit_mut.instructions = new_exit_instructions

    new_blocks: list[BasicBlock] = []
    for block in fn_mut.blocks:
        if block.name == body_name:
            new_blocks.extend(iteration_blocks)
            continue
        if block.name == latch_name:
            continue
        if mode == "latch-exit" and block.name == exit_name:
            continue
        new_blocks.append(block)
    fn_mut.blocks = new_blocks

    new_text = mut.serialize()
    return new_text, new_text != ir_text


def _full_unroll_simple_self_loop(
    ir_text: str,
    fn_name: str,
    loop,
    cfg: CFG,
    trip_count: int,
) -> tuple[str, bool]:
    if len(loop.blocks) != 1 or loop.header not in loop.blocks:
        return ir_text, False
    exit_blocks = loop.exit_blocks(cfg)
    if len(exit_blocks) != 1:
        return ir_text, False
    exit_block_name = exit_blocks[0]
    outside_preds = [
        pred_name
        for pred_name in cfg.predecessors.get(loop.header, ())
        if pred_name not in loop.blocks
    ]
    if len(outside_preds) != 1:
        return ir_text, False
    preheader = outside_preds[0]

    mut = MutableModule.parse(ir_text)
    fn = mut.function(fn_name)
    if fn is None:
        return ir_text, False
    loop_block = fn.block(loop.header)
    exit_block = fn.block(exit_block_name)
    if loop_block is None or exit_block is None:
        return ir_text, False
    if any(" = phi " in inst.text for inst in exit_block.instructions):
        return ir_text, False

    phi_info: dict[str, dict[str, str]] = {}
    body_insts: list[Instruction] = []
    term = loop_block.terminator
    if term is None:
        return ir_text, False

    before_term = loop_block.instructions[:-1]
    in_phi_prefix = True
    for inst in before_term:
        text = inst.text.rstrip("\n")
        m = _PHI_RE.match(text)
        if in_phi_prefix and m is not None:
            incoming: dict[str, str] = {}
            for g in _INCOMING_RE.finditer(m.group("rest")):
                incoming[g.group("block")] = g.group("val").strip()
            if preheader not in incoming or loop.header not in incoming:
                return ir_text, False
            phi_info[m.group("name")] = {
                "ty": m.group("ty"),
                "entry": incoming[preheader],
                "back": incoming[loop.header],
            }
            continue
        in_phi_prefix = False
        body_insts.append(inst)

    if not phi_info or not body_insts:
        return ir_text, False

    current_values = {name: info["entry"] for name, info in phi_info.items()}
    final_values: dict[str, str] = {}
    cloned: list[Instruction] = []

    for iteration in range(trip_count):
        iter_map = dict(current_values)
        for inst in body_insts:
            text = inst.text
            for old, new in iter_map.items():
                text = re.sub(
                    r"%" + re.escape(old) + r"(?![\w\.])",
                    new,
                    text,
                )
            if inst.result_name is not None:
                new_name = f"{inst.result_name}.unr{iteration}"
                text = re.sub(
                    r"^(\s*)%" + re.escape(inst.result_name) + r"(\s*=)",
                    r"\1%" + new_name + r"\2",
                    text,
                )
                iter_map[inst.result_name] = f"%{new_name}"
                final_values[inst.result_name] = f"%{new_name}"
            cloned.append(Instruction.from_text(text))
        current_values = {
            name: _substitute_token(info["back"], iter_map)
            for name, info in phi_info.items()
        }
        for name, value in current_values.items():
            final_values[name] = value

    exit_rewritten: list[Instruction] = []
    for inst in exit_block.instructions:
        inst.text = _rename_loop_values(inst.text, final_values)
        reparsed = Instruction.from_text(inst.text)
        inst.result_name = reparsed.result_name
        inst.opcode = reparsed.opcode
        exit_rewritten.append(Instruction.from_text(inst.text))

    if len(exit_rewritten) == 1 and exit_rewritten[0].opcode == "ret":
        loop_block.instructions = cloned + [Instruction.from_text(exit_rewritten[0].text)]
        fn.blocks = [block for block in fn.blocks if block.name != exit_block_name]
        loop_block.label_line = _drop_self_predecessor(loop_block.label_line, loop.header)
    else:
        loop_block.instructions = cloned + [Instruction.from_text(f"  br label %{exit_block_name}\n")]
        exit_block.instructions = exit_rewritten

    new_text = mut.serialize()
    return new_text, new_text != ir_text


def _linearize(
    ir_text: str, fn_name: str, loop, cfg: CFG
) -> tuple[str, bool]:
    """Convert the loop into straight-line code by removing the latch back-edge."""
    exit_blocks = loop.exit_blocks(cfg)
    if len(exit_blocks) != 1:
        return ir_text, False
    exit_block = exit_blocks[0]
    if len(loop.latches) != 1:
        return ir_text, False
    latch = loop.latches[0]

    lines = ir_text.splitlines(keepends=True)
    # Rewrite: in the latch, change `br label %header` (or cond) into
    # `br label %exit`.
    fn_define_re = re.compile(
        rf"^\s*define\s+[^@]*@{re.escape(fn_name)}\b"
    )
    label_re = re.compile(r"^\s*([\w\.]+):\s*(?:;.*)?$")
    fn_start = fn_end = -1
    for i, line in enumerate(lines):
        if fn_define_re.match(line):
            fn_start = i
        elif fn_start >= 0 and line.strip() == "}":
            fn_end = i
            break
    if fn_start < 0:
        return ir_text, False

    current_block: str | None = None
    for i in range(fn_start, fn_end + 1):
        lm = label_re.match(lines[i].rstrip("\n"))
        if lm:
            current_block = lm.group(1)
            continue
        if current_block != latch:
            continue
        if re.match(r"^\s*br\b", lines[i]):
            # Replace any `label %header` with `label %exit`.
            lines[i] = re.sub(
                r"label\s+%" + re.escape(loop.header) + r"\b",
                f"label %{exit_block}",
                lines[i],
            )

    # Now the header's phis still reference the latch as an incoming
    # edge that doesn't exist anymore; strip those incomings.
    new_text = "".join(lines)
    new_text = _strip_phi_incoming(new_text, fn_name, loop.header, latch)
    return new_text, new_text != ir_text


def _strip_phi_incoming(
    ir_text: str, fn_name: str, block_name: str, pred_name: str
) -> str:
    """Same helper as in loop_deletion: drop `[_, %pred]` from phis."""
    lines = ir_text.splitlines(keepends=True)
    current_fn: str | None = None
    current_block: str | None = None
    out: list[str] = []
    define_re = re.compile(r"^\s*define\s+[^@]*@([\w\.]+)")
    label_re = re.compile(r"^\s*([\w\.]+):\s*(?:;.*)?$")

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
            new_line = re.sub(r",[ \t]*\n", "\n", new_line)
            out.append(new_line)
            continue

        out.append(line)

    return "".join(out)


def _substitute_token(token: str, mapping: dict[str, str]) -> str:
    if token.startswith("%"):
        name = token[1:]
        return mapping.get(name, token)
    return token


def _try_fold_const_binop(text: str) -> str | None:
    match = _BINOP_RE.match(text.strip())
    if match is None:
        return None
    try:
        lhs = int(match.group("lhs"))
        rhs = int(match.group("rhs"))
    except ValueError:
        return None
    ty = match.group("ty")
    try:
        width = int(ty[1:])
    except ValueError:
        return None
    status, value = fold_llvm_integer_binary(
        match.group("op"),
        width,
        lhs,
        rhs,
        match.group("flags"),
    )
    if status == FOLD_POISON:
        return "poison"
    if status == FOLD_CONSTANT:
        return str(signed_value(value, width))
    return None


def _rename_loop_values(text: str, mapping: dict[str, str]) -> str:
    out = text
    for old, new in mapping.items():
        out = re.sub(
            r"%" + re.escape(old) + r"(?![\w\.])",
            new,
            out,
        )
    return out


def _value_types_for_function(fn: llvm.ValueRef) -> dict[str, str]:
    out: dict[str, str] = {}
    for arg in fn.arguments:
        if arg.name:
            out[arg.name] = str(arg.type)
    for block in fn.blocks:
        for inst in block.instructions:
            text = str(inst).strip()
            m = _ASSIGN_RE.match(text)
            if m:
                out[m.group("name")] = str(inst.type)
    return out


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


def _drop_self_predecessor(label_line: str, block_name: str) -> str:
    if "; preds =" not in label_line:
        return label_line
    prefix, suffix = label_line.split("; preds =", 1)
    preds = [pred.strip() for pred in suffix.strip().split(",")]
    kept = [pred for pred in preds if pred != f"%{block_name}"]
    if not kept:
        return prefix.rstrip() + "\n"
    return prefix + "; preds = " + ", ".join(kept) + "\n"
