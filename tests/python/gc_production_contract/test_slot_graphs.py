"""5-GC common production contract: slot-backed instance graphs.

Part of the 5-GC Production Equality Rule (docs/goal/goal-prompt.md G-track).
Slot-backed user instances store references outside the ordinary ``__dict__``
shape, so every backend must trace and preserve those slots the same way it
does ordinary instance attributes. Each program is compiled once under strict
no-libpython and run under PCC_GC_BACKEND 0..4.
"""
from __future__ import annotations

import os
import subprocess

import pytest


_PROGRAMS = {
    "slot_reachable_cycle": (
        "import gc\n"
        "class N:\n"
        "    __slots__ = ('v', 'link')\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "        self.link = None\n"
        "def main():\n"
        "    a = N(1)\n"
        "    b = N(2)\n"
        "    a.link = b\n"
        "    b.link = a\n"
        "    gc.collect()\n"
        "    print(a.link.v + b.link.v)\n"
        "main()\n",
        "3",
    ),
    "slot_mixed_boundary": (
        "import gc\n"
        "collected = []\n"
        "class N:\n"
        "    __slots__ = ('v', 'link')\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "        self.link = None\n"
        "    def __del__(self):\n"
        "        collected.append(self.v)\n"
        "def main():\n"
        "    keep = N(1)\n"
        "    keep.link = N(2)\n"
        "    x = N(100)\n"
        "    y = N(200)\n"
        "    x.link = y\n"
        "    y.link = x\n"
        "    x = None\n"
        "    y = None\n"
        "    gc.collect()\n"
        "    print(keep.v + keep.link.v, sorted(collected))\n"
        "main()\n",
        "3 [100, 200]",
    ),
}


@pytest.fixture(scope="module")
def _exes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_slots")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    built = {}
    for name, (src, _expected) in _PROGRAMS.items():
        path = tmp / f"{name}.py"
        path.write_text(src, encoding="utf-8")
        exe = tmp / f"{name}_bin"
        build = subprocess.run(
            [
                "uv",
                "run",
                "pcc",
                "--backend",
                "self",
                "--python-libpython=off",
                "--ir-scaffold=on",
                str(path),
                "-o",
                str(exe),
            ],
            text=True,
            capture_output=True,
            timeout=420,
            env=env,
        )
        assert build.returncode == 0, f"{name}: {build.stdout + build.stderr}"
        built[name] = str(exe)
    return built


@pytest.mark.parametrize("name", list(_PROGRAMS))
@pytest.mark.parametrize("backend", ["0", "1", "2", "3", "4"])
def test_slot_graph_survives_gc(_exes, name, backend):
    expected = _PROGRAMS[name][1]
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    run = subprocess.run(
        [_exes[name]],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert run.returncode == 0, (
        f"{name} backend #{backend} rc={run.returncode}: "
        f"{run.stderr.strip()[:200]}"
    )
    assert run.stdout.splitlines()[:1] == [expected], (
        f"{name} #{backend}: {run.stdout!r}"
    )
