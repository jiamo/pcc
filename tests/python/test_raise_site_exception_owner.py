"""A raise must leave exactly one reference: the thread-local slot's.

`py_raise_normalize` returns `owned = 0` for any `PY_TYPE_EXC`, so `py_raise`
increfs and the caller keeps its own reference.  `py_exc_new` always returns a
fresh `PY_TYPE_EXC`, so the inline form

    py_raise(py_exc_new(PY_EXC_TYPEERROR, "..."));

orphans one exception object per raise.  `py_raise_owned` is the runtime's
raise-then-release form and is the correct idiom for a freshly created
exception.

This gate measures the refcount of whatever the raise left pending.  A
releasing caller leaves 1; the leaking inline form leaves 2.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_gc_threading_substrate import REPO_ROOT, _compile_runtime_probe


@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_generator_type_errors_leave_a_single_pending_reference(
    tmp_path: Path, kind: str
) -> None:
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="raise_site_exception_owner",
        source_text=r'''
            #include "Python.h"
            #include <stdint.h>
            #include <stdio.h>

            extern void *py_tls_exc_get(void);

            /* refcount lives at offset 0 of every heap object header */
            static int64_t pending_rc(void) {
                PyObject *e = (PyObject *)py_tls_exc_get();
                if (e == NULL) return -1;
                return *(int64_t *)(void *)e;
            }

            int main(void) {
                PyObject *not_a_gen = py_str_new("nope", 4);
                if (not_a_gen == NULL) return 2;

                /* Control: a caller that stores and releases leaves one. */
                PyObject *d = py_dict_new();
                PyObject *missing = py_str_new("gone", 4);
                if (d == NULL || missing == NULL) return 3;
                py_clear_exception();
                (void)py_dict_getitem(d, missing);
                int64_t control = pending_rc();
                py_clear_exception();

                /* Subject: generator type checks. */
                py_clear_exception();
                (void)py_gen_state(not_a_gen);
                int64_t state_rc = pending_rc();
                py_clear_exception();

                py_clear_exception();
                py_gen_set_state(not_a_gen, 0);
                int64_t set_rc = pending_rc();
                py_clear_exception();

                /* py_bytes: fromhex on a non-string raises inline. */
                py_clear_exception();
                (void)py_bytes_fromhex(d);
                int64_t hex_rc = pending_rc();
                py_clear_exception();

                /* PyErr_Fetch hands out owned references and PyErr_Restore
                 * steals them back, so a fetch/restore round trip must not
                 * change the pending exception's refcount.  This is why the
                 * three PyErr_Restore sites keep py_raise rather than
                 * py_raise_owned: they already decref what they were given,
                 * and converting them would double free. */
                py_clear_exception();
                (void)py_dict_getitem(d, missing);
                int64_t before_roundtrip = pending_rc();
                {
                    PyObject *ty = NULL, *va = NULL, *tb = NULL;
                    PyErr_Fetch(&ty, &va, &tb);
                    PyErr_Restore(ty, va, tb);
                }
                int64_t after_roundtrip = pending_rc();
                py_clear_exception();

                /* py_list: remove of an absent item raises ValueError. */
                PyObject *lst = py_list_new(0);
                if (lst == NULL) return 5;
                py_clear_exception();
                py_list_remove(lst, missing);
                int64_t remove_rc = pending_rc();
                py_clear_exception();

                if (control != 1 || state_rc != 1 || set_rc != 1
                    || hex_rc != 1 || remove_rc != 1
                    || before_roundtrip != 1 || after_roundtrip != 1) {
                    printf(
                        "pending refcounts: control=%lld gen_state=%lld "
                        "gen_set_state=%lld bytes_fromhex=%lld "
                        "list_remove=%lld fetch_restore=%lld->%lld "
                        "(expected 1 1 1 1 1 1->1)\n",
                        (long long)control,
                        (long long)state_rc,
                        (long long)set_rc,
                        (long long)hex_rc,
                        (long long)remove_rc,
                        (long long)before_roundtrip,
                        (long long)after_roundtrip
                    );
                    return 4;
                }
                return 0;
            }
        ''',
        extra_include_dirs=(REPO_ROOT / "utils" / "fake_libc_include",),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, (
        f"{kind} raise-site exception owner returned {run.returncode}: "
        + run.stdout + run.stderr
    )
