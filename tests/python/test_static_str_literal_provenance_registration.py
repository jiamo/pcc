"""Static ``str`` literal objects are registered with the GC provenance index
at module start.

``.pystr.obj.N`` globals live in the data segment, so the allocator's
granule check cannot vouch for them; before this, every ``py_incref`` /
``py_decref`` / pin of a literal fell through the whole locked provenance
chain -- including the linear builtin-type scan -- before answering "not
managed" (``_is_type_object`` was 12% of the ``str_eq_dispatch`` per-op row,
evidence PERF-P0-PCC1-WORKER-OBJECT-PROTOCOL-TAX/001).  Each module now
emits ``_pcc_py_static_literals_<mod>()`` (one ``pcc_gc_pointer_register`` per
pooled literal, guarded) and calls it first from ``main`` /
``_pcc_py_module_top_<mod>``.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

DISPATCH = (
    "import os\n"
    "N = int(os.environ.get('BENCH_N', '1000'))\n"
    "\n"
    "def main() -> None:\n"
    "    names = ['add', 'sub', 'ldr', 'str', 'b', 'bl', 'ret', 'cmp']\n"
    "    hits = 0\n"
    "    i = 0\n"
    "    while i < N:\n"
    "        mn = names[i & 7]\n"
    "        if mn == 'ret':\n"
    "            hits += 1\n"
    "        elif mn == 'bl':\n"
    "            hits += 2\n"
    "        elif mn == 'cmp':\n"
    "            hits += 3\n"
    "        i += 1\n"
    "    d = {'add': 1, 'sub': 2}\n"
    "    print(hits, d['add'] + d['sub'], 'ret' in names, names[0] + 'x')\n"
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


def test_every_pooled_literal_is_registered_once_before_module_code(tmp_path):
    _, ll_path = _compile(tmp_path, "dispatch", DISPATCH, emit_llvm=True)
    ll = ll_path.read_text(encoding="utf-8")
    pooled = set(re.findall(r"^@\.pystr\.obj\.\d+ = ", ll, re.M))
    assert pooled, "no static str literal objects emitted"
    m = re.search(r"define [^\n]*@_pcc_py_static_literals_[A-Za-z0-9_]*\(\)[^{]*\{\n(.*?)\n\}", ll, re.S)
    assert m, "static literal init function missing"
    body = m.group(1)
    calls = re.findall(r"@pcc_gc_pointer_register\(", body)
    registered = re.findall(r"bitcast ptr (@\.pystr\.obj\.\d+) to ptr", body)
    assert len(calls) == len(pooled), (len(calls), len(pooled))
    assert set(registered) == {name.split(" ")[0] for name in pooled}, (registered, pooled)
    # Guarded: a second call must not re-register.
    assert ".pcc.static.literals.init." in body
    main = re.search(r"define [^\n]*@main\([^\n]*\{\n(.*?)\n\}", ll, re.S)
    assert main, "program main missing"
    main_body = main.group(1)
    init_pos = main_body.find("@_pcc_py_static_literals_")
    assert init_pos >= 0, "main does not call the static literal init"
    first_user_use = min(
        (pos for pos in (main_body.find("@user_"), main_body.find("@py_list_new(")) if pos >= 0),
        default=len(main_body),
    )
    assert init_pos < first_user_use, "static literals must be registered before module code runs"


def test_dispatch_program_matches_cpython_on_every_backend(tmp_path):
    src, exe = _compile(tmp_path, "dispatch", DISPATCH, emit_llvm=False)
    env = _env()
    env["BENCH_N"] = "50000"
    expected = subprocess.run([sys.executable, str(src)], text=True, capture_output=True, timeout=60, env=env).stdout
    for backend in ("0", "1", "2", "3", "4"):
        env["PCC_GC_BACKEND"] = backend
        run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=60, env=env)
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout == expected, (backend, run.stdout, expected)
