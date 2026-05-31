"""Aggressive Dead Code Elimination (ADCE), subset.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/ADCE.cpp``
  implements :cpp:class:`llvm::ADCEPass`. ADCE differs from DCE in
  that it uses post-dominance information to treat branches as dead
  when the branch has no live effect inside its region. Upstream's
  algorithm:

  1. Mark every instruction with externally observable side-effects as
     live (stores, calls, ret).
  2. Walk the reverse CFG: every operand of a live instruction is live.
  3. Walk back to the controlling branch: if the branch's
     post-dominator region contains no live instructions, the branch
     itself is dead and can be replaced with an unconditional jump to
     the nearest live successor (not done here).
  4. Drop every instruction not marked live.

Staged subset here (labelled ``subset``):

- steps 1–2 implemented (reverse-flow liveness from sinks),
- a narrow branch rewrite handles dead conditional regions whose arms
  both resolve to the same successor, or where one arm is only a
  forwarder to the other arm. Phi-bearing direct targets remain
  protected unless their incoming edges can be repaired.

For the purely value-level dead-code pattern, this subset currently
matches DCE. The module exists separately so it can evolve
independently; ADCE has additional rules (e.g. "make dead debug
intrinsics follow the carrier value") that should not be bolted into
DCE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses
from .simplifycfg import _prune_invalid_phi_incomings
from .ssa_utils import build_def_use_index_per_function

_LIVE_OPCODES = {
    "ret",
    "store",
    "call",
    "invoke",
    "br",
    "switch",
    "indirectbr",
    "atomicrmw",
    "cmpxchg",
    "fence",
    "resume",
    "catchswitch",
    "catchret",
    "cleanupret",
    "unreachable",
}

_ATTR_GROUP_RE = re.compile(
    r"^\s*attributes\s+(?P<group>#\d+)\s*=\s*\{(?P<body>[^}]*)\}\s*$"
)
_FUNC_ATTR_RE = re.compile(
    r"""
    ^\s*
    (?:declare|define)\s+
    .*?@(?P<name>[\w\.\$]+)\([^)]*\)
    (?P<tail>.*?)
    (?:\s*\{)?\s*$
    """,
    re.VERBOSE,
)
_ASSIGN_RE = re.compile(r"^\s*%([\w\.]+)\s*=\s*(\w+)")
_CALL_LINE_RE = re.compile(
    r"""
    ^\s*
    (?:%(?P<result>[\w\.]+)\s*=\s*)?
    (?:(?:tail|musttail|notail)\s+)?
    call\b.*?@(?P<callee>[\w\.\$]+)\(
    """,
    re.VERBOSE,
)
_COND_BR_LINE_RE = re.compile(
    r"^\s*br\s+i1\s+(?P<cond>[^,]+)\s*,\s*label\s+%(?P<t>[\w\.]+)\s*,\s*label\s+%(?P<f>[\w\.]+)\s*$"
)
_BR_LINE_RE = re.compile(r"^\s*br\s+label\s+%(?P<label>[\w\.]+)\s*$")
_BLOCK_LABEL_RE = re.compile(r"^\s*(?P<label>[\w\.\-]+):")


@dataclass
class _Block:
    label: str
    lines: list[str]

    def inst_lines(self) -> list[str]:
        out: list[str] = []
        for line in self.lines:
            code = line.split(";", 1)[0].strip()
            if code:
                out.append(code)
        return out

    def replace_terminator(self, new_line: str) -> None:
        for idx in range(len(self.lines) - 1, -1, -1):
            code = self.lines[idx].split(";", 1)[0].strip()
            if not code:
                continue
            self.lines[idx] = new_line
            return


class ADCEPass(ModulePass):
    name = "pcc-adce"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = adce_module_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def _collect_function_attrs(ir_text: str) -> dict[str, set[str]]:
    attr_groups: dict[str, set[str]] = {}
    for line in ir_text.splitlines():
        match = _ATTR_GROUP_RE.match(line)
        if match is None:
            continue
        body = match.group("body").replace("{", " ").replace("}", " ")
        attr_groups[match.group("group")] = {
            tok for tok in body.split() if tok and tok != ","
        }

    attrs_by_func: dict[str, set[str]] = {}
    for line in ir_text.splitlines():
        match = _FUNC_ATTR_RE.match(line)
        if match is None:
            continue
        tail = match.group("tail")
        attrs: set[str] = set()
        for tok in tail.replace(",", " ").split():
            if not tok or tok == "{":
                continue
            if tok.startswith("#") and tok in attr_groups:
                attrs.update(attr_groups[tok])
                continue
            attrs.add(tok)
        attrs_by_func[match.group("name")] = attrs
    return attrs_by_func


_DEFINE_NAME_RE = re.compile(r"^\s*define\s+[^@]*@(?P<name>[\w\.\$]+)")


def _is_trivially_dead_call_line(
    line: str,
    attrs_by_func: dict[str, set[str]],
) -> bool:
    match = _CALL_LINE_RE.match(line)
    if match is None:
        return False
    attrs = attrs_by_func.get(match.group("callee"), set())
    return (
        (
            "readnone" in attrs
            or "readonly" in attrs
            or "memory(none)" in attrs
            or "memory(read)" in attrs
        )
        and "willreturn" in attrs
        and "nounwind" in attrs
    )


def adce_module_text(ir_text: str) -> tuple[str, bool]:
    current = ir_text
    any_changed = False
    for _ in range(4):
        current, removed_changed = _remove_dead_values(current)
        current, branch_changed = _rewrite_dead_conditional_branches(current)
        any_changed = any_changed or removed_changed or branch_changed
        if not removed_changed and not branch_changed:
            break
    if not any_changed:
        return ir_text, False
    llvm.parse_assembly(current).verify()
    return current, True


def _remove_dead_values(ir_text: str) -> tuple[str, bool]:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    per_fn = build_def_use_index_per_function(module)
    attrs_by_func = _collect_function_attrs(ir_text)
    dead_void_call_present = False

    # Per-function dead-name set.
    fn_to_remove: dict[str, set[str]] = {}
    for fn_name, index in per_fn.items():
        live: set[str] = set()
        worklist: list[str] = []
        for name, rec in index.records_by_name.items():
            if rec.opcode == "call" and _is_trivially_dead_call_line(
                rec.text, attrs_by_func
            ):
                continue
            if rec.opcode in _LIVE_OPCODES:
                for op in rec.operands:
                    if op not in live:
                        live.add(op)
                        worklist.append(op)
        while worklist:
            name = worklist.pop()
            rec = index.def_of(name)
            if rec is None:
                continue
            for op in rec.operands:
                if op not in live:
                    live.add(op)
                    worklist.append(op)
        remove: set[str] = set()
        for name, rec in index.records_by_name.items():
            if rec.opcode == "call" and _is_trivially_dead_call_line(
                rec.text, attrs_by_func
            ):
                if name not in live:
                    remove.add(name)
                continue
            if name.startswith("__inst_"):
                continue
            if rec.opcode in _LIVE_OPCODES:
                continue
            if name not in live:
                remove.add(name)
        fn_to_remove[fn_name] = remove

    current_fn: str | None = None
    for line in ir_text.splitlines():
        dm = _DEFINE_NAME_RE.match(line)
        if dm is not None:
            current_fn = dm.group("name")
            continue
        if line.strip() == "}":
            current_fn = None
            continue
        if current_fn is None:
            continue
        if _ASSIGN_RE.match(line) is None and _is_trivially_dead_call_line(
            line, attrs_by_func
        ):
            dead_void_call_present = True
            break

    if not any(fn_to_remove.values()) and not dead_void_call_present:
        return ir_text, False

    new_lines: list[str] = []
    assign_re = re.compile(r"^\s*%([\w\.]+)\s*=")
    define_re = re.compile(r"^\s*define\s+[^@]*@(?P<name>[\w\.]+)")
    current_fn: str | None = None
    for line in ir_text.splitlines(keepends=True):
        dm = define_re.match(line)
        if dm:
            current_fn = dm.group("name")
        if line.strip() == "}":
            new_lines.append(line)
            current_fn = None
            continue
        if current_fn is not None:
            m = assign_re.match(line)
            if m and m.group(1) in fn_to_remove.get(current_fn, set()):
                continue
            if m is None and _is_trivially_dead_call_line(line, attrs_by_func):
                continue
        new_lines.append(line)
    return "".join(new_lines), True


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


def _parse_blocks(fn_text: str) -> tuple[str, list[_Block], str]:
    lines = fn_text.splitlines(keepends=True)
    if len(lines) < 2:
        return fn_text, [], ""
    header = lines[0]
    footer = lines[-1]
    body = lines[1:-1]
    blocks: list[_Block] = []
    current: _Block | None = None
    for line in body:
        match = _BLOCK_LABEL_RE.match(line)
        if match is not None:
            if current is not None:
                blocks.append(current)
            current = _Block(label=match.group("label"), lines=[])
            continue
        if current is not None:
            current.lines.append(line)
    if current is not None:
        blocks.append(current)
    return header, blocks, footer


def _join_function(header: str, blocks: list[_Block], footer: str) -> str:
    out = [header]
    for block in blocks:
        out.append(f"{block.label}:\n")
        out.extend(block.lines)
    out.append(footer if footer.endswith("\n") else footer + "\n")
    return "".join(out)


def _resolve_forwarder(label: str, block_map: dict[str, _Block]) -> str:
    seen: set[str] = set()
    current = label
    while current not in seen:
        seen.add(current)
        block = block_map.get(current)
        if block is None:
            break
        insts = block.inst_lines()
        if len(insts) != 1:
            break
        br = _BR_LINE_RE.match(insts[0])
        if br is None:
            break
        current = br.group("label")
    return current


def _block_has_phi(block: _Block | None) -> bool:
    if block is None:
        return False
    return any(" = phi " in inst for inst in block.inst_lines())


def _rewrite_dead_conditional_branches(ir_text: str) -> tuple[str, bool]:
    out: list[str] = []
    changed = False
    for is_function, chunk in _split_functions(ir_text):
        if not is_function:
            out.append(chunk)
            continue
        header, blocks, footer = _parse_blocks(chunk)
        if not blocks:
            out.append(chunk)
            continue
        block_map = {block.label: block for block in blocks}
        local_changed = False
        for block in blocks:
            insts = block.inst_lines()
            if not insts:
                continue
            m = _COND_BR_LINE_RE.match(insts[-1])
            if m is None:
                continue
            true_block = block_map.get(m.group("t"))
            false_block = block_map.get(m.group("f"))
            if true_block is None or false_block is None:
                continue
            true_resolved = _resolve_forwarder(m.group("t"), block_map)
            false_resolved = _resolve_forwarder(m.group("f"), block_map)
            if true_resolved == m.group("f") and not _block_has_phi(false_block):
                block.replace_terminator(f"  br label %{m.group('f')}\n")
                local_changed = True
                continue
            if false_resolved == m.group("t") and not _block_has_phi(true_block):
                block.replace_terminator(f"  br label %{m.group('t')}\n")
                local_changed = True
                continue
            if true_resolved == false_resolved:
                if _block_has_phi(block_map.get(true_resolved)):
                    continue
                block.replace_terminator(f"  br label %{m.group('t')}\n")
                local_changed = True
        if local_changed:
            blocks, phi_changed = _prune_invalid_phi_incomings(blocks)
            local_changed = local_changed or phi_changed
        out.append(_join_function(header, blocks, footer))
        changed = changed or local_changed
    if not changed:
        return ir_text, False
    return "".join(out), True
