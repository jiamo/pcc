"""``obj.field = value`` on a statically typed instance stores by slot index.

The read side already lowered ``p.v`` on a class-typed local to
``py_instance_get_field(p, idx)``; the write side went through the generic
``py_obj_setattr(p, "v", value)`` string lookup -- 3661 instructions per
``p.v = i`` against CPython's 385 (evidence
PERF-P0-PCC1-WORKER-OBJECT-PROTOCOL-TAX/001).  ``self.x = v`` inside methods
had used ``py_instance_set_field`` all along; this makes any class-hinted
receiver take the same path, except when the class (or a base) overrides
``__setattr__`` (guarded in codegen; user ``__setattr__`` overrides are a
separate pre-existing runtime gap and are not exercised here).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

STORE_LOOP = (
    "import os\n"
    "N = int(os.environ.get('BENCH_N', '1000'))\n"
    "\n"
    "class P:\n"
    "    def __init__(self, v: int):\n"
    "        self.v = v\n"
    "\n"
    "def main() -> None:\n"
    "    p = P(0)\n"
    "    total = 0\n"
    "    i = 0\n"
    "    while i < N:\n"
    "        p.v = i\n"
    "        total += p.v\n"
    "        i += 1\n"
    "    print(p.v, total)\n"
    "\n"
    "main()\n"
)

SEMANTICS = (
    "class P:\n"
    "    def __init__(self, v: int):\n"
    "        self.v = v\n"
    "        self.w = 'x'\n"
    "\n"
    "class Sub(P):\n"
    "    def __init__(self, v: int):\n"
    "        P.__init__(self, v)\n"
    "        self.extra = 1\n"
    "\n"
    "class Counter:\n"
    "    def __init__(self):\n"
    "        self.n = 0\n"
    "\n"
    "def main() -> None:\n"
    "    p = P(1)\n"
    "    p.v = 1 << 70\n"
    "    p.w = 'yz'\n"
    "    print(p.v, p.w)\n"
    "    s = Sub(2)\n"
    "    base: P = s\n"
    "    base.v = 5\n"
    "    print(s.v, s.extra)\n"
    "    q = Counter()\n"
    "    k = 0\n"
    "    while k < 5:\n"
    "        q.n = q.n + k\n"
    "        k += 1\n"
    "    r = q\n"
    "    r.n = r.n * 2\n"
    "    print(q.n, r.n, q is r)\n"
    "\n"
    "main()\n"
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def _compile(tmp_path: Path, name: str, source: str, *, emit_llvm: bool):
    src = tmp_path / f"{name}.py"
    src.write_text(source, encoding="utf-8")
    out = tmp_path / (f"{name}.ll" if emit_llvm else f"{name}.bin")
    cmd = ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off", "--ir-scaffold=on"]
    cmd += [f"--emit-llvm={out}", str(src)] if emit_llvm else [str(src), "-o", str(out)]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=420, env=_env())
    assert proc.returncode == 0, proc.stderr
    return src, out


def test_typed_attr_store_uses_slot_index(tmp_path):
    _, ll_path = _compile(tmp_path, "store_loop", STORE_LOOP, emit_llvm=True)
    ll = ll_path.read_text(encoding="utf-8")
    m = re.search(r"define [^\n]*@user_[a-z0-9_]*main\([^\n]*\{\n(.*?)\n\}", ll, re.S)
    assert m, "main not found"
    body = m.group(1)
    assert len(re.findall(r"@py_obj_setattr\(", body)) == 0, "typed store still goes through setattr"
    assert len(re.findall(r"@py_instance_set_field\(", body)) == 1
    # ``total += p.v``: a slot read cannot allocate, so the loop opens no temp
    # root frame; the one remaining frame belongs to the ``print(...)``
    # argument tuple after the loop.
    assert len(re.findall(r"@pcc_gc_frame_enter_lifo\(", body)) <= 1


def test_typed_attr_store_semantics_match_cpython_on_every_backend(tmp_path):
    for name, source, n in (("store_loop", STORE_LOOP, "1000"), ("semantics", SEMANTICS, "1")):
        src, exe = _compile(tmp_path, name, source, emit_llvm=False)
        env = _env()
        env["BENCH_N"] = n
        expected = subprocess.run([sys.executable, str(src)], text=True, capture_output=True, timeout=60, env=env).stdout
        for backend in ("0", "1", "2", "3", "4"):
            env["PCC_GC_BACKEND"] = backend
            run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=60, env=env)
            assert run.returncode == 0, (name, backend, run.stderr)
            assert run.stdout == expected, (name, backend, run.stdout, expected)
