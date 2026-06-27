from __future__ import annotations

import os
import subprocess


def test_numbers_stdlib_import_and_register_no_libpython(tmp_path):
    source = tmp_path / "main.py"
    source.write_text(
        "import numbers\n"
        "print(issubclass(numbers.Integral, numbers.Number))\n"
        "print(numbers.Integral.register(int) is int)\n",
        encoding="utf-8",
    )
    executable = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    compile_result = subprocess.run(
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
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [str(executable)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run_result.returncode == 0, run_result.stderr
    assert run_result.stdout.splitlines() == ["True", "True"]
