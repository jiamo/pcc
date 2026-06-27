from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_unresolved_import_raises_native_importerror_no_libpython(tmp_path: Path):
    src = tmp_path / "prog.py"
    src.write_text(
        "def main():\n"
        "    try:\n"
        "        import definitely_missing_for_pcc\n"
        "    except ImportError:\n"
        "        print('missing')\n"
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
    assert run.stdout.splitlines() == ["missing"]
