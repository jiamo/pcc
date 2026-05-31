"""Native ``functools.partial`` under strict no-libpython (run-based).

``functools.partial`` is the single largest ``--python-libpython=auto`` fallback
item in the numpy import diagnostic and a common stdlib feature. It lowers to the
runtime ``py_functools_partial`` (a PyFuncObject whose entry concatenates the
captured args with the call args); the function argument is emitted with
``_prefer_native_callable_values`` so a top-level Dyn-typed ``def`` boxes into a
native PyFuncObject (the path closures/lambdas use), not a libpython callable.

These tests COMPILE + RUN under ``--backend self --python-libpython=off`` (which
hard-errors on any residual ``py_cpy_*`` fallback), so a green run proves the
whole path is native end-to-end and produces the right value.

Both the ``functools.partial(...)`` attribute form and the ``from functools
import partial`` form are native at MODULE LEVEL, for both Dyn-typed and
fully type-annotated ``fn``.

KNOWN LIMITATION (follow-on): used INSIDE a function body, ``functools.partial``
still routes through libpython — the ``functools`` name resolves to a cpy module
ref there rather than the native-builtin-module alias (a function-body
scope/alias-resolution gap, not a typing gap).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _run_pcc_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "cc"
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=300, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_functools_partial_attr_form_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "import functools\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "print(functools.partial(add, 10)(5))\n"
        "print(functools.partial(add, 100)(1))\n",
    )
    assert out.split() == ["15", "101"], out


def test_functools_partial_from_import_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "from functools import partial\n"
        "def mul(a, b):\n"
        "    return a * b\n"
        "print(partial(mul, 6)(7))\n",
    )
    assert out.strip() == "42", out


def test_functools_partial_typed_fn_native_no_libpython(tmp_path):
    # A fully type-annotated top-level fn also boxes natively at module level.
    out = _run_pcc_program(
        tmp_path,
        "import functools\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "print(functools.partial(add, 10)(5))\n",
    )
    assert out.strip() == "15", out


@pytest.mark.xfail(
    reason=(
        "import-functools attr form INSIDE a function body falls back to a "
        "cpython module ref. ROOT CAUSE (diagnosed 2026-05-29): the import "
        "pre-pass alias list in generation_lowering.py registers native "
        "builtin-module aliases BEFORE function bodies are lowered, but it "
        "omits 'functools' (functools is only registered in the second/"
        "source-order pass via import_lowering.py, which the module-level call "
        "sees but a function body does not). Adding 'functools' to the pre-pass "
        "list fixes THIS case (tests 5/5, fallback baselines 17/17) BUT breaks "
        "the self-host bootstrap stage2 (pcc1 compiling pcc2): confirmed by "
        "re-running (revert restores green). pcc's own code has NO "
        "functools.partial, but it uses @functools.wraps decorators "
        "(roadmap_deepwire.py) + lru_cache, and decorator_lowering.py gates on "
        "the native-alias dict, so a pre-pass functools alias changes how those "
        "decorators lower and breaks stage2. A bootstrap-safe fix is a focused "
        "follow-on (make functools.wraps/lru_cache lower correctly under the "
        "pre-pass native alias first). The 'from functools import partial' form "
        "inside a function already works (separate test, passes)."
    ),
    strict=False,
)
def test_functools_partial_inside_function_native_no_libpython(tmp_path):
    # Used INSIDE a function body via the ``import functools`` attr form.
    # See the xfail reason above for the pre-pass / bootstrap interaction.
    out = _run_pcc_program(
        tmp_path,
        "import functools\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "def use():\n"
        "    return functools.partial(add, 10)(5)\n"
        "print(use())\n",
    )
    assert out.strip() == "15", out


def test_functools_partial_from_import_inside_function_native_no_libpython(tmp_path):
    # The ``from functools import partial`` form INSIDE a function body.
    out = _run_pcc_program(
        tmp_path,
        "from functools import partial\n"
        "def mul(a, b):\n"
        "    return a * b\n"
        "def use():\n"
        "    return partial(mul, 6)(7)\n"
        "print(use())\n",
    )
    assert out.strip() == "42", out
