"""5-GC common production contract: container-graph reachability + safety.

Part of the 5-GC Production Equality Rule (codex-goal-prompt.md G-track).
Each program is compiled once under strict no-libpython and run under
PCC_GC_BACKEND 0..4; every backend must keep the reachable objects alive across
gc.collect() and produce the same correct output.

These three shapes pass on ALL FIVE backends (clean contract lock, verified
2026-05-31), complementing test_root_graphs.py (which locks generator-frame /
dict->list->instance / set roots):

  * tuple_root      — a tuple holding instances is a frame root; its elements
                      survive gc.collect (exercises _trace_referents' tuple
                      case, tag 7 / offsets 24+i*8).
  * reachable_cycle — a self-referential reference cycle that is STILL reachable
                      via a frame root must NOT be collected (a tracing collect
                      must keep reachable cycles; only UNreachable cycles are
                      reclaimable). #0's refcount+cycle path and #1/#2/#3/#4's
                      tracing path must agree.
  * deep_nest       — a deeply nested dict -> list -> tuple -> instance graph
                      survives + stays traversable (transitive marking through
                      mixed container kinds).

A divergence on any backend (wrong sum, crash, or `<null>`) is a tracing /
reachability gap for that container shape and would be xfail'd + root-caused
like the object-lifetime and cycle-finalizer gaps before it.
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAMS = {
    # tuple holding instances is a frame root; elements survive gc.collect
    "tuple_root": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "def main():\n"
        "    t = (N(1), N(2), N(3))\n"
        "    gc.collect()\n"
        "    print(t[0].v + t[1].v + t[2].v)\n"
        "main()\n",
        "6",
    ),
    # a reachable self-referential cycle must survive (only UNreachable cycles
    # are reclaimable)
    "reachable_cycle": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "        self.link = None\n"
        "def main():\n"
        "    a = N(10)\n"
        "    b = N(20)\n"
        "    a.link = b\n"
        "    b.link = a\n"
        "    root = a\n"
        "    gc.collect()\n"
        "    print(root.link.v + root.link.link.v)\n"
        "main()\n",
        "30",
    ),
    # deeply nested dict -> list -> tuple -> instance survives + traversable
    "deep_nest": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "def main():\n"
        "    d = {'k': [(N(5), N(6)), [N(7)]]}\n"
        "    gc.collect()\n"
        "    inner = d['k']\n"
        "    print(inner[0][0].v + inner[0][1].v + inner[1][0].v)\n"
        "main()\n",
        "18",
    ),
}


@pytest.fixture(scope="module")
def _exes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_container")
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
def test_container_graph_survives_gc(_exes, name, backend):
    expected = _PROGRAMS[name][1]
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_exes[name]], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"{name} backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [expected], f"{name} #{backend}: {r.stdout!r}"
