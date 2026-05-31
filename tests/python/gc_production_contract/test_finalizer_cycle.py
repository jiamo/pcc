"""5-GC common production contract: __del__ finalizers on members of an
unreachable reference CYCLE must run (CPython PEP 442) under all five backends.

Part of the 5-GC Production Equality Rule (codex-goal-prompt.md G-track).
Compiled once under strict no-libpython and run under PCC_GC_BACKEND 0..4.

HISTORY (2026-05-31): this contract test first surfaced that only #0 ran
cycle-member finalizers; #1/#2/#3/#4 (the tracing collect) reclaimed the cycle
WITHOUT running __del__ (the dispatch happened only in the PASS-2 dealloc, AFTER
PASS-1 had cleared the fields). The fix adds a PASS-0 to the tracing sweep
(py_gc_backend.{py,c}) that runs py_user_del_dispatch on unreachable members
BEFORE clearing — __del__ now sees intact fields and PY_FLAG_FINALIZED prevents
the PASS-2 re-run. ALL FIVE backends now produce `a,b`; the xfail markers were
dropped (hard regression gate). See
docs/investigations/gc-5backend-cycle-finalizer-not-run-no-libpython.md.
(Resurrection inside __del__ — making a cycle member reachable again — is a
documented follow-up: PASS 1 still clears, so a resurrected member is mishandled;
not exercised here.)
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "_log = []\n"
    "class Node:\n"
    "    def __init__(self, name):\n"
    "        self.name = name\n"
    "        self.ref = None\n"
    "    def __del__(self):\n"
    "        _log.append(self.name)\n"
    "def main():\n"
    "    a = Node('a')\n"
    "    b = Node('b')\n"
    "    a.ref = b\n"
    "    b.ref = a\n"
    "    a = None\n"
    "    b = None\n"
    "    gc.collect()\n"
    "    _log.sort()\n"
    "    print(','.join(_log))\n"          # CPython: 'a,b' (PEP 442)
    "main()\n"
)
_EXPECTED = "a,b"


@pytest.fixture(scope="module")
def _cycfin_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_cycfin")
    src = tmp / "cycfin.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "cycfin_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


@pytest.mark.parametrize("backend", ["0", "1", "2", "3", "4"])
def test_cycle_member_finalizers_run(_cycfin_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_cycfin_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [_EXPECTED], r.stdout
