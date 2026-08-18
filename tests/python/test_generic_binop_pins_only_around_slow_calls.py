"""Generic (non exact-int) binops pin operands only around the slow runtime call.

``expr_dispatch_lowering`` pinned the lhs before lowering the rhs, pinned the
rhs, pinned the result, and unpinned all three around every binop -- also
when the op took the inline tagged-int fast path that never leaves the
function.  ``for x in xs: total += x`` spent 24% of its samples in
``pcc_gc_pin``/``pcc_gc_unpin`` (per-op row ``for_over_list``, evidence
PERF-P0-PCC1-WORKER-OBJECT-PROTOCOL-TAX/001).  The exact-int lane already
defers pins into the slow block; this test pins the same shape for the
generic int/dyn route: a GC-quiet rhs (literal, bound name, slot read) needs
no lhs pin across its evaluation, and an inline-capable op pins only inside
the block that also holds its ``py_int_*``/``py_obj_*`` slow call.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

FOR_OVER_LIST = (
    "import os\n"
    "N = int(os.environ.get('BENCH_N', '1000'))\n"
    "\n"
    "def main() -> None:\n"
    "    xs = []\n"
    "    k = 0\n"
    "    while k < 1000:\n"
    "        xs.append(k)\n"
    "        k += 1\n"
    "    total = 0\n"
    "    rounds = 0\n"
    "    while rounds * 1000 < N:\n"
    "        for x in xs:\n"
    "            total += x\n"
    "        rounds += 1\n"
    "    print(total)\n"
    "\n"
    "main()\n"
)

MIXED = (
    "def main() -> None:\n"
    "    xs = [1, 1 << 70, -3, 7]\n"
    "    total = 0\n"
    "    prod = 1\n"
    "    bits = 0\n"
    "    for x in xs:\n"
    "        total += x\n"
    "        prod = prod * x\n"
    "        bits = bits | (x & 5)\n"
    "        total = total - 1\n"
    "    print(total, prod, bits, total + prod, (total ^ bits) - prod)\n"
    "\n"
    "main()\n"
)

SLOW_CALLS = (
    "py_int_add", "py_int_sub", "py_int_mul", "py_int_and", "py_int_or", "py_int_xor",
    "py_obj_add", "py_obj_sub", "py_obj_mul", "py_obj_and", "py_obj_or", "py_obj_xor",
    "py_int_cmp",
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


def _main_body(ll: str) -> str:
    m = re.search(r"define [^\n]*@user_[a-z0-9_]*main\([^\n]*\{\n(.*?)\n\}", ll, re.S)
    assert m, "main function not found"
    return m.group(1)


def _blocks(body: str) -> list[str]:
    return re.split(r"\n(?=[A-Za-z_.][A-Za-z0-9_.]*:\n)", body)


def test_generic_int_binop_pins_sit_only_in_slow_blocks(tmp_path):
    _, ll_path = _compile(tmp_path, "for_over_list", FOR_OVER_LIST, emit_llvm=True)
    body = _main_body(ll_path.read_text(encoding="utf-8"))
    pins = 0
    for block in _blocks(body):
        if "@pcc_gc_pin(" not in block:
            continue
        pins += block.count("@pcc_gc_pin(")
        assert any("@" + name + "(" in block for name in SLOW_CALLS), block[:500]
    # Before: 10 pins in main, every one on the fast path (lhs before rhs,
    # rhs, result) plus the for-target store's pin/unpin pair.  Now 8, all in
    # slow blocks: ``k < 1000`` (1), ``rounds * 1000`` (2), ``< N`` (2),
    # ``total += x`` (2), ``rounds += 1`` (1).
    assert pins <= 8, pins


def test_generic_binops_match_cpython_on_every_backend(tmp_path):
    for name, source, n in (("for_over_list", FOR_OVER_LIST, "50000"), ("mixed", MIXED, "1")):
        src, exe = _compile(tmp_path, name, source, emit_llvm=False)
        env = _env()
        env["BENCH_N"] = n
        expected = subprocess.run([sys.executable, str(src)], text=True, capture_output=True, timeout=60, env=env).stdout
        for backend in ("0", "1", "2", "3", "4"):
            env["PCC_GC_BACKEND"] = backend
            run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=60, env=env)
            assert run.returncode == 0, (name, backend, run.stderr)
            assert run.stdout == expected, (name, backend, run.stdout, expected)
