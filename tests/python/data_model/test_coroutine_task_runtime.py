from __future__ import annotations

import os
import subprocess
import textwrap


def test_coroutine_and_task_done_result_runtime(tmp_path, c_runtime_archive):
    src = tmp_path / "coro_task_probe.c"
    exe = tmp_path / "coro_task_probe"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdio.h>

            static PyObject *entry(PyObject *captures, PyObject *args) {
                (void)captures; (void)args;
                return py_int_from_i64(42);
            }

            int main(void) {
                PyObject *coro = py_coroutine_new_native("coro", (void *)entry, NULL, NULL);
                if (py_coroutine_is_done(coro) != 0) return 1;
                PyObject *result = py_await(coro);
                if (py_int_to_i64(result, NULL) != 42) return 2;
                if (py_coroutine_is_done(coro) != 1) return 3;
                PyObject *cached = py_coroutine_get_result(coro);
                if (py_int_to_i64(cached, NULL) != 42) return 4;

                PyObject *coro2 = py_coroutine_new_native("task-coro", (void *)entry, NULL, NULL);
                PyObject *task = py_task_new(coro2);
                if (py_task_is_done(task) != 0) return 5;
                PyObject *task_result = py_task_step(task);
                if (py_int_to_i64(task_result, NULL) != 42) return 6;
                if (py_task_is_done(task) != 1) return 7;
                printf("coro-task-ok\\n");
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
            str(src), str(c_runtime_archive),
            "-lm", "-o", str(exe),
        ],
        check=True,
    )
    proc = subprocess.run([str(exe)], check=True, text=True, capture_output=True)
    assert proc.stdout.strip() == "coro-task-ok"
