from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).absolute().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_hot_container_constructors_use_pcc_gc_alloc_in_c_runtime():
    expectations = {
        "pcc/py_runtime/src/py_list.c": "pcc_gc_alloc(\n        (int64_t)sizeof(PyListObject), PY_TYPE_LIST, 0",
        "pcc/py_runtime/src/py_tuple.c": "pcc_gc_alloc(\n        (int64_t)bytes, PY_TYPE_TUPLE, 0",
        "pcc/py_runtime/src/py_dict.c": "pcc_gc_alloc(\n        (int64_t)sizeof(PyDictObject), PY_TYPE_DICT, 0",
        "pcc/py_runtime/src/py_set.c": "pcc_gc_alloc(\n        (int64_t)sizeof(PySetObject), PY_TYPE_SET, 0",
    }
    for rel, needle in expectations.items():
        text = _read(rel)
        assert needle in text, f"{rel} constructor bypasses pcc_gc_alloc"


def test_hot_container_constructors_use_pcc_gc_alloc_in_pcc_python_ports():
    expectations = {
        "pcc/py_runtime/py/py_list.py": "pcc_gc_alloc(40, 5, 0)",
        "pcc/py_runtime/py/py_tuple.py": "pcc_gc_alloc(bytes_total, 7, 0)",
        "pcc/py_runtime/py/py_dict.py": "pcc_gc_alloc(56, 6, 0)",
        "pcc/py_runtime/py/py_set.py": "pcc_gc_alloc(48, 8, 0)",
    }
    for rel, needle in expectations.items():
        text = _read(rel)
        assert 'extern("pcc_gc_alloc"' in text
        assert needle in text, f"{rel} port bypasses pcc_gc_alloc"


def test_split_string_accessors_allocate_strings_through_gc():
    c_text = _read("pcc/py_runtime/src/py_str_accessors.c")
    assert "pcc_gc_alloc(total, PY_TYPE_STR, 0)" in c_text
    assert "PyStrObject *s = (PyStrObject *)malloc(total)" not in c_text

    py_text = _read("pcc/py_runtime/py/py_str_accessors.py")
    assert 'extern("pcc_gc_alloc"' in py_text
    assert "pcc_gc_alloc(40 + byte_len + 1, 4, 0)" in py_text
    assert "s = malloc(40 + byte_len + 1)" not in py_text


def test_debug_release_stack_pointer_guard_allows_valid_untracked_objects():
    text = _read("pcc/py_runtime/src/pcc_threads.c")
    assert "pcc_debug_untracked_release_has_valid_header" in text
    assert "if (pcc_debug_untracked_release_has_valid_header(obj)) return;" in text


def test_native_log_gate_expects_alloc_object_tags_for_main_containers():
    text = _read("tests/python/test_runtime_log_native_binary.py")
    assert "assert {5, 6, 7, 8}.issubset(alloc_tags)" in text
