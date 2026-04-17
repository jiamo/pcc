"""Regression tests for ``compile_python_multi`` — the multi-file
compile infrastructure (#138.5) that feeds the three-stage bootstrap.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from unittest import mock


class MultiFileCompileTests(unittest.TestCase):
    def _run_multi(self, files, entry_module, module_names=None):
        """Write ``files`` (dict of relpath → source) to a tempdir,
        multi-compile them, run the resulting binary, and return
        (exe_path, stdout, exit_code).

        ``files`` is a dict to preserve source file → content mapping;
        the parallel ``module_names`` list (when supplied) tells the
        pipeline the dotted module name each source file should
        simulate. Default: filename stem.
        """
        from pcc.py_frontend.pipeline import compile_python_multi

        td = tempfile.mkdtemp(prefix="pcc_multi_test_")
        self.addCleanup(self._rmtree, td)
        src_paths = []
        for rel, source in files.items():
            dst = os.path.join(td, rel)
            os.makedirs(os.path.dirname(dst) or td, exist_ok=True)
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(textwrap.dedent(source).lstrip())
            src_paths.append(dst)
        exe = os.path.join(td, "a.out")
        compile_python_multi(
            src_paths, exe,
            entry_module=entry_module,
            module_names=module_names,
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        return exe, r.stdout, r.returncode

    def _rmtree(self, path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def test_entry_only_no_siblings(self):
        _, out, code = self._run_multi(
            {"entry.py": "print(1)\nprint(2)\n"},
            entry_module="entry",
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "1\n2\n")

    def test_multi_compile_backend_self_uses_self_link(self):
        from pcc.py_frontend.pipeline import compile_python_multi

        td = tempfile.mkdtemp(prefix="pcc_multi_backend_self_")
        self.addCleanup(self._rmtree, td)
        src = os.path.join(td, "entry.py")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("print(1)\n")
        exe = os.path.join(td, "a.out")
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
                    compile_python_multi(
                        [src],
                        exe,
                        entry_module="entry",
                        backend="self",
                    )

        self_link.assert_called_once()
        clang_link.assert_not_called()

    def test_sibling_top_init_runs_before_entry(self):
        _, out, code = self._run_multi(
            {
                "entry.py": 'print("entry")\n',
                "lib.py":   'print("lib")\n',
            },
            entry_module="entry",
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "lib\nentry\n")

    def test_cross_module_function_call(self):
        files = {
            "entry.py": (
                "from .lib import banner\n"
                'print("start")\n'
                "banner()\n"
                'print("done")\n'
            ),
            "lib.py": (
                "def banner() -> None:\n"
                '    print("banner called")\n'
            ),
        }
        _, out, code = self._run_multi(
            files,
            entry_module="pkg.entry",
            module_names=["pkg.entry", "pkg.lib"],
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "start\nbanner called\ndone\n")

    def test_cross_module_function_with_args(self):
        files = {
            "entry.py": (
                "from .lib import adder\n"
                "print(adder(3, 4))\n"
                "print(adder(10, 20))\n"
            ),
            "lib.py": (
                "def adder(a: int, b: int) -> int:\n"
                "    return a + b\n"
            ),
        }
        _, out, code = self._run_multi(
            files,
            entry_module="pkg.entry",
            module_names=["pkg.entry", "pkg.lib"],
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "7\n30\n")

    def test_no_libpython_dep(self):
        """Produced binaries must link only libSystem / libc++,
        confirming the multi-file path doesn't pull libpython
        through py_cpy_import for native sibling imports."""
        exe, _, code = self._run_multi(
            {
                "entry.py": (
                    "from .lib import greet\n"
                    "greet()\n"
                ),
                "lib.py": (
                    "def greet() -> None:\n"
                    '    print("hi")\n'
                ),
            },
            entry_module="pkg.entry",
            module_names=["pkg.entry", "pkg.lib"],
        )
        self.assertEqual(code, 0)
        # ``otool -L`` on macOS; ``ldd`` on Linux. Keep this check
        # macOS-only for now so CI on either platform is happy.
        if os.uname().sysname != "Darwin":
            self.skipTest("link-lib check is macOS-specific")
        r = subprocess.run(
            ["otool", "-L", exe], capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("Python", r.stdout)
        self.assertNotIn("libpython", r.stdout)

    def test_relative_import_fallback_uses_absolute_module_name(self):
        """Relative ``from . import helper`` should resolve to the
        package name before falling back to CPython import. Otherwise
        codegen emits ``py_cpy_import('')`` and leaves a pending
        ``ValueError`` behind at runtime."""
        from pcc.py_frontend.pipeline import compile_python_multi

        td = tempfile.mkdtemp(prefix="pcc_multi_ir_")
        self.addCleanup(self._rmtree, td)
        entry = os.path.join(td, "entry.py")
        with open(entry, "w", encoding="utf-8") as fh:
            fh.write(
                "def run() -> None:\n"
                "    from . import helper\n\n"
                "run()\n"
            )
        out_ll = os.path.join(td, "entry.ll")
        compile_python_multi(
            [entry],
            out_ll,
            entry_module="pkg.entry",
            module_names=["pkg.entry"],
            emit_llvm_only=True,
        )
        with open(out_ll, "r", encoding="utf-8") as fh:
            ir_text = fh.read()
        self.assertIn("@.cpy.mod.pkg =", ir_text)
        self.assertIn("%cpy.fromimport.pkg", ir_text)
        self.assertNotIn("@.cpy.mod. =", ir_text)

    def test_multi_compile_auto_closes_relative_sibling_sources(self):
        """Explicit multi-file compiles should recursively add missing
        relative-import siblings, so callers can seed just the entry
        module and still keep same-package helpers on the native path."""
        from pcc.py_frontend.pipeline import compile_python_multi

        td = tempfile.mkdtemp(prefix="pcc_multi_auto_closure_")
        self.addCleanup(self._rmtree, td)
        pkg_dir = os.path.join(td, "pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        entry = os.path.join(pkg_dir, "main.py")
        helper = os.path.join(pkg_dir, "helper.py")
        with open(entry, "w", encoding="utf-8") as fh:
            fh.write(
                "from .helper import answer\n\n"
                "print(answer())\n"
            )
        with open(helper, "w", encoding="utf-8") as fh:
            fh.write(
                "def answer() -> int:\n"
                "    return 42\n"
            )

        exe = os.path.join(td, "auto_closure.out")
        compile_python_multi(
            [entry],
            exe,
            entry_module="pkg.main",
            module_names=["pkg.main"],
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "42\n")

        if os.uname().sysname == "Darwin":
            lk = subprocess.run(
                ["otool", "-L", exe], capture_output=True, text=True,
            )
            self.assertEqual(lk.returncode, 0)
            self.assertNotIn("Python", lk.stdout)
            self.assertNotIn("libpython", lk.stdout)

    def test_native_submodule_alias_function_call_stays_native(self):
        """``from .sub import helper`` should keep
        ``helper.answer()`` on the native sibling path when that
        submodule is compiled in the same one-source closure."""
        from pcc.py_frontend.pipeline import compile_python_multi

        td = tempfile.mkdtemp(prefix="pcc_multi_submodule_alias_")
        self.addCleanup(self._rmtree, td)
        pkg_dir = os.path.join(td, "pkg")
        sub_dir = os.path.join(pkg_dir, "sub")
        os.makedirs(sub_dir, exist_ok=True)
        entry = os.path.join(pkg_dir, "main.py")
        sub_init = os.path.join(sub_dir, "__init__.py")
        helper = os.path.join(sub_dir, "helper.py")
        with open(entry, "w", encoding="utf-8") as fh:
            fh.write(
                "from .sub import helper\n\n"
                "print(helper.answer())\n"
            )
        with open(sub_init, "w", encoding="utf-8") as fh:
            fh.write("")
        with open(helper, "w", encoding="utf-8") as fh:
            fh.write(
                "def answer() -> int:\n"
                "    return 7\n"
            )

        exe = os.path.join(td, "submodule_alias.out")
        compile_python_multi(
            [entry],
            exe,
            entry_module="pkg.main",
            module_names=["pkg.main"],
        )
        r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "7\n")


if __name__ == "__main__":
    unittest.main()
