"""Function Attribute Inference — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/FunctionAttrs.cpp``
  implements :cpp:class:`llvm::PostOrderFunctionAttrsPass` and
  :cpp:class:`llvm::ReversePostOrderFunctionAttrsPass`. The pass
  walks the call graph bottom-up and, for each function, infers
  attributes like ``nounwind``, ``readnone``, ``readonly``,
  ``willreturn``, ``mustprogress``, ``norecurse``, and
  ``noreturn`` by inspecting the function body.

Subset implemented here:

- ``readnone``: the body reads no memory (no loads / volatile ops,
  no calls that aren't already readnone) and writes no memory
  (no stores, no atomic / volatile ops, no calls that aren't
  already readnone). Pure functions earn this.
- ``readonly``: the body does not write memory (no stores, no
  non-readonly calls, no atomics).
- ``nounwind``: the body contains no ``invoke`` / ``resume`` /
  ``cleanupret`` / ``catchret`` / ``catchswitch`` and no call sites
  with ``unwind`` targets.
- ``norecurse``: the function never calls itself directly.

The inferred attributes are added to the function's attribute list
textually — this is how upstream serializes them. Other attributes
(``willreturn``, ``mustprogress``, ``argmemonly``) are deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class FunctionAttrsPass(ModulePass):
    name = "pcc-function-attrs"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = infer_function_attrs(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


_DEFINE_RE = re.compile(
    r"""
    ^(?P<head>define\s+.*?@(?P<name>[\w\.]+)\s*\()
    (?P<args>[^)]*)
    (?P<close>\))
    (?P<trailing>.*?)\s*\{\s*$
    """,
    re.VERBOSE,
)
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
_CALLEE_RE = re.compile(r"\b(?:call|invoke)\b.*?@(?P<callee>[\w\.\$]+)\(")
_TERM_TARGET_RE = re.compile(r"label %([\w\.]+)")
_ARG_SPLIT_RE = re.compile(r",\s*(?![^()]*\))")
_LOAD_PTR_RE = re.compile(r"\bload\b.*?,\s*ptr\s+%(?P<ptr>[\w\.]+)\b")
_STORE_DEST_RE = re.compile(r",\s*ptr\s+%(?P<ptr>[\w\.]+)\b")
_STORE_VALUE_RE = re.compile(r"^\s*store\s+.+?\s+(?P<val>%[\w\.]+),")
_PTR_DERIVE_RE = re.compile(
    r"^\s*%(?P<dest>[\w\.]+)\s*=\s*(?:getelementptr|bitcast|addrspacecast)\b.*?\bptr\s+%(?P<src>[\w\.]+)\b"
)


@dataclass(frozen=True)
class _ArgInfo:
    name: str
    ty: str
    attrs: frozenset[str]


@dataclass(frozen=True)
class _LocalFacts:
    direct_callees: frozenset[str]
    has_unknown_call: bool
    reads_argmem: bool
    writes_argmem: bool
    reads_other_memory: bool
    writes_other_memory: bool
    may_unwind: bool
    has_sync: bool
    self_calls: bool
    has_backedge: bool
    args: tuple[_ArgInfo, ...]


def infer_function_attrs(ir_text: str) -> tuple[str, bool]:
    # 1. Parse once to collect body shapes per function.
    module = llvm.parse_assembly(ir_text)
    module.verify()

    existing_attrs = _collect_existing_attrs(ir_text)
    facts_by_fn: dict[str, _LocalFacts] = {}
    for fn in module.functions:
        if fn.is_declaration:
            continue
        facts_by_fn[fn.name] = _scan_local(fn)

    if not facts_by_fn:
        return ir_text, False

    per_fn: dict[str, set[str]] = {
        name: set(existing_attrs.get(name, set()))
        for name in facts_by_fn
    }
    for _ in range(12):
        changed = False
        next_map: dict[str, set[str]] = {}
        for name, facts in facts_by_fn.items():
            attrs = _infer_single(name, facts, per_fn, existing_attrs)
            next_map[name] = attrs
            if attrs != per_fn.get(name, set()):
                changed = True
        per_fn = next_map
        if not changed:
            break

    if not per_fn:
        return ir_text, False

    # 2. Normalize the IR text via llvmlite's canonical serializer so
    # our `define ...` regex doesn't need to worry about user-supplied
    # indentation or other layout quirks.
    ir_text = str(module)
    lines = ir_text.splitlines(keepends=True)
    changed = False
    out: list[str] = []

    for line in lines:
        m = _DEFINE_RE.match(line.rstrip("\n"))
        if not m:
            out.append(line)
            continue
        fn_name = m.group("name")
        new_attrs = per_fn.get(fn_name, set())
        fn_facts = facts_by_fn.get(fn_name)
        arg_info_by_name = (
            {arg.name: arg for arg in fn_facts.args}
            if fn_facts is not None
            else {}
        )
        rewritten_args = _rewrite_arg_list(m.group("args"), arg_info_by_name)
        if not new_attrs and rewritten_args == m.group("args"):
            out.append(line)
            continue
        trailing = m.group("trailing") or ""
        existing = set(_split_attrs(trailing))
        to_add = [a for a in sorted(new_attrs) if a not in existing]
        # Insert new attrs between `)` and `{` — LLVM accepts
        # fn-scope attributes in that position.
        new_trailing = (trailing + " " + " ".join(to_add)).strip()
        new_line = f"{m.group('head')}{rewritten_args}{m.group('close')}"
        if new_trailing:
            new_line += f" {new_trailing}"
        new_line += " {\n"
        out.append(new_line)
        changed = changed or new_line != line

    return "".join(out), changed


def _collect_existing_attrs(ir_text: str) -> dict[str, set[str]]:
    attr_groups: dict[str, set[str]] = {}
    for line in ir_text.splitlines():
        match = _ATTR_GROUP_RE.match(line)
        if match is None:
            continue
        body = match.group("body").replace("{", " ").replace("}", " ")
        attr_groups[match.group("group")] = set(_split_attrs(body))

    attrs_by_func: dict[str, set[str]] = {}
    for line in ir_text.splitlines():
        match = _FUNC_ATTR_RE.match(line)
        if match is None:
            continue
        tail = match.group("tail")
        attrs: set[str] = set()
        for tok in _split_attrs(tail):
            if not tok or tok == "{":
                continue
            if tok.startswith("#") and tok in attr_groups:
                attrs.update(attr_groups[tok])
                continue
            attrs.add(tok)
        attrs_by_func[match.group("name")] = attrs
    return attrs_by_func


def _scan_local(fn: llvm.ValueRef) -> _LocalFacts:
    ptr_args: set[str] = set()
    arg_types: dict[str, str] = {}
    for arg in fn.arguments:
        match = re.match(r"(?P<ty>\S+)\s+%(?P<name>[\w\.]+)", str(arg).strip())
        if match is None:
            continue
        name = match.group("name")
        ty = match.group("ty")
        arg_types[name] = ty
        if ty.startswith("ptr"):
            ptr_args.add(name)

    direct_callees: set[str] = set()
    has_unknown_call = False
    reads_argmem = False
    writes_argmem = False
    reads_other_memory = False
    writes_other_memory = False
    may_unwind = False
    has_sync = False
    self_calls = False
    arg_reads: set[str] = set()
    arg_writes: set[str] = set()
    arg_captured: set[str] = set()
    derived_from: dict[str, str] = {name: name for name in ptr_args}

    block_order: list[str] = []
    block_index: dict[str, int] = {}
    successors: list[tuple[str, str]] = []

    for block in fn.blocks:
        name = block.name
        block_index[name] = len(block_order)
        block_order.append(name)

    for block in fn.blocks:
        term = None
        for inst in block.instructions:
            term = inst
            opcode = inst.opcode or ""
            text = str(inst).strip()
            ptr_derive = _PTR_DERIVE_RE.match(text)
            if ptr_derive is not None:
                src_root = derived_from.get(ptr_derive.group("src"))
                if src_root in ptr_args:
                    derived_from[ptr_derive.group("dest")] = src_root

            if opcode == "load":
                load_match = _LOAD_PTR_RE.search(text)
                root = derived_from.get(load_match.group("ptr")) if load_match else None
                if root in ptr_args:
                    reads_argmem = True
                    arg_reads.add(root)
                else:
                    reads_other_memory = True
                if " volatile " in f" {text} ":
                    has_sync = True
            elif opcode == "store":
                dest_match = _STORE_DEST_RE.search(text)
                root = derived_from.get(dest_match.group("ptr")) if dest_match else None
                if root in ptr_args:
                    writes_argmem = True
                    arg_writes.add(root)
                else:
                    writes_other_memory = True
                value_match = _STORE_VALUE_RE.match(text)
                if value_match is not None:
                    value_root = derived_from.get(value_match.group("val")[1:])
                    if value_root in ptr_args:
                        arg_captured.add(value_root)
                if " volatile " in f" {text} ":
                    has_sync = True
            elif opcode in ("atomicrmw", "cmpxchg", "fence"):
                reads_other_memory = True
                writes_other_memory = True
                has_sync = True
            elif opcode in ("resume", "cleanupret", "catchret", "catchswitch"):
                may_unwind = True

            if opcode in ("call", "invoke"):
                if opcode == "invoke":
                    may_unwind = True
                match = _CALLEE_RE.search(text)
                if match is None:
                    has_unknown_call = True
                else:
                    callee = match.group("callee")
                    direct_callees.add(callee)
                    if callee == fn.name:
                        self_calls = True
                for use in re.finditer(r"%([\w\.]+)", text):
                    root = derived_from.get(use.group(1))
                    if root in ptr_args:
                        arg_captured.add(root)
            elif opcode == "ret":
                for use in re.finditer(r"%([\w\.]+)", text):
                    root = derived_from.get(use.group(1))
                    if root in ptr_args:
                        arg_captured.add(root)
        if term is not None:
            src = block.name
            for dst in _TERM_TARGET_RE.findall(str(term)):
                successors.append((src, dst))

    has_backedge = any(
        block_index.get(dst, 1 << 30) <= block_index.get(src, -1)
        for src, dst in successors
    )
    arg_infos: list[_ArgInfo] = []
    for name, ty in arg_types.items():
        attrs: set[str] = set()
        if name in ptr_args:
            if name not in arg_captured:
                attrs.add("nocapture")
            if name in arg_reads and name not in arg_writes:
                attrs.add("readonly")
            if name in arg_writes and name not in arg_reads:
                attrs.add("writeonly")
        arg_infos.append(_ArgInfo(name=name, ty=ty, attrs=frozenset(sorted(attrs))))

    return _LocalFacts(
        direct_callees=frozenset(direct_callees),
        has_unknown_call=has_unknown_call,
        reads_argmem=reads_argmem,
        writes_argmem=writes_argmem,
        reads_other_memory=reads_other_memory,
        writes_other_memory=writes_other_memory,
        may_unwind=may_unwind,
        has_sync=has_sync,
        self_calls=self_calls,
        has_backedge=has_backedge,
        args=tuple(arg_infos),
    )


def _memory_effects_from_attrs(attrs: set[str]) -> tuple[bool, bool, bool]:
    if "readnone" in attrs or "memory(none)" in attrs:
        return (False, False, True)
    if "memory(argmem: read)" in attrs:
        return (True, False, True)
    if "memory(argmem: write)" in attrs:
        return (False, True, True)
    if "readonly" in attrs or "memory(read)" in attrs:
        return (True, False, False)
    return (True, True, False)


def _infer_single(
    fn_name: str,
    facts: _LocalFacts,
    inferred: dict[str, set[str]],
    existing: dict[str, set[str]],
) -> set[str]:
    attrs: set[str] = set()
    reads_memory = facts.reads_argmem or facts.reads_other_memory
    writes_memory = facts.writes_argmem or facts.writes_other_memory
    arg_only_memory = (
        not facts.reads_other_memory
        and not facts.writes_other_memory
        and (facts.reads_argmem or facts.writes_argmem)
    )
    may_unwind = facts.may_unwind
    nofree = True
    nosync = not facts.has_sync
    norecurse = not facts.self_calls
    willreturn = not facts.self_calls and not facts.has_backedge
    mustprogress = not facts.self_calls and not facts.has_backedge

    if facts.has_unknown_call:
        reads_memory = True
        writes_memory = True
        arg_only_memory = False
        may_unwind = True
        nofree = False
        nosync = False
        willreturn = False
        mustprogress = False

    for callee in facts.direct_callees:
        if callee == fn_name:
            continue
        callee_attrs = inferred.get(callee, existing.get(callee, set()))
        if not callee_attrs:
            reads_memory = True
            writes_memory = True
            arg_only_memory = False
            may_unwind = True
            nofree = False
            nosync = False
            willreturn = False
            mustprogress = False
            continue
        callee_reads, callee_writes, callee_arg_only = _memory_effects_from_attrs(callee_attrs)
        reads_memory = reads_memory or callee_reads
        writes_memory = writes_memory or callee_writes
        if callee_reads or callee_writes:
            arg_only_memory = arg_only_memory and callee_arg_only
        if "nounwind" not in callee_attrs:
            may_unwind = True
        if "nofree" not in callee_attrs:
            nofree = False
        if "nosync" not in callee_attrs:
            nosync = False
        if "willreturn" not in callee_attrs:
            willreturn = False
        if "mustprogress" not in callee_attrs:
            mustprogress = False

    if not reads_memory and not writes_memory:
        attrs.add("memory(none)")
    elif arg_only_memory and reads_memory and not writes_memory:
        attrs.add("memory(argmem: read)")
    elif arg_only_memory and writes_memory and not reads_memory:
        attrs.add("memory(argmem: write)")
    elif not writes_memory:
        attrs.add("readonly")

    if not may_unwind:
        attrs.add("nounwind")
    if nofree:
        attrs.add("nofree")
    if nosync:
        attrs.add("nosync")
    if norecurse:
        attrs.add("norecurse")
    if willreturn:
        attrs.add("willreturn")
    if mustprogress:
        attrs.add("mustprogress")
    return attrs


def _split_attrs(text: str) -> list[str]:
    tokens: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")":
            depth = max(depth - 1, 0)
            buf.append(ch)
            continue
        if depth == 0 and (ch.isspace() or ch == ","):
            if buf:
                token = "".join(buf).strip()
                if token:
                    tokens.append(token)
                buf = []
            continue
        buf.append(ch)
    if buf:
        token = "".join(buf).strip()
        if token:
            tokens.append(token)
    return tokens


def _rewrite_arg_list(args_text: str, arg_info_by_name: dict[str, _ArgInfo]) -> str:
    if not args_text.strip():
        return args_text
    rewritten: list[str] = []
    for raw_arg in _ARG_SPLIT_RE.split(args_text):
        arg = raw_arg.strip()
        match = re.search(r"%(?P<name>[\w\.]+)\b", arg)
        if match is None:
            rewritten.append(arg)
            continue
        info = arg_info_by_name.get(match.group("name"))
        if info is None or not info.attrs:
            rewritten.append(arg)
            continue
        rewritten.append(f"{info.ty} {' '.join(sorted(info.attrs))} %{info.name}")
    return ", ".join(rewritten)
