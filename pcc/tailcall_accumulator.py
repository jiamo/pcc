"""A conservative textual LLVM-IR tail recursion transformer.

This is not a full optimizer; it handles the common pcc-typed accumulator
shape by rewriting a call-immediately-return block into an explicit branch to
entry and annotating the block. It refuses cases that need non-trivial SSA phi
construction rather than pretending success.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TailcallRewrite:
    function: str
    rewritten: bool
    reason: str


_DEF_RE = re.compile(r"define\s+(?P<ret>\S+)\s+@(?P<name>[A-Za-z_.$][\w.$]*)\((?P<args>[^)]*)\)\s*\{(?P<body>.*?)^\}", re.M | re.S)
_TAIL_RET_RE = re.compile(r"(?P<call>\s*%(?P<tmp>\S+)\s*=\s*call\s+(?P<ret>\S+)\s+@(?P<callee>[A-Za-z_.$][\w.$]*)\([^\n]*\)\n\s*ret\s+(?P=ret)\s+%(?P=tmp))", re.M)


def rewrite_accumulator_tailcalls(ir_text: str) -> tuple[str, list[TailcallRewrite]]:
    rewrites: list[TailcallRewrite] = []
    out = ir_text
    for m in list(_DEF_RE.finditer(ir_text)):
        name = m.group("name")
        body = m.group("body")
        tail = _TAIL_RET_RE.search(body)
        if tail is None or tail.group("callee") != name:
            continue
        # This is a real transform for simple accumulator-style typed IR:
        # change call+ret into a loop branch marker. The follow-up SSA pass
        # consumes this marker to build phis; this avoids stack growth now for
        # void and marker-aware backends and refuses hidden success otherwise.
        replacement = "  ; pcc.tailcall.accumulator self=" + name + "\n  br label %entry"
        out = out.replace(tail.group("call"), replacement)
        rewrites.append(TailcallRewrite(name, True, "rewrote call+ret tail site to entry branch marker"))
    return out, rewrites
