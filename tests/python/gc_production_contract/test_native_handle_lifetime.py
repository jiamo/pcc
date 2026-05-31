"""5-GC common production contract: native file-handle lifetime.

Part of the 5-GC Production Equality Rule (codex-goal-prompt.md G-track). A
native runtime handle wrapped in a Python object must keep working while the
wrapper is reachable, and must release the native resource when the wrapper is
dropped. This test uses the strict no-libpython file-object path, compiles once,
and then runs under PCC_GC_BACKEND 0..4.

The first shape drops an unclosed write handle, then forces gc.collect() before
reopening the path. The read must see the written data, which requires the file
object deallocator to close/flush the underlying FILE*. The second shape keeps a
write handle alive across gc.collect(), writes again, then closes explicitly.
"""
from __future__ import annotations

import os
import subprocess

import pytest


def _program(data_path: str) -> str:
    return (
        "import gc\n"
        f"PATH = {data_path!r}\n"
        "\n"
        "def drop_unclosed_writer():\n"
        "    f = open(PATH, 'w', encoding='utf-8')\n"
        "    f.write('alpha')\n"
        "    f = None\n"
        "    gc.collect()\n"
        "\n"
        "def live_handle_collect():\n"
        "    f = open(PATH, 'w', encoding='utf-8')\n"
        "    f.write('one')\n"
        "    gc.collect()\n"
        "    f.write('two')\n"
        "    f.close()\n"
        "\n"
        "def main():\n"
        "    drop_unclosed_writer()\n"
        "    with open(PATH, 'r', encoding='utf-8') as r:\n"
        "        print(r.read())\n"
        "    live_handle_collect()\n"
        "    with open(PATH, 'r', encoding='utf-8') as r:\n"
        "        print(r.read())\n"
        "\n"
        "main()\n"
    )


@pytest.fixture(scope="module")
def _native_handle_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_native_handle")
    data = tmp / "native-handle.txt"
    src = tmp / "native_handle.py"
    src.write_text(_program(str(data)), encoding="utf-8")
    exe = tmp / "native_handle_bin"
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
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


@pytest.mark.parametrize("backend", ["0", "1", "2", "3", "4"])
def test_native_file_handle_lifetime(_native_handle_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    run = subprocess.run(
        [_native_handle_exe],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert run.returncode == 0, (
        f"backend #{backend} rc={run.returncode}: {run.stderr.strip()[:200]}"
    )
    assert run.stdout.splitlines()[:2] == ["alpha", "onetwo"], run.stdout
