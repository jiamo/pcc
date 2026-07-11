"""5-GC common production contract: gc.collect() reentrancy from a finalizer.

Part of the 5-GC Production Equality Rule (docs/goal/goal-prompt.md G-track).

A finalizer (__del__) that calls gc.collect() while a collection is already in
progress must be SAFE on all five backends: the reentrant collect is a no-op
(CPython `gc.collecting` semantics) and the outer collection still runs every
finalizer and reclaims every unreachable object. Output must match across 0..4.

REGRESSION (found + fixed 2026-05-31 by this contract): when the finalizers
belong to an unreachable reference CYCLE (so they are reclaimed by the tracing
collect itself, and their __del__ runs DURING the outer sweep's PASS-0), the
reentrant gc.collect() used to SEGFAULT on #1/#2/#3/#4 — the reentrant mark
re-whitened objects the outer `_sweep_unreachable` was mid-iteration on
(clobbering its GC_SWEEP_CANDIDATE flags) and the reentrant sweep freed nodes
the outer sweep still held (use-after-free). Fixed by a reentrancy guard in
`pcc_gc_collect` (a collect already in progress -> reentrant call returns 0).
See docs/investigations/gc-5backend-reentrant-collect-during-finalizer-no-libpython.md.
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAMS = {
    # finalizers on plain (non-cycle) objects re-enter gc.collect
    "plain_reentrant": (
        "import gc\n"
        "done = []\n"
        "class Fin:\n"
        "    def __init__(self, tag):\n"
        "        self.tag = tag\n"
        "    def __del__(self):\n"
        "        done.append(self.tag)\n"
        "        gc.collect()\n"
        "def main():\n"
        "    f1 = Fin(1)\n"
        "    f2 = Fin(2)\n"
        "    f1 = 0\n"
        "    f2 = 0\n"
        "    gc.collect()\n"
        "    print(sorted(done))\n"
        "main()\n",
        "[1, 2]",
    ),
    # finalizers on members of an unreachable CYCLE re-enter gc.collect DURING
    # the outer sweep — the case that segfaulted before the reentrancy guard
    "cycle_reentrant": (
        "import gc\n"
        "done = []\n"
        "class Fin:\n"
        "    def __init__(self, tag):\n"
        "        self.tag = tag\n"
        "        self.peer = None\n"
        "    def __del__(self):\n"
        "        done.append(self.tag)\n"
        "        gc.collect()\n"
        "def main():\n"
        "    a = Fin(1)\n"
        "    b = Fin(2)\n"
        "    a.peer = b\n"
        "    b.peer = a\n"
        "    a = 0\n"
        "    b = 0\n"
        "    gc.collect()\n"
        "    print(sorted(done))\n"
        "main()\n",
        "[1, 2]",
    ),
}


@pytest.fixture(scope="module")
def _exes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_reentrancy")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    built = {}
    for name, (src, _expected) in _PROGRAMS.items():
        p = tmp / f"{name}.py"
        p.write_text(src, encoding="utf-8")
        exe = tmp / f"{name}_bin"
        b = subprocess.run(
            ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
             "--ir-scaffold=on", str(p), "-o", str(exe)],
            text=True, capture_output=True, timeout=420, env=env,
        )
        assert b.returncode == 0, f"{name}: {b.stdout + b.stderr}"
        built[name] = str(exe)
    return built


@pytest.mark.parametrize("name", list(_PROGRAMS))
@pytest.mark.parametrize("backend", ["0", "1", "2", "3", "4"])
def test_gc_collect_reentrancy_is_safe(_exes, name, backend):
    expected = _PROGRAMS[name][1]
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_exes[name]], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"{name} backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [expected], f"{name} #{backend}: {r.stdout!r}"
