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

    def test_user_defined_exception_catches_via_builtin_base(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write(
            "user_exc_base.py",
            """
            class MyError(Exception):
                pass

            def boom() -> None:
                raise MyError("boom")

            try:
                boom()
            except Exception as e:
                print("caught " + str(e))
            """,
        )
        exe = os.path.join(self.td, "user_exc_base.out")
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
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stdout, "caught boom\n")

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

    def test_nested_function_except_as_name_is_local_binding(self):
        from pcc.py_frontend.pipeline import compile_python

        src = self._write(
            "nested_except_binding.py",
            """
            def outer() -> None:
                def inner() -> None:
                    try:
                        print(1)
                    except Exception as exc:
                        print(exc)

                inner()

            outer()
            """,
        )
        ll_path = os.path.join(self.td, "nested_except_binding.ll")
        compile_python(src, ll_path, emit_llvm_only=True)
        self.assertTrue(os.path.isfile(ll_path))

    def test_class_without_explicit_init_does_not_emit_phantom_call(self):
        # Regression: a class that subclasses a builtin (Exception) and does
        # NOT define __init__, when instantiated with args (e.g. raised with a
        # message), used to make pcc synthesise a phantom @user_<module>_
        # <class>___init__ call symbol via _method_symbol. That symbol was
        # never emitted (no body __init__ exists to lower), producing a hard
        # link error "Undefined symbols ... _user_..._MAError___init__". This
        # capped the numpy auto-mode compile right after the .owned.N
        # generator-emission cap closed (numpy.ma.mrecords.MAError /
        # numpy.testing._private.utils._Dummy both pass-only). The fix skips
        # the phantom call at both emission sites (class_gen.py:5566,
        # native_modules.py:2063); args are emitted from already-owned slots
        # whose lifecycle is managed by their slot ownership, so this is safe.
        # See investigation
        # python-class-init-phantom-symbol-link-fail.md.
        from pcc.py_frontend.pipeline import compile_python

        src = self._write(
            "no_init_exception.py",
            """
            class MyError(Exception):
                pass

            class Marker:
                pass

            def main() -> None:
                # No-Exception class with no __init__, called with no args.
                m = Marker()
                # Exception subclass with no __init__, raised + caught.
                try:
                    raise MyError("bang")
                except MyError:
                    print("caught")
                print("done")

            if __name__ == "__main__":
                main()
            """,
        )
        exe = os.path.join(self.td, "no_init_exception.out")
        compile_python(
            src,
            exe,
            libpython_mode="off",
            ir_scaffold_mode="on",
            backend="self",
        )
        run = subprocess.run(
            [exe], capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip().splitlines(), ["caught", "done"])


if __name__ == "__main__":
    unittest.main()
