"""Native mutable-container hashability and error propagation.

Mutable builtin containers are never valid hash keys.  The runtime hash API
uses the CPython convention: ``-1`` plus a pending ``TypeError`` signals a
hash failure, while a legitimate hash value of ``-1`` is normalized to
``-2``.  Dict/set callers must inspect the pending exception before lookup or
mutation so APIs such as ``dict.get`` do not turn an unhashable key into a
default value or ``KeyError``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_hash_runtime_mirrors_reject_mutable_builtins_and_guard_callers():
    compare_c = (
        REPO / "pcc" / "py_runtime" / "src" / "py_obj_ops_compare.c"
    ).read_text(encoding="utf-8")
    compare_py = (
        REPO / "pcc" / "py_runtime" / "py" / "py_obj_ops_compare.py"
    ).read_text(encoding="utf-8")
    dict_c = (REPO / "pcc" / "py_runtime" / "src" / "py_dict.c").read_text(
        encoding="utf-8"
    )
    dict_py = (REPO / "pcc" / "py_runtime" / "py" / "py_dict.py").read_text(
        encoding="utf-8"
    )
    set_c = (REPO / "pcc" / "py_runtime" / "src" / "py_set.c").read_text(
        encoding="utf-8"
    )
    set_py = (REPO / "pcc" / "py_runtime" / "py" / "py_set.py").read_text(
        encoding="utf-8"
    )

    for source in (compare_c, compare_py):
        for type_name in ("list", "dict", "set", "bytearray"):
            assert f"unhashable type: '{type_name}'" in source
        assert "PY_TYPE_LIST" in source
        assert "PY_TYPE_DICT" in source
        assert "PY_TYPE_SET" in source
        assert "PY_TYPE_BYTEARRAY" in source
        assert "py_err_occurred()" in source

    assert dict_c.count(
        "int64_t hash = py_obj_hash(key);\n    if (py_err_occurred())"
    ) == 4
    assert dict_py.count(
        "h: int = py_obj_hash(key)\n    if py_err_occurred() != 0:"
    ) == 4
    assert set_c.count(
        "int64_t hash = py_obj_hash(item);\n    if (py_err_occurred())"
    ) == 3
    assert set_py.count(
        "h: int = py_obj_hash(item)\n    if py_err_occurred() != 0:"
    ) == 3

    assert dict_c.count(
        "if (v == NULL) {\n        if (py_err_occurred()) return NULL;"
    ) == 2
    assert (
        "if (v != NULL) return v;\n    if (py_err_occurred()) return NULL;"
        in dict_c
    )
    assert dict_py.count(
        "if ptr_is_null(v) == 0:\n        return v\n"
        "    if py_err_occurred() != 0:\n        return null()"
    ) == 2
    assert (
        "if ptr_is_null(v) == 0:\n        py_dict_del(d, key)\n        return v\n"
        "    if py_err_occurred() != 0:\n        return null()"
        in dict_py
    )


def test_mutable_builtin_hash_failures_match_cpython_without_mutation(
    tmp_path,
    monkeypatch,
    pcc_py_runtime_archive,
):
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(pcc_py_runtime_archive))
    from pcc.py_frontend.pipeline import compile_python

    source = textwrap.dedent(
        """
        def main() -> None:
            try:
                hash([])
                print("hash-list-missed")
            except TypeError:
                print("hash-list")
            try:
                hash({})
                print("hash-dict-missed")
            except TypeError:
                print("hash-dict")
            try:
                hash(set())
                print("hash-set-missed")
            except TypeError:
                print("hash-set")
            try:
                hash(bytearray(b"x"))
                print("hash-bytearray-missed")
            except TypeError:
                print("hash-bytearray")
            try:
                hash(([],))
                print("hash-nested-tuple-missed")
            except TypeError:
                print("hash-nested-tuple")

            values = {"ok": 1}
            try:
                values[[]] = 2
                print("dict-set-missed")
            except TypeError:
                print("dict-set")
            try:
                values.get([], 9)
                print("dict-get-missed")
            except TypeError:
                print("dict-get")
            try:
                values[[]]
                print("dict-getitem-missed")
            except TypeError:
                print("dict-getitem")
            try:
                del values[[]]
                print("dict-delitem-missed")
            except TypeError:
                print("dict-delitem")
            try:
                values.pop([])
                print("dict-pop-missed")
            except TypeError:
                print("dict-pop")
            try:
                values.pop([], 9)
                print("dict-pop-default-missed")
            except TypeError:
                print("dict-pop-default")
            try:
                values.setdefault([], 9)
                print("dict-setdefault-missed")
            except TypeError:
                print("dict-setdefault")
            try:
                [] in values
                print("dict-contains-missed")
            except TypeError:
                print("dict-contains")
            print(len(values), values["ok"])

            members = {1}
            try:
                members.add([])
                print("set-add-missed")
            except TypeError:
                print("set-add")
            try:
                [] in members
                print("set-contains-missed")
            except TypeError:
                print("set-contains")
            try:
                members.remove([])
                print("set-remove-missed")
            except TypeError:
                print("set-remove")
            try:
                members.discard([])
                print("set-discard-missed")
            except TypeError:
                print("set-discard")
            try:
                set([[]])
                print("set-constructor-missed")
            except TypeError:
                print("set-constructor")
            try:
                members.update([[]])
                print("set-update-missed")
            except TypeError:
                print("set-update")
            print(len(members), 1 in members)

            try:
                {item for item in [[]]}
                print("set-comprehension-missed")
            except TypeError:
                print("set-comprehension")
            try:
                {item: 1 for item in [[]]}
                print("dict-comprehension-missed")
            except TypeError:
                print("dict-comprehension")

            try:
                dict.fromkeys([[1]])
                print("fromkeys-missed")
            except TypeError:
                print("fromkeys")

        main()
        """
    ).lstrip()
    expected = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    ).stdout

    src = tmp_path / "hashability.py"
    exe = tmp_path / "hashability.out"
    src.write_text(source, encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
