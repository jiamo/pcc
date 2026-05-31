"""Relative imports + cross-module class in a pure-Python package, no-libpython.

Confirms two common real-package shapes compile AND run fully native under
``--backend self --python-libpython=off`` (no C-ext, no libpython fallback):
- relative imports inside ``__init__.py``: ``from .core import answer`` and
  ``from . import util`` (re-export / submodule binding);
- a class defined in a submodule, re-exported by ``__init__`` and used via
  ``pkg.Class(...)`` (attribute access on the package facade).

B-P0-PKG capability locks (the import tracer for these pure-Python shapes).
NOT gap fixes — these already work; this widens the regression net. (A separate
gap — computed module-level assignments in a package __init__, e.g.
``V = f() + 8`` — is documented in
docs/investigations/python-package-init-computed-module-attr-no-libpython.md.)
"""
from __future__ import annotations
import os, subprocess


def _compile_run(tmp_path, build, main_src):
    site = tmp_path / "site"
    build(site)
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
    return r.stdout


def test_relative_imports_native(tmp_path):
    def build(site):
        pkg = site / "relpkg"; pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            "from .core import answer\n"
            "from . import util\n",
            encoding="utf-8",
        )
        (pkg / "core.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        (pkg / "util.py").write_text("K = 8\n", encoding="utf-8")
    out = _compile_run(
        tmp_path, build,
        "import relpkg\n"
        "def main():\n"
        "    print(relpkg.answer())\n"   # 42 (relative from .core import)
        "    print(relpkg.util.K)\n"     # 8  (relative from . import util)
        "main()\n",
    )
    assert out.split("\n")[:2] == ["42", "8"], out


def test_package_class_native(tmp_path):
    def build(site):
        pkg = site / "cpkg"; pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("from cpkg.model import Point\n", encoding="utf-8")
        (pkg / "model.py").write_text(
            "class Point:\n"
            "    def __init__(self, x, y):\n"
            "        self.x = x\n"
            "        self.y = y\n"
            "    def norm2(self):\n"
            "        return self.x * self.x + self.y * self.y\n",
            encoding="utf-8",
        )
    out = _compile_run(
        tmp_path, build,
        "import cpkg\n"
        "def main():\n"
        "    p = cpkg.Point(3, 4)\n"
        "    print(p.norm2())\n"   # 25
        "main()\n",
    )
    assert out.split("\n")[0] == "25", out
