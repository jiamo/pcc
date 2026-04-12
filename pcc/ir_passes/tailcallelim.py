"""Tail Call Elimination — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/TailRecursionElimination.cpp``
  rewrites self-recursive tail calls into a loop with ``*.tr`` phi
  nodes in a new ``tailrecurse`` header block.

Subset implemented here (labelled ``subset``):

- Only direct self-recursion.
- Function shape must be exactly three blocks:
  - entry: computes a condition and branches to base / rec
  - base: returns a value (constant or argument-derived SSA)
  - rec: computes next arguments, calls self, immediately returns the
    call result.
- Supported argument types in this subset: integers and ``ptr``.
- Only arguments whose recursive-edge actual differs from the original
  function argument get a ``*.tr`` phi. Unchanged arguments stay on the
  original SSA value, matching the common upstream shape for threaded
  loop-carried state.

Transform:

1. Replace entry with ``br label %tailrecurse``.
2. Insert ``tailrecurse`` containing ``*.tr`` phis for each argument and
   move the original entry logic there.
3. Rewrite argument uses in ``base``/``rec`` to use the ``*.tr`` values.
4. Replace the recursive tail call + ``ret`` with ``br label %tailrecurse``.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import BasicBlock, Function, Instruction, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_ARG_RE = re.compile(r"(?P<ty>\S+)\s+%(?P<name>[\w\.]+)")
_COND_BR_RE = re.compile(
    r"^\s*br\s+i1\s+[^,]+,\s*label\s+%(?P<t>[\w\.\$]+)\s*,\s*label\s+%(?P<f>[\w\.\$]+)\s*$"
)
_CALL_ASSIGN_RE = re.compile(
    r"""
    ^\s*
    %(?P<res>[\w\.]+)\s*=\s*
    (?:(?P<mods>tail|musttail|notail)\s+)?
    call\s+(?P<ret>.+?)\s+@(?P<callee>[\w\.\$]+)\((?P<args>.*)\)
    \s*$
    """,
    re.VERBOSE,
)
_VOID_CALL_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<mods>tail|musttail|notail)\s+)?
    call\s+void\s+@(?P<callee>[\w\.\$]+)\((?P<args>.*)\)
    \s*$
    """,
    re.VERBOSE,
)
_RET_VAL_RE = re.compile(r"^\s*ret\s+(?P<ty>.+?)\s+%(?P<val>[\w\.]+)\s*$")
_RET_VOID_RE = re.compile(r"^\s*ret\s+void\s*$")


class TailCallElimPass(ModulePass):
    name = "pcc-tailcallelim"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = tailcallelim_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def tailcallelim_text(ir_text: str) -> tuple[str, bool]:
    mut = MutableModule.parse(ir_text)
    for fn in mut.functions:
        candidate = _match_tailrec_candidate(fn)
        if candidate is None:
            continue
        if _apply_tailrec_elim(mut, fn, candidate):
            text = mut.serialize()
            llvm.parse_assembly(text).verify()
            return text, True
    return ir_text, False


def _match_tailrec_candidate(fn: Function) -> dict | None:
    if not fn.args and len(fn.blocks) == 1:
        candidate = _match_zero_arg_self_loop_candidate(fn, fn.blocks[0])
        if candidate is not None:
            return candidate
    if len(fn.blocks) != 3:
        return None
    args = []
    for arg in fn.args:
        if not _supported_tailrec_arg_type(arg.ty):
            return None
        args.append((arg.ty, arg.name))

    entry = fn.blocks[0]
    if any(" = phi " in inst.text for inst in entry.instructions):
        return None
    if any(any(" = phi " in inst.text for inst in block.instructions) for block in fn.blocks[1:]):
        return None

    entry_term = entry.terminator
    if entry_term is None:
        return None
    m = _COND_BR_RE.match(entry_term.text.strip())
    if m is None:
        return None
    then_block = fn.block(m.group("t"))
    else_block = fn.block(m.group("f"))
    if then_block is None or else_block is None:
        return None
    for base, rec in ((then_block, else_block), (else_block, then_block)):
        candidate = _match_base_rec_pair(fn, entry, base, rec, args)
        if candidate is not None:
            return candidate
    return None


def _match_base_rec_pair(
    fn: Function,
    entry: BasicBlock,
    base: BasicBlock,
    rec: BasicBlock,
    args: list[tuple[str, str]],
) -> dict | None:
    if len(rec.instructions) < 2:
        return None

    call = rec.instructions[-2]
    ret = rec.instructions[-1]
    call_m = _CALL_ASSIGN_RE.match(call.text.strip())
    void_call_m = _VOID_CALL_RE.match(call.text.strip())
    ret_m = _RET_VAL_RE.match(ret.text.strip())
    is_void_tailrec = False
    if call_m is not None and ret_m is not None:
        if call_m.group("callee") != fn.name:
            return None
        if ret_m.group("val") != call_m.group("res"):
            return None
        args_text = call_m.group("args")
    elif void_call_m is not None and _RET_VOID_RE.match(ret.text.strip()) is not None:
        if void_call_m.group("callee") != fn.name:
            return None
        args_text = void_call_m.group("args")
        is_void_tailrec = True
    else:
        return None

    actuals = [_arg_value_token(arg) for arg in _split_args(args_text)]
    if len(actuals) != len(args):
        return None

    return {
        "args": args,
        "kind": "tri-block",
        "entry": entry.name,
        "base": base.name,
        "rec": rec.name,
        "entry_insts": [inst.text for inst in entry.instructions[:-1]],
        "entry_term": entry.instructions[-1].text,
        "rec_prefix": [inst.text for inst in rec.instructions[:-2]],
        "actuals": actuals,
        "is_void_tailrec": is_void_tailrec,
    }


