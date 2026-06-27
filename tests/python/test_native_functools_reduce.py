from __future__ import annotations

import os
import subprocess


def _run(tmp_path, source):
    src = tmp_path / "p.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "p_bin"
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


def test_functools_reduce_lambda_native_no_libpython(tmp_path):
    out = _run(
        tmp_path,
        "import functools\n"
        "def main():\n"
        "    print(functools.reduce(lambda x, y: x + y, [1, 2, 3, 4]))\n"
        "    print(functools.reduce(lambda x, y: x + y, [1, 2, 3], 10))\n"
        "main()\n",
    )
    assert out.splitlines() == ["10", "16"]


def test_functools_reduce_empty_without_initial_raises(tmp_path):
    out = _run(
        tmp_path,
        "import functools\n"
        "def main():\n"
        "    try:\n"
        "        functools.reduce(lambda x, y: x + y, [])\n"
        "    except TypeError:\n"
        "        print('type')\n"
        "main()\n",
    )
    assert out.splitlines() == ["type"]
