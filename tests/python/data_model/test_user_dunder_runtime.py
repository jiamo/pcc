from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


def test_user_dunder_str_hash_iter_next_native(tmp_path, c_runtime_archive):
    src = tmp_path / "dunder_probe.c"
    exe = tmp_path / "dunder_probe"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_internal.h"
            #include <stdio.h>
            #include <stdint.h>
            #include <string.h>

            static PyObject *my_str(PyObject *self) {
                (void)self;
                return py_str_new("custom", 6);
            }

            static PyObject *my_hash(PyObject *self) {
                (void)self;
                return py_int_from_i64(12345);
            }

            static PyObject *my_iter(PyObject *self) {
                PyObject *lst = py_list_new(0);
                py_list_append(lst, py_int_from_i64(7));
                py_list_append(lst, py_int_from_i64(8));
                return py_obj_iter(lst);
            }

            static PyObject *my_next(PyObject *self) {
                (void)self;
                return py_int_from_i64(99);
            }

            int main(void) {
                PyClassObject *cls = py_class_new("D", NULL, 0, NULL, 0);
                py_class_add_method(cls, "__str__", (PyObject *)(uintptr_t)my_str);
                py_class_add_method(cls, "__hash__", (PyObject *)(uintptr_t)my_hash);
                py_class_add_method(cls, "__iter__", (PyObject *)(uintptr_t)my_iter);
                py_class_add_method(cls, "__next__", (PyObject *)(uintptr_t)my_next);

                PyObject *obj = py_instance_new(cls);
                PyObject *s = py_obj_str(obj);
                if (strcmp(py_str_utf8(s), "custom") != 0) return 1;

                if (py_obj_hash(obj) != 12345) return 2;

                PyObject *it = py_obj_iter(obj);
                PyObject *first = py_obj_next(it);
                PyObject *second = py_obj_next(it);
                if (py_int_to_i64(first, NULL) != 7) return 3;
                if (py_int_to_i64(second, NULL) != 8) return 4;

                PyObject *n = py_obj_next(obj);
                if (py_int_to_i64(n, NULL) != 99) return 5;

                printf("dunder-ok\\n");
                return 0;
            }
            """
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            os.environ.get("CC", "cc"),
            "-I",
            str(c_runtime_archive.parent / "include"),
            "-I",
            str(c_runtime_archive.parent / "src"),
            str(src),
            str(c_runtime_archive),
            "-lm",
            "-o",
            str(exe),
        ],
        check=True,
    )
    proc = subprocess.run([str(exe)], check=True, text=True, capture_output=True)
    assert proc.stdout.strip() == "dunder-ok"


def test_user_dunder_sources_are_wired():
    dunder_c = Path("pcc/py_runtime/src/py_dunder.c").read_text(encoding="utf-8")
    compare_c = Path("pcc/py_runtime/src/py_obj_ops_compare.c").read_text(encoding="utf-8")
    iter_c = Path("pcc/py_runtime/src/py_iter.c").read_text(encoding="utf-8")
    dunder_py = Path("pcc/py_runtime/py/py_dunder.py").read_text(encoding="utf-8")
    iter_py = Path("pcc/py_runtime/py/py_iter.py").read_text(encoding="utf-8")

    assert "py_user_hash_dispatch" in dunder_c
    assert "py_user_iter_dispatch" in dunder_c
    assert "py_user_next_dispatch" in dunder_c
    assert "py_user_hash_dispatch(o, &handled)" in compare_c
    assert "py_user_iter_dispatch(o)" in iter_c
    assert "py_user_next_dispatch(it_obj)" in iter_c
    assert '@c_abi_export("py_user_hash_dispatch")' in dunder_py
    assert "py_user_iter_dispatch" in iter_py
    assert "py_user_next_dispatch" in iter_py
