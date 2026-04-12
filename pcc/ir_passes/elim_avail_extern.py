"""Eliminate Available Externally — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/ElimAvailExtern.cpp``
  drops bodies/initializers from ``available_externally`` functions and
  globals, leaving external declarations behind.

Subset implemented here (labelled ``subset``):

- ``define available_externally ...`` → matching ``declare ...``
- ``@g = available_externally global/constant ...`` →
  ``@g = external global/constant ...``

This preserves the declaration surface and removes only the executable
body / initializer, which is the core upstream behavior for the focused
cases we exercise.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses
from .simplifycfg import _split_functions


_AVAIL_GLOBAL_RE = re.compile(
    r"""
    ^(?P<indent>\s*)
    @(?P<name>[\w\.\$]+)\s*=\s*
    available_externally\s+
    (?P<rest>.+?)\s*$
    """,
    re.VERBOSE,
)


class ElimAvailExternPass(ModulePass):
    name = "pcc-elim-avail-extern"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = elim_avail_extern_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def elim_avail_extern_text(ir_text: str) -> tuple[str, bool]:
    changed = False
    out: list[str] = []

    for is_function, chunk in _split_functions(ir_text):
        if not is_function:
            rewritten = _rewrite_avail_globals(chunk)
            changed = changed or (rewritten != chunk)
            out.append(rewritten)
            continue
        rewritten = _rewrite_avail_function(chunk)
        changed = changed or (rewritten != chunk)
        out.append(rewritten)

    if not changed:
        return ir_text, False
    return "".join(out), True


def _rewrite_avail_function(fn_text: str) -> str:
    lines = fn_text.splitlines(keepends=True)
    if not lines:
        return fn_text
    header = lines[0].rstrip("\n")
    if "define" not in header or "available_externally" not in header:
        return fn_text

    new_header = re.sub(r"^\s*define\b", "declare", header, count=1)
    new_header = re.sub(r"\bavailable_externally\b\s*", "", new_header, count=1)
    new_header = re.sub(r"\{\s*$", "", new_header).rstrip()

    m = re.search(r"\((?P<args>[^)]*)\)", new_header)
    if m is not None:
        stripped_args = _strip_arg_names(m.group("args"))
        new_header = (
            new_header[: m.start("args")]
            + stripped_args
            + new_header[m.end("args") :]
        )
    return new_header + "\n"


def _strip_arg_names(args_text: str) -> str:
    if not args_text.strip():
        return args_text
    parts = [part.strip() for part in args_text.split(",")]
    stripped: list[str] = []
    for part in parts:
        if part == "...":
            stripped.append(part)
            continue
        stripped.append(re.sub(r"\s+%[\w\.\$]+$", "", part))
    return ", ".join(stripped)


def _rewrite_avail_globals(text: str) -> str:
    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        m = _AVAIL_GLOBAL_RE.match(line.rstrip("\n"))
        if m is None:
            out_lines.append(line)
            continue
        rest = m.group("rest")
        kind = "constant" if " constant " in f" {rest} " else "global"
        # Keep qualifiers like local_unnamed_addr and align, but drop the
        # initializer and switch linkage to external.
        prefix, _, suffix = rest.partition(kind)
        body = suffix.strip()
        if "," in body:
            ty_part, tail = body.split(",", 1)
            tail = ", " + tail.strip()
        else:
            ty_part, tail = body, ""
        ty = ty_part.split(None, 1)[0]
        out_lines.append(
            f"{m.group('indent')}@{m.group('name')} = external "
            f"{prefix.strip() + ' ' if prefix.strip() else ''}{kind} {ty}{tail}\n"
        )
    return "".join(out_lines)
