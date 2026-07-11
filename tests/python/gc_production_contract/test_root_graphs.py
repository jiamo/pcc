"""5-GC common production contract: reachability through frames + containers.

Part of the 5-GC Production Equality Rule (docs/goal/goal-prompt.md G-track).
Each program is compiled once under strict no-libpython and run under
PCC_GC_BACKEND 0..4; every backend must keep the reachable objects alive across
gc.collect() and produce the same correct output.

These three shapes pass on ALL FIVE backends (clean contract locks, verified
2026-05-31): a suspended generator frame's local survives gc.collect; a nested
dict->list->instance container graph survives + stays mutable; a set of
custom-__hash__/__eq__ instances survives. The earlier caught-exception
referent gap is now resolved and locked separately in test_exception_roots.py.
"""
from __future__ import annotations
import os
import subprocess

import pytest

pytestmark = pytest.mark.xdist_group(name="gc_root_graphs")

_PROGRAMS = {
    # suspended generator frame local must survive gc.collect (frame roots)
    "generator_frame": (
        "import gc\n"
        "def gen():\n"
        "    obj = [10, 20, 30]\n"
        "    yield 0\n"
        "    yield obj[0] + obj[1] + obj[2]\n"
        "def main():\n"
        "    g = gen()\n"
        "    next(g)\n"
        "    gc.collect()\n"
        "    print(next(g))\n"
        "main()\n",
        "60",
    ),
    # nested dict -> list -> instance graph survives + stays mutable
    "container_graph": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "def main():\n"
        "    d = {'k': [N(1), N(2)]}\n"
        "    gc.collect()\n"
        "    d['k'].append(N(3))\n"
        "    gc.collect()\n"
        "    print(sum(x.v for x in d['k']))\n"
        "main()\n",
        "6",
    ),
    # set of custom-hash/eq instances survives gc.collect
    "set_graph": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "    def __hash__(self):\n"
        "        return self.v\n"
        "    def __eq__(self, o):\n"
        "        return self.v == o.v\n"
        "def main():\n"
        "    s = set()\n"
        "    s.add(N(1))\n"
        "    s.add(N(2))\n"
        "    gc.collect()\n"
        "    print(sum(x.v for x in s))\n"
        "main()\n",
        "3",
    ),
}


@pytest.fixture(scope="module")
def _exes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_roots")
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
def test_root_graph_survives_gc(_exes, name, backend):
    expected = _PROGRAMS[name][1]
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_exes[name]], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"{name} backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [expected], f"{name} #{backend}: {r.stdout!r}"
