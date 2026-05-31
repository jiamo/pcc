"""More pure-Python package-import shapes, no-libpython.

B-P0-PKG capability locks: three further common shapes that already compile AND
run fully native under ``--backend self --python-libpython=off``. NOT gap fixes
(these already work); they widen the regression net while probing the
package-import surface.

- multi-level-up relative import in a subpackage ``__init__``:
  ``from ..top import topfn``;
- dynamic ``__all__`` (computed list) driving ``from pkg import *``;
- a single ``from pkg import a, b`` mixing a package function export and a
  submodule object.

(A separate, design-sensitive gap — an ABSENT optional dependency,
``try: import missing except ImportError``, which under strict no-libpython
lowers to ``py_cpy_import`` and is rejected at compile time — is documented in
docs/investigations/python-conditional-indented-import-no-libpython.md No.2 and
is NOT covered here because it does not yet work and changing it is a =off
contract decision.)
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
    return r.stdout.split("\n")


def test_multi_level_up_relative_import(tmp_path):
    # p/sub/__init__.py:  from ..top import topfn ; VAL = topfn() + 1
    def build(site):
        sub = site / "p" / "sub"
        sub.mkdir(parents=True)
        (site / "p" / "__init__.py").write_text("", encoding="utf-8")
        (site / "p" / "top.py").write_text("def topfn():\n    return 3\n", encoding="utf-8")
        (sub / "__init__.py").write_text(
            "from ..top import topfn\nVAL = topfn() + 1\n", encoding="utf-8"
        )
    out = _compile_run(
        tmp_path, build,
        "from p.sub import VAL\n"
        "def main():\n"
        "    print(VAL)\n"
        "main()\n",
    )
    assert out[0] == "4", out


def test_dynamic_all_star_import(tmp_path):
    # __all__ = ["a"] + ["b"]  (computed) ; from q import *
    def build(site):
        pkg = site / "q"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            "from q.mod import a, b\n__all__ = ['a'] + ['b']\n", encoding="utf-8"
        )
        (pkg / "mod.py").write_text(
            "def a():\n    return 1\ndef b():\n    return 2\n", encoding="utf-8"
        )
    out = _compile_run(
        tmp_path, build,
        "from q import *\n"
        "def main():\n"
        "    print(a() + b())\n"
        "main()\n",
    )
    assert out[0] == "3", out


def test_mixed_from_import_func_and_submodule(tmp_path):
    # from p import helper, sub  (helper is a __init__ function; sub is a submodule)
    def build(site):
        pkg = site / "p"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("def helper():\n    return 10\n", encoding="utf-8")
        (pkg / "sub.py").write_text("W = 5\n", encoding="utf-8")
    out = _compile_run(
        tmp_path, build,
        "from p import helper, sub\n"
        "def main():\n"
        "    print(helper() + sub.W)\n"
        "main()\n",
    )
    assert out[0] == "15", out
