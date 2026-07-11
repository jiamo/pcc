"""5-GC common production contract: basic object lifetime, cycle reclamation,
and container reachability must behave IDENTICALLY under all five GC backends.

Part of the 5-GC Production Equality Rule (docs/goal/goal-prompt.md G-track): the
SAME Python program is compiled once under strict no-libpython
(``--backend self --python-libpython=off``) and run under ``PCC_GC_BACKEND``
0..4; every backend must produce the same correct output. Performance MAY
differ; these SEMANTICS may NOT.

HISTORY (2026-05-31): this test first surfaced that #1/#2/#3 crashed here
(#3 `[BAD_INCREF] tag=-1` on a basic cycle; #1/#2 abort on nested-container
reachability) while #0/#4 passed — a use-after-free in the tracing collect's
sweep (it cleared+freed each unreachable object in one pass, so a sibling cycle
member's slot pointed at an already-freed object). The two-phase
clear-then-free fix (py_gc_backend.{py,c} _clear_unreachable / _sweep_unreachable
+ pcc_gc_clear_unreachable) made ALL FIVE backends pass; the xfail markers were
dropped (this test is now a hard regression gate that the common contract holds
under 0..4). See
docs/investigations/gc-5backend-object-lifetime-contract-no-libpython.md.
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "class Node:\n"
    "    def __init__(self, name):\n"
    "        self.name = name\n"
    "        self.ref = None\n"
    "def main():\n"
    "    a = Node('a')\n"
    "    print(a.name)\n"
    "    x = Node('x')\n"
    "    y = Node('y')\n"
    "    x.ref = y\n"
    "    y.ref = x\n"
    "    x = None\n"
    "    y = None\n"
    "    gc.collect()\n"
    "    print('cycle-collected')\n"
    "    box = [Node('n0'), [Node('n1')]]\n"
    "    gc.collect()\n"
    "    print(box[0].name, box[1][0].name)\n"
    "main()\n"
)
_EXPECTED = ["a", "cycle-collected", "n0 n1"]


@pytest.fixture(scope="module")
def _lifetime_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_lifetime")
    src = tmp / "lifetime.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "lifetime_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # Compile ONCE; PCC_GC_BACKEND selects the collector at runtime.
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


@pytest.mark.parametrize("backend", ["0", "1", "2", "3", "4"])
def test_object_lifetime_contract(_lifetime_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_lifetime_exe], text=True, capture_output=True,
                       timeout=60, env=env)
    assert r.returncode == 0, f"backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:3] == _EXPECTED, r.stdout
