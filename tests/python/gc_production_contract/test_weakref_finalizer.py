"""5-GC common production contract: weakref invalidation + finalizer-runs-once
must behave IDENTICALLY under all five GC backends.

Part of the 5-GC Production Equality Rule (docs/goal/goal-prompt.md G-track).
Compiled once under strict no-libpython (``--backend self
--python-libpython=off``) and run under ``PCC_GC_BACKEND`` 0..4; every backend
must produce the same correct output. Finalizer TIMING may differ across
backends, but the POLICY may NOT: a weakref resolves while its referent is
alive and is invalidated once the referent is reclaimed, and ``__del__`` runs at
most once (Rule 8). This is a hard gate on all five backends; any future backend
divergence must be root-caused rather than hidden behind a local xfail.
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "import weakref\n"
    "class Obj:\n"
    "    def __init__(self, n):\n"
    "        self.n = n\n"
    "_dead = []\n"
    "class Fin:\n"
    "    def __del__(self):\n"
    "        _dead.append(1)\n"
    "def main():\n"
    "    o = Obj(7)\n"
    "    r = weakref.ref(o)\n"
    "    print(1 if r() is o else 0)\n"          # weakref resolves while alive
    "    o = None\n"
    "    gc.collect()\n"
    "    print(1 if r() is None else 0)\n"        # weakref invalidated after collect
    "    f = Fin()\n"
    "    f = None\n"
    "    gc.collect()\n"
    "    print(len(_dead))\n"                      # __del__ ran exactly once
    "main()\n"
)
_EXPECTED = ["1", "1", "1"]

_ATTR_PROGRAM = (
    "import gc\n"
    "import weakref\n"
    "class Obj:\n"
    "    def __init__(self, n):\n"
    "        self.n = n\n"
    "def main():\n"
    "    o = Obj(9)\n"
    "    r = weakref.ref(o)\n"
    "    print(r().n)\n"
    "    o = None\n"
    "    gc.collect()\n"
    "    print(1 if r() is None else 0)\n"
    "main()\n"
)
_ATTR_EXPECTED = ["9", "1"]

_ALL_BACKENDS = ("0", "1", "2", "3", "4")


@pytest.fixture(scope="module")
def _wf_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_wf")
    src = tmp / "wf.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "wf_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


@pytest.fixture(scope="module")
def _wf_attr_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_wf_attr")
    src = tmp / "wf_attr.py"
    src.write_text(_ATTR_PROGRAM, encoding="utf-8")
    exe = tmp / "wf_attr_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


@pytest.mark.parametrize("backend", list(_ALL_BACKENDS))
def test_weakref_finalizer_contract(_wf_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_wf_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:3] == _EXPECTED, r.stdout


@pytest.mark.parametrize("backend", list(_ALL_BACKENDS))
def test_weakref_call_intermediate_attr_releases_temp(_wf_attr_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run(
        [_wf_attr_exe],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert r.returncode == 0, f"backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:2] == _ATTR_EXPECTED, r.stdout
