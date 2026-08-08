from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
RUNTIME = REPO / "pcc" / "py_runtime"


def _compile_and_run(tmp_path: Path, archive: Path) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "py_func_fail_closed.c"
    exe = tmp_path / "py_func_fail_closed"
    source.write_text(
        textwrap.dedent(
            r"""
            #include "py_runtime.h"
            #include <stdio.h>
            #include <string.h>

            static PyObject *silent_null(PyObject *captures, PyObject *args) {
                (void)captures;
                (void)args;
                return NULL;
            }

            static PyObject *dimension_error(PyObject *captures, PyObject *args) {
                (void)captures;
                (void)args;
                py_raise_owned(py_exc_new(PY_EXC_VALUEERROR, "bad dimension"));
                return NULL;
            }

            static PyObject *signature_func(int explicit_binding_error) {
                PyObject *inner = py_tuple_new(0);
                PyObject *signature = py_tuple_new(5);
                PyObject *magic = py_str_new("__pcc_func_signature_v1__", 25);
                PyObject *captures = py_tuple_new(2);
                PyObject *names = NULL;
                PyObject *kinds = NULL;
                PyObject *has_defaults = NULL;
                PyObject *defaults = NULL;
                PyObject *name = NULL;
                PyObject *fn = NULL;
                if (
                    inner == NULL || signature == NULL || magic == NULL ||
                    captures == NULL
                ) goto done;

                py_tuple_set_item(signature, 0, magic);
                if (explicit_binding_error) {
                    /* A valid signature marker with inconsistent component
                     * lengths makes the binder raise its own TypeError. */
                    names = py_tuple_new(1);
                    kinds = py_tuple_new(0);
                    has_defaults = py_tuple_new(0);
                    defaults = py_tuple_new(0);
                    name = py_str_new("value", 5);
                    if (
                        names == NULL || kinds == NULL ||
                        has_defaults == NULL || defaults == NULL || name == NULL
                    ) goto done;
                    py_tuple_set_item(names, 0, name);
                    py_tuple_set_item(signature, 1, names);
                    py_tuple_set_item(signature, 2, kinds);
                    py_tuple_set_item(signature, 3, has_defaults);
                    py_tuple_set_item(signature, 4, defaults);
                }
                /* Leaving signature slots 1..4 NULL exercises the internal
                 * binder's historically silent NULL return. */
                py_tuple_set_item(captures, 0, inner);
                py_tuple_set_item(captures, 1, signature);
                fn = py_func_new((void *)silent_null, captures);

            done:
                if (name != NULL) py_decref(name);
                if (defaults != NULL) py_decref(defaults);
                if (has_defaults != NULL) py_decref(has_defaults);
                if (kinds != NULL) py_decref(kinds);
                if (names != NULL) py_decref(names);
                if (captures != NULL) py_decref(captures);
                if (magic != NULL) py_decref(magic);
                if (signature != NULL) py_decref(signature);
                if (inner != NULL) py_decref(inner);
                return fn;
            }

            static int message_is(const char *expected) {
                PyObject *exc = py_current_exception();
                if (exc == NULL) return 0;
                PyObject *message = py_exc_get_message(exc);
                if (message == NULL) return 0;
                const char *text = py_str_utf8(message);
                return text != NULL && strcmp(text, expected) == 0;
            }

            int main(void) {
                PyObject *args = py_tuple_new(0);
                PyObject *silent = py_func_new_named(
                    (void *)silent_null,
                    NULL,
                    "silent_null"
                );
                PyObject *explicit_error = py_func_new(
                    (void *)dimension_error,
                    NULL
                );
                PyObject *silent_binding = signature_func(0);
                PyObject *invalid_signature = signature_func(1);
                if (
                    args == NULL || silent == NULL || explicit_error == NULL ||
                    silent_binding == NULL || invalid_signature == NULL
                ) return 10;

                py_clear_exception();
                PyObject *out = py_func_call_kwargs(silent, args, NULL);
                if (out != NULL) return 11;
                if (!py_err_occurred()) return 12;
                if (!message_is(
                    "compiled native function returned NULL without exception"
                )) return 13;
                if (py_exc_traceback_len(py_current_exception()) != 1) return 35;
                PyObject *traceback = py_exc_traceback_format_exc(
                    py_current_exception()
                );
                if (traceback == NULL) return 36;
                const char *traceback_text = py_str_utf8(traceback);
                if (traceback_text == NULL) return 37;
                if (strstr(
                    traceback_text,
                    "File \"<pcc runtime>\", line 0, in silent_null"
                ) == NULL) return 38;
                if (strstr(
                    traceback_text,
                    "runtime contract: NULL result without an exception"
                ) == NULL) return 39;
                py_decref(traceback);

                py_clear_exception();
                out = py_func_call_kwargs(explicit_error, args, NULL);
                if (out != NULL) return 14;
                if (!py_err_occurred()) return 15;
                if (!message_is("bad dimension")) return 16;
                if (py_exc_traceback_len(py_current_exception()) != 0) return 40;

                py_clear_exception();
                out = py_func_call_kwargs(py_int_from_i64(7), args, NULL);
                if (out != NULL) return 17;
                if (!py_err_occurred()) return 18;
                if (!message_is(
                    "native function call requires a function object"
                )) return 19;

                py_clear_exception();
                void **entry_slot = (void **)((char *)silent + 56);
                void *saved_entry = *entry_slot;
                *entry_slot = NULL;
                out = py_func_call_kwargs(silent, args, NULL);
                *entry_slot = saved_entry;
                if (out != NULL) return 20;
                if (!py_err_occurred()) return 21;
                if (!message_is("native function object has no entry point")) {
                    return 22;
                }

                py_clear_exception();
                out = py_func_call_kwargs(silent_binding, args, NULL);
                if (out != NULL) return 23;
                if (!py_err_occurred()) return 24;
                if (!message_is(
                    "native function argument binding returned NULL without exception"
                )) return 25;

                py_clear_exception();
                out = py_func_call_kwargs(invalid_signature, args, NULL);
                if (out != NULL) return 26;
                if (!py_err_occurred()) return 27;
                if (!message_is("invalid native function signature")) return 28;

                py_clear_exception();
                out = py_obj_call(silent, args, NULL);
                if (out != NULL) return 29;
                if (!py_err_occurred()) return 30;
                if (!message_is(
                    "compiled native function returned NULL without exception"
                )) return 31;
                if (py_exc_traceback_len(py_current_exception()) != 1) return 41;

                py_clear_exception();
                out = py_obj_call(explicit_error, args, NULL);
                if (out != NULL) return 32;
                if (!py_err_occurred()) return 33;
                if (!message_is("bad dimension")) return 34;

                py_clear_exception();
                py_decref(invalid_signature);
                py_decref(silent_binding);
                py_decref(explicit_error);
                py_decref(silent);
                py_decref(args);
                puts("py-func-fail-closed-ok");
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    command = [
        os.environ.get("CC", "cc"),
        "-std=c11",
        f"-I{RUNTIME / 'include'}",
        str(source),
        str(archive),
        "-pthread",
        "-lm",
    ]
    if sys.platform.startswith("linux"):
        command.append("-ldl")
    command.extend(["-o", str(exe)])
    built = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize(
    "archive_fixture",
    ["c_runtime_archive", "pcc_py_runtime_archive"],
)
def test_py_func_null_result_sets_or_preserves_exception(
    archive_fixture,
    request,
    tmp_path,
):
    archive = request.getfixturevalue(archive_fixture)
    run = _compile_and_run(tmp_path, archive)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout == "py-func-fail-closed-ok\n"


def test_py_func_call_kwargs_fail_closed_contract_is_mirrored():
    c_source = (RUNTIME / "src" / "py_func.c").read_text(encoding="utf-8")
    py_source = (RUNTIME / "py" / "py_func.py").read_text(encoding="utf-8")

    for message in (
        "native function call received NULL callable",
        "native function call requires a function object",
        "native function object has no entry point",
        "native function could not create its argument tuple",
        "native function signature has no captures tuple",
        "native function argument binding returned NULL without exception",
        "compiled native function returned NULL without exception",
    ):
        assert message in c_source
        assert message in py_source
    assert "if (result == NULL)" in c_source
    assert "if ptr_is_null(result):" in py_source
    assert "if (py_err_occurred()) return NULL;" in c_source
    assert "if py_err_occurred() != 0:" in py_source

    c_binding_guard = c_source.index(
        '"native function argument binding returned NULL without exception"'
    )
    c_binding_cleanup = c_source.index("py_decref(sig);", c_binding_guard)
    assert c_binding_guard < c_binding_cleanup

    py_binding_guard = py_source.index(
        '"native function argument binding returned NULL without exception"'
    )
    py_binding_cleanup = py_source.index("py_decref(sig)", py_binding_guard)
    assert py_binding_guard < py_binding_cleanup

    c_entry_call = c_source.index("PyObject *result = f->entry(")
    c_entry_guard = c_source.index(
        '"compiled native function returned NULL without exception"',
        c_entry_call,
    )
    c_entry_cleanup = c_source.index(
        "if (bound_args) py_decref(call_args);",
        c_entry_call,
    )
    assert c_entry_call < c_entry_guard < c_entry_cleanup

    py_entry_call = py_source.index("result = call_ptr2(")
    py_entry_guard = py_source.index(
        '"compiled native function returned NULL without exception"',
        py_entry_call,
    )
    py_entry_cleanup = py_source.index("if owns_call_args != 0:", py_entry_call)
    assert py_entry_call < py_entry_guard < py_entry_cleanup
