"""MemCpyOpt — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/MemCpyOptimizer.cpp``
  performs chained memory-operation fusion: coalescing consecutive
  stores into ``memset``/``memcpy`` calls, forwarding ``memcpy``
  sources through other ``memcpy`` calls, and eliminating redundant
  copies.

Subset implemented here (labelled ``subset``):

- Delete no-op ``llvm.memcpy`` calls whose source and destination SSA
  values are the same pointer, including trivial same-address aliases
  formed by ``bitcast`` or zero-offset ``getelementptr`` chains.
- Delete zero-length ``llvm.memcpy``/``llvm.memmove`` calls.
- Other upstream fusions (store coalescing, memcpy forwarding, call
  inlining) are deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import Function, Instruction, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_MEM_CALL_RE = re.compile(
    r"""
    ^\s*
    (?:(?:tail|musttail|notail)\s+)?
    call\s+void\s+
    @llvm\.(?P<op>memcpy|memmove|memset)\.[^(\s]+\(
    (?P<args>.*)
    \)\s*$
    """,
    re.VERBOSE,
)
_BITCAST_PTR_RE = re.compile(
    r"""
    ^\s*
    %(?P<name>[\w\.]+)\s*=\s*
    bitcast\s+ptr\s+(?P<base>(?:%[\w\.]+|@[\w\.\$]+|null))\s+to\s+ptr
    \s*$
    """,
    re.VERBOSE,
)
_ZERO_GEP_RE = re.compile(
    r"""
    ^\s*
    %(?P<name>[\w\.]+)\s*=\s*
    getelementptr(?:\s+inbounds)?\s+
    .+?,\s*ptr\s+(?P<base>(?:%[\w\.]+|@[\w\.\$]+|null))
    (?P<indices>(?:\s*,\s*i\d+\s+[^,\s]+)+)
    \s*$
    """,
    re.VERBOSE,
)


class MemCpyOptIRPass(ModulePass):
    """Drop upstream-aligned no-op memcpy and zero-length memcpy/memmove."""

    name = "pcc-memcpyopt"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = memcpyopt_text(str(module))
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def memcpyopt_text(ir_text: str) -> tuple[str, bool]:
    mut = MutableModule.parse(ir_text)
    any_changed = False
    for fn in mut.functions:
        if _drop_noop_mem_calls(fn):
            any_changed = True
    if not any_changed:
        return ir_text, False
    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _drop_noop_mem_calls(fn: Function) -> bool:
    changed = False
    ptr_equiv = _pointer_equivalence_map(fn)
    for block in fn.blocks:
        kept: list[Instruction] = []
        for inst in block.instructions:
            m = _MEM_CALL_RE.match(inst.text.rstrip("\n"))
            if m is None:
                kept.append(inst)
                continue
            args = _split_args(m.group("args"))
            op = m.group("op")
            if _is_noop_mem_call(op, args, ptr_equiv):
                changed = True
                continue
            kept.append(inst)
        block.instructions = kept
    return changed


def _pointer_equivalence_map(fn: Function) -> dict[str, str]:
    out: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for block in fn.blocks:
            for inst in block.instructions:
                text = inst.text.rstrip("\n")
                m = _BITCAST_PTR_RE.match(text)
                if m is not None:
                    base = _canonical_pointer(m.group("base"), out)
                    key = f"%{m.group('name')}"
                    if out.get(key) != base:
                        out[key] = base
                        changed = True
                    continue
                m = _ZERO_GEP_RE.match(text)
                if m is None:
                    continue
                if not _all_indices_zero(m.group("indices")):
                    continue
                base = _canonical_pointer(m.group("base"), out)
                key = f"%{m.group('name')}"
                if out.get(key) != base:
                    out[key] = base
                    changed = True
    return out


def _is_noop_mem_call(op: str, args: list[str], ptr_equiv: dict[str, str]) -> bool:
    if op in {"memcpy", "memmove"}:
        if len(args) < 3:
            return False
        dst_val = _canonical_pointer(_arg_value(args[0]), ptr_equiv)
        src_val = _canonical_pointer(_arg_value(args[1]), ptr_equiv)
        len_val = _arg_value(args[2])
        if _is_zero(len_val):
            return True
        if (
            op == "memcpy"
            and dst_val == src_val
        ):
            return True
        return False
    return False


def _canonical_pointer(token: str, ptr_equiv: dict[str, str]) -> str:
    current = token
    seen: set[str] = set()
    while current.startswith("%") and current in ptr_equiv and current not in seen:
        seen.add(current)
        current = ptr_equiv[current]
    return current


def _all_indices_zero(indices_text: str) -> bool:
    for part in indices_text.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        if not pieces:
            continue
        if pieces[-1] != "0":
            return False
    return True


def _split_args(text: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def _arg_value(arg: str) -> str:
    parts = arg.split()
    return parts[-1] if parts else arg.strip()


def _is_zero(token: str) -> bool:
    stripped = token.strip()
    if stripped == "0":
        return True
    if stripped.startswith("%") or stripped.startswith("@"):
        return False
    try:
        return int(stripped, 0) == 0
    except ValueError:
        return False
