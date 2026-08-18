#!/usr/bin/env python3
"""Audit and convert `py_raise` sites that leak their exception.

`py_raise_normalize` returns ``owned = 0`` for any ``PY_TYPE_EXC``, so
``py_raise`` increfs and a caller that *created* the exception still owns a
reference it must release.  ``py_raise_owned`` is the raise-then-release form.
Two shapes therefore leak one exception object per raise:

    py_raise(py_exc_new(...));          # inline, no variable at all
    exc = py_exc_new(...); py_raise(exc)  # fresh variable, never released

and two shapes must be left alone, because the value is borrowed:

    py_raise(exc)     where exc is a parameter
    py_raise(saved)   where saved was loaded from a root that owns it

Converting a borrowed site turns a leak into a double free, which is strictly
worse, so this tool never converts a site it cannot prove is fresh.

Usage:
    raise_owner_audit.py report FILE...        classify, change nothing
    raise_owner_audit.py convert FILE...       apply only the proven-safe ones
    raise_owner_audit.py counts DIR            per-file totals, largest first
"""
from __future__ import annotations

import sys
from pathlib import Path

INLINE = "py_raise(py_exc_new("
INLINE_OWNED = "py_raise_owned(py_exc_new("


def _is_c(path: Path) -> bool:
    return path.suffix == ".c"


def multiline_inline_indices(lines: list[str]) -> list[int]:
    """`py_raise(` alone on its line, then a line starting `py_exc_new(`.

    Matched structurally rather than by a text pattern so reflowed source does
    not silently drop out of the audit.
    """
    found = []
    for i, line in enumerate(lines):
        if line.strip() != "py_raise(":
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j < len(lines) and lines[j].strip().startswith("py_exc_new("):
            found.append(i)
    return found


def _enclosing_end(lines: list[str], start: int) -> int:
    """Index just past the function containing `start`.

    A two-line lookahead is not enough: a cleanup sequence can put the
    `py_decref` several lines after the raise, and calling such a site
    unreleased would convert it and produce a double free.
    """
    # The enclosing def may be nested (a method inside a class, or an inner
    # function), so the boundary is the next def/class at an indent less than
    # or equal to the one that opened this function -- not indent 0.  Treating
    # only column 0 as a boundary made a sibling method look like a
    # continuation of the previous one, which hides real leaks.
    own_indent = None
    for j in range(start, -1, -1):
        stripped = lines[j].strip()
        if stripped.startswith("def ") or stripped.startswith("class "):
            own_indent = len(lines[j]) - len(lines[j].lstrip())
            break
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if not stripped:
            continue
        indent = len(lines[j]) - len(lines[j].lstrip())
        if lines[j].startswith("}"):          # C function close at column 0
            return j
        if own_indent is not None and indent <= own_indent and (
            stripped.startswith("def ")
            or stripped.startswith("@")
            or stripped.startswith("class ")
        ):
            return j
        if own_indent is None and indent == 0 and (
            stripped.startswith("def ")
            or stripped.startswith("@")
            or stripped.startswith("class ")
        ):
            return j
    return len(lines)


def _released_later(lines: list[str], raise_idx: int, var: str) -> bool:
    """Whether `var` is released anywhere after the raise in the same function."""
    end = _enclosing_end(lines, raise_idx)
    needle = f"py_decref({var})"
    return any(needle in lines[j] for j in range(raise_idx + 1, end))


def variable_sites(lines: list[str]) -> list[dict]:
    """Classify every `py_raise(<variable>)` site.

    `released` is checked for *that variable's own name*, and across the whole
    enclosing function rather than the next couple of lines.  Both narrower
    versions of this check were wrong: matching the literal `py_decref(exc)`
    miscounted correct sites using another variable name, and a two-line
    lookahead missed a cleanup sequence that releases further down -- which
    would have converted a released site and produced a double free.
    """
    sites = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("py_raise("):
            continue
        if stripped.startswith("py_raise_owned("):
            continue
        if "py_exc_new(" in stripped or stripped == "py_raise(":
            continue
        var = stripped[len("py_raise("):].rstrip(");")
        if not var.isidentifier():
            continue
        released = _released_later(lines, i, var)
        fresh = False
        for back in range(i - 1, max(i - 8, -1), -1):
            prev = lines[back].strip()
            if prev.startswith(f"{var} = py_exc_new") or prev.startswith(
                f"PyObject *{var} = py_exc_new"
            ):
                fresh = True
                break
            if prev.startswith(f"{var} = ") or prev.startswith(
                f"PyObject *{var} = "
            ):
                break
        sites.append(
            {"line": i + 1, "var": var, "released": released, "fresh": fresh}
        )
    return sites


