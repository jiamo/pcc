"""Facade pure-Python package import under strict no-libpython.

A real-world package pattern: ``pkg/__init__.py`` RE-EXPORTS names from
submodules (``from pkg.core import compute``), and user code does
``import pkg; pkg.compute(...)`` (attribute access on the package). This is
distinct from the existing direct-submodule-import coverage
(``from pkg.core import answer`` in test_package_import_path.py) and is the more
common package-usage shape.

This is a B-P0-PKG capability lock (real multi-module pure-Python package import
compiles AND runs fully native under ``--backend self --python-libpython=off``,
with no C-extension and no libpython fallback). It is NOT a gap fix — the
capability already works; this regression-locks the facade variant.
"""
from __future__ import annotations
import os, subprocess


def _build_site(site):
    pkg = site / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from mypkg.core import compute\n"
        "from mypkg.util import label\n"
        "VERSION = '1.0'\n",
        encoding="utf-8",
    )
    (pkg / "core.py").write_text(
        "def compute(xs):\n"
        "    total = 0\n"
        "    for x in xs:\n"
        "        total += x * 2\n"
        "    return total\n",
        encoding="utf-8",
    )
    (pkg / "util.py").write_text(
        "def label(name, n):\n"
        "    return f'{name}: {n}'\n",
        encoding="utf-8",
    )


def test_facade_package_import_native(tmp_path):
    site = tmp_path / "site"
    _build_site(site)
    main = tmp_path / "main.py"
    main.write_text(
        "import mypkg\n"
        "def main():\n"
        "    print(mypkg.VERSION)\n"          # 1.0
        "    print(mypkg.compute([1, 2, 3]))\n"  # 12
        "    print(mypkg.label('sum', 12))\n"    # sum: 12
        "main()\n",
        encoding="utf-8",
    )
    exe = tmp_path / "facade_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    # Strict no-libpython: a CPython fallback would error PCC-PY-COMPILE-001.
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(main), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split("\n")[:3] == ["1.0", "12", "sum: 12"], r.stdout
