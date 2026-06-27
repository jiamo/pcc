from __future__ import annotations

import os
import subprocess
import textwrap


def test_context_enter_exit_runtime(tmp_path, c_runtime_archive):
    src = tmp_path / "context_probe.c"
    exe = tmp_path / "context_probe"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_internal.h"
            #include <stdio.h>
            #include <stdint.h>
            #include <string.h>

            static PyObject *enter(PyObject *self) {
                (void)self;
                return py_str_new("entered", 7);
            }

            static PyObject *exit_(PyObject *self, PyObject *exc_type, PyObject *exc, PyObject *tb) {
                (void)self; (void)exc_type; (void)exc; (void)tb;
                return py_bool_from_bit(1);
            }

            int main(void) {
                PyClassObject *cls = py_class_new("CM", NULL, 0, NULL, 0);
                py_class_add_method(cls, "__enter__", (PyObject *)(uintptr_t)enter);
                py_class_add_method(cls, "__exit__", (PyObject *)(uintptr_t)exit_);
                PyObject *obj = py_instance_new(cls);
                PyObject *value = py_context_enter(obj);
                if (strcmp(py_str_utf8(value), "entered") != 0) return 1;
                if (py_context_exit(obj, py_None, py_None, py_None) != 1) return 2;
                printf("context-ok\\n");
                return 0;
            }
            """
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            os.environ.get("CC", "cc"),
            "-I", str(c_runtime_archive.parent / "include"),
            "-I", str(c_runtime_archive.parent / "src"),
            str(src), str(c_runtime_archive),
            "-lm", "-o", str(exe),
        ],
        check=True,
    )
    proc = subprocess.run([str(exe)], check=True, text=True, capture_output=True)
    assert proc.stdout.strip() == "context-ok"
