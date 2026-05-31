"""Global Value Numbering (GVN), subset.

Upstream reference:

- ``/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/GVN.cpp``
  implements :cpp:class:`llvm::GVNPass`. The full pass combines
  dominator-tree traversal, expression value numbering, memory
  dependence, PRE, and load elimination.

Subset here:

- value-number pure binops across dominated blocks,
- value-number repeated pure ``icmp`` instructions across dominated
  blocks,
- canonicalize commutative binop operands in the value-number key,
- reuse dominated loads from the same pointer across blocks when every
  visible store is provably noalias to that pointer,
- scope replacements per function so identical SSA names in different
  functions do not collide.

MemorySSA/PRE-grade load elimination is still deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .alias_analysis import AliasAnalysis, AliasResult
from .dce import dce_module_text
from .dominator_tree import compute_dominator_tree
from .instsimplify import simplify_module_text
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_BINOP_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<result>[\w\.]+)\s*=\s*"
    r"(?P<op>add|sub|mul|and|or|xor|shl|lshr|ashr)"
    r"(?P<flags>(?:\s+(?:nsw|nuw|exact))*)\s+"
    r"(?P<ty>i\d+)\s+(?P<lhs>[^,]+?)\s*,\s*(?P<rhs>.+?)\s*$"
)
_ICMP_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<result>[\w\.]+)\s*=\s*icmp\s+"
    r"(?P<pred>eq|ne|ugt|uge|ult|ule|sgt|sge|slt|sle)\s+"
    r"(?P<ty>i\d+)\s+(?P<lhs>[^,]+?)\s*,\s*(?P<rhs>.+?)\s*$"
)
_SELECT_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<result>[\w\.]+)\s*=\s*select\s+"
    r"(?P<cond_ty>[^ ]+)\s+(?P<cond>[^,]+?)\s*,\s*"
    r"(?P<true_ty>[^ ]+)\s+(?P<true_val>[^,]+?)\s*,\s*"
    r"(?P<false_ty>[^ ]+)\s+(?P<false_val>.+?)\s*$"
)
_CAST_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<result>[\w\.]+)\s*=\s*"
    r"(?P<op>zext|sext|trunc|bitcast)\s+"
    r"(?P<src_ty>[^ ]+)\s+(?P<src>[^ ]+)\s+to\s+(?P<dst_ty>.+?)\s*$"
)
_GEP_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<result>[\w\.]+)\s*=\s*getelementptr"
    r"(?P<inbounds>\s+inbounds)?\s+"
    r"(?P<elt_ty>[^,]+),\s+ptr\s+[%@](?P<base>[\w\.]+)"
    r"(?P<idxs>(?:\s*,\s*[^,]+?\s+[^,]+)+)\s*$"
)
_LOAD_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<result>[\w\.]+)\s*=\s*load(?P<volatile>\s+volatile)?\s+"
    r"(?P<ty>[^,]+?)\s*,\s*ptr\s+[%@](?P<ptr>[\w\.]+)\b"
)
_STORE_RE = re.compile(
    r"^\s*store(?P<volatile>\s+volatile)?\b.*?,\s*ptr\s+[%@](?P<ptr>[\w\.]+)\b"
)
_UNKNOWN_MEMORY_CLOBBER_RE = re.compile(r"\b(call|invoke|atomicrmw|cmpxchg|fence)\b")
_BITCAST_ALIAS_RE = re.compile(
    r"^\s*%(?P<dst>[\w\.]+)\s*=\s*bitcast\s+ptr\s+(?P<srcsig>[%@])(?P<src>[\w\.]+)\s+to\s+ptr\b"
)
_ZERO_GEP_ALIAS_RE = re.compile(
    r"^\s*%(?P<dst>[\w\.]+)\s*=\s*getelementptr(?:\s+inbounds)?\s+[^,]+,\s+ptr\s+(?P<srcsig>[%@])(?P<src>[\w\.]+)"
    r"(?P<idxs>(?:\s*,\s*i\d+\s+0)+)\s*$"
)
_PHI_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<name>[\w\.]+)\s*=\s*phi\s+"
    r"(?P<ty>[^ ]+)\s+(?P<rest>\[.*)$"
)
_INCOMING_RE = re.compile(
    r"\[\s*(?P<val>[^,\]]+?)\s*,\s*%(?P<block>[\w\.]+)\s*\]"
)
_BLOCK_LABEL_RE = re.compile(r"^(?P<label>[\w\.]+):(?:\s*;.*)?$")

_COMMUTATIVE_BINOPS = {"add", "mul", "and", "or", "xor"}
_COMMUTATIVE_ICMPS = {"eq", "ne"}


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


def _canon_pair(lhs: str, rhs: str) -> tuple[str, str]:
    a, b = lhs.strip(), rhs.strip()
    if a <= b:
        return a, b
    return b, a


def _exact_alias_source(text: str) -> tuple[str, str] | None:
    match = _BITCAST_ALIAS_RE.match(text)
    if match is not None:
        return match.group("dst"), match.group("src")
    match = _ZERO_GEP_ALIAS_RE.match(text)
    if match is not None:
        return match.group("dst"), match.group("src")
    return None


def _exact_alias_replacement(text: str) -> tuple[str, str] | None:
    match = _BITCAST_ALIAS_RE.match(text)
    if match is not None:
        return match.group("dst"), f"{match.group('srcsig')}{match.group('src')}"
    match = _ZERO_GEP_ALIAS_RE.match(text)
    if match is not None:
        return match.group("dst"), f"{match.group('srcsig')}{match.group('src')}"
    return None


def _canonical_ptr_name(ptr: str, aliases: dict[str, str]) -> str:
    current = ptr
    seen: set[str] = set()
    while current in aliases and current not in seen:
        seen.add(current)
        nxt = aliases[current]
        if nxt == current:
            break
        current = nxt
    return current


def _resolve_replacement(
    name: str,
    replacements: dict[str, str],
) -> str:
    rep = replacements[name]
    seen = {name}
    while rep.startswith("%"):
        next_name = rep[1:]
        if next_name in seen or next_name not in replacements:
            break
        seen.add(next_name)
        rep = replacements[next_name]
    return rep


def _collapse_replacement_chains(
    replacements: dict[str, str],
) -> dict[str, str]:
    return {
        name: _resolve_replacement(name, replacements)
        for name in replacements
    }


def _member_dominates(
    candidate: tuple[str, int, str],
    target: tuple[str, int, str],
    doms: dict[str, list[str]],
) -> bool:
    cand_block, cand_idx, _ = candidate
    target_block, target_idx, _ = target
    if cand_block == target_block:
        return cand_idx < target_idx
    return cand_block in doms.get(target_block, [])


def _dominance_rank(
    candidate: tuple[str, int, str],
    target: tuple[str, int, str],
    doms: dict[str, list[str]],
) -> tuple[int, int, int]:
    cand_block, cand_idx, _ = candidate
    target_block, _, _ = target
    same_block = 1 if cand_block == target_block else 0
    depth = len(doms.get(cand_block, []))
    return (same_block, depth, cand_idx)


class GVNPass(ModulePass):
    name = "pcc-gvn"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        fn_doms: dict[str, dict[str, list[str]]] = {}
        for fn in module.functions:
            if fn.is_declaration:
                continue
            dom = compute_dominator_tree(fn)
            fn_doms[fn.name] = {
                block: dom.dominators(block) for block in dom.all_blocks()
            }
        new_text, changed = gvn_text(ir_text, fn_doms)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def _rewrite_function(
    fn_text: str,
    doms: dict[str, list[str]],
    aa: AliasAnalysis,
) -> tuple[str, bool]:
    lines = fn_text.splitlines(keepends=True)
    current_block = "entry"
    dead_lines: set[int] = set()
    replacements: dict[str, str] = {}
    groups: dict[tuple, list[tuple[str, int, str]]] = {}
    unknown_memory_clobber = False
    store_ptrs: set[str] = set()
    exact_aliases: dict[str, str] = {}

    for idx, line in enumerate(lines):
        stripped = line.strip()
        label_match = _BLOCK_LABEL_RE.match(stripped)
        if label_match is not None:
            current_block = label_match.group("label")
            continue
        alias = _exact_alias_source(stripped)
        if alias is not None:
            dst, src = alias
            exact_aliases[dst] = _canonical_ptr_name(src, exact_aliases)
        store = _STORE_RE.match(stripped)
        if store is not None:
            store_ptrs.add(_canonical_ptr_name(store.group("ptr"), exact_aliases))
            continue
        if _UNKNOWN_MEMORY_CLOBBER_RE.search(stripped):
            unknown_memory_clobber = True

        match = _BINOP_RE.match(line.rstrip("\n"))
        if match is not None:
            lhs, rhs = match.group("lhs").strip(), match.group("rhs").strip()
            if match.group("op") in _COMMUTATIVE_BINOPS:
                lhs, rhs = _canon_pair(lhs, rhs)
            key = ("binop", match.group("op"), match.group("ty"), lhs, rhs)
            groups.setdefault(key, []).append((current_block, idx, match.group("result")))
            continue

        match = _ICMP_RE.match(line.rstrip("\n"))
        if match is not None:
            lhs, rhs = match.group("lhs").strip(), match.group("rhs").strip()
            if match.group("pred") in _COMMUTATIVE_ICMPS:
                lhs, rhs = _canon_pair(lhs, rhs)
            key = ("icmp", match.group("pred"), match.group("ty"), lhs, rhs)
            groups.setdefault(key, []).append((current_block, idx, match.group("result")))
            continue

        match = _SELECT_RE.match(line.rstrip("\n"))
        if match is not None:
            key = (
                "select",
                match.group("cond_ty").strip(),
                match.group("cond").strip(),
                match.group("true_ty").strip(),
                match.group("true_val").strip(),
                match.group("false_ty").strip(),
                match.group("false_val").strip(),
            )
            groups.setdefault(key, []).append((current_block, idx, match.group("result")))
            continue

        match = _CAST_RE.match(line.rstrip("\n"))
        if match is not None:
            key = (
                "cast",
                match.group("op"),
                match.group("src_ty").strip(),
                match.group("src").strip(),
                match.group("dst_ty").strip(),
            )
            groups.setdefault(key, []).append((current_block, idx, match.group("result")))
            continue

        match = _GEP_RE.match(line.rstrip("\n"))
        if match is not None:
            key = (
                "gep",
                bool(match.group("inbounds")),
                match.group("elt_ty").strip(),
                _canonical_ptr_name(match.group("base"), exact_aliases),
                match.group("idxs").strip(),
            )
            groups.setdefault(key, []).append((current_block, idx, match.group("result")))
            continue

        match = _LOAD_RE.match(line.rstrip("\n"))
        if match is not None and not match.group("volatile"):
            key = (
                "load",
                match.group("ty").strip(),
                _canonical_ptr_name(match.group("ptr"), exact_aliases),
            )
            groups.setdefault(key, []).append((current_block, idx, match.group("result")))

    for key, members in groups.items():
        if len(members) < 2:
            continue
        if key[0] == "load":
            if unknown_memory_clobber:
                continue
            load_ptr = key[2]
            if any(
                aa.alias_names(load_ptr, store_ptr) != AliasResult.NoAlias
                for store_ptr in store_ptrs
            ):
                continue
        for target in members:
            candidates = [
                candidate
                for candidate in members
                if candidate != target and _member_dominates(candidate, target, doms)
            ]
            if not candidates:
                continue
            best = max(candidates, key=lambda candidate: _dominance_rank(candidate, target, doms))
            _, _, leader_name = best
            _, line_idx, name = target
            replacements[name] = f"%{leader_name}"
            dead_lines.add(line_idx)

    kept = [ln for i, ln in enumerate(lines) if i not in dead_lines]
    text = "".join(kept)
    replacements = _collapse_replacement_chains(replacements)
    for _ in range(8):
        nt = text
        for name, rep in replacements.items():
            nt = re.sub(r"%" + re.escape(name) + r"\b", rep, nt)
        if nt == text:
            break
        text = nt
    text, alias_changed = _strip_exact_aliases_in_function(text)
    text, phi_changed = _fold_redundant_phis_in_function(text)
    text, dead_load_changed = _drop_dead_nonvolatile_loads(text)
    overall_changed = bool(replacements or alias_changed or phi_changed or dead_load_changed)
    if not overall_changed:
        return fn_text, False
    return text, True


def _drop_dead_nonvolatile_loads(fn_text: str) -> tuple[str, bool]:
    lines = fn_text.splitlines(keepends=True)
    dead_lines: set[int] = set()
    for idx, line in enumerate(lines):
        match = _LOAD_RE.match(line.rstrip("\n"))
        if match is None or match.group("volatile"):
            continue
        name = match.group("result")
        used = False
        token = re.compile(r"%" + re.escape(name) + r"(?![\w\.])")
        for j, other in enumerate(lines):
            if j == idx:
                continue
            if token.search(other):
                used = True
                break
        if not used:
            dead_lines.add(idx)
    if not dead_lines:
        return fn_text, False
    return "".join(line for i, line in enumerate(lines) if i not in dead_lines), True


def _fold_redundant_phis_in_function(fn_text: str) -> tuple[str, bool]:
    lines = fn_text.splitlines(keepends=True)
    dead_lines: set[int] = set()
    replacements: dict[str, str] = {}

    for idx, line in enumerate(lines):
        match = _PHI_RE.match(line.rstrip("\n"))
        if match is None:
            continue
        incomings = [g.group("val").strip() for g in _INCOMING_RE.finditer(match.group("rest"))]
        if len(incomings) < 2:
            continue
        if any(val != incomings[0] for val in incomings[1:]):
            continue
        replacements[match.group("name")] = incomings[0]
        dead_lines.add(idx)

    if not replacements:
        return fn_text, False

    text = "".join(line for i, line in enumerate(lines) if i not in dead_lines)
    for _ in range(8):
        new_text = text
        for old, new in replacements.items():
            new_text = re.sub(
                r"%" + re.escape(old) + r"(?![\w\.])",
                new,
                new_text,
            )
        if new_text == text:
            break
        text = new_text
    return text, True


def _strip_exact_aliases_in_function(fn_text: str) -> tuple[str, bool]:
    lines = fn_text.splitlines(keepends=True)
    dead_lines: set[int] = set()
    replacements: dict[str, str] = {}

    for idx, line in enumerate(lines):
        alias = _exact_alias_replacement(line.strip())
        if alias is None:
            continue
        dst, src = alias
        replacements[dst] = src
        dead_lines.add(idx)

    if not replacements:
        return fn_text, False

    text = "".join(line for i, line in enumerate(lines) if i not in dead_lines)
    for _ in range(8):
        new_text = text
        for old, new in replacements.items():
            new_text = re.sub(
                r"%" + re.escape(old) + r"(?![\w\.])",
                new,
                new_text,
            )
        if new_text == text:
            break
        text = new_text
    return text, True


def gvn_text(
    ir_text: str,
    fn_doms: dict[str, dict[str, list[str]]],
    aa: AliasAnalysis | None = None,
    *,
    run_dce: bool = False,
) -> tuple[str, bool]:
    """GVN across basic blocks within each function, respecting dominance."""
    if aa is None:
        module = llvm.parse_assembly(ir_text)
        module.verify()
        aa = AliasAnalysis(module)
    out: list[str] = []
    changed = False
    current_fn = None
    for is_function, chunk in _split_functions(ir_text):
        if not is_function:
            out.append(chunk)
            continue
        match = re.search(r"define\s+[^@]*@([\w\.]+)", chunk)
        current_fn = match.group(1) if match else None
        doms = fn_doms.get(current_fn or "", {})
        rewritten, fn_changed = _rewrite_function(chunk, doms, aa)
        out.append(rewritten)
        changed = changed or fn_changed
    if not changed:
        return ir_text, False
    current = "".join(out)
    current, _ = simplify_module_text(current)
    if run_dce:
        current, _ = dce_module_text(current)
    return current, True
