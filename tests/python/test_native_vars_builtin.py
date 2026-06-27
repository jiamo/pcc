"""Native ``vars`` builtin under strict no-libpython."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run_pcc_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog_bin"
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
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_vars_user_object_includes_slots_and_dynamic_attrs_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "class C:\n"
        "    def __init__(self):\n"
        "        self.a = 1\n"
        "        self.b = 'two'\n"
        "\n"
        "def main():\n"
        "    c = C()\n"
        "    setattr(c, 'z', 3)\n"
        "    d = vars(c)\n"
        "    print(d['a'])\n"
        "    print(d['b'])\n"
        "    print(d['z'])\n"
        "\n"
        "main()\n",
    )
    assert out.splitlines() == ["1", "two", "3"]
