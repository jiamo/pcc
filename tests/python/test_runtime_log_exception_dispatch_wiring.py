from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).absolute().parents[2]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_exception_runtime_log_points_are_mirrored_between_c_and_pcc_python():
    c_tls = _read("pcc/py_runtime/src/py_exc_tls.c")
    c_obj = _read("pcc/py_runtime/src/py_exc_objects.c")
    py_tls = _read("pcc/py_runtime/py/py_exc_tls.py")
    py_obj = _read("pcc/py_runtime/py/py_exc_objects.py")

    for needle in ["6, 3", "6, 4"]:
        assert needle in c_tls
        assert needle in py_tls
    for needle in ["6, 1", "6, 2", "6, 5", "6, 6", "6, 7"]:
        assert needle in c_obj
        assert needle in py_obj


def test_dispatch_runtime_log_points_are_mirrored_between_c_and_pcc_python():
    c_src = _read("pcc/py_runtime/src/py_obj_ops_dispatch.c")
    py_src = (
        _read("pcc/py_runtime/py/py_obj_ops_dispatch.py")
        + _read("pcc/py_runtime/py/py_obj_ops_slice.py")
    )
    for event_code in range(1, 10):
        needle = f"7, {event_code}"
        assert needle in c_src
        assert needle in py_src


def test_runtime_log_code_map_names_exception_and_dispatch_channels():
    src = _read("pcc/py_runtime/src/pcc_runtime_log.c")
    assert 'case 6: return "exception";' in src
    assert 'case 7: return "dispatch";' in src
    for event in [
        'return "raise";',
        'return "clear";',
        'return "set_cause";',
        'return "set_context";',
        'return "getitem";',
        'return "getattr";',
        'return "call";',
        'return "isinstance";',
    ]:
        assert event in src
