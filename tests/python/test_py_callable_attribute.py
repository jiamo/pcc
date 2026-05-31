from __future__ import annotations

import textwrap

from pcc.py_frontend.pipeline import compile_python


def test_instance_field_callable_call_compiles_to_dynamic_attribute_call(tmp_path):
    src = tmp_path / "callable_attr.py"
    src.write_text(
        textwrap.dedent(
            """
            class Adder:
                def __call__(self, x: int, y: int) -> int:
                    return x + y

            class Box:
                def __init__(self, pyfunc) -> None:
                    self.pyfunc = pyfunc

                def run(self) -> int:
                    return self.pyfunc(20, 22)

            print(Box(Adder()).run())
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "callable_attr.out"

    compile_python(
        str(src),
        str(exe),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    assert exe.exists()


def test_local_callable_shadows_same_named_function_for_codegen(tmp_path):
    src = tmp_path / "local_callable_shadow.py"
    src.write_text(
        textwrap.dedent(
            """
            class Box:
                def put(self, ind, v, mode=None):
                    return v

            def put(a, ind, v, mode="raise"):
                put = a.put
                return put(ind, v, mode=mode)

            print(put(Box(), 0, 7))
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "local_callable_shadow.out"

    compile_python(
        str(src),
        str(exe),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    assert exe.exists()


def test_unhinted_module_global_dunder_new_does_not_bind_unrelated_class(tmp_path):
    src = tmp_path / "foreign_dunder_new.py"
    src.write_text(
        textwrap.dedent(
            """
            ndarray = None

            class recarray:
                def __new__(subtype, shape, buf=None):
                    return ndarray.__new__(
                        subtype, shape, buffer=buf,
                    )

            print("compiled")
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "foreign_dunder_new.out"

    compile_python(
        str(src),
        str(exe),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    assert exe.exists()


def test_dyn_receiver_dunder_new_does_not_bind_unrelated_class_method_self_backend(tmp_path):
    src = tmp_path / "dyn_receiver_dunder_new.py"
    src.write_text(
        textwrap.dedent(
            """
            class MaskedRecords:
                def __new__(cls, shape, dtype=None, mask=None, **options):
                    return cls

            def reconstruct(subtype, data, mask, basetype):
                return subtype.__new__(
                    subtype,
                    data,
                    mask=mask,
                    dtype=basetype,
                )

            print("compiled")
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "dyn_receiver_dunder_new.out"

    compile_python(
        str(src),
        str(exe),
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    assert exe.exists()


def test_native_module_alias_call_does_not_bind_unrelated_class_method(tmp_path):
    src = tmp_path / "module_alias_call_no_class_method_bind.py"
    src.write_text(
        textwrap.dedent(
            """
            import math as N

            class Matrix:
                def sqrt(self, x):
                    return x

            print(N.sqrt(4.0))
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "module_alias_call_no_class_method_bind.out"

    compile_python(
        str(src),
        str(exe),
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    assert exe.exists()
