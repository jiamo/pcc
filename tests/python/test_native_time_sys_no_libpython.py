from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_time_strftime_datetime_encode_and_stdin_no_libpython(tmp_path: Path):
    src = tmp_path / "prog.py"
    src.write_text(
        "import datetime, sys, time\n"
        "def main():\n"
        "    year = time.strftime('%Y')\n"
        "    print(len(year), year.isdigit())\n"
        "    stamp = datetime.datetime.now().strftime('%Y-%m-%d').encode()\n"
        "    print(stamp.decode().count('-'))\n"
        "    print(sys.stdin.readline().strip())\n"
        "main()\n",
        encoding="utf-8",
    )
    exe = tmp_path / "prog"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
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
        input="typed\n",
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["4 True", "2", "typed"]


def test_sys_standard_stream_attrs_are_native_values(tmp_path: Path):
    src = tmp_path / "prog.py"
    src.write_text(
        "import sys\n"
        "def accept(stream):\n"
        "    print(stream is not None)\n"
        "def main():\n"
        "    accept(sys.stdin)\n"
        "    accept(sys.stdout)\n"
        "    accept(sys.stderr)\n"
        "main()\n",
        encoding="utf-8",
    )
    exe = tmp_path / "prog"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
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
    assert run.stdout.splitlines() == ["True", "True", "True"]
