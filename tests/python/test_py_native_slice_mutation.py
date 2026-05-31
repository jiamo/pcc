from __future__ import annotations

import subprocess
import textwrap

from pcc.py_frontend.pipeline import compile_python


def test_list_slice_assignment_and_delete_stay_native(tmp_path):
    src = tmp_path / "slice_mutation.py"
    src.write_text(
        textwrap.dedent(
            """
            xs = [1, 2, 3, 4]
            xs[1:3] = [8, 9, 10]
            print(len(xs))
            print(xs[0])
            print(xs[1])
            print(xs[3])
            del xs[1:3]
            print(len(xs))
            print(xs[1])
            xs[2:1] = [11]
            print(len(xs))
            print(xs[2])
            print(xs[3])
            """
        ),
        encoding="utf-8",
    )
    exe = tmp_path / "slice_mutation.out"

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
    assert run.stdout == "5\n1\n8\n10\n3\n10\n4\n11\n4\n"


def test_list_extended_slice_assignment_and_delete_stay_native(tmp_path):
    src = tmp_path / "extended_slice_mutation.py"
    src.write_text(
        textwrap.dedent(
            """
            xs = [0, 1, 2, 3, 4, 5]
            xs[1:6:2] = [7, 8, 9]
            print(xs[1])
            print(xs[3])
            print(xs[5])
            del xs[::2]
            print(len(xs))
            print(xs[0])
            print(xs[1])
            """
        ),
        encoding="utf-8",
    )
    exe = tmp_path / "extended_slice_mutation.out"

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
    assert run.stdout == "7\n8\n9\n3\n7\n8\n"
