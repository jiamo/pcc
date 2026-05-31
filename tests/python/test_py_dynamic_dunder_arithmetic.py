from __future__ import annotations

import textwrap


def test_call_result_arithmetic_compiles_dynamic_dunder_self_backend(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "dynamic_dunder_arithmetic.py"
    src.write_text(textwrap.dedent(
        """
        class Box:
            def __add__(self, other):
                return 5

        def make():
            return Box()

        print(make() + Box())
        """
    ))
    exe = tmp_path / "dynamic_dunder_arithmetic.out"
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
        backend="self",
    )
    assert exe.exists()
