from __future__ import annotations

import os
import subprocess


def test_low_level_thread_get_ident_is_native_without_libpython(tmp_path):
    source = tmp_path / "main.py"
    source.write_text(
        "from _thread import get_ident\n"
        "print(get_ident())\n"
        "print(get_ident() == get_ident())\n",
        encoding="utf-8",
    )
    executable = tmp_path / "thread-get-ident"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "cc"
    env["PCC_RUNTIME_HIGH"] = "c"
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
        timeout=30,
        env=env,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr

    run = subprocess.run(
        [str(executable)],
        text=True,
        capture_output=True,
        timeout=10,
        env=env,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1", "True"]
