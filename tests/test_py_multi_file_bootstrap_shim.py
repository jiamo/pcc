"""Integration-style test: multi-module program that mirrors the
``pcc/__main__.py`` → ``pcc/pcc.py`` bootstrap shape but without
click or any external deps — verifies the multi-file compile
infrastructure can produce a native binary that calls a sibling
module's entry function from ``__main__.py``'s top-level body.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest


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


if __name__ == "__main__":
    unittest.main()
