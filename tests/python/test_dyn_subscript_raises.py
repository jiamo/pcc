"""A dynamic ``x[k]`` must raise what CPython raises, never return a NULL.

``def lookup(table, key): return table[key]`` lowers the untyped operand to
the generic runtime subscript.  The getitem primitives (``py_obj_getitem`` /
``py_obj_getitem_i64``) keep a silent-NULL contract for internal callers
(tuple unpacking, argument splatting, C-API shims), so the frontend used to
receive NULL with no pending exception and no post-call check: ``print``
showed ``<null>`` and ``lookup(d, 'missing') + 10`` raised TypeError instead
of KeyError.  The raising entry points ``py_obj_subscript(_i64)`` convert the
NULL into KeyError (carrying the key), IndexError or TypeError, mirrored in
the C runtime and the pcc-Python port, and the frontend checks after them.

``repr(exc.args)`` is printed for every KeyError: the port's ``args`` getter
read the message's type tag straight from the object header, which
dereferenced the tagged small int a ``KeyError(3)`` carries and segfaulted.

Typed dict/list subscripts already raised correctly and stay untouched.
"""

from __future__ import annotations

import os
import subprocess

import pytest

_CASES = [
    ("dict-hit", "lookup(d, 'a')"),
    ("dict-miss", "lookup(d, 'missing')"),
    ("dict-miss-plus", "lookup(d, 'missing') + 10"),
    ("method-miss", "box.get('missing')"),
    ("list-hit", "lookup_index(values, 1)"),
    ("list-oor", "lookup_index(values, 5)"),
    ("list-neg-oor", "lookup_index(values, -3)"),
    ("list-obj-key", "lookup(values, 7)"),
    ("tuple-oor", "lookup_index(pair, 2)"),
    ("str-oor", "lookup_index(text, 9)"),
    ("int-subscript", "lookup(5, 0)"),
    ("none-subscript", "lookup(None, 'k')"),
    ("dict-int-key-miss", "lookup_index(d, 3)"),
]

_PROGRAM_HEAD = """\
def lookup(table, key):
    return table[key]


def lookup_index(seq, index: int):
    return seq[index]


class Box:
    def __init__(self):
        self.items = {'a': 1}

    def get(self, key):
        return lookup(self.items, key)


d = {'a': 1}
values = [10, 20]
pair = (1, 2)
text = 'ab'
box = Box()
"""

_CASE_TEMPLATE = """\
try:
    print('{label}', {expr})
except KeyError as exc:
    print('{label}', 'KeyError', str(exc), repr(exc.args))
except IndexError as exc:
    print('{label}', 'IndexError', str(exc))
except TypeError as exc:
    print('{label}', 'TypeError', str(exc))
"""

PROGRAM = _PROGRAM_HEAD + "".join(
    _CASE_TEMPLATE.format(label=label, expr=expr) for label, expr in _CASES
) + "print('end')\n"

# Recorded from CPython 3 on the same program.
EXPECTED = [
    "dict-hit 1",
    "dict-miss KeyError 'missing' ('missing',)",
    "dict-miss-plus KeyError 'missing' ('missing',)",
    "method-miss KeyError 'missing' ('missing',)",
    "list-hit 20",
    "list-oor IndexError list index out of range",
    "list-neg-oor IndexError list index out of range",
    "list-obj-key IndexError list index out of range",
    "tuple-oor IndexError tuple index out of range",
    "str-oor IndexError string index out of range",
    "int-subscript TypeError 'int' object is not subscriptable",
    "none-subscript TypeError 'NoneType' object is not subscriptable",
    "dict-int-key-miss KeyError 3 (3,)",
    "end",
]


def _build(tmp_path, *, runtime_cc: bool):
    src = tmp_path / "prog.py"
    src.write_text(PROGRAM, encoding="utf-8")
    exe = tmp_path / ("prog_cc" if runtime_cc else "prog_port")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    if runtime_cc:
        # Link the C runtime sources instead of the pcc-Python port so both
        # mirrors of the raising subscript are exercised.
        env["PCC_RUNTIME_CC"] = "cc"
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=900, env=env,
    )
    assert build.returncode == 0, build.stderr
    return exe


def _run_all_backends(exe):
    for backend in ("0", "1", "2", "3", "4"):
        env = os.environ.copy()
        env.pop("LC_ALL", None)
        env["PCC_GC_BACKEND"] = backend
        run = subprocess.run(
            [str(exe)], text=True, capture_output=True, timeout=30, env=env
        )
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout.splitlines() == EXPECTED, (backend, run.stdout)


def test_dyn_subscript_raises_like_cpython_port_runtime(tmp_path):
    _run_all_backends(_build(tmp_path, runtime_cc=False))


@pytest.mark.integration
def test_dyn_subscript_raises_like_cpython_c_runtime(tmp_path):
    _run_all_backends(_build(tmp_path, runtime_cc=True))
