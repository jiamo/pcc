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


def test_isinstance_tuple_accepts_type_none(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "isinstance_type_none.py"
    src.write_text(textwrap.dedent(
        """
        def check(obj):
            return isinstance(obj, (type(None), str))

        print(check(None))
        print(check("x"))
        print(check(1))
        """
    ))
    exe = tmp_path / "isinstance_type_none.out"
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
    assert run.stdout == "True\nTrue\nFalse\n"


def test_isinstance_accepts_dynamic_type_call(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "isinstance_dynamic_type.py"
    src.write_text(textwrap.dedent(
        """
        class Box:
            def same_type(self, other):
                return isinstance(other, type(self))

        print(Box().same_type(Box()))
        print(Box().same_type(1))
        """
    ))
    exe = tmp_path / "isinstance_dynamic_type.out"
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


def test_isinstance_tuple_accepts_dynamic_type_call(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "isinstance_tuple_dynamic_type.py"
    src.write_text(textwrap.dedent(
        """
        class Box:
            def same_or_text(self, other):
                return isinstance(other, (str, type(self)))

        print(Box().same_or_text(Box()))
        print(Box().same_or_text("x"))
        print(Box().same_or_text(1))
        """
    ))
    exe = tmp_path / "isinstance_tuple_dynamic_type.out"
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
    assert run.stdout == "True\nTrue\nFalse\n"
