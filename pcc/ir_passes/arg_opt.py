"""Dead Argument Elimination + Argument Promotion (subsets).

Upstream references:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/DeadArgumentElimination.cpp``
  implements :cpp:class:`llvm::DeadArgumentEliminationPass`. It
  identifies function arguments that are never read in the body and
  rewrites both the function signature and every call site to drop
  those arguments. It also handles dead return values similarly.

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/ArgumentPromotion.cpp``
  implements :cpp:class:`llvm::ArgumentPromotionPass`. When a pointer
  argument is only loaded (never stored, never escaped), the pass
  promotes each load site to pass the pointed-to value instead.

Subset implemented here (labelled ``subset``):

- ``deadargelim``: eliminate only arguments that are unreferenced in
  the body *and* where the function has **internal** linkage
  (private to the module) so every call site is visible.
- ``dead return``: when an internal function's direct call sites never
  use the returned SSA value, rewrite the function to return ``void``
  and drop the dead call assignment. This mirrors LLVM's focused
  dead-ret pruning without tackling aggregate / multi-result cases.
- ``argpromotion``: deferred — requires analysis over all call sites
  and reshaping load patterns. This module currently implements the
  dead-arg half only; argpromotion remains migration-scaffold.

Both transforms preserve all function calls that exist; only the dropped
arguments and dead return values are removed from the ABI surface.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_DEFINE_INTERNAL_RE = re.compile(
    r"^(?P<prefix>define\s+internal\s+(?P<ret>.+?)\s+@)"
    r"(?P<name>[\w\.]+)\s*\((?P<args>[^)]*)\)(?P<trailing>.*?)\s*\{\s*$"
)
_DEFINE_ANY_RE = re.compile(
    r"^\s*define\s+.*?@(?P<name>[\w\.]+)\s*\([^)]*\)\s*.*\{\s*$"
)
_ARG_SPLIT_RE = re.compile(r",\s*(?![^()<>]*[\)\}])")  # naive split
_CALL_ASSIGN_DIRECT_RE = re.compile(
    r"""
    ^\s*
    %(?P<result>[\w\.]+)\s*=\s*
    (?P<mods>(?:(?:tail|musttail|notail)\s+)*)?
    call\s+(?P<ret>.+?)\s+@(?P<name>[\w\.]+)\((?P<args>[^)]*)\)
    (?P<suffix>.*)$
    """,
    re.VERBOSE,
)
_CALL_DIRECT_RE = re.compile(
    r"""
    ^\s*
    (?P<mods>(?:(?:tail|musttail|notail)\s+)*)?
    call\s+(?P<ret>.+?)\s+@(?P<name>[\w\.]+)\((?P<args>[^)]*)\)
    (?P<suffix>.*)$
    """,
    re.VERBOSE,
)
_RET_VAL_RE = re.compile(r"^\s*ret\s+(?!void\b)(?P<ret>.+?)\s+.+$")


class DeadArgElimPass(ModulePass):
    name = "pcc-deadargelim"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = deadargelim_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def deadargelim_text(ir_text: str) -> tuple[str, bool]:
    original_ir = ir_text
    module = llvm.parse_assembly(ir_text)
    module.verify()
    ir_text = str(module)

    # Per-internal-function: list of (arg_name, arg_type) in order.
    # Argument names are llvmlite-numbered %0, %1, ... if unnamed.
    # We collect arg-read-set by scanning body references.
    dead_plan: dict[str, list[int]] = {}  # fn_name → list of dead arg positions
    fn_sigs: dict[str, list[tuple[str, str]]] = {}  # fn_name → [(ty, argname)]
    dead_return_plan: set[str] = set()

    for fn in module.functions:
        if fn.is_declaration:
            continue
        if not _is_internal(fn):
            continue
        arg_names: list[tuple[str, str]] = []
        for arg in fn.arguments:
            # llvmlite exposes str(arg) like "i32 %x"; we need the %name.
            arg_str = str(arg).strip()
            m = re.match(r"(\S+)\s+%([\w\.]+)", arg_str)
            if not m:
                arg_names = []
                break
            arg_names.append((m.group(1), m.group(2)))
        if not arg_names:
            continue
        fn_sigs[fn.name] = arg_names

        # Collect body references to each arg.
        body_refs: set[str] = set()
        for block in fn.blocks:
            for inst in block.instructions:
                text = str(inst)
                for match in re.finditer(r"%([\w\.]+)", text):
                    body_refs.add(match.group(1))
        dead = [
            i for i, (_, name) in enumerate(arg_names)
            if name not in body_refs
        ]
        if dead:
            dead_plan[fn.name] = dead

    fn_sections = _collect_function_sections(ir_text)
    for fn in module.functions:
        if fn.is_declaration or not _is_internal(fn):
            continue
        header = str(fn).splitlines()[0].strip()
        if re.search(r"^define\s+.*\bvoid\s+@" + re.escape(fn.name) + r"\(", header):
            continue
        if _has_dead_return_users(ir_text, fn_sections, fn.name):
            dead_return_plan.add(fn.name)

    if not dead_plan and not dead_return_plan:
        return original_ir, False

    return _rewrite_signatures_and_calls(
        ir_text,
        fn_sigs,
        dead_plan,
        dead_return_plan,
    ), True


def _is_internal(fn) -> bool:
    """Heuristic: function has 'internal' or 'private' linkage."""
    text = str(fn).splitlines()[0] if str(fn).strip().startswith("define") else ""
    # llvmlite's ValueRef doesn't expose linkage directly; check text.
    return "internal" in text or "private" in text


def _rewrite_signatures_and_calls(
    ir_text: str,
    fn_sigs: dict[str, list[tuple[str, str]]],
    dead_plan: dict[str, list[int]],
    dead_return_plan: set[str],
) -> str:
    lines = ir_text.splitlines(keepends=True)
    out: list[str] = []
    current_fn: str | None = None

    # Rewrite function define lines.
    for line in lines:
        dm = _DEFINE_ANY_RE.match(line.rstrip("\n"))
        if dm:
            current_fn = dm.group("name")
        m = _DEFINE_INTERNAL_RE.match(line.rstrip("\n"))
        if m:
            fn_name = m.group("name")
            sig = fn_sigs.get(fn_name, [])
            dead = set(dead_plan.get(fn_name, []))
            kept = [
                f"{ty} %{name}"
                for i, (ty, name) in enumerate(sig)
                if i not in dead
            ] if sig else [a.strip() for a in _ARG_SPLIT_RE.split(m.group("args")) if a.strip()]
            new_args = ", ".join(kept)
            prefix = m.group("prefix")
            if fn_name in dead_return_plan:
                prefix = prefix.replace(f" {m.group('ret')} @", " void @", 1)
            rebuilt = (
                f"{prefix}{fn_name}({new_args})"
                f"{m.group('trailing')} {{\n"
            )
            out.append(rebuilt)
            continue
        if (
            current_fn is not None
            and current_fn in dead_return_plan
            and _RET_VAL_RE.match(line.rstrip("\n"))
        ):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}ret void\n")
            continue
        out.append(line)
        if line.strip() == "}":
            current_fn = None

    text = "".join(out)

    # Rewrite call sites for the same functions.
    # Pattern: `call TY @fn(arg0, arg1, ..., argN)`. llvmlite never
    # emits nested parens inside a call's args for integer IR, so a
    # naive parens-balanced split suffices.
    for fn_name, dead in dead_plan.items():
        call_re = re.compile(
            r"(call\s+[^@]*@" + re.escape(fn_name) + r")\s*\((?P<args>[^)]*)\)"
        )

        def repl(mm: re.Match, dead=dead) -> str:
            args = [a.strip() for a in _ARG_SPLIT_RE.split(mm.group("args"))] if mm.group("args").strip() else []
            kept = [a for i, a in enumerate(args) if i not in dead]
            return f"{mm.group(1)}({', '.join(kept)})"

        text = call_re.sub(repl, text)

    for fn_name in dead_return_plan:
        call_assign_re = re.compile(
            r"""
            ^(?P<indent>\s*)
            %(?P<result>[\w\.]+)\s*=\s*
            (?P<mods>(?:(?:tail|musttail|notail)\s+)*)?
            call\s+(?P<ret>.+?)\s+@""" + re.escape(fn_name) + r"""\((?P<args>[^)]*)\)
            (?P<suffix>.*)$
            """,
            re.VERBOSE | re.MULTILINE,
        )
        text = call_assign_re.sub(
            lambda mm: (
                f"{mm.group('indent')}{mm.group('mods') or ''}call void "
                f"@{fn_name}({mm.group('args')}){mm.group('suffix')}"
            ),
            text,
        )
        call_re = re.compile(
            r"""
            ^(?P<indent>\s*)
            (?P<mods>(?:(?:tail|musttail|notail)\s+)*)?
            call\s+(?P<ret>.+?)\s+@""" + re.escape(fn_name) + r"""\((?P<args>[^)]*)\)
            (?P<suffix>.*)$
            """,
            re.VERBOSE | re.MULTILINE,
        )
        text = call_re.sub(
            lambda mm: (
                f"{mm.group('indent')}{mm.group('mods') or ''}call void "
                f"@{fn_name}({mm.group('args')}){mm.group('suffix')}"
            ),
            text,
        )
    return text


def _collect_function_sections(ir_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_fn: str | None = None
    for line in ir_text.splitlines():
        match = _DEFINE_ANY_RE.match(line)
        if match:
            current_fn = match.group("name")
            sections[current_fn] = [line]
            continue
        if current_fn is not None:
            sections[current_fn].append(line)
            if line.strip() == "}":
                current_fn = None
    return sections


def _has_dead_return_users(
    ir_text: str,
    fn_sections: dict[str, list[str]],
    fn_name: str,
) -> bool:
    found_call = False
    for caller, lines in fn_sections.items():
        for idx, line in enumerate(lines):
            match = _CALL_ASSIGN_DIRECT_RE.match(line)
            if match and match.group("name") == fn_name:
                found_call = True
                result = match.group("result")
                use_re = re.compile(r"%" + re.escape(result) + r"\b")
                if any(use_re.search(other) for other in lines[idx + 1:]):
                    return False
                continue
            bare = _CALL_DIRECT_RE.match(line)
            if bare and bare.group("name") == fn_name:
                found_call = True
    return found_call
