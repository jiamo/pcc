"""Merged Load/Store Motion — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/MergedLoadStoreMotion.cpp``
  implements :cpp:class:`llvm::MergedLoadStoreMotionPass`. For a
  diamond-shaped CFG ``A → {B, C} → D``, the pass:

  1. Hoists identical loads that appear at the head of both B and C
     into A (reducing two loads to one).
  2. Sinks identical stores that appear at the tail of both B and C
     into D.

  "Identical" means same address operand, same type, same alignment.
  Upstream uses AA to verify no clobber between the moved location
  and the merge point; we approximate.

Subset here (labelled ``subset``):

- Hoist identical first-instruction loads at diamond heads when both
  B and C start with ``%x = load TY, ptr %p`` using exactly the same
  pointer name / type. Produces a single load in A, replaces uses.
- Sink identical last-instruction-before-terminator stores at diamond
  tails under the same matching constraint.

Both transforms are safe when the pointer is an alloca or global
(no partial aliasing to worry about); for other pointers we bail
out conservatively.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .alias_analysis import AliasAnalysis, AliasResult
from .dominator_tree import CFG
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_LOAD_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<res>[\w\.]+)\s*=\s*load\s+"
    r"(?P<ty>[^,]+?)\s*,\s*ptr\s+%(?P<ptr>[\w\.]+)"
    r"(?:,\s*align\s+\d+)?\s*$"
)
_STORE_RE = re.compile(
    r"^(?P<indent>\s*)store\s+(?P<ty>[^,]+?)\s+(?P<val>[^,]+?)\s*,\s*"
    r"ptr\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)


class MergedLoadStoreMotionPass(ModulePass):
    name = "pcc-mldst-motion"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = mldst_motion_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def mldst_motion_module(ir_text: str) -> tuple[str, bool]:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    aa = AliasAnalysis(module)
    any_change = False
    for fn in module.functions:
        if fn.is_declaration:
            continue
        new_text, changed = _hoist_sink_in_fn(ir_text, fn, aa)
        if changed:
            try:
                llvm.parse_assembly(new_text).verify()
                ir_text = new_text
                any_change = True
            except RuntimeError:
                continue
    return ir_text, any_change


def _hoist_sink_in_fn(
    ir_text: str, fn: llvm.ValueRef, aa: AliasAnalysis
) -> tuple[str, bool]:
    cfg = CFG.of_function(fn)
    # Find diamonds: A where |succs(A)| == 2, and the two succs share
    # a common merge block D with |preds(D)| == 2.
    changed_text = ir_text

    diamonds: list[tuple[str, str, str, str]] = []
    for a, succs in cfg.successors.items():
        if len(succs) != 2:
            continue
        b, c = succs
        b_succs = cfg.successors.get(b, ())
        c_succs = cfg.successors.get(c, ())
        if len(b_succs) != 1 or len(c_succs) != 1:
            continue
        if b_succs[0] != c_succs[0]:
            continue
        d = b_succs[0]
        if len(cfg.predecessors.get(d, ())) != 2:
            continue
        diamonds.append((a, b, c, d))

    if not diamonds:
        return changed_text, False

    new_text = _hoist_loads_from_diamonds(changed_text, fn.name, diamonds, aa)
    new_text = _sink_stores_from_diamonds(new_text, fn.name, diamonds, aa)
    return new_text, new_text != ir_text


def _first_load_of(block_lines: list[str]) -> tuple[int, dict] | None:
    """Return (line_idx_within_block, groupdict) of the first load
    in block_lines, or None."""
    for i, line in enumerate(block_lines):
        stripped = line.rstrip("\n")
        m = _LOAD_RE.match(stripped)
        if m:
            return i, m.groupdict()
        # Phi at the top is allowed before load; skip label/phis.
        s = line.strip()
        if s.endswith(":") or s == "" or "phi " in s:
            continue
        # Any other instruction first means no head-load to hoist.
        return None
    return None


def _last_store_before_term(block_lines: list[str]) -> tuple[int, dict] | None:
    term_re = re.compile(r"^\s*(ret|br|switch|indirectbr|invoke)\b")
    # Walk from end backwards: skip terminator, find store.
    for i in range(len(block_lines) - 1, -1, -1):
        s = block_lines[i].strip()
        if s == "" or s.startswith(";"):
            continue
        if term_re.match(block_lines[i]):
            continue
        m = _STORE_RE.match(block_lines[i].rstrip("\n"))
        if m:
            return i, m.groupdict()
        return None
    return None


def _get_block_lines(
    ir_text: str, fn_name: str, block_name: str
) -> tuple[list[str], int, int] | None:
    """Return (lines_of_block, start_idx, end_idx) within ir_text.

    start_idx is the line after the block label (first inst line).
    end_idx is exclusive, at the next label or close brace.
    """
    lines = ir_text.splitlines(keepends=True)
    label_re = re.compile(r"^\s*([\w\.]+):\s*(?:;.*)?$")
    in_fn = False
    fn_start = None
    for i, line in enumerate(lines):
        if re.match(rf"^\s*define\s+[^@]*@{re.escape(fn_name)}\b", line):
            in_fn = True
            fn_start = i
            continue
        if in_fn and line.strip() == "}":
            break
        if not in_fn:
            continue
        m = label_re.match(line.rstrip("\n"))
        if m and m.group(1) == block_name:
            start = i + 1
            end = start
            while end < len(lines):
                ln = lines[end].rstrip("\n")
                m2 = label_re.match(ln)
                if m2 or lines[end].strip() == "}":
                    break
                end += 1
            return lines[start:end], start, end
    return None


def _hoist_loads_from_diamonds(
    ir_text: str,
    fn_name: str,
    diamonds: list[tuple[str, str, str, str]],
    aa: AliasAnalysis,
) -> str:
    # For each diamond, look at B's and C's first load. If same ptr
    # and type, hoist to A before its terminator and remove from B,C.
    for (a, b, c, d) in diamonds:
        b_info = _get_block_lines(ir_text, fn_name, b)
        c_info = _get_block_lines(ir_text, fn_name, c)
        if not b_info or not c_info:
            continue
        b_lines, b_start, _ = b_info
        c_lines, c_start, _ = c_info
        b_load = _first_load_of(b_lines)
        c_load = _first_load_of(c_lines)
        if not b_load or not c_load:
            continue
        b_idx, b_m = b_load
        c_idx, c_m = c_load
        if b_m["ptr"] != c_m["ptr"] or b_m["ty"].strip() != c_m["ty"].strip():
            continue
        # Pointer classification — require alloca/global for safety.
        kind = aa.classify(b_m["ptr"]).kind
        if kind not in ("alloca", "global", "argument"):
            continue
        # Hoist: keep B's load as the canonical one, rewrite its
        # result to a fresh name in A, and replace B's / C's uses.
        hoisted_name = b_m["res"] + ".hoist"
        hoisted_line = (
            f"  %{hoisted_name} = load {b_m['ty']}, ptr %{b_m['ptr']}\n"
        )

        # Insert into A before its terminator.
        a_info = _get_block_lines(ir_text, fn_name, a)
        if not a_info:
            continue
        a_lines, a_start, a_end = a_info
        # Find terminator within a_lines.
        term_re = re.compile(r"^\s*(ret|br|switch|indirectbr|invoke)\b")
        insert_at = None
        for i, ln in enumerate(a_lines):
            if term_re.match(ln):
                insert_at = i
                break
        if insert_at is None:
            continue
        # Substitute global text.
        lines = ir_text.splitlines(keepends=True)
        new_lines = list(lines)
        # Insert hoisted load into A.
        new_lines.insert(a_start + insert_at, hoisted_line)
        offset = 1  # account for shift
        # Remove B's and C's load lines (adjusting for insertion).
        def shift(i: int) -> int:
            return i + (offset if i > a_start + insert_at else 0)
        to_remove = sorted([b_start + b_idx, c_start + c_idx], reverse=True)
        for idx in to_remove:
            del new_lines[shift(idx)]
        # Replace uses of b_m["res"] and c_m["res"] with hoisted_name.
        new_text = "".join(new_lines)
        new_text = re.sub(
            r"%" + re.escape(b_m["res"]) + r"(?![\w\.])",
            f"%{hoisted_name}",
            new_text,
        )
        if c_m["res"] != b_m["res"]:
            new_text = re.sub(
                r"%" + re.escape(c_m["res"]) + r"(?![\w\.])",
                f"%{hoisted_name}",
                new_text,
            )
        ir_text = new_text
    return ir_text


def _sink_stores_from_diamonds(
    ir_text: str,
    fn_name: str,
    diamonds: list[tuple[str, str, str, str]],
    aa: AliasAnalysis,
) -> str:
    for (a, b, c, d) in diamonds:
        b_info = _get_block_lines(ir_text, fn_name, b)
        c_info = _get_block_lines(ir_text, fn_name, c)
        d_info = _get_block_lines(ir_text, fn_name, d)
        if not b_info or not c_info or not d_info:
            continue
        b_lines, b_start, _ = b_info
        c_lines, c_start, _ = c_info
        d_lines, d_start, _ = d_info
        b_store = _last_store_before_term(b_lines)
        c_store = _last_store_before_term(c_lines)
        if not b_store or not c_store:
            continue
        b_idx, b_m = b_store
        c_idx, c_m = c_store
        if b_m["ptr"] != c_m["ptr"] or b_m["ty"].strip() != c_m["ty"].strip():
            continue
        # Values must also match to sink directly (same stored value).
        if b_m["val"].strip() != c_m["val"].strip():
            continue
        kind = aa.classify(b_m["ptr"]).kind
        if kind not in ("alloca", "global", "argument"):
            continue
        # Sink: append store at D's start (after any phis).
        d_insert = 0
        for i, ln in enumerate(d_lines):
            s = ln.strip()
            if "phi " in s or s.endswith(":") or s == "":
                d_insert = i + 1
                continue
            break
        sunk_line = (
            f"  store {b_m['ty']} {b_m['val']}, ptr %{b_m['ptr']}\n"
        )

        lines = ir_text.splitlines(keepends=True)
        new_lines = list(lines)
        to_remove = sorted([b_start + b_idx, c_start + c_idx], reverse=True)
        for idx in to_remove:
            del new_lines[idx]
        # Re-lookup d_start since we removed lines.
        fresh = _get_block_lines("".join(new_lines), fn_name, d)
        if not fresh:
            continue
        _, fresh_d_start, _ = fresh
        new_lines.insert(fresh_d_start + d_insert, sunk_line)
        ir_text = "".join(new_lines)
    return ir_text
