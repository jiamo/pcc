"""Elide two exact backend-0 retain/release temporary shapes.

This is intentionally not generic reference-count optimisation.  It removes
only a net-zero retain/release pair in one basic block when the retained alias
has either no intervening use, or one non-dereferencing observation (``icmp``
or ``ptrtoint``).  The source pointer is already live at both operations; the
pair cannot be what owns its lifetime, and no operation between the pair can
retain, release, store, root, throw, invoke a finalizer, or escape it.

Calls, loads, stores, GEPs, PHIs, branches between the pair, debug release
guards, EH syntax, and cross-block aliases are deliberately not recognised.
The pass is opt-in and becomes a no-op for every collector except backend 0.
"""

from __future__ import annotations

import os
import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_VALUE = r"%[-A-Za-z$._0-9]+"
_RETAIN_RE = re.compile(
    rf"^(?P<indent>\s*)(?P<alias>{_VALUE})\s*=\s*"
    rf"call\s+ptr\s+@\"?pcc_gc_retain\"?\(ptr\s+(?P<source>{_VALUE})\)\s*$"
)
_RELEASE_RE = re.compile(
    rf"^\s*call\s+void\s+@\"?pcc_gc_release\"?\(ptr\s+(?P<value>{_VALUE})\)\s*$"
)
_LABEL_RE = re.compile(r"^[-A-Za-z$._0-9]+:\s*(?:;.*)?$")
_FUNC_START_RE = re.compile(r"^\s*define\b")
_TOKEN_CHARS = "-A-Za-z$._0-9"


def _value_occurrences(lines: list[str], value: str) -> int:
    token = re.compile(
        rf"(?<![{_TOKEN_CHARS}]){re.escape(value)}(?![{_TOKEN_CHARS}])"
    )
    return sum(len(token.findall(line)) for line in lines)


def _replace_value(line: str, old: str, new: str) -> str:
    token = re.compile(
        rf"(?<![{_TOKEN_CHARS}]){re.escape(old)}(?![{_TOKEN_CHARS}])"
    )
    return token.sub(new, line)


def _substantive_index(lines: list[str], start: int) -> int | None:
    index = start
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith(";"):
            return index
        index += 1
    return None


def _observes_pointer_once(line: str, alias: str) -> bool:
    escaped = re.escape(alias)
    icmp = re.compile(
        rf"^\s*{_VALUE}\s*=\s*icmp\s+(?:eq|ne)\s+ptr\s+"
        rf"(?:{escaped}\s*,\s*(?:null|{_VALUE})|(?:null|{_VALUE})\s*,\s*{escaped})"
        rf"\s*$"
    )
    ptrtoint = re.compile(
        rf"^\s*{_VALUE}\s*=\s*ptrtoint\s+ptr\s+{escaped}\s+to\s+i(?:8|16|32|64)\s*$"
    )
    return bool(icmp.match(line) or ptrtoint.match(line))


def _owned_source_guard(
    lines: list[str],
    retain_index: int,
    release_index: int,
    source: str,
) -> bool:
    """Require an outer retain/release proving ``source`` is locally owned.

    The outer retain must immediately precede the candidate and its release
    must immediately follow it, modulo comments/blank lines.  Its SSA alias is
    otherwise unused.  This deliberately narrow nesting makes concurrent
    finalization impossible while the inner pair is removed.
    """
    owner_index = retain_index - 1
    while owner_index >= 0:
        stripped = lines[owner_index].strip()
        if stripped and not stripped.startswith(";"):
            break
        owner_index -= 1
    if owner_index < 0 or _LABEL_RE.match(lines[owner_index].strip()):
        return False
    owner = _RETAIN_RE.match(lines[owner_index])
    if owner is None or owner.group("alias") != source:
        return False
    owner_release_index = _substantive_index(lines, release_index + 1)
    if owner_release_index is None or _LABEL_RE.match(
        lines[owner_release_index].strip()
    ):
        return False
    owner_release = _RELEASE_RE.match(lines[owner_release_index])
    if owner_release is None or owner_release.group("value") != source:
        return False
    return _value_occurrences(lines, source) == 3


def _rewrite_function(lines: list[str]) -> tuple[list[str], int]:
    removed: set[int] = set()
    replacements: dict[int, str] = {}
    elisions = 0
    index = 0
    while index < len(lines):
        retain = _RETAIN_RE.match(lines[index])
        if retain is None:
            index += 1
            continue
        alias = retain.group("alias")
        source = retain.group("source")
        next_index = _substantive_index(lines, index + 1)
        if next_index is None or _LABEL_RE.match(lines[next_index].strip()):
            index += 1
            continue
        release = _RELEASE_RE.match(lines[next_index])
        if release is not None and release.group("value") == alias:
            if (
                _value_occurrences(lines, alias) == 2
                and _owned_source_guard(
                    lines, index, next_index, source
                )
            ):
                removed.update((index, next_index))
                elisions += 1
                index = next_index + 1
                continue

        if not _observes_pointer_once(lines[next_index], alias):
            index += 1
            continue
        release_index = _substantive_index(lines, next_index + 1)
        if release_index is None or _LABEL_RE.match(lines[release_index].strip()):
            index += 1
            continue
        release = _RELEASE_RE.match(lines[release_index])
        if release is None or release.group("value") != alias:
            index += 1
            continue
        if _value_occurrences(lines, alias) != 3:
            index += 1
            continue
        if not _owned_source_guard(
            lines, index, release_index, source
        ):
            index += 1
            continue
        removed.update((index, release_index))
        replacements[next_index] = _replace_value(
            lines[next_index], alias, source
        )
        elisions += 1
        index = release_index + 1

    rewritten: list[str] = []
    for line_index, line in enumerate(lines):
        if line_index in removed:
            continue
        rewritten.append(replacements.get(line_index, line))
    return rewritten, elisions


def elide_refcount_pairs(
    ir_text: str,
    *,
    gc_backend: int,
) -> tuple[str, int]:
    """Return ``(IR, elision_count)`` for the exact finite contract."""
    if gc_backend != 0:
        return str(ir_text), 0
    source_lines = str(ir_text).splitlines()
    output: list[str] = []
    function: list[str] | None = None
    depth = 0
    count = 0
    for line in source_lines:
        if function is None:
            if _FUNC_START_RE.match(line):
                function = [line]
                depth = line.count("{") - line.count("}")
            else:
                output.append(line)
            continue
        function.append(line)
        depth += line.count("{") - line.count("}")
        if depth > 0:
            continue
        rewritten, changed = _rewrite_function(function)
        output.extend(rewritten)
        count += changed
        function = None
    if function is not None:
        # Invalid/unclosed LLVM is left for the normal verifier to diagnose;
        # never partially rewrite an ambiguous function body.
        output.extend(function)
    suffix = "\n" if str(ir_text).endswith("\n") else ""
    return "\n".join(output) + suffix, count


class RefcountPairElisionPass(ModulePass):
    name = "pcc-refcount-pair-elision"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None
        self.elision_count = 0

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        self.elision_count = 0
        raw_backend = str(os.environ.get("PCC_GC_BACKEND", "") or "").strip()
        if raw_backend != "0":
            return PreservedAnalyses.all()
        rewritten, count = elide_refcount_pairs(str(module), gc_backend=0)
        if count == 0:
            return PreservedAnalyses.all()
        llvm.parse_assembly(rewritten).verify()
        self.rewritten_ir = rewritten
        self.elision_count = count
        return PreservedAnalyses.none()


__all__ = ["RefcountPairElisionPass", "elide_refcount_pairs"]
