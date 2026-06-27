"""Strict native ``ast.literal_eval`` integer/tuple provider surface."""
from __future__ import annotations

import os
import subprocess


def test_ast_literal_eval_integer_tuple_subset_no_libpython(tmp_path):
    source = tmp_path / "prog.py"
    source.write_text(
        "import ast\n"
        "print(ast.literal_eval('2'))\n"
        "print(ast.literal_eval(' (2) '))\n"
        "print(ast.literal_eval('(2,)'))\n"
        "print(ast.literal_eval('(2, 3)'))\n"
        "print(ast.literal_eval('2, 3'))\n"
        "print(ast.literal_eval('()'))\n"
        "try:\n"
        "    ast.literal_eval('(2,,3)')\n"
        "except ValueError:\n"
        "    print('invalid')\n",
        encoding="utf-8",
    )
    executable = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "cc"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=20, env=env
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "2",
        "2",
        "(2,)",
        "(2, 3)",
        "(2, 3)",
        "()",
        "invalid",
    ]
