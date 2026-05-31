from __future__ import annotations

from pathlib import Path


def test_exception_accessor_symbols_are_wired_in_c_py_and_abi():
    c_src = Path("pcc/py_runtime/src/py_exc_objects.c").read_text()
    py_src = Path("pcc/py_runtime/py/py_exc_objects.py").read_text()
    header = Path("pcc/py_runtime/include/py_runtime.h").read_text()
    abi = Path("pcc/py_frontend/codegen/runtime_abi.py").read_text()

    assert "PyObject *py_exc_get_cause(PyObject *exc)" in c_src
    assert "PyObject *py_exc_get_context(PyObject *exc)" in c_src
    assert "int64_t py_exc_traceback_len(PyObject *exc)" in c_src

    assert '@c_abi_export("py_exc_get_cause")' in py_src
    assert '@c_abi_export("py_exc_get_context")' in py_src
    assert '@c_abi_export("py_exc_traceback_len")' in py_src

    assert "PyObject *py_exc_get_cause(PyObject *exc);" in header
    assert '"py_exc_get_cause": (_PYOBJ, [_PYOBJ], False)' in abi
    assert '"py_exc_traceback_len": (_I64, [_PYOBJ], False)' in abi
