"""5-GC common production contract: roots survive gc.collect under threads.

Part of the 5-GC Production Equality Rule (codex-goal-prompt.md G-track). The
concurrent backend (#2) is a 5-GC research pillar; threaded root survival must
hold on ALL five backends, exercised with PCC_WITH_THREADS=1.

Each program runs `threading.Thread(target=<module-level fn>)` so the worker
allocates concurrently with the main thread's gc.collect(). `t.join()` makes the
OUTPUT deterministic (no flaky assertions), while the concurrent allocation +
collection still exercises the GC's thread-safety. Both shapes pass on 0..4
(clean contract lock, verified 2026-05-31):

  * root_survives_concurrent_alloc — a main-thread root list survives gc.collect
    while a worker appends 50 lists to a shared global; after join, sum of the
    root + the shared count are exact ("60 50").
  * root_survives_concurrent_churn — a main-thread root survives 5 gc.collect()
    calls while a worker churns 200 short-lived (immediately-dropped) lists,
    stressing the concurrent collector against another thread's garbage ("60").

A crash / wrong value on any backend (especially #2) is a thread-safety or
concurrent-reachability gap and would be xfail'd + root-caused.
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAMS = {
    "root_survives_concurrent_alloc": (
        "import gc\n"
        "import threading\n"
        "shared = []\n"
        "def worker():\n"
        "    i = 0\n"
        "    while i < 50:\n"
        "        shared.append([i, i + 1])\n"
        "        i = i + 1\n"
        "def main():\n"
        "    keep = [10, 20, 30]\n"
        "    t = threading.Thread(target=worker)\n"
        "    t.start()\n"
        "    gc.collect()\n"
        "    t.join()\n"
        "    print(keep[0] + keep[1] + keep[2], len(shared))\n"
        "main()\n",
        "60 50",
    ),
    "root_survives_concurrent_churn": (
        "import gc\n"
        "import threading\n"
        "def worker():\n"
        "    i = 0\n"
        "    while i < 200:\n"
        "        junk = [i, i, i]\n"
        "        junk = 0\n"
        "        i = i + 1\n"
        "def main():\n"
        "    keep = [10, 20, 30]\n"
        "    t = threading.Thread(target=worker)\n"
        "    t.start()\n"
        "    j = 0\n"
        "    while j < 5:\n"
        "        gc.collect()\n"
        "        j = j + 1\n"
        "    t.join()\n"
        "    print(keep[0] + keep[1] + keep[2])\n"
        "main()\n",
        "60",
    ),
}


@pytest.fixture(scope="module")
def _exes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_threaded")
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
def test_threaded_root_survives_gc(_exes, name, backend):
    expected = _PROGRAMS[name][1]
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    env["PCC_WITH_THREADS"] = "1"
    r = subprocess.run([_exes[name]], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"{name} backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [expected], f"{name} #{backend}: {r.stdout!r}"
