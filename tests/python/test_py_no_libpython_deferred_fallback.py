from __future__ import annotations

import subprocess
import textwrap

from pcc.py_frontend.pipeline import compile_python


def test_deferred_cpython_fallback_becomes_native_error_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    src = tmp_path / "deferred_fallback.py"
    src.write_text(
        textwrap.dedent("""
            def deferred(value):
                return locals()

            print("imported")
            try:
                deferred("1")
            except NotImplementedError as exc:
                print(str(exc))
            """),
        encoding="utf-8",
    )
    exe = tmp_path / "deferred_fallback.out"

    compile_python(
        str(src),
        str(exe),
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "imported",
        "no-libpython function unavailable: deferred_fallback.deferred",
    ]


def test_deferred_method_and_generator_fallbacks_become_native_stubs(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    monkeypatch.setenv("PCC_RUNTIME_HIGH", "c")
    src = tmp_path / "deferred_shapes.py"
    src.write_text(
        textwrap.dedent("""
            class Deferred:
                def method(self, value):
                    return locals()

            def generator(value):
                yield locals()

            try:
                Deferred().method("1")
            except NotImplementedError as exc:
                print(str(exc))

            try:
                next(generator("1"))
            except NotImplementedError as exc:
                print(str(exc))
            """),
        encoding="utf-8",
    )
    exe = tmp_path / "deferred_shapes.out"

    compile_python(
        str(src),
        str(exe),
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "no-libpython function unavailable: deferred_shapes.method",
        "no-libpython function unavailable: deferred_shapes.generator",
    ]
