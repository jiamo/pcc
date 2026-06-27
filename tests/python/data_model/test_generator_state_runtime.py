from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


def test_generator_next_send_finish_and_stop_value_native(
    tmp_path, c_runtime_archive
):
    src = tmp_path / "gen_probe.c"
    exe = tmp_path / "gen_probe"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdio.h>

            static PyObject *resume(PyObject *gen, PyObject *frame) {
                (void)frame;
                int64_t state = py_gen_state(gen);
                if (state == 0) {
                    PyObject *sent = py_gen_take_send(gen);
                    if (sent != py_None) return py_gen_finish(gen, py_int_from_i64(-1));
                    py_gen_set_state(gen, 1);
                    return py_int_from_i64(10);
                }
                if (state == 1) {
                    PyObject *sent = py_gen_take_send(gen);
                    int64_t v = py_int_to_i64(sent, NULL);
                    py_gen_set_state(gen, 2);
                    return py_int_from_i64(v + 1);
                }
                return py_gen_finish(gen, py_int_from_i64(99));
            }

            int main(void) {
                PyObject *frame = py_tuple_new(0);
                PyObject *gen = py_gen_new((void *)resume, frame);

                PyObject *a = py_gen_next(gen);
                if (py_int_to_i64(a, NULL) != 10) return 1;
                if (py_gen_is_done(gen) != 0) return 2;

                PyObject *b = py_gen_send(gen, py_int_from_i64(20));
                if (py_int_to_i64(b, NULL) != 21) return 3;

                PyObject *c = py_gen_next(gen);
                if (c != NULL) return 4;
                PyObject *stop = py_current_exception();
                if (!py_exc_matches(stop, (PyObject *)py_exc_builtin_class(PY_EXC_STOPITERATION))) return 5;
                PyObject *msg = py_exc_get_message(stop);
                if (py_int_to_i64(msg, NULL) != 99) return 6;
                py_clear_exception();

                if (py_gen_is_done(gen) != 1) return 7;

                PyObject *d = py_gen_next(gen);
                if (d != NULL) return 8;
                if (!py_exc_matches(py_current_exception(), (PyObject *)py_exc_builtin_class(PY_EXC_STOPITERATION))) return 9;
                py_clear_exception();

                printf("generator-ok\\n");
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
            str(src),
            str(c_runtime_archive),
            "-lm",
            "-o",
            str(exe),
        ],
        check=True,
    )
    proc = subprocess.run([str(exe)], check=True, text=True, capture_output=True)
    assert proc.stdout.strip() == "generator-ok"


def test_generator_finish_symbols_are_wired():
    c_src = Path("pcc/py_runtime/src/py_gen.c").read_text(encoding="utf-8")
    py_src = Path("pcc/py_runtime/py/py_gen.py").read_text(encoding="utf-8")
    header = Path("pcc/py_runtime/include/py_runtime.h").read_text(encoding="utf-8")
    abi = Path("pcc/py_frontend/codegen/runtime_abi.py").read_text(encoding="utf-8")

    assert "PyObject *py_gen_finish(PyObject *gen, PyObject *value)" in c_src
    assert "int64_t py_gen_is_done(PyObject *gen)" in c_src
    assert '@c_abi_export("py_gen_finish")' in py_src
    assert "PyObject *py_gen_finish(PyObject *gen, PyObject *value);" in header
    assert '"py_gen_finish": (_PYOBJ, [_PYOBJ, _PYOBJ], False)' in abi
