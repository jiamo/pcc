"""5-GC common production contract: the collection BOUNDARY + scale.

Part of the 5-GC Production Equality Rule (docs/goal/goal-prompt.md G-track).

A collection must keep everything reachable from a root and reclaim ONLY what is
unreachable — the boundary between the two must be exact on all five backends,
and must hold at scale. These two shapes pass on ALL FIVE backends (clean
contract lock, verified 2026-05-31):

  * mixed_boundary — a reachable chain (keep -> N(2)) coexists with an
    unreachable cycle (x <-> y). After gc.collect the reachable chain survives
    intact (its __del__ does NOT run) and the unreachable cycle is reclaimed
    (its __del__ DOES run). Output: "3 [100, 200]" (CPython-identical). This is
    the precise partial-collection boundary — neither over-collecting the
    reachable side nor leaking the unreachable side.
  * large_graph — 200 reachable instances survive gc.collect while 200
    unreachable self-cycle objects are reclaimed in the same collection
    (stresses the sweep over many candidates). Output: "19900".

A divergence on any backend (wrong sum, crash, or a survivor's __del__ firing /
a victim's __del__ not firing) is a reachability/boundary or scale gap and would
be xfail'd + root-caused like the prior gaps.
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAMS = {
    # reachable chain survives intact; unreachable cycle is reclaimed
    "mixed_boundary": (
        "import gc\n"
        "collected = []\n"
        "class N:\n"
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
        "    x = 0\n"
        "    y = 0\n"
        "    gc.collect()\n"
        "    print(keep.v + keep.link.v, sorted(collected))\n"
        "main()\n",
        "3 [100, 200]",
    ),
    # 200 reachable survive + 200 unreachable self-cycles reclaimed (scale)
    "large_graph": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "def main():\n"
        "    items = []\n"
        "    i = 0\n"
        "    while i < 200:\n"
        "        items.append(N(i))\n"
        "        i = i + 1\n"
        "    j = 0\n"
        "    while j < 200:\n"
        "        junk = N(-1)\n"
        "        junk.self = junk\n"
        "        junk = 0\n"
        "        j = j + 1\n"
        "    gc.collect()\n"
        "    total = 0\n"
        "    k = 0\n"
        "    while k < len(items):\n"
        "        total = total + items[k].v\n"
        "        k = k + 1\n"
        "    print(total)\n"
        "main()\n",
        "19900",
    ),
}


@pytest.fixture(scope="module")
def _exes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_mixed")
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
def test_collection_boundary_and_scale(_exes, name, backend):
    expected = _PROGRAMS[name][1]
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_exes[name]], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"{name} backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [expected], f"{name} #{backend}: {r.stdout!r}"
