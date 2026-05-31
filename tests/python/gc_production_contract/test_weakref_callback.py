"""5-GC common production contract: weakref callback fires on referent collection.

Part of the 5-GC Production Equality Rule (codex-goal-prompt.md G-track).

A weakref created with a callback — `weakref.ref(obj, cb)` — must, when its
referent is reclaimed, (a) run the callback exactly once and (b) thereafter
resolve to None. This is a dispatch-during-collection hazard in the same family
as finalizers (the callback runs as the referent dies). It must behave
identically on all five backends.

This shape passes on ALL FIVE backends (clean contract lock, verified
2026-05-31): dropping the only strong ref + gc.collect() fires the callback once
and invalidates the weakref (`1 True`), matching CPython. Complements
test_weakref_finalizer.py (which locks resolve-while-alive / invalidate /
__del__-once).

NOTE: a separate, narrow refcount-discipline bug — a weakref-call result used as
an intermediate attribute-access receiver (`r().v`) leaks the temporary strong
ref on #0 (refcount), keeping the referent alive — is tracked apart from this
contract in docs/investigations/gc-weakref-call-intermediate-attr-refleak-no-libpython.md.
This test deliberately binds/uses the resolve result so it does not depend on
that leak.
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "import weakref\n"
    "log = []\n"
    "def cb(ref):\n"
    "    log.append(1)\n"
    "class T:\n"
    "    def __init__(self, v):\n"
    "        self.v = v\n"
    "def main():\n"
    "    t = T(5)\n"
    "    r = weakref.ref(t, cb)\n"
    "    t = 0\n"
    "    gc.collect()\n"
    "    print(len(log), r() is None)\n"
    "main()\n"
)
_EXPECTED = "1 True"


@pytest.fixture(scope="module")
def _exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_wrcb")
    src = tmp / "wrcb.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "wrcb_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    return str(exe)


@pytest.mark.parametrize("backend", ["0", "1", "2", "3", "4"])
def test_weakref_callback_fires_on_collection(_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [_EXPECTED], f"#{backend}: {r.stdout!r}"
