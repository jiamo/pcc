from __future__ import annotations

import subprocess
import textwrap


def test_dynamic_isinstance_second_arg_dispatches_to_runtime(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "dynamic_isinstance.py"
    src.write_text(textwrap.dedent(
        """
        class A:
            pass

        class B:
            pass

        def check(obj, cls):
            return isinstance(obj, cls)

        print(check(A(), A))
        print(check(B(), A))
        """
    ))
    exe = tmp_path / "dynamic_isinstance.out"
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
    assert run.stdout == "True\nFalse\n"


def test_dyn_builtin_isinstance_uses_runtime_type_tag(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "builtin_dyn_isinstance.py"
    src.write_text(textwrap.dedent(
        """
        def check_tuple(obj):
            return isinstance(obj, tuple)

        print(check_tuple((1, 2)))
        print(check_tuple([1, 2]))
        """
    ))
    exe = tmp_path / "builtin_dyn_isinstance.out"
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
    assert run.stdout == "True\nFalse\n"
