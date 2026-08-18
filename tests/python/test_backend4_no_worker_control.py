"""No-worker control for GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED.

Runs the displaced-value accounting of the concurrent commit-race probes
on COLORED_RELOCATING with NO second thread: same replace churn, same
drain, same exact-count and survivor assertions.  If this passes, the
intermittent "more finalizations than displaced values" on backend 4 is
concurrency-dependent; if it fails, the accounting (or single-threaded
backend-4 reclamation) is the defect.
"""
import subprocess
from pathlib import Path

import pytest

from tests.python.test_gc_threading_substrate import (
    _compile_runtime_probe,
    ALL_GC_KINDS,
)

pytestmark = pytest.mark.parametrize("kind", ["c", "pcc_python"])


@pytest.mark.parametrize("gc_kind", ["PCC_GC_KIND_COLORED_RELOCATING"])
def test_backend4_displaced_accounting_no_worker(
    tmp_path: Path, kind: str, gc_kind: str
) -> None:
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=False,
        stem="b4_no_worker_control_" + gc_kind.lower(),
        source_text=r"""
            #include "py_internal.h"
            #include <stdio.h>

            #define ROUNDS 64

            static PyObject *dict_root;
            static PyObject *key_root;
            static struct PyClassObject *value_class;
            static int64_t finalized;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static int64_t refcount_of(PyObject *obj) {
                return pcc_refcount_load(&((PyObjectHeader *)obj)->refcount);
            }

            static PyObject *probe_del(PyObject *self) {
                (void)self;
                __atomic_add_fetch(&finalized, 1, __ATOMIC_ACQ_REL);
                return py_int_from_i64(0);
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;
                value_class = py_class_new("CtlValue", NULL, 0, NULL, 0);
                if (value_class == NULL) return 3;
                pcc_gc_pin((PyObject *)value_class);
                py_class_add_method(
                    value_class, "__del__",
                    (PyObject *)(uintptr_t)probe_del
                );

                dict_root = py_dict_new();
                key_root = py_int_from_i64(5);
                if (dict_root == NULL || key_root == NULL) return 4;
                void *d_h = pcc_gc_scheduler_root_register_handle(&dict_root);
                void *k_h = pcc_gc_scheduler_root_register_handle(&key_root);
                if (d_h == NULL || k_h == NULL) return 5;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                PyObject *last = NULL;
                int64_t inserted = 0;
                for (int i = 0; i < ROUNDS; i++) {
                    PyObject *v = py_instance_new(value_class);
                    if (v == NULL) return 8;
                    py_dict_set(pcc_gc_load_ptr(NULL, &dict_root),
                                pcc_gc_load_ptr(NULL, &key_root), v);
                    inserted++;
                    last = v;   /* borrowed identity; dict owns storage */
                    py_decref(v);
                    if (py_err_occurred() != NULL) {
                        printf("insert %d left an exception pending\n", i);
                        return 9;
                    }
                }
                dict_root = pcc_gc_load_ptr(NULL, &dict_root);

                if (py_dict_len(dict_root) != 1) {
                    printf("len=%lld (expected 1)\n",
                           (long long)py_dict_len(dict_root));
                    return 11;
                }
                PyObject *got = py_dict_get(dict_root, key_root);
                if (got == NULL || got != last) {
                    printf("survivor lost or not the last inserted\n");
                    return 12;
                }

                for (int i = 0; i < 16; i++) {
                    (void)pcc_gc_collect(0);
                }

                int64_t seen = __atomic_load_n(&finalized, __ATOMIC_ACQUIRE);
                if (seen != inserted - 1) {
                    printf("finalized=%lld expected=%lld (%s)\n",
                           (long long)seen, (long long)(inserted - 1),
                           seen > inserted - 1 ? "premature free" : "leak");
                    return 14;
                }
                if (refcount_of(last) != 2) {   /* dict + get ref */
                    printf("survivor rc=%lld (expected 2)\n",
                           (long long)refcount_of(last));
                    return 29;
                }
                py_decref(got);
                got = py_dict_get(dict_root, key_root);
                if (got == NULL || refcount_of(got) != 2) {
                    printf("dict + fresh get ref rc wrong\n");
                    return 30;
                }
                py_decref(got);

                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 15;
                }

                py_decref(pcc_gc_load_ptr(NULL, &key_root));
                pcc_gc_scheduler_root_unregister_handle(k_h);
                pcc_gc_scheduler_root_unregister_handle(d_h);
                py_decref(pcc_gc_load_ptr(NULL, &dict_root));
                py_decref((PyObject *)value_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, (
        f"{kind} no-worker control returned {run.returncode}: "
        + run.stdout + run.stderr
    )
