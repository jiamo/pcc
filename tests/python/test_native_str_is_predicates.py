"""str.isidentifier/isprintable/isascii/isnumeric/isdecimal/istitle under
strict no-libpython.

These six str predicate methods had no native lowering, so under
``--backend self --python-libpython=off`` they fell back to py_cpy_* helpers
and the compile hard-errored (PCC-PY-COMPILE-001). Added:
  - runtime py_str_isascii / py_str_isidentifier / py_str_isprintable /
    py_str_isnumeric / py_str_isdecimal / py_str_istitle
    (py_str_accessors.c + port .py). ASCII-scope semantics mirroring the
    existing py_str_isdigit/isalpha helpers (raw-byte scan, ASCII ranges);
    they match CPython for ASCII-only input, which is what this test asserts.
  - frontend string_method_lowering dispatch for the six methods, in both the
    StrType fast path and the DynType path (return i64 0/1 -> i1 bool).

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode) and asserts CPython-exact
output.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run_pcc_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # DEFAULT mode (pcc_py ports) — the no-libpython goal mode. The six
    # predicates are mirrored in py_str_accessors.c and the port .py.
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


# ASCII-only inputs where the ASCII-scope runtime helpers match CPython exactly.
_CASES = [
    "",
    "abc",
    "ABC",
    "Abc",
    "abc123",
    "_abc",
    "abc_1",
    "9abc",
    "hello world",
    "Hello World",
    "Title Case",
    "TITLE",
    "title",
    "This Is A Title",
    "123",
    "12.3",
    " ",
    "\t",
    "Hello!",
    "A",
    "a",
    "1234",
    "~",
]

_METHODS = [
    "isidentifier",
    "isprintable",
    "isascii",
    "isnumeric",
    "isdecimal",
    "istitle",
]


def _expected() -> list[str]:
    out: list[str] = []
    for m in _METHODS:
        for c in _CASES:
            out.append(str(getattr(c, m)()))
    return out


def test_str_is_predicates_native_no_libpython(tmp_path):
    # Build a program that prints method(case) for every method/case pair, in
    # the same order as _expected(), so we can diff line-for-line vs CPython.
    lines = ["def main():"]
    for m in _METHODS:
        for c in _CASES:
            lit = repr(c)
            lines.append(f"    print(({lit}).{m}())")
    lines.append("main()")
    source = "\n".join(lines) + "\n"

    out = _run_pcc_program(tmp_path, source)
    got = out.splitlines()
    expected = _expected()
    assert got == expected, f"got={got!r}\nexpected={expected!r}"


def test_str_is_predicates_dynamic_receiver_no_libpython(tmp_path):
    # Exercise the DynType lowering path (receiver typed via a container whose
    # element type is not statically StrType) in addition to the StrType path.
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    words = ['Title', 'abc', 'A1', 'HELLO', '123', 'Hello World']\n"
        "    for w in words:\n"
        "        print(w.istitle(), w.isidentifier(), w.isascii(),"
        " w.isprintable(), w.isnumeric(), w.isdecimal())\n"
        "main()\n",
    )
    got = out.splitlines()
    words = ["Title", "abc", "A1", "HELLO", "123", "Hello World"]
    expected = [
        " ".join(
            str(getattr(w, m)())
            for m in (
                "istitle",
                "isidentifier",
                "isascii",
                "isprintable",
                "isnumeric",
                "isdecimal",
            )
        )
        for w in words
    ]
    assert got == expected, f"got={got!r}\nexpected={expected!r}"
