from __future__ import annotations

import subprocess
import textwrap

from pcc.py_frontend.pipeline import compile_python
from pcc.py_frontend.pipeline import compile_python_multi


def test_imported_decorator_factory_call_is_metadata_noop(tmp_path):
    decos = tmp_path / "decos.py"
    decos.write_text(
        textwrap.dedent(
            """
            def metadata_decorator(dispatcher, module=None):
                return dispatcher
            """
        ).lstrip(),
        encoding="utf-8",
    )
    entry = tmp_path / "entry.py"
    entry.write_text(
        textwrap.dedent(
            """
            from decos import metadata_decorator

            def dispatch(value):
                return value

            @metadata_decorator(dispatch, module="entry")
            def value() -> int:
                return 42

            print(value())
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "decorated.out"

    compile_python_multi(
        [str(decos), str(entry)],
        str(exe),
        entry_module="entry",
        module_names=["decos", "entry"],
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "42\n"


def test_module_global_decorator_factory_call_is_metadata_noop(tmp_path):
    entry = tmp_path / "entry.py"
    entry.write_text(
        textwrap.dedent(
            """
            def dispatch(value):
                return value

            decorator_factory = 0

            @decorator_factory(dispatch, module="entry")
            def value() -> int:
                return 43

            print(value())
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "module_global_decorator.out"

    compile_python(
        str(entry),
        str(exe),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "43\n"


def test_noop_decorated_function_with_kwargs_uses_direct_call(tmp_path):
    entry = tmp_path / "entry_kwargs.py"
    entry.write_text(
        textwrap.dedent(
            """
            decorator_factory = 0

            def dispatch(value):
                return value

            @decorator_factory(dispatch, module="entry")
            def value(x: int, y: int = 0) -> int:
                return x + y

            print(value(40, y=4))
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "noop_decorator_kwargs.out"

    compile_python(
        str(entry),
        str(exe),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "44\n"


def test_native_decorated_function_call_accepts_kwargs_for_codegen(tmp_path):
    entry = tmp_path / "native_decorator_kwargs.py"
    entry.write_text(
        textwrap.dedent(
            """
            def identity(fn):
                return fn

            @identity
            def value(x: int, y: int = 0) -> int:
                return x + y

            result = value(40, y=4)
            print(result)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "native_decorator_kwargs.out"

    compile_python(
        str(entry),
        str(exe),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
