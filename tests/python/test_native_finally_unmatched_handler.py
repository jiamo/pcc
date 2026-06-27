"""``finally`` must run when no ``except`` handler matches, no-libpython.

The try/except/finally lowering branched the unmatched-exception ("propagate")
path straight to the outer error block WITHOUT executing the ``finally`` body —
so a ``finally`` next to a non-matching ``except`` was silently skipped before
the exception propagated (Python guarantees ``finally`` always runs). Fix emits
``finally_body`` on the propagate path too (`exception_lowering.py`). The
bare ``try/finally`` (no handlers) path was already correct.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


def test_finally_runs_on_unmatched_handler_matches_cpython(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "fin.py"
    exe = tmp_path / "fin.out"
    src.write_text(textwrap.dedent("""
        def f_return() -> int:
            try:
                return 1
            finally:
                print("f_return finally")

        def main() -> None:
            try:
                print("normal body")
            finally:
                print("finally-1")
            try:
                raise ValueError("x")
            except ValueError:
                print("handled")
            finally:
                print("finally-2")
            try:
                try:
                    raise KeyError("k")
                except IndexError:
                    print("nope")
                finally:
                    print("finally-3")
            except KeyError:
                print("outer caught k")
            print("ret", f_return())
            try:
                try:
                    raise RuntimeError("r")
                finally:
                    print("finally-5")
            except RuntimeError:
                print("caught r")

        if __name__ == "__main__":
            main()
        """).lstrip(), encoding="utf-8")
    compile_python(
        str(src), str(exe),
        ir_scaffold_mode="on", libpython_mode="off", backend="self",
    )
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=30,
    ).stdout
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == cpython
