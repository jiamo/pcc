"""Aliased / namespace / re-export package imports, no-libpython.

B-P0-PKG capability locks: four common pure-Python package-import shapes that
already compile AND run fully native under ``--backend self
--python-libpython=off`` (no C-extension, no libpython fallback). NOT gap fixes
— these already work; this widens the regression net while probing the
package-import surface.

- ``import pkg as alias`` then ``alias.func()`` / ``alias.CONST``;
- ``import a.b.c as alias`` (dotted package aliased) then ``alias.X``;
- namespace package (no ``__init__.py``): ``from ns import mod`` then ``mod.K``;
- re-export depth: ``pkg/__init__`` re-exports from ``pkg.sub`` which re-exports
  from ``pkg.sub.subsub``, used via ``from pkg import name``.

(A separate gap surfaced in the same probe batch — a submodule that mutates a
package global during import, which depends on module-init ORDER — is documented
in docs/investigations/python-package-init-submodule-exec-order-no-libpython.md
and is NOT covered here because it does not yet work.)
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


def test_import_pkg_as_alias(tmp_path):
    def build(site):
        pkg = site / "p"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text('def go():\n    return 7\nVER = "1.2"\n', encoding="utf-8")
    out = _compile_run(
        tmp_path, build,
        "import p as pkg\n"
        "def main():\n"
        "    print(pkg.go())\n"
        "    print(pkg.VER)\n"
        "main()\n",
    )
    assert out[:2] == ["7", "1.2"], out


def test_import_dotted_pkg_as_alias(tmp_path):
    def build(site):
        for parts, body in ((("a",), ""), (("a", "b"), ""), (("a", "b", "c"), "X = 55\n")):
            d = site
            for p in parts:
                d = d / p
            d.mkdir(parents=True, exist_ok=True)
            (d / "__init__.py").write_text(body, encoding="utf-8")
    out = _compile_run(
        tmp_path, build,
        "import a.b.c as deep\n"
        "def main():\n"
        "    print(deep.X)\n"
        "main()\n",
    )
    assert out[0] == "55", out


def test_namespace_package_no_init(tmp_path):
    def build(site):
        ns = site / "ns"
        ns.mkdir(parents=True)  # NOTE: no __init__.py
        (ns / "mod.py").write_text("K = 21\n", encoding="utf-8")
    out = _compile_run(
        tmp_path, build,
        "from ns import mod\n"
        "def main():\n"
        "    print(mod.K)\n"
        "main()\n",
    )
    assert out[0] == "21", out


def test_reexport_depth(tmp_path):
    def build(site):
        sub = site / "p" / "sub"
        sub.mkdir(parents=True)
        (site / "p" / "__init__.py").write_text("from p.sub import deepfn\n", encoding="utf-8")
        (sub / "__init__.py").write_text("from p.sub.subsub import deepfn\n", encoding="utf-8")
        (sub / "subsub.py").write_text("def deepfn():\n    return 88\n", encoding="utf-8")
    out = _compile_run(
        tmp_path, build,
        "from p import deepfn\n"
        "def main():\n"
        "    print(deepfn())\n"
        "main()\n",
    )
    assert out[0] == "88", out


def test_from_import_submodule_aliased(tmp_path):
    # from p import sub as s ; s.K   (from-import of a submodule, aliased)
    def build(site):
        pkg = site / "p"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "sub.py").write_text("K = 9\n", encoding="utf-8")
    out = _compile_run(
        tmp_path, build,
        "from p import sub as s\n"
        "def main():\n"
        "    print(s.K)\n"
        "main()\n",
    )
    assert out[0] == "9", out


def test_from_import_function_aliased(tmp_path):
    # from p import compute as c ; c()   (from-import of a function, aliased)
    def build(site):
        pkg = site / "p"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("def compute():\n    return 42\n", encoding="utf-8")
    out = _compile_run(
        tmp_path, build,
        "from p import compute as c\n"
        "def main():\n"
        "    print(c())\n"
        "main()\n",
    )
    assert out[0] == "42", out
