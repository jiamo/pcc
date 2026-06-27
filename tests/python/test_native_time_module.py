"""Native ``time`` module calls under strict no-libpython."""
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


def test_time_time_and_perf_counter_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "import time\n"
        "from time import time as now, perf_counter\n"
        "\n"
        "def main():\n"
        "    print(time.time() > 1000000000.0)\n"
        "    print(now() > 1000000000.0)\n"
        "    print(time.perf_counter() >= 0.0)\n"
        "    print(perf_counter() >= 0.0)\n"
        "    print(time.monotonic() >= 0.0)\n"
        "main()\n",
    )
    assert out.splitlines() == ["True", "True", "True", "True", "True"]
