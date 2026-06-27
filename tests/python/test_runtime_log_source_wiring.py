from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).absolute().parents[2]
RUNTIME = REPO / "pcc" / "py_runtime"


def _read(rel: str) -> str:
    return (RUNTIME / rel).read_text(encoding="utf-8")


def test_refcount_log_events_are_in_c_and_pcc_python_dispatch_layers():
    c_src = _read("src/py_obj.c")
    py_src = _read("py/py_obj.py")
    log_map = _read("src/pcc_runtime_log.c")
    assert "pcc_obj_runtime_log_event_code" in c_src
    assert "pcc_runtime_log_event_code(category, event, value0, value1, ptr)" in c_src
    for event_code in ("(3, 1", "(3, 2", "(3, 3"):
        assert f"pcc_obj_runtime_log_event_code{event_code}" in c_src
        assert f"pcc_runtime_log_event_code{event_code}" in py_src
    for event in ("incref", "decref", "free"):
        assert f'return "{event}";' in log_map


def test_weakref_log_events_are_in_c_and_pcc_python_layers():
    c_src = _read("src/py_weakref.c")
    py_src = _read("py/py_weakref.py")
    for event in ("new", "invalidate", "callback", "dealloc"):
        assert f'pcc_runtime_log_event("weakref", "{event}"' in c_src
    for event_code in ("(4, 1", "(4, 2", "(4, 3", "(4, 4"):
        assert f"pcc_runtime_log_event_code{event_code}" in py_src


def test_finalizer_log_events_are_in_c_and_pcc_python_layers():
    c_src = _read("src/py_dunder.c")
    py_src = _read("py/py_dunder.py")
    for event in ("call", "done", "skipped"):
        assert f'pcc_runtime_log_event("finalizer", "{event}"' in c_src
    for event_code in ("(5, 2", "(5, 3", "(5, 4"):
        assert f"pcc_runtime_log_event_code{event_code}" in py_src


def test_integer_coded_runtime_log_api_is_declared_for_pcc_python_ports():
    header = _read("src/py_internal.h")
    impl = _read("src/pcc_runtime_log.c")
    assert "pcc_runtime_log_event_code" in header
    assert "pcc_runtime_log_category_from_code" in impl
