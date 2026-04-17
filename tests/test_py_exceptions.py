from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest


class PyExceptionTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="pcc_py_exc_")
        self.addCleanup(shutil.rmtree, self.td, True)

    def _write(self, rel: str, source: str) -> str:
        dst = os.path.join(self.td, rel)
        os.makedirs(os.path.dirname(dst) or self.td, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(source).lstrip())
        return dst

    def test_user_defined_exception_raise_and_catch(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write(
            "user_exc.py",
            """
            import sys

            class MyError(Exception):
                pass

            def boom() -> None:
                raise MyError("boom")

            try:
                boom()
            except MyError as e:
                print(str(e))
            """,
        )
        exe = os.path.join(self.td, "user_exc.out")
        compile_python(src, exe)
        run = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "boom\n")

    def test_unhandled_exception_traceback_has_frames(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write(
            "traceback_frames.py",
            """
            def boom() -> None:
                raise TypeError("boom")

            boom()
            """,
        )
        exe = os.path.join(self.td, "traceback_frames.out")
        compile_python(
            src,
            exe,
            libpython_mode="off",
            ir_scaffold_mode="on",
            backend="self",
        )
        run = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(run.returncode, 1)
        self.assertIn("Traceback (most recent call last):\n", run.stderr)
        self.assertIn('File "' + src + '", line 2, in boom', run.stderr)
        self.assertIn('File "' + src + '", line 4, in <module>', run.stderr)
        self.assertIn("TypeError: boom\n", run.stderr)


if __name__ == "__main__":
    unittest.main()
