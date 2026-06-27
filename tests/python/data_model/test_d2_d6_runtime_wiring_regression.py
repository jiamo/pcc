from __future__ import annotations

from pathlib import Path


def test_d2_generator_runtime_wiring_stays_intact():
    header = Path("pcc/py_runtime/include/py_runtime.h").read_text(encoding="utf-8")
    abi = Path("pcc/py_frontend/codegen/runtime_abi.py").read_text(encoding="utf-8")
    c_src = Path("pcc/py_runtime/src/py_gen.c").read_text(encoding="utf-8")
    py_src = Path("pcc/py_runtime/py/py_gen.py").read_text(encoding="utf-8")

    for sym in ["py_gen_finish", "py_gen_is_done", "py_gen_send", "py_gen_throw", "py_gen_close"]:
        assert sym in header
        assert sym in abi or sym in c_src
    assert '@c_abi_export("py_gen_finish")' in py_src


def test_d3_async_runtime_wiring_stays_intact():
    header = Path("pcc/py_runtime/include/py_runtime.h").read_text(encoding="utf-8")
    abi = Path("pcc/py_frontend/codegen/runtime_abi.py").read_text(encoding="utf-8")
    c_src = Path("pcc/py_runtime/src/py_coroutine.c").read_text(encoding="utf-8")
    py_src = Path("pcc/py_runtime/py/py_coroutine.py").read_text(encoding="utf-8")

    for sym in [
        "py_coroutine_new_native",
        "py_coroutine_is_done",
        "py_coroutine_get_result",
        "py_task_is_done",
        "py_await",
    ]:
        assert sym in header
        assert sym in abi or sym in c_src
    assert '@c_abi_export("py_task_is_done")' in py_src


def test_d4_d5_d6_runtime_wiring_stays_intact():
    header = Path("pcc/py_runtime/include/py_runtime.h").read_text(encoding="utf-8")
    abi = Path("pcc/py_frontend/codegen/runtime_abi.py").read_text(encoding="utf-8")
    makefile = Path("pcc/py_runtime/Makefile").read_text(encoding="utf-8")
    protocol = Path("pcc/py_runtime/src/py_protocol.c").read_text(encoding="utf-8")

    # D4 context manager
    for sym in ["py_context_enter", "py_context_exit"]:
        assert sym in header
        assert sym in abi
    assert "py_context.c" in makefile

    # D5 protocol polish
    for sym in [
        "py_user_len_dispatch",
        "py_user_bool_dispatch",
        "py_user_contains_dispatch",
        "py_user_getitem_dispatch",
        "py_user_setitem_dispatch",
        "py_user_delitem_dispatch",
    ]:
        assert sym in protocol

    # D6 format
    assert "py_obj_format" in header
    assert '"py_obj_format": (_PYOBJ, [_PYOBJ, _PYOBJ], False)' in abi
    assert "py_format.c" in makefile
