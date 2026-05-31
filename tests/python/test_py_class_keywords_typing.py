from __future__ import annotations

import subprocess
import textwrap


def test_typeddict_total_keyword_is_noop_self_backend(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "typeddict_total.py"
    src.write_text(textwrap.dedent(
        """
        class TypedDict:
            pass

        class Base(TypedDict):
            name: str

        class Child(Base, total=False):
            value: int

        print("ok")
        """
    ))
    exe = tmp_path / "typeddict_total.out"
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
    assert run.stdout == "ok\n"
