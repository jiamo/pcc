from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).absolute().parents[2]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_c_scalar_and_object_constructors_route_through_pcc_gc_alloc():
    checks = {
        "pcc/py_runtime/src/py_str.c": [
            "pcc_gc_alloc(\n        (int64_t)total, PY_TYPE_STR, 0)",
        ],
        "pcc/py_runtime/src/py_bytes.c": [
            "PY_TYPE_BYTES, 0",
            "PY_TYPE_BYTEARRAY, 0",
            "PY_TYPE_MEMORYVIEW, 0",
        ],
        "pcc/py_runtime/src/py_obj_stubs.c": [
            "PY_TYPE_FLOAT, 0",
            "PY_TYPE_COMPLEX, 0",
        ],
        "pcc/py_runtime/src/py_func.c": [
            "PY_TYPE_FUNC, 0",
        ],
        "pcc/py_runtime/src/py_class.c": [
            "PY_TYPE_CLASS, 0",
            "cls->type_tag_alloc, 0",
        ],
        "pcc/py_runtime/src/py_weakref.c": [
            "PY_TYPE_WEAKREF, 0",
        ],
        "pcc/py_runtime/src/py_exc_objects.c": [
            "PY_TYPE_EXC, 0",
        ],
    }
    for path, needles in checks.items():
        text = _text(path)
        assert "pcc_gc_alloc" in text, path
        for needle in needles:
            assert needle in text, f"{needle!r} missing from {path}"


def test_pcc_py_scalar_and_object_constructors_route_through_pcc_gc_alloc():
    checks = {
        "pcc/py_runtime/py/py_str.py": ["pcc_gc_alloc(40 + byte_len + 1, 4, 0)"],
        "pcc/py_runtime/py/py_obj_stubs.py": [
            "pcc_gc_alloc(24, 3, 0)",
            "pcc_gc_alloc(32, 16, 0)",
            "pcc_gc_alloc(24 + byte_len + 1, 17, 0)",
            "pcc_gc_alloc(24 + byte_len + 1, 18, 0)",
            "pcc_gc_alloc(24, 19, 0)",
        ],
        # ``PyClassObject`` is 120 bytes — header (24) + name (8) +
        # bases (8) + n_bases (8) + methods (8) + n_methods (8) +
        # del_method (8) + attrs (8) + metaclass (8) + various other
        # slots. The metaclass field at offset 112 brought the total
        # to 120 (was 112 before that field was added). Verified
        # against ``pcc/py_runtime/include/py_runtime.h`` and
        # ``pcc/py_runtime/src/py_internal.h::PyClassObject``.
        "pcc/py_runtime/py/py_class.py": [
            "pcc_gc_alloc(120, 10, 0)",
            "pcc_gc_alloc(size, load_i32(cls, 92), 0)",
        ],
        "pcc/py_runtime/py/py_weakref.py": ["pcc_gc_alloc(48, 21, 0)"],
        "pcc/py_runtime/py/py_exc_objects.py": ["pcc_gc_alloc(64, 12, 0)"],
    }
    for path, needles in checks.items():
        text = _text(path)
        for needle in needles:
            assert needle in text, f"{needle!r} missing from {path}"


def test_known_bignum_raw_allocation_is_documented_until_collapse_path_is_fixed():
    text = _text("pcc/py_runtime/src/py_int_core.c")
    assert "PyIntObject *py_bigint_alloc" in text
    assert "malloc(bytes)" in text
    assert "free(b);" in text, (
        "py_bigint_to_pyobject still frees freshly collapsed bignums directly; "
        "do not route py_bigint_alloc through pcc_gc_alloc until that path "
        "emits object-freeing/forget events symmetrically."
    )