def _match_zero_arg_self_loop_candidate(fn: Function, block: BasicBlock) -> dict | None:
    if len(block.instructions) != 2:
        return None
    call = block.instructions[0]
    ret = block.instructions[1]
    call_m = _CALL_ASSIGN_RE.match(call.text.strip())
    if call_m is None or call_m.group("callee") != fn.name:
        return None
    if call_m.group("args").strip():
        return None
    ret_m = _RET_VAL_RE.match(ret.text.strip())
    if ret_m is None or ret_m.group("val") != call_m.group("res"):
        return None
    return {
        "kind": "zero-arg-self-loop",
        "entry": block.name,
    }


def _apply_tailrec_elim(mut: MutableModule, fn: Function, cand: dict) -> bool:
    if cand["kind"] == "zero-arg-self-loop":
        entry = fn.block(cand["entry"])
        if entry is None:
            return False
        tail_name = _fresh_block_name(fn, "tailrecurse")
        entry.instructions = [Instruction.from_text(f"  br label %{tail_name}\n")]
        tail_block = BasicBlock(
            name=tail_name,
            label_line=f"{tail_name}:\n",
            instructions=[Instruction.from_text(f"  br label %{tail_name}\n")],
        )
        mut.insert_blocks_after(fn, cand["entry"], [tail_block])
        return True

    entry = fn.block(cand["entry"])
    rec = fn.block(cand["rec"])
    if entry is None or rec is None:
        return False

    tail_name = _fresh_block_name(fn, "tailrecurse")
    phi_args: list[tuple[str, str, str]] = []
    for (ty, name), actual in zip(cand["args"], cand["actuals"]):
        if actual == f"%{name}":
            continue
        phi_args.append((ty, name, actual))
    arg_map = {name: f"{name}.tr" for _, name, _ in phi_args}

    # Rewrite argument uses in all non-entry blocks.
    for block in fn.blocks:
        if block.name == cand["entry"]:
            continue
        _rewrite_arg_uses(block, arg_map)

    # Entry now just jumps to tailrecurse.
    entry.instructions = [Instruction.from_text(f"  br label %{tail_name}\n")]

    # Build the tailrecurse block: phis + moved entry logic.
    tail_insts: list[Instruction] = []
    for ty, name, actual in phi_args:
        actual = _rewrite_value_token(actual, arg_map)
        tail_insts.append(
            Instruction.from_text(
                f"  %{name}.tr = phi {ty} [ %{name}, %{cand['entry']} ], "
                f"[ {actual}, %{cand['rec']} ]\n"
            )
        )
    for text in cand["entry_insts"]:
        tail_insts.append(Instruction.from_text(_rewrite_text_args(text, arg_map)))
    tail_insts.append(Instruction.from_text(_rewrite_text_args(cand["entry_term"], arg_map)))
    tail_block = BasicBlock(name=tail_name, label_line=f"{tail_name}:\n", instructions=tail_insts)
    mut.insert_blocks_after(fn, cand["entry"], [tail_block])

    # Recursive block keeps its setup instructions, drops call/ret, and jumps back.
    rec_prefix = [Instruction.from_text(text) for text in cand["rec_prefix"]]
    rec.instructions = rec_prefix + [Instruction.from_text(f"  br label %{tail_name}\n")]
    _rewrite_arg_uses(rec, arg_map)
    return True


def _rewrite_arg_uses(block: BasicBlock, arg_map: dict[str, str]) -> None:
    new_insts: list[Instruction] = []
    for inst in block.instructions:
        new_insts.append(Instruction.from_text(_rewrite_text_args(inst.text, arg_map)))
    block.instructions = new_insts


def _rewrite_text_args(text: str, arg_map: dict[str, str]) -> str:
    out = text
    for old, new in arg_map.items():
        out = re.sub(r"%" + re.escape(old) + r"\b", f"%{new}", out)
    return out


def _rewrite_value_token(tok: str, arg_map: dict[str, str]) -> str:
    if tok.startswith("%"):
        name = tok[1:]
        if name in arg_map:
            return f"%{arg_map[name]}"
    return tok


def _split_args(args_text: str) -> list[str]:
    if not args_text.strip():
        return []
    return [part.strip() for part in args_text.split(",")]


def _arg_value_token(arg: str) -> str:
    parts = arg.split()
    return parts[-1] if parts else arg


def _supported_tailrec_arg_type(ty: str) -> bool:
    return ty.startswith("i") or ty == "ptr"


def _fresh_block_name(fn: Function, base: str) -> str:
    names = {block.name for block in fn.blocks}
    if base not in names:
        return base
    idx = 1
    while f"{base}.{idx}" in names:
        idx += 1
    return f"{base}.{idx}"
