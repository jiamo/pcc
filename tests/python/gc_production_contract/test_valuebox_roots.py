"""5-GC common production contract: valuebox pointer payload roots.

Part of the 5-GC Production Equality Rule (codex-goal-prompt.md G-track) and
the value-model obligation: a boxed valueclass that crosses an object boundary
must trace pointer-bearing payload fields under every backend.

The program boxes ``Bag(items: list, label: str, count: int)`` through an
``Any`` boundary, runs gc.collect(), mutates the payload list through the boxed
object, runs gc.collect() again, then reads the same fields back through dynamic
attribute access. A crash or wrong output means the valuebox object failed to
retain/update one of its pointer payloads.
"""
from __future__ import annotations

import os
import subprocess

import pytest


_PROGRAM = (
    "import gc\n"
    "import pcc\n"
    "from typing import Any\n"
    "\n"
    "@pcc.valueclass\n"
    "class Bag:\n"
    "    items: list\n"
    "    label: str\n"
    "    count: int\n"
    "\n"
    "def ident(x: Any) -> Any:\n"
    "    return x\n"
    "\n"
    "def main():\n"
    "    bag = Bag([1, 2, 3], 'bag', 4)\n"
    "    d = ident(bag)\n"
    "    gc.collect()\n"
    "    d.items.append(5)\n"
    "    gc.collect()\n"
    "    print(len(d.items))\n"
    "    print(d.label)\n"
    "    print(d.items[3])\n"
    "    print(d.count)\n"
    "\n"
    "main()\n"
)
_EXPECTED = ["4", "bag", "5", "4"]


@pytest.fixture(scope="module")
def _valuebox_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_valuebox")
    src = tmp / "valuebox.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "valuebox_bin"
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
def test_valuebox_pointer_payload_survives_gc(_valuebox_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    run = subprocess.run(
        [_valuebox_exe],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert run.returncode == 0, (
        f"backend #{backend} rc={run.returncode}: {run.stderr.strip()[:200]}"
    )
    assert run.stdout.splitlines()[:4] == _EXPECTED, run.stdout
