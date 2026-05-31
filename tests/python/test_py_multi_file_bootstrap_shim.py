"""Integration-style test: multi-module program that mirrors the
``pcc/__main__.py`` → ``pcc/pcc.py`` bootstrap shape but without
click or any external deps — verifies the multi-file compile
infrastructure can produce a native binary that calls a sibling
module's entry function from ``__main__.py``'s top-level body.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


_COMPILED_REPO_MAIN_CACHE_DIR = None
_COMPILED_REPO_MAIN_CACHE = {}


def _cleanup_compile_cache():
    if _COMPILED_REPO_MAIN_CACHE_DIR is not None:
        shutil.rmtree(_COMPILED_REPO_MAIN_CACHE_DIR, ignore_errors=True)


atexit.register(_cleanup_compile_cache)


def _test_compile_cache_disabled():
    value = os.environ.get("PCC_TEST_DISABLE_COMPILE_CACHE", "")
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    value = os.environ.get("PCC_TEST_COMPILE_CACHE", "")
    return value.lower() in {"0", "false", "no", "off"}


def _repo_source_fingerprint(repo_root):
    roots = [
        os.path.join(repo_root, "pcc"),
        os.path.join(repo_root, "scripts"),
    ]
    parts = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in {"__pycache__", ".pytest_cache"}
            ]
            for filename in filenames:
                if not filename.endswith((".py", ".c", ".h")):
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    st = os.stat(path)
                except FileNotFoundError:
                    continue
                rel = os.path.relpath(path, repo_root)
                parts.append((rel, st.st_mtime_ns, st.st_size))
    return tuple(sorted(parts))


def _compile_repo_main_binary(main_py, exe):
    """Compile pcc/__main__.py once per pytest process unless disabled.

    This cache is intentionally process-local. It never reuses a binary
    across pytest invocations, and its key includes source mtimes/sizes so a
    source edit during a long test process forces a fresh compile.
    """
    if _test_compile_cache_disabled():
        from pcc.py_frontend.pipeline import compile_python

        with mock.patch.dict(os.environ, {"PCC_PYTHON_IR_PASSES": "off"}):
            compile_python(main_py, exe, libpython_mode="off")
        return

    global _COMPILED_REPO_MAIN_CACHE_DIR
    repo_root = os.path.dirname(os.path.dirname(__file__))
    key = (os.path.realpath(main_py), _repo_source_fingerprint(repo_root))
    cached = _COMPILED_REPO_MAIN_CACHE.get(key)
    if cached is None or not os.path.isfile(cached):
        from pcc.py_frontend.pipeline import compile_python

        if _COMPILED_REPO_MAIN_CACHE_DIR is None:
            _COMPILED_REPO_MAIN_CACHE_DIR = tempfile.mkdtemp(
                prefix="pcc_test_compile_cache_",
            )
        cached = os.path.join(
            _COMPILED_REPO_MAIN_CACHE_DIR,
            f"pcc_main_{len(_COMPILED_REPO_MAIN_CACHE)}.out",
        )
        # These tests exercise the compiled CLI surface, not the LLVM IR pass
        # batch optimizer. Keep the large repo-main smoke bounded.
        with mock.patch.dict(os.environ, {"PCC_PYTHON_IR_PASSES": "off"}):
            compile_python(main_py, cached, libpython_mode="off")
        _COMPILED_REPO_MAIN_CACHE[key] = cached
    shutil.copy2(cached, exe)


class MultiFileBootstrapShimTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="pcc_bootstrap_shim_")
        self.addCleanup(shutil.rmtree, self.td, True)

    def _write(self, rel, source):
        dst = os.path.join(self.td, rel)
        os.makedirs(os.path.dirname(dst) or self.td, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(source).lstrip())
        return dst

    def _assert_no_libpython_fallback_calls(self, ll_path):
        from pcc.py_frontend import pipeline

        with open(ll_path, "r", encoding="utf-8") as f:
            ir_text = f.read()
        self.assertFalse(
            pipeline._ir_needs_libpython(ir_text),
            msg=f"unexpected py_cpy_* fallback call in {ll_path}",
        )

    def test_main_imports_from_sibling_and_runs(self):
        """``pkg/__main__.py`` imports ``run`` from ``pkg.pcc`` and
        invokes it at top level; the sibling defines ``run`` plus
        a helper function it dispatches to.  Matches the bootstrap
        shape with zero runtime dependencies on click or argparse."""
        entry = self._write("main.py", """
            from .pcc import run

            run(5)
        """)
        lib = self._write("pcc.py", """
            def helper(x: int) -> int:
                return x * x


            def run(n: int) -> None:
                print(helper(n))
                print(helper(n + 1))
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "shim.out")
        compile_python_multi(
            [entry, lib], exe,
            module_names=["pkg.__main__", "pkg.pcc"],
            entry_module="pkg.__main__",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "25\n36\n")

        # The produced binary must have no libpython dependency —
        # this is the bootstrap gate's whole point.
        if os.uname().sysname == "Darwin":
            lk = subprocess.run(
                ["otool", "-L", exe], capture_output=True, text=True,
            )
            self.assertEqual(lk.returncode, 0)
            self.assertNotIn("Python", lk.stdout)
            self.assertNotIn("libpython", lk.stdout)

    def test_cross_module_class_instantiate_and_method_call(self):
        """``from .lib import MyClass`` declares the class as an
        extern + synthesises an external function for each method, so
        ``MyClass(args)`` and ``instance.method()`` stay on the
        pcc-native dispatch path with zero libpython dep."""
        self._write("entry.py", """
            from .lib import Point

            p = Point(3, 4)
            print(p.x)
            print(p.y)
            print(p.area())
        """)
        self._write("lib.py", """
            class Point:
                def __init__(self, x: int, y: int) -> None:
                    self.x = x
                    self.y = y

                def area(self) -> int:
                    return self.x * self.y
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "klass.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "lib.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.lib"],
            entry_module="pkg.entry",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "3\n4\n12\n")

        if os.uname().sysname == "Darwin":
            lk = subprocess.run(
                ["otool", "-L", exe], capture_output=True, text=True,
            )
            self.assertNotIn("Python", lk.stdout)

    def test_cross_module_return_type_flows_into_arithmetic(self):
        """Step 4 of the spike: cross-module exports feed type
        inference, so ``result = lib.compute(); total = result * 2``
        types ``result`` as int (not DynType) and lets the ``*``
        emit a native integer multiply instead of erroring."""
        self._write("entry.py", """
            from .lib import compute

            result = compute(5)
            total = result * 2
            print(total)
        """)
        self._write("lib.py", """
            def compute(x: int) -> int:
                return x + 10
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "typed.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "lib.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.lib"],
            entry_module="pkg.entry",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "30\n")

    def test_chain_of_three_modules(self):
        """Entry imports from mid, which imports from base — tests
        that the native-extern path propagates through a chain of
        sibling modules."""
        self._write("entry.py", """
            from .mid import combine

            print(combine(3, 4))
        """)
        self._write("mid.py", """
            from .base import add


            def combine(a: int, b: int) -> int:
                return add(a, b) * 2
        """)
        self._write("base.py", """
            def add(a: int, b: int) -> int:
                return a + b
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "chain.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "mid.py"),
                os.path.join(self.td, "base.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.mid", "pkg.base"],
            entry_module="pkg.entry",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "14\n")

    def test_cross_module_string_list_membership(self):
        """Cross-module string args should compare equal inside list
        membership tests. This exercises the runtime list-contains path
        that bootstrap helpers use for ``entry_module in module_names``."""
        self._write("entry.py", """
            from .helper import contains_module

            if contains_module("pkg.main"):
                print("yes")
            else:
                print("no")
        """)
        self._write("helper.py", """
            def contains_module(name: str) -> bool:
                module_names = ["pkg.main"]
                return name in module_names
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "contains.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "helper.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.helper"],
            entry_module="pkg.entry",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "yes\n")

    def test_module_alias_class_call_and_isinstance_stay_native(self):
        """``from . import defs as d`` should let ``d.Node(...)`` and
        ``isinstance(x, d.Node)`` stay on the native sibling-module path
        instead of routing through CPython module objects."""
        self._write("entry.py", """
            from . import defs as d

            node = d.Node(5)
            if isinstance(node, d.Node):
                print(node.value)
            else:
                print(0)
        """)
        self._write("defs.py", """
            class Node:
                def __init__(self, value: int) -> None:
                    self.value = value
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "alias_class.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "defs.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.defs"],
            entry_module="pkg.entry",
            libpython_mode="off",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "5\n")
        if os.uname().sysname == "Darwin":
            lk = subprocess.run(
                ["otool", "-L", exe], capture_output=True, text=True,
            )
            self.assertEqual(lk.returncode, 0)
            self.assertNotIn("Python", lk.stdout)
            self.assertNotIn("libpython", lk.stdout)

    def test_cross_module_default_none_argument_is_optional(self):
        """A callee defaulted as ``arg=None`` should stay optional in
        multi-file mode. This guards the AST/codegen distinction between
        "no default" and "default literal None"."""
        self._write("entry.py", """
            from .helper import run

            run()
        """)
        self._write("helper.py", """
            def run(argv=None) -> None:
                if argv is None:
                    print("none")
                else:
                    print("value")
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "default_none.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "helper.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.helper"],
            entry_module="pkg.entry",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "none\n")

    def test_cross_module_dataclass_kwargs_resolve_via_synthetic_init(self):
        """Extern dataclass imports should export a synthetic
        ``__init__`` signature so keyword construction stays native."""
        self._write("entry.py", """
            from .lib import TranslationUnit

            unit = TranslationUnit(name="main.c", path="/tmp/main.c", source="int main(void) { return 0; }")
            print(unit.name)
            print(unit.path)
        """)
        self._write("lib.py", """
            from dataclasses import dataclass

            @dataclass(frozen=True)
            class TranslationUnit:
                name: str
                path: str
                source: str
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "extern_dataclass.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "lib.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.lib"],
            entry_module="pkg.entry",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "main.c\n/tmp/main.c\n")

    def test_function_scope_cross_module_class_staticmethod_import(self):
        """Function-scope ``from .lib import Helper`` should still
        register the extern class so a following ``Helper.method()``
        resolves through the native class-method table."""
        self._write("entry.py", """
            def main() -> None:
                from .lib import Helper
                print(Helper.greet("pcc"))

            main()
        """)
        self._write("lib.py", """
            class Helper:
                @staticmethod
                def greet(name: str) -> str:
                    return "hello " + name
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "function_scope_class_import.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "lib.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.lib"],
            entry_module="pkg.entry",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "hello pcc\n")

    def test_cross_module_inherited_dataclass_kwargs(self):
        """Synthetic extern dataclass ``__init__`` should inherit base
        dataclass fields, matching local dataclass expansion."""
        self._write("entry.py", """
            from .lib import DynType

            value = DynType(name="dyn")
            print(value.name)
        """)
        self._write("lib.py", """
            from dataclasses import dataclass

            @dataclass(frozen=True)
            class Type:
                name: str

            @dataclass(frozen=True)
            class DynType(Type):
                pass
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "extern_inherited_dataclass.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "lib.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.lib"],
            entry_module="pkg.entry",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "dyn\n")

    def test_cross_module_inheritance_init_order_is_explicit(self):
        """Cross-module subclassing should not rely on linker ctor
        order. The executable path must initialize the base module
        before the child module's class init runs."""
        pkg_dir = os.path.join(self.td, "pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        self._write("pkg/__init__.py", "")
        main_py = self._write("pkg/__main__.py", """
            from .child import B

            print(123)
        """)
        self._write("pkg/base.py", """
            class A:
                pass
        """)
        self._write("pkg/child.py", """
            from .base import A

            class B(A):
                pass
        """)
        from pcc.py_frontend.pipeline import compile_python
        exe = os.path.join(self.td, "inherit_order.out")
        compile_python(main_py, exe, libpython_mode="auto")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "123\n")

    def test_explicit_base_class_instance_method_dispatch(self):
        """``Base.method(self, ...)`` should treat the first positional
        arg as the explicit receiver rather than failing during codegen
        as if an implicit class receiver were still required."""
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("base_dispatch.py", """
            class Base:
                def greet(self, name: str) -> str:
                    return "hello " + name

            class Child(Base):
                def greet(self, name: str) -> str:
                    return Base.greet(self, name) + "!"

            print(Child().greet("pcc"))
        """)
        ll = os.path.join(self.td, "base_dispatch.ll")
        compile_python(main_py, ll, emit_llvm_only=True)
        self.assertTrue(os.path.isfile(ll))

    def test_nested_helper_reads_module_import_without_capture(self):
        """Nested helpers should treat top-level imported names as
        module-scope bindings rather than synthetic closure captures."""
        self._write("entry.py", """
            from .lib import add_one

            def outer() -> None:
                def inner(value: int) -> int:
                    return add_one(value)

                print(inner(4))

            outer()
        """)
        self._write("lib.py", """
            def add_one(value: int) -> int:
                return value + 1
        """)
        from pcc.py_frontend.pipeline import compile_python_multi
        exe = os.path.join(self.td, "nested_module_import.out")
        compile_python_multi(
            [
                os.path.join(self.td, "entry.py"),
                os.path.join(self.td, "lib.py"),
            ],
            exe,
            module_names=["pkg.entry", "pkg.lib"],
            entry_module="pkg.entry",
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "5\n")

    def test_callable_type_alias_literal_with_cpython_values(self):
        """Type-alias literals like ``Callable[[str], str]`` should
        build CPython containers directly instead of storing foreign
        CPython refs inside pcc-native list/tuple objects."""
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("callable_alias.py", """
            from collections.abc import Callable

            SelfAsmEmitter = Callable[[str], str]
            print(1)
        """)
        exe = os.path.join(self.td, "callable_alias.out")
        compile_python(main_py, exe, libpython_mode="auto")
        r = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, msg=f"exe={exe}\nstderr={r.stderr}")
        self.assertEqual(r.stdout, "1\n")

    def test_import_from_native_submodule_binds_module_object(self):
        """``from pkg import submod`` should bind the submodule object
        itself when the imported name is another compiled sibling
        module, not ``getattr(pkg, 'submod')`` on an empty package."""
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("pkg/__main__.py", """
            from .ast import c_ast

            print(c_ast.answer())
        """)
        self._write("pkg/__init__.py", "")
        self._write("pkg/ast/__init__.py", "")
        self._write("pkg/ast/c_ast.py", """
            def answer() -> int:
                return 123
        """)
        exe = os.path.join(self.td, "import_submodule.out")
        compile_python(main_py, exe)
        r = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "123\n")

    def test_relative_import_inside_package_init_uses_package_scope(self):
        """Package ``__init__.py`` should resolve ``from .context`` to
        ``pkg.sub.context`` instead of incorrectly stripping one extra
        segment and looking for ``pkg.context``."""
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("pkg/__main__.py", """
            from .passes import VALUE

            print(VALUE)
        """)
        self._write("pkg/__init__.py", "")
        self._write("pkg/passes/__init__.py", """
            from .context import VALUE
        """)
        self._write("pkg/passes/context.py", """
            VALUE = 123
        """)
        exe = os.path.join(self.td, "package_init_relative.out")
        compile_python(main_py, exe, libpython_mode="auto")
        r = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "123\n")

    def test_getattr_default_on_cpython_submodule_binding(self):
        """Built-in ``getattr(obj, name, default)`` should work when
        ``obj`` is a CPython-backed sibling submodule object imported
        through the native multi-file closure."""
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("pkg/__main__.py", """
            from .ast import c_ast

            print(getattr(c_ast, "Pragma", 123))
        """)
        self._write("pkg/__init__.py", "")
        self._write("pkg/ast/__init__.py", "")
        self._write("pkg/ast/c_ast.py", """
            VALUE = 1
        """)
        exe = os.path.join(self.td, "getattr_default_submodule.out")
        compile_python(main_py, exe, libpython_mode="auto")
        r = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "123\n")

    def test_cpython_sys_argv_slice_and_help_membership(self):
        """CPython-backed ``sys.argv`` slices should support list
        slicing, string equality, and tuple-membership checks without
        falling through the pcc-native list/str paths."""
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("argv_probe.py", """
            import sys

            argv = sys.argv[1:]
            print(len(argv))
            print(argv[0] == "--help")
            print(argv[0] in ("-h", "--help"))
        """)
        exe = os.path.join(self.td, "argv_probe.out")
        compile_python(main_py, exe)
        r = subprocess.run(
            [exe, "--help"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "1\nTrue\nTrue\n")

    def test_compiled_repo_main_help_path(self):
        """Compiling the real ``pcc/__main__.py`` should preserve the
        top-level ``--help`` path without crashes or spurious error
        output."""
        repo_root = os.path.dirname(os.path.dirname(__file__))
        main_py = os.path.join(repo_root, "pcc", "__main__.py")
        exe = os.path.join(self.td, "pcc_main.out")
        _compile_repo_main_binary(main_py, exe)
        r = subprocess.run(
            [exe, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Usage: pcc", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_compiled_repo_main_help_with_backend_option(self):
        """Global help should still win when a backend option appears
        before ``--help`` on the compiled real CLI."""
        repo_root = os.path.dirname(os.path.dirname(__file__))
        main_py = os.path.join(repo_root, "pcc", "__main__.py")
        exe = os.path.join(self.td, "pcc_main_help_backend.out")
        _compile_repo_main_binary(main_py, exe)
        r = subprocess.run(
            [exe, "--backend=self", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Usage: pcc", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_compiled_repo_main_auto_closes_same_package_absolute_imports(self):
        """Single-file compile of the real repo main should pull
        ``pcc.cli_bootstrap`` into the native package closure instead of
        leaving a direct CPython import of that sibling module."""
        from pcc.py_frontend.pipeline import compile_python

        repo_root = os.path.dirname(os.path.dirname(__file__))
        main_py = os.path.join(repo_root, "pcc", "__main__.py")
        out_ll = os.path.join(self.td, "pcc_main_self.ll")
        with mock.patch.dict(os.environ, {"PCC_PYTHON_IR_PASSES": "off"}):
            compile_python(main_py, out_ll, emit_llvm_only=True)
        self.assertTrue(os.path.isfile(out_ll))
        with open(out_ll, "r", encoding="utf-8") as f:
            ir_text = f.read()
        self.assertIn("user_pcc_cli_bootstrap_bootstrap_cli_sys_argv_exit", ir_text)
        self.assertNotIn("cpy.fromimport.pcc.cli_bootstrap", ir_text)
        self.assertNotIn("cpy.fromimport.pcc.parse", ir_text)
        self.assertNotIn("cpy.fromimport.pcc.parse.py_lift", ir_text)
        self.assertNotIn("cpy.fromimport.pcc.parse.c_parser", ir_text)
        self.assertNotIn("cpy.fromimport.pcc.parse.c_parse_driver", ir_text)
        self.assertNotIn("cpy.fromimport.pcc.parse.plyparser", ir_text)
        self.assertNotIn("cpy.fromimport.pcc.ply", ir_text)

    def test_compiled_repo_main_can_compile_toy_python_program(self):
        """The compiled real CLI should preserve positional PATH
        parsing for a normal ``pcc file.py -o out`` invocation."""
        repo_root = os.path.dirname(os.path.dirname(__file__))
        main_py = os.path.join(repo_root, "pcc", "__main__.py")
        exe = os.path.join(self.td, "pcc_main_compile.out")
        _compile_repo_main_binary(main_py, exe)

        toy_src = self._write("toy_main.py", """
            def main() -> None:
                print(123)

            if __name__ == "__main__":
                main()
        """)
        toy_bin = os.path.join(self.td, "toy_main.bin")
        build = subprocess.run(
            [exe, toy_src, "-o", toy_bin],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(build.returncode, 0, msg=build.stderr)
        self.assertTrue(os.path.isfile(toy_bin))

        toy = subprocess.run(
            [toy_bin],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(toy.returncode, 0, msg=toy.stderr)
        self.assertEqual(toy.stdout, "123\n")

    def test_compiled_repo_main_missing_python_input_reports_error(self):
        """The compiled real CLI should return a friendly nonzero
        error for a missing Python source input."""
        repo_root = os.path.dirname(os.path.dirname(__file__))
        main_py = os.path.join(repo_root, "pcc", "__main__.py")
        exe = os.path.join(self.td, "pcc_main_missing.out")
        _compile_repo_main_binary(main_py, exe)

        missing_src = os.path.join(self.td, "does_not_exist.py")
        missing_out = os.path.join(self.td, "does_not_exist.bin")
        build = subprocess.run(
            [exe, missing_src, "-o", missing_out],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(build.returncode, 1, msg=build.stderr)
        self.assertIn("input file not found", build.stderr)
        self.assertNotIn("Traceback", build.stderr)
        self.assertFalse(os.path.exists(missing_out))

    def test_compiled_repo_main_cache_can_be_disabled(self):
        main_py = self._write("fake_repo_main.py", "print(1)\n")
        out1 = os.path.join(self.td, "fake_repo_main_1.out")
        out2 = os.path.join(self.td, "fake_repo_main_2.out")

        def fake_compile(_src, out, **_kwargs):
            with open(out, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(out, 0o755)

        with mock.patch.dict(os.environ, {"PCC_TEST_DISABLE_COMPILE_CACHE": "1"}):
            with mock.patch(
                "pcc.py_frontend.pipeline.compile_python",
                side_effect=fake_compile,
            ) as compile_mock:
                _compile_repo_main_binary(main_py, out1)
                _compile_repo_main_binary(main_py, out2)

        self.assertEqual(compile_mock.call_count, 2)
        self.assertTrue(os.path.isfile(out1))
        self.assertTrue(os.path.isfile(out2))

    def test_compiled_pcc_multi_help_path(self):
        """The bootstrap CLI's help path should stay available under
        CPython even after we replaced argparse with the self-host
        friendly manual parser."""
        repo_root = os.path.dirname(os.path.dirname(__file__))
        r = subprocess.run(
            [
                os.environ.get("PYTHON", "python3"),
                os.path.join(repo_root, "scripts", "pcc_multi.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage:", r.stdout)
        self.assertIn("--entry MODULE", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_compiled_pcc_multi_can_compile_toy_module(self):
        """Compile ``scripts/pcc_multi.py`` + ``pipeline.py`` into a
        native pair binary, then use that compiled helper to build and
        run a one-file toy module. Guards the real bootstrap-facing
        path rather than just the CLI help surface."""
        repo_root = os.path.dirname(os.path.dirname(__file__))
        pair = os.path.join(self.td, "pcc_multi_pair")
        build = subprocess.run(
            [
                sys.executable,
                os.path.join(repo_root, "scripts", "pcc_multi.py"),
                "--python-libpython",
                "off",
                "--entry", "bootstrap.pcc_multi",
                "--out", pair,
                os.path.join(
                    repo_root, "scripts", "pcc_multi.py",
                ) + "=bootstrap.pcc_multi",
                os.path.join(
                    repo_root, "pcc", "py_frontend", "pipeline.py",
                ) + "=pcc.py_frontend.pipeline",
            ],
            cwd=repo_root,
            env={
                **os.environ,
                # This smoke checks the compiled pcc_multi surface; LLVM IR
                # pass performance is covered separately and can dominate this
                # large bootstrap-facing closure.
                "PCC_PYTHON_IR_PASSES": "off",
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(build.returncode, 0, msg=build.stderr)

        toy_entry = self._write("toypkg/main.py", """
            class ToyError(Exception):
                pass

            def main() -> None:
                print(123)
                try:
                    raise ToyError("ok")
                except Exception as exc:
                    print(str(exc))

            if __name__ == "__main__":
                main()
        """)
        toy_bin = os.path.join(self.td, "toybin")
        run = subprocess.run(
            [
                pair,
                "--entry", "toypkg.main",
                "--python-libpython", "off",
                "--out", toy_bin,
                toy_entry + "=toypkg.main",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertTrue(os.path.isfile(toy_bin))
        self.assertNotIn("pcc cpy error", run.stderr)
        self.assertNotIn("TemporaryDirectory.__exit__", run.stderr)
        self.assertNotIn("Exception ignored", run.stderr)

        toy = subprocess.run(
            [toy_bin],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(toy.returncode, 0, msg=toy.stderr)
        self.assertEqual(toy.stdout, "123\nok\n")

    def test_single_file_package_main_uses_dotted_module_name(self):
        """Single-file package entry compilation should infer
        ``pkg.__main__`` instead of bare ``__main__`` so relative
        imports like ``from .tool import run`` resolve correctly."""
        from pcc.py_frontend.pipeline import compile_python

        pkg_dir = os.path.join(self.td, "pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        self._write("pkg/__init__.py", "")
        main_py = self._write("pkg/__main__.py", """
            from .tool import run

            run()
        """)
        self._write("pkg/tool.py", """
            def run() -> None:
                print("pkg ok")
        """)
        exe = os.path.join(self.td, "pkg_main.out")
        compile_python(main_py, exe)
        r = subprocess.run(
            [exe],
            cwd=self.td,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "pkg ok\n")
        if os.uname().sysname == "Darwin":
            lk = subprocess.run(
                ["otool", "-L", exe],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(lk.returncode, 0)
            self.assertNotIn("Python", lk.stdout)
            self.assertNotIn("libpython", lk.stdout)

    def test_single_file_package_main_unpacks_unannotated_sibling_tuple(self):
        """Auto-closure package compile should treat an unannotated
        sibling return as dynamic rather than ``None``, so tuple
        unpacking across the module boundary still compiles."""
        from pcc.py_frontend.pipeline import compile_python

        pkg_dir = os.path.join(self.td, "pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        self._write("pkg/__init__.py", "")
        main_py = self._write("pkg/__main__.py", """
            from .tool import parts

            left, right = parts()
            print(left + right)
        """)
        self._write("pkg/tool.py", """
            def parts():
                return 10, 20
        """)
        exe = os.path.join(self.td, "pkg_unpack.out")
        compile_python(main_py, exe)
        r = subprocess.run(
            [exe],
            cwd=self.td,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "30\n")
        if os.uname().sysname == "Darwin":
            lk = subprocess.run(
                ["otool", "-L", exe],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(lk.returncode, 0)
            self.assertNotIn("Python", lk.stdout)
            self.assertNotIn("libpython", lk.stdout)

    def test_nested_returned_callable_keeps_method_self_capture(self):
        """Returning a nested def that itself closes over ``self``
        should at least compile through the hoist/capture lowering
        instead of failing with an unbound ``self`` during codegen."""
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("nested_self.py", """
            class Box:
                def __init__(self, x: int) -> None:
                    self.x = x

                def make_reader(self):
                    def outer():
                        def inner() -> int:
                            return self.x

                        return inner

                    return outer()

            def main() -> None:
                box = Box(123)
                reader = box.make_reader()
                print(reader())

            if __name__ == "__main__":
                main()
        """)
        out_ll = os.path.join(self.td, "nested_self.ll")
        compile_python(main_py, out_ll, emit_llvm_only=True)
        self.assertTrue(os.path.isfile(out_ll))

    def test_tuple_of_class_objects_boxes_cleanly(self):
        """Tuple literals containing class objects should marshal as
        object pointers instead of erroring on ``ClassType``."""
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("class_tuple.py", """
            class A:
                pass

            class B:
                pass

            def main() -> None:
                pair = (A, B)
                print(len(pair))

            if __name__ == "__main__":
                main()
        """)
        exe = os.path.join(self.td, "class_tuple.out")
        compile_python(main_py, exe)
        r = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "2\n")

    def test_real_python_frontend_core_self_compile_still_emits_llvm(self):
        """Core frontend/bootstrap Python files should self-compile to
        LLVM IR. This locks the recent real-file fixes in the codegen
        layer and keeps the main pipeline / CLI entry path on the same
        compile-only self-host baseline."""
        from pcc.py_frontend.pipeline import compile_python

        repo_root = os.path.dirname(os.path.dirname(__file__))
        cases = [
            (
                os.path.join(
                    repo_root, "pcc", "py_frontend", "codegen", "layer1.py"
                ),
                os.path.join(self.td, "layer1_self.ll"),
            ),
            (
                os.path.join(repo_root, "pcc", "py_frontend", "pipeline.py"),
                os.path.join(self.td, "pipeline_self.ll"),
            ),
            (
                os.path.join(repo_root, "pcc", "cli_core.py"),
                os.path.join(self.td, "cli_core_self.ll"),
            ),
        ]
        for src_py, out_ll in cases:
            with mock.patch.dict(os.environ, {"PCC_PYTHON_IR_PASSES": "off"}):
                compile_python(src_py, out_ll, emit_llvm_only=True)
            self.assertTrue(
                os.path.isfile(out_ll),
                msg=f"expected LLVM output for {src_py}",
            )

    def test_importing_pcc_py_frontend_does_not_eagerly_import_api(self):
        """Bootstrap helpers import ``pcc.py_frontend`` inside an
        embedded interpreter that may not have llvmlite available.
        Importing the frontend package should therefore avoid the
        top-level ``pcc.api`` / C-evaluator path entirely."""
        code = (
            "import importlib, sys\n"
            "importlib.import_module('pcc.py_frontend')\n"
            "print('pcc.api' in sys.modules)\n"
            "print('pcc.evaluater.c_evaluator' in sys.modules)\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "False\nFalse\n")

    def test_link_with_clang_prefers_explicit_python_ldflags_env(self):
        """When libpython linkage is needed, an explicit
        ``PCC_PYTHON_LDFLAGS`` override should suppress any fallback
        ``python3-config`` probe. This keeps compiled bootstrap stages
        bound to the intended interpreter's embed flags."""
        from pcc.py_frontend import pipeline

        in_ll = os.path.join(self.td, "in.ll")
        with open(in_ll, "w", encoding="utf-8") as f:
            f.write('define i32 @main() { ret i32 0 }\n')
        env = dict(os.environ)
        env["PCC_PYTHON_LDFLAGS"] = "-L/test/python -lpython9.9"
        env["PCC_PYTHON_CONFIG"] = "/definitely/missing/python-config"

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("pcc.py_frontend.pipeline.subprocess.run") as run_mock:
                with mock.patch(
                    "pcc.py_frontend.pipeline.subprocess.check_output"
                ) as check_output_mock:
                    run_mock.return_value = subprocess.CompletedProcess(
                        ["clang"], 0,
                    )
                    pipeline._link_with_clang(
                        [in_ll],
                        "/tmp/out",
                        "/tmp/libpy_runtime_libpython.a",
                        False,
                        needs_libpython=True,
                    )

        check_output_mock.assert_not_called()
        cmd = run_mock.call_args.args[0]
        self.assertIn("-L/test/python", cmd)
        self.assertIn("-lpython9.9", cmd)
        self.assertNotIn("/definitely/missing/python-config", " ".join(cmd))

    def test_runtime_archive_link_args_only_force_capi_for_native_extensions(self):
        from pcc.py_frontend import pipeline

        with mock.patch("pcc.py_frontend.pipeline.sys.platform", "darwin"):
            self.assertEqual(
                pipeline._runtime_archive_link_args_for_native_extensions(
                    "/tmp/libpy_runtime.a",
                    False,
                ),
                ["/tmp/libpy_runtime.a"],
            )
            self.assertEqual(
                pipeline._runtime_archive_link_args_for_native_extensions(
                    "/tmp/libpy_runtime.a",
                    True,
                ),
                ["-Wl,-u,_PyArg_ParseTuple", "/tmp/libpy_runtime.a"],
            )
            self.assertEqual(pipeline._native_extension_export_link_flags(False), [])
            self.assertEqual(
                pipeline._native_extension_export_link_flags(True),
                ["-Wl,-export_dynamic"],
            )

    def test_compile_python_backend_llvm_uses_legacy_clang_link(self):
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("backend_llvm.py", "print(1)\n")
        exe = os.path.join(self.td, "backend_llvm.out")
        with mock.patch(
            "pcc.py_frontend.pipeline._ensure_runtime",
            return_value="/tmp/libpy_runtime.a",
        ):
            with mock.patch(
                "pcc.py_frontend.pipeline._link_with_clang"
            ) as clang_link:
                with mock.patch(
                    "pcc.py_frontend.pipeline._link_with_self_backend"
                ) as self_link:
                    compile_python(main_py, exe, backend="llvm")

        clang_link.assert_called_once()
        self_link.assert_not_called()
        self.assertFalse(
            clang_link.call_args.kwargs.get("needs_native_extension_exports")
        )

    def test_compile_python_backend_env_self_uses_self_link(self):
        from pcc.py_frontend.pipeline import compile_python

        main_py = self._write("backend_self_env.py", "print(1)\n")
        exe = os.path.join(self.td, "backend_self_env.out")
        with mock.patch.dict(os.environ, {"PCC_BACKEND": "self"}):
            with mock.patch(
                "pcc.py_frontend.pipeline._ensure_runtime",
                return_value="/tmp/libpy_runtime.a",
            ):
                with mock.patch(
                    "pcc.py_frontend.pipeline._link_with_clang"
                ) as clang_link:
                    with mock.patch(
                        "pcc.py_frontend.pipeline._link_with_self_backend_ir_texts"
                    ) as self_link:
                        compile_python(main_py, exe)

        self_link.assert_called_once()
        clang_link.assert_not_called()

    def test_self_native_link_reaches_self_emitter_and_host_triple(self):
        from pcc.py_frontend import pipeline

        ll_path = os.path.join(self.td, "self_input.ll")
        with open(ll_path, "w", encoding="utf-8") as f:
            f.write(
                'target triple = "unknown-unknown-unknown"\n'
                "define i32 @main() {\n"
                "entry:\n"
                "  ret i32 0\n"
                "}\n"
            )
        asm_text = (
            ".globl _main\n"
            "_main:\n"
            "  mov w0, #0\n"
            "  ret\n"
            ".subsections_via_symbols\n"
        )
        def fake_check_output(cmd, *, text=False):
            self.assertTrue(text)
            with open(cmd[-1], "r", encoding="utf-8") as f:
                host_ir = f.read()
            self.assertIn(
                'target triple = "arm64-apple-darwin23.6.0"',
                host_ir,
            )
            return "self-aarch64-darwin-v0\n" + asm_text

        with mock.patch(
            "pcc.py_frontend.pipeline._host_target_triple_for_self_backend",
            return_value="arm64-apple-darwin23.6.0",
        ):
            with mock.patch(
                "pcc.py_frontend.pipeline.subprocess.check_output",
                side_effect=fake_check_output,
            ) as check_output_mock:
                with mock.patch(
                    "pcc.py_frontend.pipeline.subprocess.run",
                    return_value=subprocess.CompletedProcess(["cc"], 0),
                ) as run_mock:
                    pipeline._link_with_self_backend(
                        [ll_path],
                        os.path.join(self.td, "self.out"),
                        "/tmp/libpy_runtime.a",
                        False,
                    )

        check_output_mock.assert_called_once()
        self.assertIn("self_backend_dispatch", check_output_mock.call_args.args[0][2])
        run_cmds = [call.args[0] for call in run_mock.call_args_list]
        self.assertTrue(
            any("/tmp/libpy_runtime.a" in cmd for cmd in run_cmds),
            msg=run_cmds,
        )
        if sys.platform == "darwin":
            self.assertTrue(
                any("-Wl,-dead_strip" in cmd for cmd in run_cmds),
                msg=run_cmds,
            )

    def test_self_native_link_keeps_host_emission_for_multiple_modules(self):
        from pcc.py_frontend import pipeline

        ll_paths = []
        for idx in range(2):
            ll_path = os.path.join(self.td, f"self_input_{idx}.ll")
            with open(ll_path, "w", encoding="utf-8") as f:
                f.write(
                    'target triple = "unknown-unknown-unknown"\n'
                    f"define i32 @main_{idx}() {{\n"
                    "entry:\n"
                    "  ret i32 0\n"
                    "}\n"
                )
            ll_paths.append(ll_path)

        host_runs = []

        def fake_run(cmd, **kwargs):
            if len(cmd) > 2 and cmd[1] == "-c":
                host_runs.append(cmd)
                self.assertEqual(cmd[3], "2")
                self.assertEqual(cmd[4], "cc")
                self.assertEqual(cmd[5], "0")
                result_path = cmd[6]
                paths = cmd[7:]
                lines = []
                for idx, path in enumerate(paths):
                    with open(path, "r", encoding="utf-8") as f:
                        host_ir = f.read()
                    self.assertIn(
                        'target triple = "arm64-apple-darwin23.6.0"',
                        host_ir,
                    )
                    obj_path = path + ".o"
                    with open(obj_path, "w", encoding="utf-8") as f:
                        f.write("object")
                    lines.append(
                        f"{idx}\tself-aarch64-darwin-v0\t{obj_path}"
                    )
                with open(result_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                return subprocess.CompletedProcess(cmd, 0)
            return subprocess.CompletedProcess(cmd, 0)

        def unused_check_output(cmd, *, text=False):
            raise AssertionError("multi-module self backend should use result-file run")

        with mock.patch.dict(os.environ, {"PCC_SELF_BACKEND_JOBS": "2"}):
            with mock.patch(
                "pcc.py_frontend.pipeline._host_target_triple_for_self_backend",
                return_value="arm64-apple-darwin23.6.0",
            ):
                with mock.patch(
                    "pcc.py_frontend.pipeline.subprocess.check_output",
                    side_effect=unused_check_output,
                ) as check_output_mock:
                    with mock.patch(
                        "pcc.py_frontend.pipeline.subprocess.run",
                        side_effect=fake_run,
                    ) as run_mock:
                        pipeline._link_with_self_backend(
                            ll_paths,
                            os.path.join(self.td, "self_parallel.out"),
                            "/tmp/libpy_runtime.a",
                            False,
                        )

        check_output_mock.assert_not_called()
        self.assertEqual(len(host_runs), 1)
        link_cmds = [
            call.args[0] for call in run_mock.call_args_list
            if "/tmp/libpy_runtime.a" in call.args[0]
        ]
        self.assertEqual(len(link_cmds), 1)
        link_cmd = link_cmds[0]
        linked_objects = [
            part for part in link_cmd
            if part.endswith(".ll.o")
        ]
        self.assertEqual(len(linked_objects), 2)
        self.assertIn("/tmp/libpy_runtime.a", link_cmd)
        self.assertNotIn("self_backend.s", " ".join(link_cmd))

    def test_self_backend_large_ir_module_split_keeps_one_global_definition(self):
        from pcc.py_frontend import pipeline

        ir_text = (
            'target triple = "unknown-unknown-unknown"\n'
            "@counter = internal global i64 0\n"
            "declare void @ext(i64)\n"
            "\n"
            "define i64 @load_counter() {\n"
            "entry:\n"
            "  %v = load i64, ptr @counter\n"
            "  ret i64 %v\n"
            "}\n"
            "\n"
            "define void @store_counter(i64 %x) {\n"
            "entry:\n"
            "  store i64 %x, ptr @counter\n"
            "  ret void\n"
            "}\n"
        )

        with mock.patch.dict(
            os.environ,
            {
                "PCC_SELF_BACKEND_SPLIT_LARGE_MODULES": "1",
                "PCC_SELF_BACKEND_SPLIT_THRESHOLD_BYTES": "1",
                "PCC_SELF_BACKEND_SPLIT_SHARD_BYTES": "120",
            },
        ):
            shards = pipeline._split_self_backend_large_ir_modules([ir_text])

        self.assertGreaterEqual(len(shards), 3)
        exported_name = "@__pco0_counter"
        global_defs = [
            shard for shard in shards
            if exported_name + " = global i64 0" in shard
        ]
        self.assertEqual(len(global_defs), 1)
        self.assertNotIn("@counter = internal global", "\n".join(shards))
        self.assertNotIn("@counter = global", "\n".join(shards))
        function_shards = [
            shard for shard in shards
            if "define i64 @load_counter" in shard
            or "define void @store_counter" in shard
        ]
        self.assertTrue(function_shards)
        for shard in function_shards:
            self.assertIn(exported_name, shard)
            self.assertNotIn(exported_name + " = global i64 0", shard)

    def test_self_backend_large_ir_module_split_namespaces_internal_symbols_per_module(self):
        from pcc.py_frontend import pipeline

        def module_ir(func_name):
            return (
                'target triple = "unknown-unknown-unknown"\n'
                "@counter = internal global i64 0\n"
                "\n"
                "define i64 @" + func_name + "() {\n"
                "entry:\n"
                "  %v = load i64, ptr @counter\n"
                "  ret i64 %v\n"
                "}\n"
                "\n"
                "define void @" + func_name + "_store(i64 %x) {\n"
                "entry:\n"
                "  store i64 %x, ptr @counter\n"
                "  ret void\n"
                "}\n"
            )

        with mock.patch.dict(
            os.environ,
            {
                "PCC_SELF_BACKEND_SPLIT_LARGE_MODULES": "1",
                "PCC_SELF_BACKEND_SPLIT_THRESHOLD_BYTES": "1",
                "PCC_SELF_BACKEND_SPLIT_SHARD_BYTES": "120",
            },
        ):
            shards = pipeline._split_self_backend_large_ir_modules([
                module_ir("load_a"),
                module_ir("load_b"),
            ])

        joined = "\n".join(shards)
        self.assertIn("@__pco0_counter = global i64 0", joined)
        self.assertIn("@__pco1_counter = global i64 0", joined)
        self.assertNotIn("@counter = global i64 0", joined)
        self.assertNotIn("ptr @counter", joined)

    def test_self_backend_link_requests_host_split_for_large_single_module(self):
        from pcc.py_frontend import pipeline

        ir_text = (
            'target triple = "unknown-unknown-unknown"\n'
            "@counter = internal global i64 0\n"
            "\n"
            "define i64 @f0() {\n"
            "entry:\n"
            "  %v = load i64, ptr @counter\n"
            "  ret i64 %v\n"
            "}\n"
            "\n"
            "define i64 @f1() {\n"
            "entry:\n"
            "  %v = load i64, ptr @counter\n"
            "  ret i64 %v\n"
            "}\n"
        )
        captured = []
        split_flags = []

        def fake_emit_objects(
            ir_texts,
            tmp_dir,
            cc,
            *,
            split_large_modules=False,
            profile=None,
        ):
            captured.extend(ir_texts)
            split_flags.append(split_large_modules)
            results = []
            for idx, _text in enumerate(ir_texts):
                obj_path = os.path.join(tmp_dir, f"obj_{idx}.o")
                with open(obj_path, "w", encoding="utf-8") as f:
                    f.write("object")
                results.append(("self-aarch64-darwin-v0", obj_path))
            return results

        with mock.patch.dict(
            os.environ,
            {
                "PCC_SELF_BACKEND_SPLIT_LARGE_MODULES": "1",
                "PCC_SELF_BACKEND_SPLIT_THRESHOLD_BYTES": "1",
                "PCC_SELF_BACKEND_SPLIT_SHARD_BYTES": "100",
            },
        ):
            with mock.patch(
                "pcc.py_frontend.pipeline._host_target_triple_for_self_backend",
                return_value="arm64-apple-darwin23.6.0",
            ):
                with mock.patch(
                    "pcc.py_frontend.pipeline._emit_self_objects_many_via_host_python",
                    side_effect=fake_emit_objects,
                ) as emit_many:
                    with mock.patch(
                        "pcc.py_frontend.pipeline.subprocess.run",
                        return_value=subprocess.CompletedProcess(["cc"], 0),
                    ) as run_mock:
                        pipeline._link_with_self_backend_ir_texts(
                            [ir_text],
                            os.path.join(self.td, "split.out"),
                            None,
                            False,
                            tmp_dir=self.td,
                        )

        emit_many.assert_called_once()
        self.assertEqual(len(captured), 1)
        self.assertEqual(split_flags, [True])
        link_cmds = [call.args[0] for call in run_mock.call_args_list]
        linked_objects = [
            part
            for cmd in link_cmds
            for part in cmd
            if part.endswith(".o")
        ]
        self.assertEqual(len(linked_objects), 1)

    def test_self_backend_skip_ll_temp_defaults_to_direct_module_ir_texts(self):
        from pcc.py_frontend import pipeline

        main_py = self._write("main.py", """
            def main() -> None:
                print("ok")

            if __name__ == "__main__":
                main()
        """)
        exe = os.path.join(self.td, "skip_ll_temp.out")
        linked = []

        def fake_link(ir_texts, out_path, runtime, verbose, *,
                      needs_libpython=False, needs_native_extension_exports=False,
                      tmp_dir=None, profile=None):
            linked.append((ir_texts, out_path, runtime, needs_libpython))
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("linked")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PCC_SELF_BACKEND_SKIP_LL_TEMP", None)
            with mock.patch(
                "pcc.py_frontend.pipeline._ensure_runtime",
                return_value="/tmp/libpy_runtime.a",
            ):
                with mock.patch(
                    "pcc.py_frontend.pipeline._link_with_self_backend_ir_texts",
                    side_effect=fake_link,
                ) as text_link_mock:
                    with mock.patch(
                        "pcc.py_frontend.pipeline._link_native",
                    ) as path_link_mock:
                        pipeline.compile_python(
                            main_py,
                            exe,
                            backend="self",
                            libpython_mode="off",
                        )

        text_link_mock.assert_called_once()
        path_link_mock.assert_not_called()
        self.assertEqual(linked[0][1], exe)
        self.assertEqual(linked[0][2], "/tmp/libpy_runtime.a")
        self.assertFalse(linked[0][3])
        self.assertEqual(len(linked[0][0]), 1)
        self.assertIn("define", linked[0][0][0])

    def test_self_backend_skip_ll_temp_can_be_disabled(self):
        from pcc.py_frontend import pipeline

        with mock.patch.dict(
            os.environ,
            {"PCC_SELF_BACKEND_SKIP_LL_TEMP": "off"},
        ):
            self.assertFalse(pipeline._self_backend_skip_ll_temp())

    def test_self_backend_target_triple_scan_is_header_limited(self):
        from pcc.py_frontend import pipeline

        ir_text = (
            '; module without target in header\n'
            + ('; filler\n' * 600)
            + 'target triple = "x-should-not-count"\n'
            + 'define i32 @main() { ret i32 0 }\n'
        )

        normalized = pipeline._self_backend_ir_text(ir_text)

        self.assertTrue(normalized.startswith('target triple = "'))
        self.assertIn('target triple = "x-should-not-count"', normalized)

    def test_self_backend_failure_does_not_fallback_to_llvm(self):
        from pcc.py_frontend.pipeline import PyPipelineError, compile_python

        main_py = self._write("backend_self_fail.py", "print(1)\n")
        exe = os.path.join(self.td, "backend_self_fail.out")
        with mock.patch(
            "pcc.py_frontend.pipeline._ensure_runtime",
            return_value="/tmp/libpy_runtime.a",
        ):
            with mock.patch(
                "pcc.py_frontend.pipeline._link_with_self_backend_ir_texts",
                side_effect=PyPipelineError("self backend stopped here"),
            ) as self_link:
                with mock.patch(
                    "pcc.py_frontend.pipeline._link_with_clang"
                ) as clang_link:
                    with self.assertRaisesRegex(
                        PyPipelineError, "self backend stopped here"
                    ):
                        compile_python(main_py, exe, backend="self")

        self_link.assert_called_once()
        clang_link.assert_not_called()

    def test_python_self_backend_smoke_runs_minimal_program(self):
        from pcc.backend.self_backend_targets import (
            is_supported_self_backend_target_triple,
        )
        from pcc.py_frontend import pipeline
        from pcc.py_frontend.pipeline import compile_python

        triple = pipeline._host_target_triple_for_self_backend()
        if not is_supported_self_backend_target_triple(triple):
            self.skipTest(f"self backend target not supported: {triple}")

        main_py = self._write("python_self_backend_smoke.py", "print(1)\n")
        exe = os.path.join(self.td, "python_self_backend_smoke.out")
        compile_python(main_py, exe, backend="self")
        r = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "1\n")

    def test_bootstrap_cli_threads_backend_to_python_pipeline(self):
        from pcc import cli_bootstrap

        main_py = self._write("bootstrap_backend.py", "print(1)\n")
        exe = os.path.join(self.td, "bootstrap_backend.out")
        with mock.patch(
            "pcc.cli_bootstrap._compile_python"
        ) as compile_mock:
            rc = cli_bootstrap.bootstrap_cli_main([
                "--backend=self",
                main_py,
                "-o",
                exe,
            ])

        self.assertEqual(rc, 0)
        compile_mock.assert_called_once()
        self.assertEqual(compile_mock.call_args.kwargs["backend"], "self")
        self.assertIsNone(compile_mock.call_args.kwargs["libpython_mode"])

    def test_resolve_python_config_command_uses_sysconfig_bindir(self):
        from pcc.py_frontend import pipeline

        values = {
            "BINDIR": "/opt/homebrew/python/bin",
            "LDVERSION": "3.13",
            "VERSION": "3.13",
        }

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("sysconfig.get_config_var") as get_var:
                with mock.patch("pcc.py_frontend.pipeline.os.path.isfile") as isfile:
                    with mock.patch("pcc.py_frontend.pipeline.os.access") as access:
                        get_var.side_effect = lambda name: values.get(name)
                        isfile.side_effect = (
                            lambda path: path
                            == "/opt/homebrew/python/bin/python3.13-config"
                        )
                        access.side_effect = (
                            lambda path, mode: path
                            == "/opt/homebrew/python/bin/python3.13-config"
                        )
                        resolved = pipeline._resolve_python_config_command()

        self.assertEqual(
            resolved, "/opt/homebrew/python/bin/python3.13-config",
        )

    def test_dataclass_field_imports_stay_off_libpython_path(self):
        """``from dataclasses import dataclass, field`` should be
        consumed at compile time when the names are only used for
        dataclass expansion / default-factory lowering."""
        from pcc.py_frontend.pipeline import compile_python

        src = self._write(
            "dataclass_field_off.py",
            """
            from dataclasses import dataclass, field

            @dataclass
            class Box:
                items: list = field(default_factory=list)

            def main() -> None:
                box = Box()
                if box.items is not None:
                    print(1)
                else:
                    print(0)

            if __name__ == "__main__":
                main()
            """,
        )
        exe = os.path.join(self.td, "dataclass_field_off.out")
        compile_python(src, exe, libpython_mode="off")

        r = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "1\n")
        if os.uname().sysname == "Darwin":
            lk = subprocess.run(
                ["otool", "-L", exe],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(lk.returncode, 0, msg=lk.stderr)
            self.assertNotIn("Python", lk.stdout)
            self.assertNotIn("libpython", lk.stdout)

    def test_compile_python_libpython_off_rejects_fallback_program(self):
        from pcc.py_frontend.pipeline import PyPipelineError, compile_python

        src = self._write(
            "needs_libpython.py",
            """
            import tempfile

            print(tempfile.gettempdir())
            """,
        )
        exe = os.path.join(self.td, "needs_libpython.out")

        with self.assertRaises(PyPipelineError) as ctx:
            compile_python(src, exe, libpython_mode="off")

        self.assertIn("requires libpython fallback", str(ctx.exception))
        self.assertIn("auto/on", str(ctx.exception))

    def test_types_module_self_compiles_without_libpython(self):
        """The frontend type helpers should stay off-safe instead of
        importing ``pcc.py_frontend.py_ast`` through the CPython module
        path."""
        from pcc.py_frontend.pipeline import compile_python

        repo_root = os.path.dirname(os.path.dirname(__file__))
        src = os.path.join(repo_root, "pcc", "py_frontend", "types.py")
        out_ll = os.path.join(self.td, "types_off.ll")
        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        with open(out_ll, "r", encoding="utf-8") as f:
            ir_text = f.read()
        self.assertNotIn("cpy.import.pcc_py_frontend_py_ast", ir_text)

    def test_native_parser_modules_self_compile_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python

        repo_root = os.path.dirname(os.path.dirname(__file__))
        cases = [
            (
                os.path.join(repo_root, "pcc", "parse", "py_lex.py"),
                os.path.join(self.td, "py_lex_off.ll"),
            ),
            (
                os.path.join(repo_root, "pcc", "parse", "py_parse.py"),
                os.path.join(self.td, "py_parse_off.ll"),
            ),
        ]
        for src, out_ll in cases:
            compile_python(
                src,
                out_ll,
                emit_llvm_only=True,
                libpython_mode="off",
            )
            self.assertTrue(os.path.isfile(out_ll), msg=src)

    def test_native_parser_pair_compiles_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python_multi

        repo_root = os.path.dirname(os.path.dirname(__file__))
        out_ll = os.path.join(self.td, "py_parse_pair.ll")
        compile_python_multi(
            [
                os.path.join(repo_root, "pcc", "parse", "py_parse.py"),
                os.path.join(repo_root, "pcc", "parse", "py_lex.py"),
            ],
            out_ll,
            module_names=["pcc.parse.py_parse", "pcc.parse.py_lex"],
            entry_module="pcc.parse.py_parse",
            emit_llvm_only=True,
            libpython_mode="off",
        )
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)

    def test_native_lift_stack_compiles_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python_multi

        repo_root = os.path.dirname(os.path.dirname(__file__))
        out_ll = os.path.join(self.td, "py_lift_stack.ll")
        compile_python_multi(
            [
                os.path.join(repo_root, "pcc", "parse", "py_lift.py"),
                os.path.join(repo_root, "pcc", "parse", "py_parse.py"),
                os.path.join(repo_root, "pcc", "parse", "py_lex.py"),
                os.path.join(repo_root, "pcc", "py_frontend", "py_ast.py"),
            ],
            out_ll,
            module_names=[
                "pcc.parse.py_lift",
                "pcc.parse.py_parse",
                "pcc.parse.py_lex",
                "pcc.py_frontend.py_ast",
            ],
            entry_module="pcc.parse.py_lift",
            emit_llvm_only=True,
            libpython_mode="off",
        )
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)

    def test_native_type_infer_stack_compiles_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python_multi

        repo_root = os.path.dirname(os.path.dirname(__file__))
        out_ll = os.path.join(self.td, "type_infer_stack.ll")
        compile_python_multi(
            [
                os.path.join(repo_root, "pcc", "py_frontend", "type_infer.py"),
                os.path.join(repo_root, "pcc", "py_frontend", "py_ast.py"),
                os.path.join(repo_root, "pcc", "py_frontend", "export_meta.py"),
                os.path.join(repo_root, "pcc", "py_frontend", "types.py"),
            ],
            out_ll,
            module_names=[
                "pcc.py_frontend.type_infer",
                "pcc.py_frontend.py_ast",
                "pcc.py_frontend.export_meta",
                "pcc.py_frontend.types",
            ],
            entry_module="pcc.py_frontend.type_infer",
            emit_llvm_only=True,
            libpython_mode="off",
        )
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)

    def test_pcc_unsafe_memory_intrinsics_compile_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write("unsafe_probe.py", """
            from pcc.unsafe import (
                free,
                load_i32,
                load_i64,
                load_ptr,
                malloc,
                null,
                ptr_add,
                ptr_eq,
                ptr_is_null,
                store_i32,
                store_i64,
                store_ptr,
            )


            def main() -> None:
                p = malloc(24)
                child = malloc(8)
                store_i64(p, 0, 41)
                store_i32(p, 8, 1)
                store_ptr(p, 16, child)
                q = ptr_add(p, 0)
                print(load_i64(q, 0) + load_i32(p, 8))
                print(ptr_eq(load_ptr(p, 16), child))
                print(ptr_is_null(null()))
                free(child)
                free(p)


            main()
        """)

        out_ll = os.path.join(self.td, "unsafe_probe.ll")
        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)
        with open(out_ll, "r", encoding="utf-8") as f:
            ir_text = f.read()
        self.assertIn("@malloc", ir_text)
        self.assertNotIn("@py_mem_", ir_text)

        exe = os.path.join(self.td, "unsafe_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "42\nTrue\nTrue\n")

    def test_py_gc_backend_runtime_file_compiles_without_libpython_fallback(self):
        from pcc.py_frontend.pipeline import compile_python

        repo_root = os.path.dirname(os.path.dirname(__file__))
        src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_gc_backend.py",
        )
        out_ll = os.path.join(self.td, "py_gc_backend.ll")

        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)

    def test_py_gc_telemetry_runtime_file_compiles_without_libpython_fallback(self):
        from pcc.py_frontend.pipeline import compile_python

        repo_root = os.path.dirname(os.path.dirname(__file__))
        src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_gc_telemetry.py",
        )
        out_ll = os.path.join(self.td, "py_gc_telemetry.ll")

        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)

    def test_py_gc_telemetry_split_preserves_runtime_api(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write("gc_telemetry_split_probe.py", """
            from pcc.extern import extern, c_int64, c_void

            pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
            pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)
            pcc_gc_backend2_worker_buffer_score = extern(
                "pcc_gc_backend2_worker_buffer_score", (), c_int64
            )
            pcc_gc_backend3_minor_productivity_score = extern(
                "pcc_gc_backend3_minor_productivity_score", (), c_int64
            )


            def main() -> None:
                pcc_gc_telemetry_reset()
                print(pcc_gc_telemetry(0))
                print(pcc_gc_telemetry(29))
                print(pcc_gc_backend2_worker_buffer_score())
                print(pcc_gc_backend3_minor_productivity_score())
                print(pcc_gc_telemetry(999))


            main()
        """)

        exe = os.path.join(self.td, "gc_telemetry_split_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "0\n0\n0\n0\n-1\n")

    def test_py_gc_backend_no_longer_exports_read_telemetry_dispatch(self):
        import pcc

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(pcc.__file__)))
        backend_src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_gc_backend.py",
        )
        telemetry_src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_gc_telemetry.py",
        )

        with open(backend_src, "r", encoding="utf-8") as f:
            backend_text = f.read()
        with open(telemetry_src, "r", encoding="utf-8") as f:
            telemetry_text = f.read()

        for needle in (
            '@c_abi_export("pcc_gc_telemetry")',
            '@c_abi_export("pcc_gc_backend2_worker_buffer_score")',
            '@c_abi_export("pcc_gc_backend2_production_score")',
            '@c_abi_export("pcc_gc_backend3_minor_productivity_score")',
            '@c_abi_export("pcc_gc_backend3_remembered_update_score")',
        ):
            self.assertNotIn(needle, backend_text)
            self.assertIn(needle, telemetry_text)
        self.assertIn('@c_abi_export("pcc_gc_telemetry_reset")', backend_text)

    def test_py_obj_ops_mod_runtime_file_compiles_without_libpython_fallback(self):
        import pcc
        from pcc.py_frontend.pipeline import compile_python

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(pcc.__file__)))
        src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_obj_ops_mod.py",
        )
        out_ll = os.path.join(self.td, "py_obj_ops_mod.ll")

        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)

    def test_py_obj_ops_dispatch_no_longer_forces_str_mod_closure(self):
        import pcc

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(pcc.__file__)))
        src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_obj_ops_dispatch.py",
        )

        with open(src, "r", encoding="utf-8") as f:
            text = f.read()

        self.assertNotIn('extern("py_str_mod"', text)
        self.assertNotIn('@c_abi_export("py_obj_mod")', text)

    def test_py_obj_mod_split_preserves_int_and_string_modulo_runtime(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write("obj_mod_split_probe.py", """
            def main() -> None:
                print(7 % 3)
                print("hello %s" % "world")


            main()
        """)

        exe = os.path.join(self.td, "obj_mod_split_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "1\nhello world\n")

    def test_list_set_slice_split_runtime_files_compile_without_libpython_fallback(self):
        import pcc
        from pcc.py_frontend.pipeline import compile_python

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(pcc.__file__)))
        for name in ("py_list_set_slice.py", "py_obj_ops_set_slice.py"):
            src = os.path.join(repo_root, "pcc", "py_runtime", "py", name)
            out_ll = os.path.join(self.td, name + ".ll")

            compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
            self.assertTrue(os.path.isfile(out_ll))
            self._assert_no_libpython_fallback_calls(out_ll)

    def test_common_list_and_dispatch_members_no_longer_force_set_slice_closure(self):
        import pcc

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(pcc.__file__)))
        list_src = os.path.join(repo_root, "pcc", "py_runtime", "py", "py_list.py")
        dispatch_src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_obj_ops_dispatch.py",
        )

        with open(list_src, "r", encoding="utf-8") as f:
            list_text = f.read()
        with open(dispatch_src, "r", encoding="utf-8") as f:
            dispatch_text = f.read()

        self.assertNotIn('@c_abi_export("py_list_set_slice")', list_text)
        self.assertNotIn('extern("py_list_set_slice"', dispatch_text)
        self.assertNotIn('@c_abi_export("py_obj_set_slice")', dispatch_text)

    def test_list_set_slice_split_preserves_slice_assignment_runtime(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write("list_set_slice_split_probe.py", """
            def main() -> None:
                xs = [1, 2, 3, 4]
                xs[1:3] = [8, 9, 10]
                print(xs[0])
                print(xs[1])
                print(xs[2])
                print(xs[3])
                print(xs[4])
                ys = [1, 2, 3, 4]
                ys[::2] = [7, 8]
                print(ys[0])
                print(ys[2])


            main()
        """)

        exe = os.path.join(self.td, "list_set_slice_split_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "1\n8\n9\n10\n4\n7\n8\n")

    def test_slice_split_runtime_files_compile_without_libpython_fallback(self):
        import pcc
        from pcc.py_frontend.pipeline import compile_python

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(pcc.__file__)))
        for name in ("py_str_slice.py", "py_tuple_slice.py", "py_obj_ops_slice.py"):
            src = os.path.join(repo_root, "pcc", "py_runtime", "py", name)
            out_ll = os.path.join(self.td, name + ".ll")

            compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
            self.assertTrue(os.path.isfile(out_ll))
            self._assert_no_libpython_fallback_calls(out_ll)

    def test_common_string_and_dispatch_members_no_longer_force_slice_closure(self):
        import pcc

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(pcc.__file__)))
        str_src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_str_accessors.py",
        )
        dispatch_src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_obj_ops_dispatch.py",
        )

        with open(str_src, "r", encoding="utf-8") as f:
            str_text = f.read()
        with open(dispatch_src, "r", encoding="utf-8") as f:
            dispatch_text = f.read()

        self.assertNotIn('@c_abi_export("py_str_slice")', str_text)
        self.assertNotIn('extern("py_str_slice"', dispatch_text)
        self.assertNotIn('@c_abi_export("py_obj_slice")', dispatch_text)

    def test_common_tuple_member_no_longer_forces_tuple_slice_closure(self):
        import pcc

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(pcc.__file__)))
        tuple_src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_tuple.py",
        )
        tuple_slice_src = os.path.join(
            repo_root,
            "pcc",
            "py_runtime",
            "py",
            "py_tuple_slice.py",
        )

        with open(tuple_src, "r", encoding="utf-8") as f:
            tuple_text = f.read()
        with open(tuple_slice_src, "r", encoding="utf-8") as f:
            tuple_slice_text = f.read()

        self.assertNotIn('@c_abi_export("py_tuple_slice")', tuple_text)
        self.assertIn('@c_abi_export("py_tuple_slice")', tuple_slice_text)

    def test_slice_split_preserves_string_list_and_tuple_slice_runtime(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write("slice_split_probe.py", """
            def main() -> None:
                print("abcdef"[1:5:2])
                print("héllo"[1:4])
                xs = [1, 2, 3, 4]
                ys = xs[1:4:2]
                print(ys[0])
                print(ys[1])
                ts = (5, 6, 7)
                us = ts[::-1]
                print(us[0])
                print(us[2])


            main()
        """)

        exe = os.path.join(self.td, "slice_split_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "bd\néll\n2\n4\n7\n5\n")

    def test_pcc_unsafe_tagged_int_intrinsics_compile_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write("unsafe_tagged_int_probe.py", """
            from pcc.unsafe import is_tagged_int, tag_int, untag_int


            def main() -> None:
                a = tag_int(123)
                b = tag_int(-45)
                print(is_tagged_int(a))
                print(untag_int(a))
                print(untag_int(b))


            main()
        """)

        out_ll = os.path.join(self.td, "unsafe_tagged_int_probe.ll")
        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)
        with open(out_ll, "r", encoding="utf-8") as f:
            ir_text = f.read()
        self.assertIn("inttoptr", ir_text)
        self.assertIn("ptrtoint", ir_text)

        exe = os.path.join(self.td, "unsafe_tagged_int_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "True\n123\n-45\n")

    def test_pcc_unsafe_f64_intrinsics_compile_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write("unsafe_f64_probe.py", """
            from pcc.unsafe import free, load_f64, malloc, store_f64


            def main() -> None:
                p = malloc(8)
                store_f64(p, 0, 2.5)
                print(load_f64(p, 0) + 0.5)
                free(p)


            main()
        """)

        out_ll = os.path.join(self.td, "unsafe_f64_probe.ll")
        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)
        with open(out_ll, "r", encoding="utf-8") as f:
            ir_text = f.read()
        self.assertIn("store double", ir_text)
        self.assertIn("load double", ir_text)

        exe = os.path.join(self.td, "unsafe_f64_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "3.0\n")

    def test_pcc_unsafe_libc_buffer_intrinsics_compile_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write("unsafe_libc_probe.py", """
            from pcc.unsafe import (
                calloc,
                free,
                load_i8,
                malloc,
                memcpy,
                memmove,
                memset,
                ptr_add,
                realloc,
                store_i8,
                write,
            )


            def main() -> None:
                p = calloc(8, 1)
                store_i8(p, 0, load_i8(p, 0) + 79)
                store_i8(p, 1, 75)
                store_i8(p, 2, 10)
                write(1, p, 3)

                memset(p, 65, 4)
                p = realloc(p, 16)
                q = malloc(16)
                memcpy(q, p, 4)
                memmove(ptr_add(q, 2), q, 4)
                store_i8(q, 6, 10)
                write(1, q, 7)

                free(q)
                free(p)


            main()
        """)

        out_ll = os.path.join(self.td, "unsafe_libc_probe.ll")
        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)
        with open(out_ll, "r", encoding="utf-8") as f:
            ir_text = f.read()
        for symbol in ("calloc", "realloc", "memset", "memcpy", "memmove", "write"):
            self.assertIn(f"@{symbol}", ir_text)
        self.assertNotIn("@py_mem_", ir_text)

        exe = os.path.join(self.td, "unsafe_libc_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "OK\nAAAAAA\n")

    def test_pcc_unsafe_env_and_access_intrinsics_compile_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python

        marker = os.path.join(self.td, "unsafe_access_marker.txt")
        with open(marker, "w", encoding="utf-8") as f:
            f.write("present\n")

        src = self._write("unsafe_env_probe.py", f"""
            from pcc.unsafe import (
                access,
                cstr,
                getenv,
                load_i8,
                ptr_is_null,
                setenv,
                strlen,
                unsetenv,
            )


            def main() -> None:
                name = cstr("PCC_UNSAFE_ENV_PROBE")
                setenv(name, cstr("ZX"), 1)
                value = getenv(name)
                print(ptr_is_null(value))
                print(load_i8(value, 0))
                print(strlen(value))
                print(access(cstr({marker!r}), 0))
                unsetenv(name)
                print(ptr_is_null(getenv(name)))


            main()
        """)

        out_ll = os.path.join(self.td, "unsafe_env_probe.ll")
        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)
        with open(out_ll, "r", encoding="utf-8") as f:
            ir_text = f.read()
        for symbol in ("getenv", "setenv", "unsetenv", "strlen", "access"):
            self.assertIn(f"@{symbol}", ir_text)
        self.assertNotIn("@py_mem_", ir_text)

        exe = os.path.join(self.td, "unsafe_env_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "False\n90\n2\n0\nTrue\n")

    def test_pcc_unsafe_external_pointer_globals_compile_without_libpython(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write("unsafe_global_probe.py", """
            from pcc.unsafe import (
                global_addr,
                global_load_ptr,
                global_store_ptr,
                null,
                ptr_is_null,
            )


            def main() -> None:
                print(ptr_is_null(global_load_ptr("py_None")))


            main()
        """)

        out_ll = os.path.join(self.td, "unsafe_global_probe.ll")
        compile_python(src, out_ll, emit_llvm_only=True, libpython_mode="off")
        self.assertTrue(os.path.isfile(out_ll))
        self._assert_no_libpython_fallback_calls(out_ll)
        with open(out_ll, "r", encoding="utf-8") as f:
            ir_text = f.read()
        self.assertIn("@py_None = external global ptr", ir_text)
        self.assertNotIn("@py_mem_", ir_text)

        exe = os.path.join(self.td, "unsafe_global_probe.out")
        compile_python(src, exe, libpython_mode="off")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "False\n")

        store_src = self._write("unsafe_global_store_probe.py", """
            from pcc.unsafe import global_addr, global_store_ptr, null


            def main() -> None:
                global_store_ptr(
                    "pcc_unsafe_test_slot",
                    global_addr("pcc_unsafe_test_storage"),
                )
                global_store_ptr("pcc_unsafe_test_slot", null())


            main()
        """)
        store_ll = os.path.join(self.td, "unsafe_global_store_probe.ll")
        compile_python(
            store_src, store_ll, emit_llvm_only=True, libpython_mode="off",
        )
        self.assertTrue(os.path.isfile(store_ll))
        self._assert_no_libpython_fallback_calls(store_ll)
        with open(store_ll, "r", encoding="utf-8") as f:
            store_ir = f.read()
        self.assertIn("@pcc_unsafe_test_slot = external global ptr", store_ir)
        self.assertIn("@pcc_unsafe_test_storage = external global i8", store_ir)

    def test_resolve_python_config_command_falls_back_to_python3_config(self):
        """When sysconfig yields no executable candidate, pipeline
        should fall back to a bare ``python3-config`` probe."""
        from pcc.py_frontend import pipeline

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("sysconfig.get_config_var", return_value=None):
                resolved = pipeline._resolve_python_config_command()

        self.assertEqual(resolved, "python3-config")


if __name__ == "__main__":
    unittest.main()
