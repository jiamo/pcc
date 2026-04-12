"""Call-Site Splitting — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/CallSiteSplitting.cpp``
  duplicates a single call into predecessor-specific split blocks when
  the join block feeds the call through predecessor-known values.

Subset implemented here (labelled ``subset``):

- The candidate block must have only phi nodes, then one direct
  ``call`` instruction, then a ``ret``.
- Every predecessor must end in ``br label %join``.
- Call arguments may use phi values from the join block; each split
  block specializes those arguments using the predecessor's incoming.
- Covered return shapes:
  - ``%r = call ...`` followed by ``ret TY %r``
  - ``call void ...`` followed by ``ret void``

Transform:

1. For each predecessor ``%pred`` create ``%pred.split``.
2. Rewrite ``pred`` to branch to ``pred.split`` instead of ``join``.
3. Clone the call into each split block with predecessor-specialized
   arguments.
4. Replace the join block body with either:
   - ``ret void``, or
   - a phi over the split-call results followed by ``ret``.

This intentionally leaves more general call cloning, invoke support,
and blocks with extra side instructions to the fuller implementation.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import BasicBlock, Function, Instruction, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_UNCOND_BR_RE = re.compile(r"^\s*br\s+label\s+%(?P<target>[\w\.\$]+)\s*$")
_PHI_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*phi\s+(?P<ty>.+?)\s+(?P<incoming>.+?)\s*$"
)
_PHI_INCOMING_RE = re.compile(
    r"\[\s*(?P<value>[^,\]]+?)\s*,\s*%(?P<pred>[\w\.\$]+)\s*\]"
)
_DIRECT_CALL_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<res>%[\w\.]+)\s*=\s*)?
    (?:(?P<tail>tail|musttail|notail)\s+)?
    call
    \s+(?P<ret_ty>[^@]+?)\s+
    @(?P<callee>[\w\.\$]+)
    \((?P<args>.*)\)
    \s*$
    """,
    re.VERBOSE,
)
_RET_VAL_RE = re.compile(r"^\s*ret\s+(?P<ty>.+?)\s+(?P<val>.+?)\s*$")


class CallSiteSplittingPass(ModulePass):
    name = "pcc-callsite-splitting"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = callsite_splitting_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def callsite_splitting_text(ir_text: str) -> tuple[str, bool]:
    module = MutableModule.parse(ir_text)
    for fn in module.functions:
        if _split_one_function(module, fn):
            text = module.serialize()
            llvm.parse_assembly(text).verify()
            return text, True
    return ir_text, False


def _split_one_function(module: MutableModule, fn: Function) -> bool:
    block_names = {block.name for block in fn.blocks}
    defined_functions = {f.name for f in module.functions}
    for join in fn.blocks:
        candidate = _match_join_candidate(fn, join, defined_functions)
        if candidate is None:
            continue
        preds = _find_unconditional_predecessors(fn, join.name)
        if len(preds) < 2:
            continue
        if any(
            pred not in incoming
            for incoming in candidate["phi_incomings"].values()
            for pred in preds
        ):
            continue
        split_names = {
            pred: _fresh_split_name(pred, block_names)
            for pred in preds
        }
        if _apply_callsite_split(module, fn, join, preds, split_names, candidate):
            return True
    return False


def _match_join_candidate(
    fn: Function,
    join: BasicBlock,
    defined_functions: set[str],
) -> dict | None:
    if len(join.instructions) < 2:
        return None

    phi_instrs: list[Instruction] = []
    idx = 0
    while idx < len(join.instructions) and " = phi " in join.instructions[idx].text:
        phi_instrs.append(join.instructions[idx])
        idx += 1
    if idx >= len(join.instructions):
        return None
    if len(join.instructions) != idx + 2:
        return None

    call_inst = join.instructions[idx]
    ret_inst = join.instructions[idx + 1]
    call_match = _DIRECT_CALL_RE.match(call_inst.text.strip())
    if not call_match:
        return None
    if call_match.group("callee") not in defined_functions:
        return None

    phi_incomings: dict[str, dict[str, str]] = {}
    for inst in phi_instrs:
        m = _PHI_RE.match(inst.text.strip())
        if not m:
            return None
        incoming_map: dict[str, str] = {}
        for inc in _PHI_INCOMING_RE.finditer(m.group("incoming")):
            incoming_map[inc.group("pred")] = inc.group("value").strip()
        if not incoming_map:
            return None
        phi_incomings[m.group("name")] = incoming_map

    args = _split_args(call_match.group("args"))
    varying = False
    for arg in args:
        value = _arg_value_token(arg)
        if value.startswith("%") and value[1:] in phi_incomings:
            varying = True
    if not varying:
        return None

    res_name = call_match.group("res")
    ret_text = ret_inst.text.strip()
    if res_name is None:
        if ret_text != "ret void":
            return None
    else:
        ret_match = _RET_VAL_RE.match(ret_text)
        if not ret_match:
            return None
        if ret_match.group("val").strip() != res_name:
            return None
        if ret_match.group("ty").strip() != call_match.group("ret_ty").strip():
            return None

    return {
        "call": call_match.groupdict(),
        "phi_incomings": phi_incomings,
        "call_indent": _leading_ws(call_inst.text),
        "ret_indent": _leading_ws(ret_inst.text),
    }


