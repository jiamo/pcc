"""Function-local class statements execute at their source position."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def test_function_local_class_has_per_call_identity_capture_and_errors(
    tmp_path,
    monkeypatch,
    pcc_py_runtime_archive,
):
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(pcc_py_runtime_archive))
    from pcc.py_frontend.pipeline import compile_python

    source = textwrap.dedent(
        """
        events = []

        def record(cls):
            events.append("class")
            return cls

        def reject(cls):
            raise ValueError("class boom")

        def make(value):
            @record
            class Local:
                def get(self):
                    return value
            return Local, Local()

        def explode():
            @reject
            class Bad:
                pass
            print("class-error-missed")

        first_class, first = make(3)
        second_class, second = make(7)
        print(first_class is second_class)
        print(first.get(), second.get())
        print(len(events))
        try:
            explode()
        except ValueError as exc:
            print(str(exc))
        """
    ).lstrip()
    expected = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    ).stdout

    src = tmp_path / "local_class.py"
    exe = tmp_path / "local_class.out"
    src.write_text(source, encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    for backend in range(5):
        run_env = {**os.environ, "PCC_GC_BACKEND": str(backend)}
        result = subprocess.run(
            [str(exe)],
            capture_output=True,
            text=True,
            timeout=20,
            env=run_env,
        )
        assert result.returncode == 0, (
            f"PCC_GC_BACKEND={backend}\n" + result.stderr
        )
        assert result.stdout == expected, f"PCC_GC_BACKEND={backend}"


def test_function_local_class_binding_has_owned_gc_root_shape():
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.codegen.layer1 import L1CodeGen
    from pcc.py_frontend.type_infer import infer_module

    source = textwrap.dedent(
        """
        def make(value):
            class Local:
                def get(self):
                    return value
            return Local, Local()
        """
    ).lstrip()
    typed = infer_module(
        parse_and_lift(source, "local_class_shape.py", "local_class_shape")
    )
    ir_text = str(L1CodeGen(typed, ir_scaffold_mode="on").generate(typed))
    start = ir_text.index(
        "define external ptr @user_local_class_shape_make("
    )
    end = ir_text.index("}\n", start)
    body = ir_text[start:end]

    assert "Local.class.addr" in body
    assert "@pcc_gc_frame_enter" in body
    assert "@pcc_gc_store_root" in body
    assert "@pcc_gc_load_ptr" in body
    assert "@pcc_gc_frame_leave" in body
    assert "_Local__pcc_cap_value" in body
