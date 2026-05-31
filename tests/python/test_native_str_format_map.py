"""str.format_map(mapping) under strict no-libpython (run-based).

``str.format_map`` had no native lowering (=off errored / required libpython).
Added a frontend-only lowering (``format_lowering._maybe_emit_literal_str_format_map``)
that reuses the literal-format field parser of ``.format()`` but resolves each
``{name}`` field at runtime against the single mapping argument:

* dict mapping -> ``py_dict_getitem`` (a missing key raises a catchable
  ``KeyError``, exactly like CPython ``format_map``);
* any other mapping -> the generic ``py_obj_getitem``.

Bounded scope: the format string must be a resolvable literal and every field
must be a ``{name}`` named field (auto ``{}`` / indexed ``{0}`` bail to the
libpython fallback). Format specs (``{n:>5}``, ``{x:.2f}``), literal braces
``{{`` / ``}}`` and repeated fields go through the existing ``py_obj_format`` /
``py_str_concat`` path, so they match ``.format()``.

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode). No runtime/ABI change was
needed; this is a pure frontend lowering.
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


def test_format_map_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print('{name}={val}'.format_map({'name': 'x', 'val': 7}))\n"   # x=7
        "    print('{a}{b}{a}'.format_map({'a': '1', 'b': '2'}))\n"          # 121
        "    print('{n:>5}|'.format_map({'n': 'hi'}))\n"                     # '   hi|'
        "    print('{x:.2f}'.format_map({'x': 3.14159}))\n"                  # 3.14
        "    print('{{}}{name}'.format_map({'name': 'z'}))\n"               # {}z
        "    print('plain'.format_map({}))\n"                                # plain
        "    d = {'k': 'rt', 'v': 42}\n"
        "    print('{k}->{v}'.format_map(d))\n"                             # rt->42
        "main()\n",
    )
    assert out.split("\n")[:7] == [
        "x=7",
        "121",
        "   hi|",
        "3.14",
        "{}z",
        "plain",
        "rt->42",
    ], out


def test_format_map_missing_key_raises_catchable_keyerror(tmp_path):
    # CPython format_map raises KeyError for a missing key; a real dict mapping
    # routes through py_dict_getitem so the KeyError is catchable (not a silent
    # null). The exact str(KeyError) text is an orthogonal exception-repr gap;
    # this asserts only that the typed KeyError propagates and is caught.
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    try:\n"
        "        print('{miss}'.format_map({'present': 1}))\n"
        "        print('NO-RAISE')\n"
        "    except KeyError:\n"
        "        print('caught KeyError')\n"
        "main()\n",
    )
    assert out.split("\n")[0] == "caught KeyError", out