def _find_unconditional_predecessors(fn: Function, target: str) -> list[str]:
    preds: list[str] = []
    for block in fn.blocks:
        term = block.terminator
        if term is None:
            continue
        m = _UNCOND_BR_RE.match(term.text.strip())
        if m and m.group("target") == target:
            preds.append(block.name)
    return preds


def _fresh_split_name(pred: str, existing: set[str]) -> str:
    base = f"{pred}.split"
    if base not in existing:
        existing.add(base)
        return base
    idx = 1
    while f"{base}.{idx}" in existing:
        idx += 1
    name = f"{base}.{idx}"
    existing.add(name)
    return name


def _apply_callsite_split(
    module: MutableModule,
    fn: Function,
    join: BasicBlock,
    preds: list[str],
    split_names: dict[str, str],
    candidate: dict,
) -> bool:
    call_info = candidate["call"]
    phi_incomings = candidate["phi_incomings"]
    call_indent = candidate["call_indent"] or "  "
    ret_indent = candidate["ret_indent"] or "  "

    split_blocks: list[BasicBlock] = []
    incoming_lines: list[str] = []
    ret_ty = call_info["ret_ty"].strip()
    is_void = call_info["res"] is None

    for pred in preds:
        pred_block = fn.block(pred)
        if pred_block is None:
            return False
        module.replace_branch_target(pred_block, join.name, split_names[pred])

        specialized_args = [
            _specialize_arg(arg, pred, phi_incomings)
            for arg in _split_args(call_info["args"])
        ]
        call_line = _build_call_line(
            indent=call_indent,
            result_name=None if is_void else f"%css.{pred}",
            ret_ty=ret_ty,
            callee=call_info["callee"],
            args=specialized_args,
            tail=call_info.get("tail"),
        )
        block_insts = [Instruction.from_text(call_line)]
        if not is_void:
            incoming_lines.append(
                f"[ %css.{pred}, %{split_names[pred]} ]"
            )
        block_insts.append(
            Instruction.from_text(f"{ret_indent}br label %{join.name}\n")
        )
        split_blocks.append(
            BasicBlock(
                name=split_names[pred],
                label_line=f"{split_names[pred]}:\n",
                instructions=block_insts,
            )
        )

    module.insert_blocks_before(fn, join.name, split_blocks)

    if is_void:
        join.instructions = [Instruction.from_text(f"{ret_indent}ret void\n")]
        return True

    join.instructions = [
        Instruction.from_text(
            f"{call_indent}%phi.call = phi {ret_ty} "
            + ", ".join(incoming_lines)
            + "\n"
        ),
        Instruction.from_text(f"{ret_indent}ret {ret_ty} %phi.call\n"),
    ]
    return True


def _split_args(args_text: str) -> list[str]:
    if not args_text.strip():
        return []
    return [part.strip() for part in args_text.split(",")]


def _arg_value_token(arg: str) -> str:
    parts = arg.split()
    return parts[-1] if parts else arg


def _specialize_arg(
    arg: str,
    pred: str,
    phi_incomings: dict[str, dict[str, str]],
) -> str:
    value = _arg_value_token(arg)
    if not value.startswith("%"):
        return arg
    phi_name = value[1:]
    incoming = phi_incomings.get(phi_name)
    if incoming is None or pred not in incoming:
        return arg
    replacement = incoming[pred]
    prefix = arg[: len(arg) - len(value)]
    return f"{prefix}{replacement}"


def _build_call_line(
    *,
    indent: str,
    result_name: str | None,
    ret_ty: str,
    callee: str,
    args: list[str],
    tail: str | None,
) -> str:
    prefix = indent
    if result_name is not None:
        prefix += f"{result_name} = "
    if tail:
        prefix += f"{tail} "
    prefix += f"call {ret_ty} @{callee}("
    return prefix + ", ".join(args) + ")\n"


def _leading_ws(text: str) -> str:
    return text[: len(text) - len(text.lstrip())]
