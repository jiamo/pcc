"""``d[keys[i]]``: the owned key temporary must be released, and the lookup
must not drown in protocol.

``py_list_getitem`` returns a NEW reference.  When that reference was the key
of an exact-dict subscript, ``_emit_exact_container_subscript_load_object``
handed it to ``py_dict_getitem`` and never released it: a key object with a
``__del__`` was never finalized (evidence
PERF-P0-PCC1-WORKER-OBJECT-PROTOCOL-TAX/001, per-op row ``dict_get_str``).

The same site also parked every subscript result in a temporary root frame
(store_root, frame_enter_lifo, load_ptr, store_root(null), frame_leave_lifo)
even when nothing was released between rooting and reloading, unboxed a tagged
index through a ``py_int_to_i64`` call, and checked ``py_err_occurred`` after
the inline tagged ``+`` fast path.  The ratchet pins the corrected shape; the
run tests pin semantics on every GC backend.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

KEY_CANARY = (
    "class K:\n"
    "    def __init__(self, n: int):\n"
    "        self.n = n\n"
    "    def __hash__(self) -> int:\n"
    "        return self.n\n"
    "    def __eq__(self, other) -> bool:\n"
    "        return self.n == other.n\n"
    "    def __del__(self):\n"
    "        print('del', self.n)\n"
    "\n"
    "def main() -> None:\n"
    "    keys = [K(0), 'x']\n"
    "    d = {keys[0]: 10, 'x': 20}\n"
    "    total = 0\n"
    "    i = 0\n"
    "    while i < 4:\n"
    "        total += d[keys[i & 1]]\n"
    "        i += 1\n"
    "    d[keys[0]] = 30\n"
    "    d[keys[1]] = 40\n"
    "    print(total, d[keys[0]] + d[keys[1]])\n"
    "    print('before-drop')\n"
    "    d = {}\n"
    "    keys = []\n"
    "    print('after-drop')\n"
    "\n"
    "main()\n"
    "print('end')\n"
)

DICT_GET_STR_LOOP = (
    "import os\n"
    "N = int(os.environ.get('BENCH_N', '1000'))\n"
    "\n"
    "def main() -> None:\n"
    "    d = {}\n"
    "    keys = []\n"
    "    k = 0\n"
    "    while k < 64:\n"
    "        key = 'symbol_' + str(k)\n"
    "        d[key] = k\n"
    "        keys.append(key)\n"
    "        k += 1\n"
    "    total = 0\n"
    "    i = 0\n"
    "    while i < N:\n"
    "        total += d[keys[i & 63]]\n"
    "        i += 1\n"
    "    print(total)\n"
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


def _main_body(ll: str) -> str:
    m = re.search(r"define [^\n]*@user_[a-z0-9_]*main\([^\n]*\{\n(.*?)\n\}", ll, re.S)
    assert m, "main function not found"
    return m.group(1)


def _blocks(body: str) -> list[str]:
    return re.split(r"\n(?=[A-Za-z_.][A-Za-z0-9_.]*:\n)", body)


def test_dict_subscript_owned_key_is_released_on_every_backend(tmp_path):
    src, exe = _compile(tmp_path, "key_canary", KEY_CANARY, emit_llvm=False)
    expected = subprocess.run([sys.executable, str(src)], text=True, capture_output=True, timeout=60).stdout
    assert "before-drop\ndel 0\nafter-drop" in expected
    env = _env()
    for backend in ("0", "1", "2", "3", "4"):
        env["PCC_GC_BACKEND"] = backend
        run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=60, env=env)
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout == expected, (backend, run.stdout, expected)


def test_dict_get_str_loop_protocol_ratchet(tmp_path):
    _, ll_path = _compile(tmp_path, "dict_get_str", DICT_GET_STR_LOOP, emit_llvm=True)
    body = _main_body(ll_path.read_text(encoding="utf-8"))
    counts = {
        name: len(re.findall(r"@" + name + r"\(", body))
        for name in (
            "pcc_gc_frame_enter_lifo", "py_int_to_i64", "py_list_getitem",
            "py_dict_getitem", "pcc_gc_store_root", "pcc_gc_pin",
        )
    }
    # Before: lifo 5 (one per exact-container subscript result), an
    # unconditional py_int_to_i64 for the tagged ``i & 63`` index, 10 plain
    # store_root.  The unbox call survives only as the bignum slow block.
    assert counts["pcc_gc_frame_enter_lifo"] == 0, counts
    assert counts["py_int_to_i64"] <= 1, counts
    for block in _blocks(body):
        if "@py_int_to_i64(" in block:
            assert block.startswith("m.int.unbox.slow"), block[:200]
    assert counts["py_list_getitem"] == 1, counts
    assert counts["py_dict_getitem"] == 1, counts
    assert counts["pcc_gc_store_root"] == 0, counts
    # Every error check must sit next to the runtime call it guards; the
    # inline tagged fast paths (``i & 63``, ``total += v``, ``i += 1``) cannot
    # raise and get no check of their own.
    for block in _blocks(body):
        if "@py_err_occurred(" in block:
            assert re.search(r"call [^\n]*@py_(?!err_occurred)", block), block[:400]


def test_dict_get_str_loop_matches_cpython_on_every_backend(tmp_path):
    src, exe = _compile(tmp_path, "dict_get_str", DICT_GET_STR_LOOP, emit_llvm=False)
    env = _env()
    env["BENCH_N"] = "100000"
    expected = subprocess.run([sys.executable, str(src)], text=True, capture_output=True, timeout=60, env=env).stdout
    for backend in ("0", "1", "2", "3", "4"):
        env["PCC_GC_BACKEND"] = backend
        run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=60, env=env)
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout == expected, (backend, run.stdout, expected)
