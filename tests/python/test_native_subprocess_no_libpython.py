from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_subprocess_check_output_and_check_call_no_libpython(tmp_path: Path):
    src = tmp_path / "prog.py"
    src.write_text(
        "import subprocess\n"
        "def main():\n"
        "    out = subprocess.check_output(['/bin/echo', 'hello'])\n"
        "    print(out.decode().strip())\n"
        "    text = subprocess.check_output(['/bin/echo', 'world'], text=True)\n"
        "    print(text.strip())\n"
        "    print(subprocess.check_call(['/bin/echo', 'check']))\n"
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
    assert run.stdout.splitlines() == ["hello", "world", "check", "0"]
