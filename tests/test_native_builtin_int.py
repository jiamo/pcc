"""Native dispatch for ``int(x)`` on dynamic runtime values."""
from __future__ import annotations

import subprocess
import textwrap


def test_dyn_int_builtin_handles_tagged_int_without_str_path(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(
        textwrap.dedent(
            """
            def ident(x):
                return x

            def main() -> None:
                print(int(ident(21)))
                print(int(ident(True)))
                print(int(ident(3.7)))
                print(int(ident("42")))

            if __name__ == "__main__":
                main()
            """
        ).lstrip()
    )
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
        timeout=20,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "21\n1\n3\n42\n"
