"""Inter-Procedural SCCP — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/SCCP.cpp``
  implements :cpp:class:`llvm::IPSCCPPass`. It extends SCCP across
  function boundaries: if a function has internal linkage and every
  call site passes the same constant for an argument, the argument
  is substituted with the constant in the body and SCCP propagates.
  Return values that are always the same constant are also
  propagated back to every call site.

Subset implemented here (labelled ``subset``):

- For each function with internal linkage and at least one call
  site, if **every** call site passes the same integer constant for
  argument position K, substitute that constant for ``%arg_k`` in
  the body. This is the core IPSCCP win.

Return-value propagation (call sites consume the constant return
from a callee that always returns the same constant) is deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dce import dce_module_text
from .instsimplify import simplify_module_text
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class IPSCCPPass(ModulePass):
    name = "pcc-ipsccp"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = ipsccp_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


_DEFINE_INTERNAL_RE = re.compile(
    r"^(?P<prefix>define\s+internal\s+(?:\w+\s+)*[\w\*]+\s+@)"
    r"(?P<name>[\w\.]+)\s*\((?P<args>[^)]*)\)(?P<trailing>.*?)\s*\{\s*$"
)
_CALL_RE_TEMPLATE = r"(call\s+[^@]*@{name})\s*\((?P<args>[^)]*)\)"


def ipsccp_module(ir_text: str) -> tuple[str, bool]:
    module = llvm.parse_assembly(ir_text)
    module.verify()

    # Collect internal function signatures (names of their args).
    internal_fns: dict[str, list[str]] = {}  # fn → list of arg %names
    for fn in module.functions:
        if fn.is_declaration:
            continue
        header = _define_header(fn)
        if header is None or "internal" not in header:
            continue
        arg_names: list[str] = []
        for arg in fn.arguments:
            m = re.match(r"\S+\s+%([\w\.]+)", str(arg).strip())
            if not m:
                arg_names = []
                break
            arg_names.append(m.group(1))
        if arg_names:
            internal_fns[fn.name] = arg_names

    if not internal_fns:
        return ir_text, False

    # For each internal fn, gather actual-argument lists across all
    # call sites and detect positions where every caller passes the
    # same int constant.
    to_substitute: dict[str, dict[str, str]] = {}  # fn → {arg_name → const}
    for fn_name, arg_names in internal_fns.items():
        call_re = re.compile(
            r"call\s+[^@]*@" + re.escape(fn_name) + r"\s*\((?P<args>[^)]*)\)"
        )
        per_pos: dict[int, set[str]] = {}
        call_count = 0
        for m in call_re.finditer(ir_text):
            call_count += 1
            raw_args = [a.strip() for a in m.group("args").split(",") if a.strip()]
            for i, raw in enumerate(raw_args):
                parts = raw.split()
                if len(parts) >= 2:
                    val = parts[-1]
                else:
                    val = raw
                per_pos.setdefault(i, set()).add(val)
        if call_count == 0:
            continue
        fn_subs: dict[str, str] = {}
        for i, vals in per_pos.items():
            if len(vals) != 1:
                continue
            (only_val,) = vals
            if not _is_int_const(only_val):
                continue
            if i >= len(arg_names):
                continue
            fn_subs[arg_names[i]] = only_val
        if fn_subs:
            to_substitute[fn_name] = fn_subs

    changed = False
    current = ir_text
    if to_substitute:
        current = _rewrite_fn_bodies(current, to_substitute)
        changed = current != ir_text
        current, _ = simplify_module_text(current)
        current, _ = dce_module_text(current)

    ret_any = False
    for _ in range(8):
        current, ret_changed = _propagate_constant_returns(current)
        current, _ = simplify_module_text(current)
        current, _ = dce_module_text(current)
        ret_any = ret_any or ret_changed
        if not ret_changed:
            break
    return current, changed or ret_any


def _is_int_const(tok: str) -> bool:
    if tok in ("true", "false"):
        return True
    try:
        int(tok)
        return True
    except ValueError:
        return False


def _define_header(fn) -> str | None:
    # We only need the first line from fn's textual form.
    s = str(fn)
    first = s.splitlines()[0] if s.strip() else ""
    return first if first.startswith("define") else None


def _rewrite_fn_bodies(
    ir_text: str, plan: dict[str, dict[str, str]]
) -> str:
    lines = ir_text.splitlines(keepends=True)
    out: list[str] = []
    current_fn: str | None = None
    depth = 0
    define_re = re.compile(r"^\s*define\s+[^@]*@([\w\.]+)")

    for line in lines:
        m = define_re.match(line)
        if m:
            current_fn = m.group(1)
            depth = 1
            out.append(line)
            continue
        if current_fn and line.strip() == "}":
            current_fn = None
            out.append(line)
            continue
        if current_fn in plan:
            new_line = line
            for arg_name, const in plan[current_fn].items():
                new_line = re.sub(
                    r"%" + re.escape(arg_name) + r"(?![\w\.])",
                    const,
                    new_line,
                )
            out.append(new_line)
            continue
        out.append(line)
    return "".join(out)


_RET_CONST_RE = re.compile(r"^\s*ret\s+(?P<ty>i\d+|i1)\s+(?P<val>-?\d+|true|false)\s*$")
_CALL_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<res>[\w\.]+)\s*=\s*"
    r"(?:tail\s+|musttail\s+|notail\s+)?call\s+"
    r"(?P<ty>i\d+|i1)\s+@(?P<callee>[\w\.]+)\s*\((?P<args>[^)]*)\)\s*$"
)
_SIDE_EFFECTING_OPCODES = {
    "store", "invoke", "ret", "br", "switch", "indirectbr",
    "fence", "atomicrmw", "cmpxchg", "resume", "catchswitch",
    "catchret", "cleanupret", "unreachable",
}


def _propagate_constant_returns(ir_text: str) -> tuple[str, bool]:
    module = llvm.parse_assembly(ir_text)
    module.verify()

    const_returns = _collect_constant_returns(module)

    if not const_returns:
        return ir_text, False

    lines = ir_text.splitlines(keepends=True)
    subs: dict[str, str] = {}
    for idx, line in enumerate(lines):
        m = _CALL_ASSIGN_RE.match(line.rstrip("\n"))
        if not m or m.group("callee") not in const_returns:
            continue
        ty, val = const_returns[m.group("callee")]
        if ty != m.group("ty"):
            continue
        subs[m.group("res")] = val

    if not subs:
        return ir_text, False

    out: list[str] = []
    for line in lines:
        updated = line
        for name, val in subs.items():
            if re.match(rf"^\s*%{re.escape(name)}\s*=", line):
                continue
            updated = re.sub(r"%" + re.escape(name) + r"(?![\w\.])", val, updated)
        out.append(updated)
    return "".join(out), True


def _collect_constant_returns(module) -> dict[str, tuple[str, str]]:
    const_returns: dict[str, tuple[str, str]] = {}
    for _ in range(8):
        changed = False
        for fn in module.functions:
            if fn.is_declaration or fn.name in const_returns:
                continue
            header = _define_header(fn)
            if header is None or "internal" not in header:
                continue
            const_ret = _function_constant_return(fn, const_returns)
            if const_ret is None:
                continue
            const_returns[fn.name] = const_ret
            changed = True
        if not changed:
            break
    return const_returns


def _function_constant_return(
    fn,
    known_const_returns: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    blocks = list(fn.blocks)
    if len(blocks) != 1:
        return None
    insts = list(blocks[0].instructions)
    if not insts:
        return None
    term = insts[-1]
    m = _RET_CONST_RE.match(str(term).strip())
    if not m:
        return None
    ret_ty = m.group("ty")
    ret_val = m.group("val")
    for inst in insts[:-1]:
        opcode = inst.opcode or ""
        if opcode == "call":
            call_text = str(inst).strip()
            cm = re.search(r"\bcall\s+(?:[^@]*?)@(?P<callee>[\w\.]+)\(", call_text)
            if cm is None:
                return None
            callee = cm.group("callee")
            callee_const = known_const_returns.get(callee)
            if callee_const is None:
                return None
            if callee_const[0] not in call_text:
                # Be conservative if the textual return type shape drifts.
                return None
            continue
        if opcode in _SIDE_EFFECTING_OPCODES:
            return None
    return (ret_ty, ret_val)
