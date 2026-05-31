"""Conditional / indented imports in the entry module, no-libpython.

An import that is INDENTED in the entry module — inside a module-level
``try:`` / ``if:`` block, or inside a function (lazy import) — fell back to
libpython under ``--backend self --python-libpython=off``
("imports still lower through CPython fallback"). The entry import-discovery
(``_top_level_import_targets`` in pcc/py_frontend/pipeline.py) was called with
``top_level_only=True``, so it skipped every indented import line; the
referenced module was never added to the native compile set and the import
lowered through ``py_cpy_import``, tripping the no-libpython gate.

The fix scans the ENTRY module's imports including indented ones.
``add_candidate`` only adds names that resolve to a real module file, so a
missing / optional C-extension import inside ``try`` is still left to
``py_cpy_import`` (the absent-optional-dependency case is a separate follow-up,
documented in the investigation). These tests cover the present-dependency
shapes that now compile + run fully native.

B-P0-PKG gap fix. See
docs/investigations/python-conditional-indented-import-no-libpython.md.
"""
from __future__ import annotations
import os, subprocess


def _compile_run(tmp_path, main_src):
    site = tmp_path / "site"
    pkg = site / "p"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "real.py").write_text("Z = 42\n", encoding="utf-8")
    main = tmp_path / "main.py"
    main.write_text(main_src, encoding="utf-8")
    exe = tmp_path / "bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(main), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.split("\n")[0]


def test_try_import_present_dep(tmp_path):
    # try: from p import real as m  (present dependency -> native)
    out = _compile_run(
        tmp_path,
        "try:\n"
        "    from p import real as m\n"
        "except ImportError:\n"
        "    m = None\n"
        "def main():\n"
        "    print(m.Z)\n"
        "main()\n",
    )
    assert out == "42", out


def test_if_block_import(tmp_path):
    # if True: from p import real as m  (module-level conditional)
    out = _compile_run(
        tmp_path,
        "if True:\n"
        "    from p import real as m\n"
        "def main():\n"
        "    print(m.Z)\n"
        "main()\n",
    )
    assert out == "42", out


def test_function_local_import(tmp_path):
    # def main(): from p import real as m  (lazy / function-level import)
    out = _compile_run(
        tmp_path,
        "def main():\n"
        "    from p import real as m\n"
        "    print(m.Z)\n"
        "main()\n",
    )
    assert out == "42", out


def test_try_import_submodule_then_attr(tmp_path):
    # try: from p import real ; real.Z + 1  (submodule object via try-import)
    out = _compile_run(
        tmp_path,
        "try:\n"
        "    from p import real\n"
        "except ImportError:\n"
        "    real = None\n"
        "def main():\n"
        "    print(real.Z + 1)\n"
        "main()\n",
    )
    assert out == "43", out
