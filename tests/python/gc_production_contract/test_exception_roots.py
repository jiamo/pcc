"""5-GC common production contract: a live exception's message referent must
survive gc.collect under all five backends.

Part of the 5-GC Production Equality Rule (codex-goal-prompt.md G-track).

CURRENT STATE (resolved 2026-05-31): all five backends keep a caught
exception's message across gc.collect. The original gap was frontend root
mapping for locals assigned from except-handler bindings (`saved = e`), not an
exception trace-slot omission. See
docs/investigations/gc-5backend-exception-referent-roots-no-libpython.md.
"""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "def boom(payload):\n"
    "    raise ValueError(payload)\n"
    "def main():\n"
    "    saved = None\n"
    "    try:\n"
    "        boom([1, 2, 3])\n"
    "    except ValueError as e:\n"
    "        saved = e\n"
    "    gc.collect()\n"
    "    print(str(saved))\n"
    "main()\n"
)
_EXPECTED = "[1, 2, 3]"
_XFAIL: set = set()  # hard gate on 0..4


@pytest.fixture(scope="module")
def _exc_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_exc")
    src = tmp / "exc.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "exc_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    return str(exe)


def _params():
    out = []
    for b in ("0", "1", "2", "3", "4"):
        if b in _XFAIL:
            out.append(pytest.param(b, marks=pytest.mark.xfail(
                reason="tracing collect reclaims a live exception's message referent",
                strict=False)))
        else:
            out.append(b)
    return out


@pytest.mark.parametrize("backend", _params())
def test_exception_referents_survive_gc(_exc_exe, backend):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = backend
    r = subprocess.run([_exc_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"backend #{backend} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [_EXPECTED], r.stdout
