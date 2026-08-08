from __future__ import annotations

"""A small line-oriented CHECK/CHECK-NEXT/CHECK-NOT matcher.

This intentionally owns only the finite semantics needed by pcc's assembly
and IR shape tests.  Patterns are literal substrings, not regular expressions;
that keeps diagnostics deterministic and avoids silently accepting a weaker
pattern because regex punctuation was not escaped.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckDirective:
    kind: str
    pattern: str
    spec_line: int


def parse_check_directives(spec: str) -> list[CheckDirective]:
    directives: list[CheckDirective] = []
    for line_number, raw_line in enumerate(spec.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        kind = ""
        pattern = ""
        for candidate in ("CHECK-NEXT", "CHECK-NOT", "CHECK"):
            prefix = candidate + ":"
            if line.startswith(prefix):
                kind = candidate
                pattern = line[len(prefix) :].strip()
                break
        if not kind:
            raise AssertionError(
                f"FileCheck spec line {line_number} has no supported directive: "
                f"{raw_line!r}"
            )
        if not pattern:
            raise AssertionError(
                f"FileCheck spec line {line_number} has an empty {kind} pattern"
            )
        directives.append(CheckDirective(kind, pattern, line_number))
    if not directives:
        raise AssertionError("FileCheck spec has no directives")
    return directives


def _fail(label: str, directive: CheckDirective, detail: str) -> None:
    raise AssertionError(
        f"{label}: {directive.kind} at spec line {directive.spec_line} "
        f"for {directive.pattern!r} {detail}"
    )


def _check_pending_not(
    lines: list[str],
    start: int,
    end: int,
    pending: list[CheckDirective],
    *,
    label: str,
) -> None:
    if not pending or end < start:
        return
    line_index = start
    while line_index <= end:
        line = lines[line_index]
        for directive in pending:
            if directive.pattern in line:
                _fail(
                    label,
                    directive,
                    f"matched forbidden input line {line_index + 1}: {line!r}",
                )
        line_index += 1


def check_text(text: str, spec: str, *, label: str = "input") -> None:
    """Match *spec* against *text* or raise one actionable AssertionError.

    ``CHECK`` searches forward by line. ``CHECK-NEXT`` requires the line
    immediately after the preceding positive match. ``CHECK-NOT`` forbids a
    literal from the preceding positive match through the next one (or EOF).
    Multiple adjacent NOT directives share that region.
    """

    directives = parse_check_directives(spec)
    lines = text.splitlines()
    previous_match = -1
    pending_not: list[CheckDirective] = []

    for directive in directives:
        if directive.kind == "CHECK-NOT":
            pending_not.append(directive)
            continue

        search_start = previous_match + 1
        match_index = -1
        if directive.kind == "CHECK-NEXT":
            if previous_match < 0:
                _fail(label, directive, "has no preceding CHECK match")
            if search_start < len(lines) and directive.pattern in lines[search_start]:
                match_index = search_start
            else:
                actual = "<end of input>"
                if search_start < len(lines):
                    actual = repr(lines[search_start])
                _fail(
                    label,
                    directive,
                    f"did not match required input line {search_start + 1}; got {actual}",
                )
        else:
            index = search_start
            while index < len(lines):
                if directive.pattern in lines[index]:
                    match_index = index
                    break
                index += 1
            if match_index < 0:
                _fail(
                    label,
                    directive,
                    f"was not found after input line {previous_match + 1}",
                )

        _check_pending_not(
            lines,
            search_start,
            match_index,
            pending_not,
            label=label,
        )
        pending_not = []
        previous_match = match_index

    _check_pending_not(
        lines,
        previous_match + 1,
        len(lines) - 1,
        pending_not,
        label=label,
    )


__all__ = ["CheckDirective", "check_text", "parse_check_directives"]
