"""5-GC common production contract: object resurrection inside __del__ (PEP 442).

Part of the 5-GC Production Equality Rule (codex-goal-prompt.md G-track).

If a finalizer (__del__) makes its object reachable again — e.g. stores `self`
into a still-reachable global — that object MUST survive the collection intact
(PEP 442: after finalizers run, the collector re-checks reachability and does
NOT reclaim resurrected objects). CPython and #0 (refcount+cycle) do this.

RESOLVED 2026-05-31 by a PEP-442 post-finalizer reachability recheck in
`_sweep_unreachable` (re-mark from roots after PASS-0; clear the 1024
sweep-candidate flag on any object a __del__ resurrected, so PASS-1/PASS-2 skip
it). Before the fix the tracing backends mishandled resurrection — #1/#2 aborted
(heap corruption: a resurrected object freed while still referenced by the
global -> double-free), #3/#4 raised AttributeError (its fields were cleared);
#0 was correct. Now all five backends keep the resurrected objects intact
(`2 [42, 43]`). Hard gate on 0..4. See
docs/investigations/gc-5backend-finalizer-resurrection-no-libpython.md.
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "keeper = []\n"
    "class R:\n"
    "    def __init__(self, v):\n"
    "        self.v = v\n"
    "        self.peer = None\n"
    "    def __del__(self):\n"
    "        keeper.append(self)\n"
    "def main():\n"
    "    a = R(42)\n"
    "    b = R(43)\n"
    "    a.peer = b\n"
    "    b.peer = a\n"
    "    a = 0\n"
    "    b = 0\n"
    "    gc.collect()\n"
    "    n = len(keeper)\n"
    "    vals = []\n"
    "    i = 0\n"
    "    while i < n:\n"
    "        vals.append(keeper[i].v)\n"
    "        i = i + 1\n"
    "    print(n, sorted(vals))\n"
    "main()\n"
)
_EXPECTED = "2 [42, 43]"
_XFAIL: set = set()  # was {1,2,3,4}; PEP-442 recheck landed -> hard gate on 0..4


@pytest.fixture(scope="module")
def _exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_resurrect")
    src = tmp / "resurrect.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "resurrect_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    return str(exe)


def _params():
    out = []
    for b in ("0", "1", "2", "3", "4"):
        if b in _XFAIL:
            out.append(pytest.param(b, marks=pytest.mark.xfail(
                reason="two-phase sweep clears/frees objects resurrected in __del__ "
                       "(PEP-442 reachability recheck not yet implemented)",
                strict=False)))
        else:
            out.append(b)
    return out


@pytest.mark.parametrize("backend", _params())
def test_resurrected_object_survives_gc(_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [_EXPECTED], r.stdout
