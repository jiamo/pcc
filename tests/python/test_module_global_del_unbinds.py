"""``del`` on a module-level name must actually unbind it.

Three things have to happen together, and each was missing:

* the module slot is cleared **and its reference released** -- storing null on
  top of it orphans the value so it is never finalized;
* the ``.initialized`` flag is cleared, so the module epilogue stops
  republishing the name;
* ``py_module_attr_del`` removes an entry the flag-gated publish already
  inserted -- that publish is additive and never removes, so clearing the flag
  alone left the name in ``globals()``;

and reads of a name this module deletes somewhere consult the flag and raise
``NameError``.  Only deleted names carry that check, so every other module
global keeps a plain load.

``global x; del x`` inside a function unbinds the module global the same way,
which is why the del-target scan walks function bodies -- an earlier version
matched only ``list`` bodies and silently skipped every function, since these
AST nodes carry tuples.

Function-local ``del`` was already correct and is the control here.  A local
that merely *shares a name* with a module global is a second control: deleting
it must leave the global alone, which an unconditional unbind got wrong.
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


def local_del() -> None:
    a = T('local')
    del a
    print('after local del')


local_del()

g = T('global')
del g
print('after global del')

n = 42
del n
try:
    print('n is', n)
except NameError:
    print('NameError as expected')
print('n in globals:', 'n' in globals())

kept = 7
print('kept is', kept, 'in globals:', 'kept' in globals())

shadowed = "global"


def local_shadow() -> None:
    shadowed = "local"
    del shadowed


local_shadow()
print('shadowed still', shadowed)

gv = 99


def del_via_global() -> None:
    global gv
    del gv


del_via_global()
try:
    print('gv is', gv)
except NameError:
    print('gv NameError')
print('gv in globals:', 'gv' in globals())

again = 1
del again
try:
    del again
except NameError:
    print('second del NameError')
"""

# Verified against CPython 3 as the oracle.
EXPECTED = [
    "freed local",
    "after local del",
    "freed global",
    "after global del",
    "NameError as expected",
    "n in globals: False",
    "kept is 7 in globals: True",
    "shadowed still global",
    "gv NameError",
    "gv in globals: False",
    "second del NameError",
]


def test_module_level_del_unbinds_and_releases(tmp_path):
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
