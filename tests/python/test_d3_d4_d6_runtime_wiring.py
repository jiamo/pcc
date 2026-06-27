from __future__ import annotations

from pathlib import Path


def test_d3_d4_d6_runtime_symbols_are_wired():
    header = Path("pcc/py_runtime/include/py_runtime.h").read_text(encoding="utf-8")
    abi = Path("pcc/py_frontend/codegen/runtime_abi.py").read_text(encoding="utf-8")
    makefile = Path("pcc/py_runtime/Makefile").read_text(encoding="utf-8")
    coro_c = Path("pcc/py_runtime/src/py_coroutine.c").read_text(encoding="utf-8")
    coro_py = Path("pcc/py_runtime/py/py_coroutine.py").read_text(encoding="utf-8")

    assert "py_coroutine_is_done" in header
    assert "py_context_enter" in header
    assert "py_obj_format" in header
    assert '"py_coroutine_is_done": (_I64, [_PYOBJ], False)' in abi
    assert '"py_context_exit": (_I64, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False)' in abi
    assert '"py_obj_format": (_PYOBJ, [_PYOBJ, _PYOBJ], False)' in abi
    assert "py_context.c" in makefile
    assert "py_format.c" in makefile
    assert "py_coroutine_get_result" in coro_c
    assert '@c_abi_export("py_task_is_done")' in coro_py
