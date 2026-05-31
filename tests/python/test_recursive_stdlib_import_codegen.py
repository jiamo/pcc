"""Issue 11.B.1 part 2: codegen-side wiring for recursive stdlib.

When ``recursive_stdlib=True`` causes the closure walker to pull
e.g. ``keyword`` into the multi-file compile set, the codegen for an
``import keyword`` statement in user code must:
  - NOT emit ``py_cpy_import("keyword")`` (that pulls libpython)
  - register keyword as a native module alias so subsequent
    ``keyword.X`` accesses route to native ``user_keyword_X`` symbols
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).absolute().parents[2]
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(source: str, name: str, *, recursive: bool) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source)
    compile_python(
        str(src), str(out),
        emit_llvm_only=True,
        recursive_stdlib=recursive,
    )
    return out.read_text()


def _count_py_cpy_import_for(ir_text: str, mod_name: str) -> int:
    """Count call sites that ``py_cpy_import(@.cpy.mod.<mod_name>)``."""
    # Match a call followed by a getelementptr pulling the module name
    # global. LLVM emits the GEP on the line just above the call.
    pattern = re.compile(
        r"%\.\w+\s*=\s*getelementptr[^\n]+@\.cpy\.mod\."
        + re.escape(mod_name)
        + r"\b[^\n]*\n[^\n]*=\s*call[^\n]+@py_cpy_import",
        re.MULTILINE,
    )
    return len(pattern.findall(ir_text))


def test_recursive_import_skips_py_cpy_import():
    """``import keyword`` with recursive_stdlib=True should NOT
    emit ``py_cpy_import("keyword")`` because keyword is now in the
    native compile closure."""
    program = textwrap.dedent(
        """
        import keyword
        def f(s: str):
            pass
        """
    )
    ir_text = _compile_to_ll(program, "rec_import_keyword_on", recursive=True)
    n = _count_py_cpy_import_for(ir_text, "keyword")
    assert n == 0, (
        f"recursive=True should produce ZERO py_cpy_import for keyword; "
        f"got {n} call sites"
    )


def test_pcc_py_stdlib_constant_import_stays_native():
    """``import string`` should use the pcc/py_stdlib port and resolve
    exported literal constants without CPython module fallback."""
    program = textwrap.dedent(
        """
        import string
        def f():
            return string.ascii_lowercase
        """
    )
    ir_text = _compile_to_ll(program, "rec_import_string_on", recursive=True)
    assert "call ptr @py_cpy_import" not in ir_text
    assert "@.cpy.mod.string" not in ir_text
    assert "abcdefghijklmnopqrstuvwxyz" in ir_text


def test_pcc_py_stdlib_from_import_constant_stays_native():
    """``from string import CONST`` should bind the exported pcc/py_stdlib
    constant directly, using normal CPython spelling without a module
    fallback."""
    program = textwrap.dedent(
        """
        from string import ascii_lowercase as letters
        def f():
            return letters
        print(f())
        """
    )
    ir_text = _compile_to_ll(
        program, "rec_from_import_string_const_on", recursive=True,
    )
    assert "call ptr @py_cpy_import" not in ir_text
    assert "@.cpy.mod.string" not in ir_text
    assert "abcdefghijklmnopqrstuvwxyz" in ir_text


def test_dotted_pcc_py_stdlib_import_routes_to_native_submodule():
    """``import urllib.parse`` should bind the top-level CPython name
    while routing ``urllib.parse.fn`` to the native pcc/py_stdlib
    submodule."""
    program = textwrap.dedent(
        """
        import urllib.parse
        def f():
            return urllib.parse.quote("a/b", "/")
        print(f())
        """
    )
    ir_text = _compile_to_ll(
        program, "rec_import_urllib_parse_on", recursive=True,
    )
    assert "call ptr @py_cpy_import" not in ir_text
    assert "@.cpy.mod.urllib" not in ir_text
    assert "@user_urllib_parse_quote" in ir_text


def test_native_sibling_import_alias_value_position_stays_native():
    """A function-local ``import pkg.sub as sub; return sub`` should not
    re-materialize the native sibling module through CPython fallback."""
    from pcc.py_frontend.pipeline import compile_python_multi

    root = _BUILD / "native_pkg_alias_value"
    pkg = root / "pkg_alias_value"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        "def get_sub():\n"
        "    import pkg_alias_value.sub as sub\n"
        "    return sub\n",
        encoding="utf-8",
    )
    (pkg / "sub.py").write_text("VALUE = 7\n", encoding="utf-8")
    (root / "main.py").write_text(
        "import pkg_alias_value\n"
        "def main():\n"
        "    print('ok')\n"
        "main()\n",
        encoding="utf-8",
    )
    out = root / "out.ll"
    compile_python_multi(
        [
            str(root / "main.py"),
            str(pkg / "__init__.py"),
            str(pkg / "sub.py"),
        ],
        str(out),
        emit_llvm_only=True,
        entry_module="main",
        module_names=["main", "pkg_alias_value", "pkg_alias_value.sub"],
        libpython_mode="off",
        ir_scaffold_mode="on",
        backend="self",
    )
    ir_text = out.read_text(encoding="utf-8")
    assert "call ptr @py_cpy_import" not in ir_text
    assert "@.cpy.mod.pkg_alias_value.sub" not in ir_text
    assert "pkg_alias_value.sub" in ir_text


def test_default_off_mode_auto_routes_available_native_stdlib():
    """With libpython off by default, an available pcc/py_stdlib provider
    should be selected automatically; users should not need a private
    ``std.*`` spelling or an explicit recursive_stdlib flag."""
    program = textwrap.dedent(
        """
        import string
        print(string.ascii_lowercase)
        """
    )
    ir_text = _compile_to_ll(
        program, "rec_import_string_auto_native", recursive=False,
    )
    assert "call ptr @py_cpy_import" not in ir_text
    assert "@.cpy.mod.string" not in ir_text
    assert "abcdefghijklmnopqrstuvwxyz" in ir_text


def test_default_off_mode_auto_routes_warnings_native():
    """``import warnings`` should route to the native stdlib shim instead
    of emitting a CPython module import in no-libpython package closures."""
    program = textwrap.dedent(
        """
        import warnings
        def f():
            warnings.warn("x", stacklevel=2)
            warnings.filterwarnings("ignore")
            warnings.simplefilter("default")
        """
    )
    ir_text = _compile_to_ll(
        program, "rec_import_warnings_auto_native", recursive=False,
    )
    assert "call ptr @py_cpy_import" not in ir_text
    assert "@.cpy.mod.warnings" not in ir_text


def test_off_mode_preserves_py_cpy_import():
    """recursive_stdlib=False (default) keeps the historical
    ``py_cpy_import`` path."""
    program = textwrap.dedent(
        """
        import keyword
        def f(s: str):
            pass
        """
    )
    ir_text = _compile_to_ll(program, "rec_import_keyword_off", recursive=False)
    # Without recursive_stdlib, status quo: py_cpy_import is emitted.
    assert "@py_cpy_import" in ir_text
