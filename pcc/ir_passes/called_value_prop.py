"""Called-Value Propagation + Call-Site Splitting — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/CalledValuePropagation.cpp``
  tracks values assigned to function pointers through a sparse
  lattice and folds ``call %fp(...)`` into direct ``call @f(...)``
  when the lattice proves a unique callee.
- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/CallSiteSplitting.cpp``
  duplicates a single call into per-predecessor specialized calls
  when each predecessor has a different known value for the callee.

Subset implemented here (labelled ``subset``):

- Attach ``!callees`` metadata to indirect calls whose loaded callee
  comes from a unique internal/private global initialized to a single
  ``@fn`` and never overwritten in the module. This mirrors the
  focused upstream shape:

    @slot = internal global ptr @f
    %tmp = load ptr, ptr @slot
    call void %tmp()
    →
    call void %tmp(), !callees !0
    !0 = !{ptr @f}

- Local stack-slot devirtualization and call-site splitting remain
  deferred; upstream's ``called-value-propagation`` does not fold the
  simple alloca/load/store case by itself.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_GLOBAL_PTR_FN_RE = re.compile(
    r"""
    ^\s*
    @(?P<slot>[\w\.\$]+)\s*=\s*
    (?:(?:internal|private)\s+)?
    (?:(?:local_unnamed_addr|unnamed_addr)\s+)?
    (?P<kind>global|constant)\s+
    ptr\s+@(?P<fn>[\w\.\$]+)\b
    """,
    re.VERBOSE,
)
_GLOBAL_PTR_STORE_RE = re.compile(
    r"^\s*store\s+ptr\s+@(?P<fn>[\w\.\$]+)\s*,\s*ptr\s+@(?P<slot>[\w\.\$]+)\b"
)
_LOAD_PTR_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*load\s+ptr\s*,\s*ptr\s+@(?P<slot>[\w\.\$]+)\b"
)
_CALL_INDIRECT_RE = re.compile(
    r"(?P<prefix>call\s+[^%@]*?)%(?P<fp>[\w\.]+)\s*\((?P<args>[^)]*)\)"
)
_METADATA_DEF_RE = re.compile(r"^\s*!(?P<id>\d+)\s*=")


class CalledValuePropagationPass(ModulePass):
    name = "pcc-called-value-propagation"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = called_value_prop_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def called_value_prop_text(ir_text: str) -> tuple[str, bool]:
    global_inits: dict[str, str] = {}
    stored_globals: set[str] = set()
    for line in ir_text.splitlines():
        m = _GLOBAL_PTR_FN_RE.match(line.rstrip("\n"))
        if m:
            global_inits[m.group("slot")] = m.group("fn")
            continue
        m = _GLOBAL_PTR_STORE_RE.match(line.rstrip("\n"))
        if m:
            stored_globals.add(m.group("slot"))

    unique_globals: dict[str, str] = {
        slot: fn
        for slot, fn in global_inits.items()
        if slot not in stored_globals
    }

    if not unique_globals:
        return ir_text, False

    # Collect loads whose result carries a known callee.
    load_results: dict[str, str] = {}  # load_result_name → fn_name
    for line in ir_text.splitlines():
        m = _LOAD_PTR_RE.match(line.rstrip("\n"))
        if m and m.group("slot") in unique_globals:
            load_results[m.group("res")] = unique_globals[m.group("slot")]

    if not load_results:
        return ir_text, False

    existing_md = [
        int(m.group("id"))
        for line in ir_text.splitlines()
        if (m := _METADATA_DEF_RE.match(line.rstrip("\n"))) is not None
    ]
    next_md = max(existing_md, default=-1) + 1
    md_for_fn: dict[str, str] = {}
    new_md_defs: list[str] = []

    # Rewrite indirect calls by attaching !callees metadata.
    lines = ir_text.splitlines(keepends=True)
    changed = False
    for i, line in enumerate(lines):
        m = _CALL_INDIRECT_RE.search(line)
        if not m:
            continue
        fp = m.group("fp")
        if fp not in load_results:
            continue
        if "!callees" in line:
            continue
        callee_fn = load_results[fp]
        md_ref = md_for_fn.get(callee_fn)
        if md_ref is None:
            md_ref = f"!{next_md}"
            next_md += 1
            md_for_fn[callee_fn] = md_ref
            new_md_defs.append(f"{md_ref} = !{{ptr @{callee_fn}}}\n")
        newline = "\n" if line.endswith("\n") else ""
        core = line[:-1] if newline else line
        new_line = f"{core}, !callees {md_ref}{newline}"
        if new_line != line:
            lines[i] = new_line
            changed = True

    if not changed:
        return ir_text, False
    text = "".join(lines)
    if new_md_defs:
        if not text.endswith("\n"):
            text += "\n"
        text += "".join(new_md_defs)
    return text, True
