"""Regression tests for ``compile_python_multi`` — the multi-file
compile infrastructure (#138.5) that feeds the three-stage bootstrap.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest


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


if __name__ == "__main__":
    unittest.main()
