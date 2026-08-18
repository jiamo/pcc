"""A temporary passed as a call argument must be released after the call.

The call lowering already pins each pointer argument across the call and
releases it afterwards when ``_last_call_arg_owned_temp`` is set.  That flag
only meant "this lowering boxed a native value into an object", so an argument
whose *expression* produced a fresh object -- ``f(T())``, ``f([T()])`` -- was
pinned, unpinned, and never released.  The emitted call had a
``pcc_gc_release`` only on the exception edge.

The ``IntType`` branch of the same function already classified this correctly
with ``_pcc_pointer_source_is_owned``; the object branch did not.

Single-argument builtins (``repr``, ``ascii``, ``hash``) inline the object
straight into the runtime call and never went through the argument-ABI path
that owns the flag, so they leaked separately.

A name argument is the control: ``f(x)`` passes a borrowed local and must not
gain a release.
"""

from __future__ import annotations

import os
import subprocess

PROGRAM = """
class T:
    def __init__(self, tag):
        self.tag = tag

    def __del__(self):
        print('freed', self.tag)


def takes(a) -> None:
    print('inside')


takes(T('direct'))
print('after direct')

takes([T('elem')])
print('after container')

kept = T('kept')
takes(kept)
print('after borrowed')

r = repr(T('repr'))
print('after repr')

a = ascii(T('ascii'))
print('after ascii')

hv = hash(T('hash'))
print('after hash')
"""

# Verified against CPython 3 as the oracle.
EXPECTED = [
    "inside",
    "freed direct",
    "after direct",
    "inside",
    "freed elem",
    "after container",
    "inside",
    "after borrowed",
    "freed repr",
    "after repr",
    "freed ascii",
    "after ascii",
    "freed hash",
    "after hash",
    "freed kept",
]


def test_temporary_call_arguments_are_released(tmp_path):
    src = tmp_path / "prog.py"
    src.write_text(PROGRAM, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "0"
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=600, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip().splitlines() == EXPECTED