def audit(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    return {
        "path": path,
        "inline_single": sum(1 for line in lines if INLINE in line),
        "inline_multi": multiline_inline_indices(lines),
        "variables": variable_sites(lines),
    }


def report(path: Path) -> None:
    a = audit(path)
    convertible = [s for s in a["variables"] if s["fresh"] and not s["released"]]
    leave = [
        s for s in a["variables"] if not s["fresh"] and not s["released"]
    ]
    ok = [s for s in a["variables"] if s["released"]]
    print(f"{path}")
    print(
        f"  inline: {a['inline_single']} single, "
        f"{len(a['inline_multi'])} multi-line   -> all safe to convert"
    )
    print(f"  variable: {len(convertible)} fresh (convert), "
          f"{len(ok)} already released, {len(leave)} NOT PROVEN FRESH")
    for s in convertible:
        print(f"    convert  line {s['line']:>6}  {s['var']}")
    for s in leave:
        print(f"    REVIEW   line {s['line']:>6}  {s['var']}  "
              f"(borrowed? parameter? rooted?)")


def convert(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    a = audit(path)
    before_single = a["inline_single"]
    before_owned = text.count(INLINE_OWNED)

    text = text.replace(INLINE, INLINE_OWNED)
    assert text.count(INLINE) == 0, f"{path}: inline sites survived"
    assert text.count(INLINE_OWNED) == before_owned + before_single, path

    lines = text.splitlines(keepends=True)
    for i in multiline_inline_indices([line.rstrip("\n") for line in lines]):
        assert lines[i].strip() == "py_raise(", (path, i)
        lines[i] = lines[i].replace("py_raise(", "py_raise_owned(", 1)
    multi = len(a["inline_multi"])

    converted_vars = 0
    for site in a["variables"]:
        if not site["fresh"] or site["released"]:
            continue
        idx = site["line"] - 1
        assert lines[idx].strip().startswith("py_raise("), (path, site)
        lines[idx] = lines[idx].replace("py_raise(", "py_raise_owned(", 1)
        converted_vars += 1

    text = "".join(lines)
    changed = before_single + multi + converted_vars
    # Only declare the helper when this file actually calls it now.  A file
    # with nothing to convert must not grow a declaration, and py_exc_tls.py
    # *defines* py_raise rather than importing it, so there is no extern to
    # anchor to there.
    if changed and not _is_c(path) and "py_raise_owned = extern" not in text:
        decls = [
            line for line in text.splitlines()
            if line.startswith("py_raise") and "extern(" in line
        ]
        assert decls, f"{path}: no py_raise extern to anchor the declaration"
        text = text.replace(
            decls[0],
            decls[0]
            + "\n# py_raise increfs; a caller that created the exception must"
              " release it.\n"
              'py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)',
            1,
        )
    path.write_text(text, encoding="utf-8")
    print(
        f"{path}: {before_single} inline + {multi} multi-line + "
        f"{converted_vars} variable -> py_raise_owned"
    )


def counts(root: Path) -> None:
    rows = []
    for path in sorted(root.rglob("*.c")):
        n = path.read_text(encoding="utf-8").count(INLINE)
        if n:
            rows.append((n, path.name))
    rows.sort(reverse=True)
    print(f"remaining inline sites: {sum(n for n, _ in rows)}")
    for n, name in rows:
        print(f"  {name}: {n}")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    mode, targets = argv[1], [Path(p) for p in argv[2:]]
    if mode == "counts":
        counts(targets[0])
    elif mode == "report":
        for t in targets:
            report(t)
    elif mode == "convert":
        for t in targets:
            convert(t)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
