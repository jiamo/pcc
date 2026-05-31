"""Native ``str.format()`` with explicit field indices in no-libpython mode.

Auto fields (``{}``) and format specs (``{:.2f}``) were already native; this
adds explicit index fields (``{0}``, ``{1}``, with reuse/reorder and specs),
parsed at compile time in ``format_lowering.py`` (no runtime change).  Named
``{name}`` (kwargs) is still a follow-up.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def _compile(monkeypatch, src: Path, exe: Path) -> None:
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    from pcc.py_frontend.pipeline import compile_python

    compile_python(
        str(src), str(exe),
        ir_scaffold_mode="on", libpython_mode="off", backend="self",
    )


def test_str_format_explicit_index_matches_cpython(tmp_path, monkeypatch):
    src = tmp_path / "fmt.py"
    exe = tmp_path / "fmt.out"
    program = textwrap.dedent("""
        def main() -> None:
            print("{}-{}".format(1, 2))
            print("{0} {1} {0}".format("a", "b"))
            print("{1}{0}".format("x", "y"))
            print("{0:.2f}".format(3.14159))
            print("{0:>6}|".format("hi"))
            print("{:05d}".format(42))
            print("{2} {0} {1}".format("a", "b", "c"))

        if __name__ == "__main__":
            main()
        """).lstrip()
    src.write_text(program)
    _compile(monkeypatch, src, exe)
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=30,
    ).stdout
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == cpython


def test_str_format_named_matches_cpython(tmp_path, monkeypatch):
    # Named ``{name}`` fields (kwargs), incl. specs and mixing with positional
    # index fields.  Required letting ``format`` through the str-method kwargs
    # gate and threading kwargs into the compile-time format parser.
    src = tmp_path / "fmtn.py"
    exe = tmp_path / "fmtn.out"
    program = textwrap.dedent("""
        def main() -> None:
            print("{name}={val}".format(name="x", val=5))
            print("{greeting}, {name}!".format(greeting="Hi", name="bob"))
            print("{name:>8}|".format(name="hi"))
            print("{n:05d}".format(n=42))
            print("{0} {name}".format("pos", name="kw"))

        if __name__ == "__main__":
            main()
        """).lstrip()
    src.write_text(program)
    _compile(monkeypatch, src, exe)
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=30,
    ).stdout
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == cpython


def test_str_format_name_bound_literal_takes_native_path(tmp_path, monkeypatch):
    """A function-local ``fmt = "<literal>"; fmt.format(name=...)``
    must take the same native compile-time-parsed path as a literal
    call site.

    Before this support the call fell through ``_maybe_emit_literal_str_format``
    (which only accepted ``StrLit`` at ``attr.obj``) into the libpython
    fallback. The fallback returned a CPython ``PyStr`` whose layout
    pcc's print path doesn't natively decode — visible as
    ``<object tag=N>`` when the result was stored in a pcc list / dict
    and later printed via multi-arg ``print``. The bug appears in
    ``numpy/__init__.py:663`` as
    ``{n: _msg.format(n=n, extended_msg=extended_msg) for n, extended_msg in _type_info}``;
    see docs/investigations/python-cpy-call-kw-null-kwarg-segfault-diagnostic.md.
    """
    src = tmp_path / "name_fmt.py"
    exe = tmp_path / "name_fmt.out"
    program = textwrap.dedent("""
        def main() -> None:
            items = [("a", "msg_a"), ("b", "msg_b")]
            fmt = "{n}={extended_msg}"
            d = {}
            for n, extended_msg in items:
                d[n] = fmt.format(n=n, extended_msg=extended_msg)
            for k in sorted(d.keys()):
                print(k, d[k])
            # Also exercise the dict-comprehension shape (matches the
            # exact numpy/__init__.py:663 idiom).
            d2 = {n: fmt.format(n=n, extended_msg=extended_msg)
                  for n, extended_msg in items}
            print("---")
            for k in sorted(d2.keys()):
                print(k, d2[k])

        if __name__ == "__main__":
            main()
        """).lstrip()
    src.write_text(program)
    _compile(monkeypatch, src, exe)
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=30,
    ).stdout
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == cpython


def test_str_format_module_const_literal_takes_native_path(tmp_path, monkeypatch):
    """A module-level ``_MSG = "<literal>"`` referenced inside a function
    or a top-level dict-comprehension must also take the native fast
    path. Closes the exact ``numpy/__init__.py:637-663`` shape:

        _msg = ("module 'numpy' has no attribute '{n}'.\\n"
                "extended: {extended_msg}")
        __former_attrs__ = {
            n: _msg.format(n=n, extended_msg=extended_msg)
            for n, extended_msg in _type_info
        }

    The Assign is inside the top-level ``else:`` of
    ``if __NUMPY_SETUP__:``, so it's not in any FuncDef body.
    ``_resolve_str_literal_value`` must walk the ast_module.body
    (descending into top-level If/Try/With) when no current function
    binding exists.
    """
    src = tmp_path / "mod_fmt.py"
    exe = tmp_path / "mod_fmt.out"
    program = textwrap.dedent("""
        # Module-level constant; referenced inside the comprehension at
        # module init. The if-else wrapping matches numpy's exact shape.
        if not False:
            _MSG = (
                "key '{n}'\\n"
                "extended: {extended_msg}")
            _items = [("a", "msg_a"), ("b", "msg_b")]
            _d = {n: _MSG.format(n=n, extended_msg=extended_msg)
                  for n, extended_msg in _items}
            for k in sorted(_d.keys()):
                print(k)
                print(_d[k])
                print("---")
        """).lstrip()
    src.write_text(program)
    _compile(monkeypatch, src, exe)
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=30,
    ).stdout
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == cpython
