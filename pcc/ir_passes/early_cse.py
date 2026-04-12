"""Early Common Subexpression Elimination (subset).

Upstream reference:

- ``/private/tmp/llvm-src/llvm-project-20.1.8.src/llvm/lib/Transforms/Scalar/EarlyCSE.cpp``
  implements :cpp:class:`llvm::EarlyCSEPass`. It walks the dominator
  tree with scoped value tables and eliminates redundant pure
  instructions and some redundant loads.

Subset here:

- within a single basic block, recognize identical pure binary
  expressions,
- canonicalize commutative binop operands for the local value table,
- CSE repeated non-volatile loads from the same pointer until a
  store/call/fence invalidates memory state,
- CSE repeated ``icmp`` expressions,
- run a local simplifier afterwards so rewrites like
  ``and i1 %a, %a`` collapse to ``%a``.

Cross-block CSE and MemorySSA-aware load reuse remain out of scope.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

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
_LOAD_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<result>[\w\.]+)\s*=\s*load(?P<volatile>\s+volatile)?\b.*?,\s*ptr\s+%(?P<ptr>[\w\.]+)\b"
)
_STORE_RE = re.compile(r"^\s*store\b")
_STORE_VALUE_RE = re.compile(
    r"^\s*store(?P<volatile>\s+volatile)?\b.*?\s+(?P<value>[^,]+),\s*ptr\s+%(?P<ptr>[\w\.]+)\b"
)
_BITCAST_ALIAS_RE = re.compile(
    r"^\s*%(?P<dst>[\w\.]+)\s*=\s*bitcast\s+ptr\s+[@%](?P<src>[\w\.]+)\s+to\s+ptr\b"
)

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


class EarlyCSEPass(ModulePass):
    name = "pcc-early-cse"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = early_cse_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def _rewrite_function(fn_text: str) -> tuple[str, bool]:
    lines = fn_text.splitlines(keepends=True)
    replacements: dict[str, str] = {}
    dead_lines: set[int] = set()
    known_exprs: dict[tuple, str] = {}
    known_loads: dict[tuple, str] = {}
    known_store_values: dict[str, str] = {}
    exact_aliases: dict[str, str] = {}
    changed = False

    def reset_block() -> None:
        known_exprs.clear()
        known_loads.clear()
        known_store_values.clear()
        exact_aliases.clear()

    def flush_memory() -> None:
        known_loads.clear()
        known_store_values.clear()

    def canonical_ptr(ptr: str) -> str:
        current = ptr
        seen: set[str] = set()
        while current in exact_aliases and current not in seen:
            seen.add(current)
            nxt = exact_aliases[current]
            if nxt == current:
                break
            current = nxt
        return current

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("define "):
            reset_block()
            continue
        if stripped == "}":
            reset_block()
            continue
        if re.match(r"^[\w\.]+:\s*$", stripped):
            reset_block()
            continue

        alias = _BITCAST_ALIAS_RE.match(stripped)
        if alias is not None:
            exact_aliases[alias.group("dst")] = canonical_ptr(alias.group("src"))
            continue

        match = _LOAD_RE.match(line.rstrip("\n"))
        if match is not None:
            if match.group("volatile"):
                flush_memory()
                continue
            ptr = canonical_ptr(match.group("ptr"))
            if ptr in known_store_values:
                replacements[match.group("result")] = known_store_values[ptr]
                dead_lines.add(idx)
                changed = True
                continue
            key = ("load", ptr)
            if key in known_loads:
                replacements[match.group("result")] = f"%{known_loads[key]}"
                dead_lines.add(idx)
                changed = True
            else:
                known_loads[key] = match.group("result")
            continue

        store = _STORE_VALUE_RE.match(line.rstrip("\n"))
        if store is not None:
            if store.group("volatile"):
                flush_memory()
                continue
            ptr = canonical_ptr(store.group("ptr"))
            flush_memory()
            value = store.group("value").strip().split()[-1]
            known_store_values[ptr] = value
            continue

        match = _BINOP_RE.match(line.rstrip("\n"))
        if match is not None:
            lhs, rhs = match.group("lhs").strip(), match.group("rhs").strip()
            if match.group("op") in _COMMUTATIVE_BINOPS:
                lhs, rhs = _canon_pair(lhs, rhs)
            key = (match.group("op"), match.group("ty"), lhs, rhs)
            if key in known_exprs:
                replacements[match.group("result")] = f"%{known_exprs[key]}"
                dead_lines.add(idx)
                changed = True
            else:
                known_exprs[key] = match.group("result")
            continue

        match = _ICMP_RE.match(line.rstrip("\n"))
        if match is not None:
            lhs, rhs = match.group("lhs").strip(), match.group("rhs").strip()
            if match.group("pred") in _COMMUTATIVE_ICMPS:
                lhs, rhs = _canon_pair(lhs, rhs)
            key = ("icmp", match.group("pred"), match.group("ty"), lhs, rhs)
            if key in known_exprs:
                replacements[match.group("result")] = f"%{known_exprs[key]}"
                dead_lines.add(idx)
                changed = True
            else:
                known_exprs[key] = match.group("result")
            continue

        if _STORE_RE.match(stripped) or "call " in line or stripped.startswith("fence") or stripped.startswith("atomicrmw") or stripped.startswith("cmpxchg"):
            flush_memory()

    if not replacements:
        return fn_text, False

    kept = [ln for i, ln in enumerate(lines) if i not in dead_lines]
    text = "".join(kept)
    for _ in range(8):
        nt = text
        for name, rep in replacements.items():
            nt = re.sub(r"%" + re.escape(name) + r"\b", rep, nt)
        if nt == text:
            break
        text = nt
    for _ in range(4):
        lines = text.splitlines(keepends=True)
        dead_alias_lines: set[int] = set()
        changed_alias = False
        for idx, line in enumerate(lines):
            alias = _BITCAST_ALIAS_RE.match(line.strip())
            if alias is None:
                continue
            name = alias.group("dst")
            use_count = sum(
                len(re.findall(r"%" + re.escape(name) + r"\b", other))
                for j, other in enumerate(lines)
                if j != idx
            )
            if use_count == 0:
                dead_alias_lines.add(idx)
                changed_alias = True
        if not changed_alias:
            break
        text = "".join(line for i, line in enumerate(lines) if i not in dead_alias_lines)
    simplified, _ = simplify_module_text(text)
    return simplified, True


def early_cse_text(ir_text: str) -> tuple[str, bool]:
    out: list[str] = []
    changed = False
    for is_function, chunk in _split_functions(ir_text):
        if not is_function:
            out.append(chunk)
            continue
        current = chunk
        fn_changed = False
        for _ in range(8):
            current, local_changed = _rewrite_function(current)
            if not local_changed:
                break
            fn_changed = True
        out.append(current)
        changed = changed or fn_changed
    if not changed:
        return ir_text, False
    return "".join(out), True
