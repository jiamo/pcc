from __future__ import annotations

import os
import subprocess


def test_runtime_textwrap_dedent_stays_native_for_dynamic_string(tmp_path):
    src = tmp_path / "dynamic_dedent.py"
    src.write_text(
        "import textwrap\n"
        "def normalize(value):\n"
        "    return textwrap.dedent(value)\n"
        "print(normalize('  alpha\\n    beta\\n  \\n') == "
        "'alpha\\n  beta\\n\\n')\n",
        encoding="utf-8",
    )
    exe = tmp_path / "dynamic_dedent"
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
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr

    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=10,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "True\n"
