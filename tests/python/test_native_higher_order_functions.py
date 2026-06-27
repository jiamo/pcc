from __future__ import annotations

import os
import subprocess
import textwrap


def _run_strict_self_backend(tmp_path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    exe = tmp_path / "prog.out"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_user_function_returned_from_function_stays_native_callable(tmp_path):
    out = _run_strict_self_backend(
        tmp_path,
        """
        def add(a, b):
            return a + b


        def pick():
            return add


        def main():
            g = pick()
            print(g(3, 4))


        main()
        """,
    )
    assert out == "7\n"


def test_user_function_passed_as_arg_stays_native_callable(tmp_path):
    out = _run_strict_self_backend(
        tmp_path,
        """
        def add(a, b):
            return a + b


        def apply(fn, a, b):
            return fn(a, b)


        def main():
            print(apply(add, 3, 4))


        main()
        """,
    )
    assert out == "7\n"
