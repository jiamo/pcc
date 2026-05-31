from __future__ import annotations

import subprocess
import textwrap


def test_call_to_function_with_unused_varargs_does_not_overrun_abi(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "varargs_call_abi.py"
    src.write_text(textwrap.dedent(
        """
        def visit(value, *args, **kwargs):
            print(value)

        visit(7, 8, named=9)
        """
    ))
    exe = tmp_path / "varargs_call_abi.out"
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
        backend="self",
    )
    run = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "7\n"
