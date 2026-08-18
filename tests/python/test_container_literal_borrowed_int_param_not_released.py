"""A borrowed pointer-form ``int`` name stored into a container is not released.

``_container_store_temp_needs_release`` treated every ``int``-typed element
as a freshly boxed scalar temporary that the store must consume.  An exact-int
local was already recognised as a borrowed pointer load, but an ``int``
*parameter* (and any other pointer-slot name) was not: ``def pair(a: int):
return [a, a]`` emitted ``pcc_gc_release(a)`` after each append.  The list
then held two uncounted references; for a tagged small int the release is a
no-op, for a bignum the caller's object was freed while still referenced and
the program died silently (per-op row ``call_returns_obj``, evidence
PERF-P0-PCC1-WORKER-OBJECT-PROTOCOL-TAX/001).  List, tuple and dict literals
and ``list.append`` share the predicate; this program exercises all four with
a bignum argument on every backend.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PROGRAM = (
    "def pair(a: int) -> list:\n"
    "    return [a, a]\n"
    "\n"
    "def tup(a: int) -> tuple:\n"
    "    return (a, a)\n"
    "\n"
    "def mapping(a: int) -> dict:\n"
    "    return {a: a}\n"
    "\n"
    "def pushed(a: int) -> list:\n"
    "    xs = []\n"
    "    xs.append(a)\n"
    "    xs.append(a)\n"
    "    return xs\n"
    "\n"
    "def main() -> None:\n"
    "    big = 1 << 70\n"
    "    i = 0\n"
    "    while i < 20000:\n"
    "        x = pair(big)\n"
    "        t = tup(big)\n"
    "        d = mapping(big)\n"
    "        p = pushed(big)\n"
    "        i += 1\n"
    "    print(big, big + 1)\n"
    "    print(x[0] == big, t[1] == big, d[big] == big, p[1] == big, len(x) + len(t) + len(d) + len(p))\n"
    "    small = 5\n"
    "    print(pair(small), tup(small), mapping(small), pushed(small))\n"
    "\n"
    "main()\n"
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def _compile(tmp_path: Path, name: str, *, emit_llvm: bool):
    src = tmp_path / f"{name}.py"
    src.write_text(PROGRAM, encoding="utf-8")
    out = tmp_path / (f"{name}.ll" if emit_llvm else f"{name}.bin")
    cmd = ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off", "--ir-scaffold=on"]
    cmd += [f"--emit-llvm={out}", str(src)] if emit_llvm else [str(src), "-o", str(out)]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=600, env=_env())
    assert proc.returncode == 0, proc.stderr
    return src, out


def test_bignum_param_survives_container_stores_on_every_backend(tmp_path):
    src, exe = _compile(tmp_path, "borrowed_param", emit_llvm=False)
    expected = subprocess.run([sys.executable, str(src)], text=True, capture_output=True, timeout=60).stdout
    assert expected.startswith("1180591620717411303424 1180591620717411303425\nTrue True True True 7\n"), expected
    env = _env()
    for backend in ("0", "1", "2", "3", "4"):
        env["PCC_GC_BACKEND"] = backend
        run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=120, env=env)
        assert run.returncode == 0, (backend, run.stdout, run.stderr)
        assert run.stdout == expected, (backend, run.stdout, expected)


def test_pointer_param_element_is_not_released_after_append(tmp_path):
    _, ll_path = _compile(tmp_path, "borrowed_param", emit_llvm=True)
    ll = ll_path.read_text(encoding="utf-8")
    m = re.search(r"define [^\n]*@user_[a-z0-9_]*pair\([^\n]*\{\n(.*?)\n\}", ll, re.S)
    assert m, "pair not found"
    body = m.group(1)
    # Success path: the parameter is appended (list retains it) and never
    # released.  Before the fix each append was followed by a release of the
    # borrowed parameter load; releases remain only in the error-edge cleanup
    # blocks that drop the half-built list.
    for block in re.split(r"\n(?=[A-Za-z_.][A-Za-z0-9_.]*:\n)", body):
        if "@pcc_gc_release(" in block:
            assert "@pcc_gc_release(ptr %list.new" in block, block[:300]
            assert block.count("@pcc_gc_release(") == 1, block[:300]
