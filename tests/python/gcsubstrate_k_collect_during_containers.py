"""Collect-during-container-op liveness family and dict update snapshots.

Split from ``test_gc_threading_substrate.py``; collected only through
that facade so pytest node ids stay stable.
"""
from _gc_substrate_common import *  # noqa: F401,F403




@pytest.mark.parametrize("phase", ["contains", "remove"])
@pytest.mark.parametrize("gc_kind", ALL_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_collect_during_list_op_keeps_list_and_collector_live(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
    phase: str,
) -> None:
    """The list and tuple half of the collect-during-mutation class.

    Two phases, differing only in whether the operation holding the callback
    mutates:

    ``contains``  equality runs mid-scan, nothing is committed.  Green on all
                  five backends.
    ``remove``    equality runs mid-scan and then the list commits a removal
                  and releases the element.  **Currently red on backends 1, 3
                  and 4.**  The removal itself is correct on every arm --
                  length, order, and exactly-once finalization of the removed
                  element all hold.  What differs is that the control cycle is
                  reclaimed when the collect is driven from ``contains`` and not
                  when it is driven from ``remove``.  Tracked as
                  GC-P1-COLLECT-INSIDE-LIST-REMOVE-LEAVES-CYCLE-UNCOLLECTED.

    Read the task row before theorizing: it is **not** established that the
    collector stops working.  Measured on both arms at the same point,
    ``pcc_gc_has_tracing_sweep`` and ``pcc_gc_step`` report the same idle state,
    and a second unreachable cycle created late in the probe is uncollected on
    the healthy arm too -- so neither "the collector is dead" nor "later
    collects do nothing" survives its control.

    Tuples are deliberately absent: immutable, so the mutation-commit class
    does not apply, and ``tuple.index`` is the same scan shape as
    ``list.contains``.  (``py_tuple_index`` also does not link against the
    reduced runtime archive these probes use.)
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="collect_during_list_" + phase + "_" + gc_kind.lower(),
        source_text=r"""
            #include "Python.h"
            #include <stdint.h>
            #include <stdio.h>

            typedef struct ProbeElemObject {
                PyObject_HEAD
            } ProbeElemObject;

            static PyObject *list_root;
            static PyObject *victim_root;
            static PyObject *keeper_root;
            static PyObject *other_root;
            static PyObject *target_root;
            static struct PyClassObject *victim_class;
            static struct PyClassObject *control_class;
            static int64_t armed;
            static int64_t remove_collects;
            static int64_t contains_collects;
            static int64_t victim_finalized;
            static int64_t control_finalized;
            /* Single-variable switch: count the callback but skip its
             * collect, to separate 'the in-op collect wedged the
             * collector' from 'this probe never had anything to collect'. */
            static int64_t collect_in_remove = __ARM_REMOVE__;
            static int64_t collect_in_contains = __ARM_CONTAINS__;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            /* Equality on the probe's own target object.  Collects once per
             * armed phase, then answers by identity against the object the
             * phase is looking for. */
            static PyObject *probe_eq(PyObject *self, PyObject *other, int op) {
                (void)self;
                if (op != Py_EQ) {
                    Py_INCREF(Py_NotImplemented);
                    return Py_NotImplemented;
                }
                PyObject *want = NULL;
                if (armed == 1) {
                    if (remove_collects == 0) {
                        if (collect_in_remove)
                            (void)pcc_gc_collect(0);
                        remove_collects++;
                    }
                    want = pcc_gc_load_ptr(NULL, &victim_root);
                } else if (armed == 2) {
                    if (contains_collects == 0) {
                        if (collect_in_contains)
                            (void)pcc_gc_collect(0);
                        contains_collects++;
                    }
                    want = pcc_gc_load_ptr(NULL, &keeper_root);
                }
                if (want != NULL && other == want) {
                    Py_INCREF(Py_True);
                    return Py_True;
                }
                Py_INCREF(Py_False);
                return Py_False;
            }

            static PyObject *probe_victim_del(PyObject *self) {
                (void)self;
                victim_finalized++;
                Py_INCREF(Py_None);
                return Py_None;
            }

            static PyObject *probe_control_del(PyObject *self) {
                (void)self;
                control_finalized++;
                Py_INCREF(Py_None);
                return Py_None;
            }

            static PyTypeObject ProbeElemType = {
                PyVarObject_HEAD_INIT(NULL, 0)
                .tp_name = "pcc_probe.ListProbeElem",
                .tp_basicsize = sizeof(ProbeElemObject),
                .tp_flags = Py_TPFLAGS_DEFAULT,
                .tp_richcompare = probe_eq,
            };

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;

                if (PyType_Ready(&ProbeElemType) != 0) return 3;

                victim_class = py_class_new("ListVictim", NULL, 0, NULL, 0);
                control_class = py_class_new("ListControl", NULL, 0, NULL, 0);
                if (victim_class == NULL || control_class == NULL) return 4;
                pcc_gc_pin((PyObject *)victim_class);
                pcc_gc_pin((PyObject *)control_class);
                py_class_add_method(
                    victim_class, "__del__",
                    (PyObject *)(uintptr_t)probe_victim_del
                );
                py_class_add_method(
                    control_class, "__del__",
                    (PyObject *)(uintptr_t)probe_control_del
                );

                PyObject *cyc_a = py_dict_new();
                PyObject *cyc_b = py_dict_new();
                PyObject *control = py_instance_new(control_class);
                PyObject *link = py_str_new("link", 4);
                PyObject *held = py_str_new("held", 4);
                if (cyc_a == NULL || cyc_b == NULL || control == NULL
                    || link == NULL || held == NULL) return 5;
                py_dict_set(cyc_a, link, cyc_b);
                py_dict_set(cyc_b, link, cyc_a);
                py_dict_set(cyc_a, held, control);
                py_decref(control);
                py_decref(cyc_a);
                py_decref(cyc_b);
                py_decref(link);
                py_decref(held);

                list_root = py_list_new(0);
                if (list_root == NULL) return 6;
                keeper_root = py_str_new("keeper", 6);
                other_root = py_str_new("other", 5);
                victim_root = py_instance_new(victim_class);
                if (keeper_root == NULL || other_root == NULL
                    || victim_root == NULL) return 7;
                py_list_append(list_root, keeper_root);
                py_list_append(list_root, victim_root);
                py_list_append(list_root, other_root);
                py_decref(victim_root);  /* the list holds it */

                void *l_h = pcc_gc_scheduler_root_register_handle(&list_root);
                void *v_h = pcc_gc_scheduler_root_register_handle(&victim_root);
                void *k_h = pcc_gc_scheduler_root_register_handle(&keeper_root);
                void *o_h = pcc_gc_scheduler_root_register_handle(&other_root);
                if (l_h == NULL || v_h == NULL || k_h == NULL
                    || o_h == NULL) return 8;

                /* The collect driven from the callback performs a real
                 * mark+sweep, so an object reachable only from a C local is
                 * garbage to it.  Every other pointer this probe needs across
                 * the callback is a registered root; this one was an omission,
                 * and it surfaced only once the drain stopped exiting early and
                 * the sweep actually ran. */
                target_root = (PyObject *)PyObject_New(
                    ProbeElemObject, &ProbeElemType
                );
                if (target_root == NULL) return 9;
                void *target_h = pcc_gc_scheduler_root_register_handle(
                    &target_root
                );
                if (target_h == NULL) return 25;

                /* ---- list.remove: equality collects mid-scan, then the list
                 *      commits the removal and releases the element ---- */
                armed = 1;
                py_list_remove(list_root, pcc_gc_load_ptr(NULL, &target_root));
                armed = 0;
                if (py_err_occurred() != NULL) {
                    printf("list.remove left an exception pending\n");
                    return 26;
                }
                list_root = pcc_gc_load_ptr(NULL, &list_root);
                keeper_root = pcc_gc_load_ptr(NULL, &keeper_root);
                other_root = pcc_gc_load_ptr(NULL, &other_root);

                if (remove_collects != 1) {
                    printf("list.remove did not run the probe equality: "
                           "collects=%lld -- the runtime compares from the "
                           "other side, so this phase proved nothing\n",
                           (long long)remove_collects);
                    return 10;
                }
                if (py_list_len(list_root) != 2) {
                    printf("list.remove left len=%lld (expected 2)\n",
                           (long long)py_list_len(list_root));
                    return 11;
                }
                PyObject *at0 = py_list_get(list_root, 0);
                PyObject *at1 = py_list_get(list_root, 1);
                if (py_err_occurred() != NULL) {
                    printf("py_list_get on a valid index raised\n");
                    return 27;
                }
                int order_ok = (at0 == keeper_root && at1 == other_root);
                if (at0 != NULL) py_decref(at0);   /* py_list_get: new ref */
                if (at1 != NULL) py_decref(at1);
                if (!order_ok) {
                    printf("list order damaged across the collect\n");
                    return 12;
                }

                /* The removed element is gone: py_list_remove released the
                 * list's only reference and its finalizer has run.  Its
                 * scheduler root now points at freed memory, so retire it
                 * before anything else scans the root set.  Leaving it
                 * registered made the contains phase and every later collect
                 * walk a dangling root. */
                pcc_gc_scheduler_root_unregister_handle(v_h);
                victim_root = NULL;

                /* ---- list.contains: equality collects, nothing mutates ---- */
                armed = 2;
                int64_t found = py_list_contains(list_root, pcc_gc_load_ptr(NULL, &target_root));
                armed = 0;
                if (py_err_occurred() != NULL) {
                    printf("list.contains left an exception pending\n");
                    return 28;
                }
                list_root = pcc_gc_load_ptr(NULL, &list_root);
                keeper_root = pcc_gc_load_ptr(NULL, &keeper_root);
                if (contains_collects != 1) {
                    printf("list.contains did not run the probe equality\n");
                    return 13;
                }
                if (found != 1) {
                    printf("list.contains answered %lld (expected 1)\n",
                           (long long)found);
                    return 14;
                }
                if (py_list_len(list_root) != 2) {
                    printf("list.contains changed the list\n");
                    return 15;
                }

                int64_t roots_before = pcc_gc_scheduler_root_count();

                /* Backends 1 and 2 request a cycle from the allocator, so a
                 * drain loop with no allocation in it can find nothing to do. */
                int64_t collected_total = 0;
                for (int i = 0; i < 8; i++) {
                    PyObject *churn = py_dict_new();
                    if (churn != NULL) py_decref(churn);
                    collected_total += pcc_gc_collect(0);
                    for (int j = 0; j < 64; j++) (void)pcc_gc_step(64);
                }
                list_root = pcc_gc_load_ptr(NULL, &list_root);

                if (victim_finalized != 1) {
                    printf("removed element finalized %lld times "
                           "(expected exactly 1)\n",
                           (long long)victim_finalized);
                    return 21;
                }
                if (control_finalized == 0) {
                    printf("control cycle was never collected: the tracing "
                           "sweep did not run, so this probe is vacuous "
                           "(collect returned %lld total, remove=%lld "
                           "contains=%lld victim_final=%lld)\n",
                           (long long)collected_total,
                           (long long)remove_collects,
                           (long long)contains_collects,
                           (long long)victim_finalized);
                    return 22;
                }
                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 23;
                }

                pcc_gc_scheduler_root_unregister_handle(target_h);
                Py_DECREF(pcc_gc_load_ptr(NULL, &target_root));
                py_decref(pcc_gc_load_ptr(NULL, &keeper_root));
                py_decref(pcc_gc_load_ptr(NULL, &other_root));
                pcc_gc_scheduler_root_unregister_handle(k_h);
                pcc_gc_scheduler_root_unregister_handle(o_h);
                pcc_gc_scheduler_root_unregister_handle(l_h);
                py_decref(pcc_gc_load_ptr(NULL, &list_root));
                pcc_gc_unpin((PyObject *)victim_class);
                pcc_gc_unpin((PyObject *)control_class);
                py_decref((PyObject *)victim_class);
                py_decref((PyObject *)control_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind)
        .replace("__ARM_REMOVE__", "1" if phase == "remove" else "0")
        .replace("__ARM_CONTAINS__", "1" if phase == "contains" else "0"),
        extra_include_dirs=(REPO_ROOT / "utils" / "fake_libc_include",),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} collect-during-list-{phase} returned "
        f"{run.returncode}: " + run.stdout + run.stderr
    )


@pytest.mark.parametrize("gc_kind", ALL_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_collect_during_set_add_update_discard_keeps_set_consistent(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """The set counterpart of the dict collect-during-mutation probes.

    ``py_set_add`` publishes table, size and fill; ``py_set_update`` walks a
    source across destination callbacks; discard commits a tombstone and
    releases the stored element.  Each is a window in which a callback-driven
    mark/sweep can act on a half-committed set, and none was covered outside
    the relocating backend.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="collect_during_set_ops_" + gc_kind.lower(),
        source_text=r"""
            #include "Python.h"
            #include <stdint.h>
            #include <stdio.h>

            static PyObject *set_root;
            static PyObject *src_root;
            static PyObject *e0_root;
            static PyObject *e1_root;
            static PyObject *e2_root;
            static struct PyClassObject *hash_class;
            static struct PyClassObject *control_class;
            static int64_t armed;
            static int64_t add_collects;
            static int64_t update_collects;
            static int64_t discard_collects;
            static int64_t control_finalized;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                if (armed == 1 && add_collects == 0) {
                    (void)pcc_gc_collect(0);
                    add_collects++;
                } else if (armed == 2 && update_collects == 0) {
                    (void)pcc_gc_collect(0);
                    update_collects++;
                } else if (armed == 3 && discard_collects == 0) {
                    (void)pcc_gc_collect(0);
                    discard_collects++;
                }
                return py_int_from_i64(7);
            }

            /* One control per armed phase, checked immediately after that
             * phase.  A single control checked at the end only shows that SOME
             * later collect swept, which is not the claim being made. */
            static int make_control(void) {
                PyObject *a = py_dict_new();
                PyObject *b = py_dict_new();
                PyObject *c = py_instance_new(control_class);
                PyObject *l = py_str_new("link", 4);
                PyObject *h = py_str_new("held", 4);
                if (a == NULL || b == NULL || c == NULL
                    || l == NULL || h == NULL) return 0;
                py_dict_set(a, l, b);
                py_dict_set(b, l, a);
                py_dict_set(a, h, c);
                py_decref(c);
                py_decref(a);
                py_decref(b);
                py_decref(l);
                py_decref(h);
                return 1;
            }

            static PyObject *probe_control_del(PyObject *self) {
                (void)self;
                control_finalized++;
                Py_INCREF(Py_None);
                return Py_None;
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;

                hash_class = py_class_new("SetCollectingKey", NULL, 0, NULL, 0);
                control_class = py_class_new("SetControl", NULL, 0, NULL, 0);
                if (hash_class == NULL || control_class == NULL) return 3;
                pcc_gc_pin((PyObject *)hash_class);
                pcc_gc_pin((PyObject *)control_class);
                py_class_add_method(
                    hash_class, "__hash__", (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    control_class, "__del__",
                    (PyObject *)(uintptr_t)probe_control_del
                );

                PyObject *cyc_a = py_dict_new();
                PyObject *cyc_b = py_dict_new();
                PyObject *control = py_instance_new(control_class);
                PyObject *link = py_str_new("link", 4);
                PyObject *held = py_str_new("held", 4);
                if (cyc_a == NULL || cyc_b == NULL || control == NULL
                    || link == NULL || held == NULL) return 4;
                py_dict_set(cyc_a, link, cyc_b);
                py_dict_set(cyc_b, link, cyc_a);
                py_dict_set(cyc_a, held, control);
                py_decref(control);
                py_decref(cyc_a);
                py_decref(cyc_b);
                py_decref(link);
                py_decref(held);

                set_root = py_set_new();
                src_root = py_set_new();
                if (set_root == NULL || src_root == NULL) return 5;
                e0_root = py_instance_new(hash_class);
                e1_root = py_instance_new(hash_class);
                e2_root = py_instance_new(hash_class);
                if (e0_root == NULL || e1_root == NULL
                    || e2_root == NULL) return 6;

                void *s_h = pcc_gc_scheduler_root_register_handle(&set_root);
                void *sr_h = pcc_gc_scheduler_root_register_handle(&src_root);
                void *e0_h = pcc_gc_scheduler_root_register_handle(&e0_root);
                void *e1_h = pcc_gc_scheduler_root_register_handle(&e1_root);
                void *e2_h = pcc_gc_scheduler_root_register_handle(&e2_root);
                if (s_h == NULL || sr_h == NULL || e0_h == NULL
                    || e1_h == NULL || e2_h == NULL) return 7;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                /* ---- add ---- */
                armed = 1;
                if (!make_control()) return 90;
                int64_t ctrl_add = control_finalized;
                py_set_add(set_root, e0_root);
                armed = 0;
                if (control_finalized == ctrl_add) {
                    printf("the add callback's collect swept "
                           "nothing: that phase proves nothing\\n");
                    return 91;
                }
                set_root = pcc_gc_load_ptr(NULL, &set_root);
                e0_root = pcc_gc_load_ptr(NULL, &e0_root);
                if (py_err_occurred() != NULL) {
                    printf("set operation left an exception pending\n");
                    return 99;
                }
                if (add_collects != 1) {
                    printf("add callback did not collect: %lld\n",
                           (long long)add_collects);
                    return 8;
                }
                if (py_set_len(set_root) != 1
                    || py_set_contains(set_root, e0_root) != 1) {
                    printf("add lost the element: len=%lld\n",
                           (long long)py_set_len(set_root));
                    return 9;
                }

                /* ---- update ---- */
                py_set_add(src_root, e1_root);
                py_set_add(src_root, e2_root);
                src_root = pcc_gc_load_ptr(NULL, &src_root);
                e1_root = pcc_gc_load_ptr(NULL, &e1_root);
                e2_root = pcc_gc_load_ptr(NULL, &e2_root);
                if (py_err_occurred() != NULL) {
                    printf("set operation left an exception pending\n");
                    return 99;
                }
                armed = 2;
                if (!make_control()) return 92;
                int64_t ctrl_update = control_finalized;
                py_set_update(set_root, pcc_gc_load_ptr(NULL, &src_root));
                armed = 0;
                if (control_finalized == ctrl_update) {
                    printf("the update callback's collect swept "
                           "nothing: that phase proves nothing\\n");
                    return 93;
                }
                set_root = pcc_gc_load_ptr(NULL, &set_root);
                src_root = pcc_gc_load_ptr(NULL, &src_root);
                e1_root = pcc_gc_load_ptr(NULL, &e1_root);
                e2_root = pcc_gc_load_ptr(NULL, &e2_root);
                if (py_err_occurred() != NULL) {
                    printf("set operation left an exception pending\n");
                    return 99;
                }
                if (update_collects != 1) {
                    printf("update callback did not collect: %lld\n",
                           (long long)update_collects);
                    return 10;
                }
                if (py_set_len(set_root) != 3) {
                    printf("update lost elements: len=%lld (expected 3)\n",
                           (long long)py_set_len(set_root));
                    return 11;
                }
                if (py_set_contains(set_root, e1_root) != 1
                    || py_set_contains(set_root, e2_root) != 1
                    || py_set_contains(set_root, e0_root) != 1) {
                    printf("update dropped a member across the collect\n");
                    return 12;
                }

                /* ---- discard ---- */
                armed = 3;
                if (!make_control()) return 94;
                int64_t ctrl_discard = control_finalized;
                (void)py_set_remove(
                    set_root, pcc_gc_load_ptr(NULL, &e1_root)
                );
                armed = 0;
                if (control_finalized == ctrl_discard) {
                    printf("the discard callback's collect swept "
                           "nothing: that phase proves nothing\\n");
                    return 95;
                }
                if (py_err_occurred() != NULL) {
                    printf("set operation left an exception pending\n");
                    return 99;
                }
                set_root = pcc_gc_load_ptr(NULL, &set_root);
                e0_root = pcc_gc_load_ptr(NULL, &e0_root);
                e1_root = pcc_gc_load_ptr(NULL, &e1_root);
                e2_root = pcc_gc_load_ptr(NULL, &e2_root);
                if (discard_collects != 1) {
                    printf("discard callback did not collect: %lld\n",
                           (long long)discard_collects);
                    return 13;
                }
                if (py_set_len(set_root) != 2) {
                    printf("discard left len=%lld (expected 2)\n",
                           (long long)py_set_len(set_root));
                    return 14;
                }
                if (py_set_contains(set_root, e1_root) != 0) {
                    printf("discarded element still present\n");
                    return 15;
                }
                if (py_set_contains(set_root, e0_root) != 1
                    || py_set_contains(set_root, e2_root) != 1) {
                    printf("discard removed a survivor\n");
                    return 16;
                }

                for (int i = 0; i < 8; i++) {
                    (void)pcc_gc_collect(0);
                    for (int j = 0; j < 64; j++) (void)pcc_gc_step(64);
                }
                set_root = pcc_gc_load_ptr(NULL, &set_root);

                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 18;
                }

                py_decref(pcc_gc_load_ptr(NULL, &e0_root));
                py_decref(pcc_gc_load_ptr(NULL, &e1_root));
                py_decref(pcc_gc_load_ptr(NULL, &e2_root));
                pcc_gc_scheduler_root_unregister_handle(e0_h);
                pcc_gc_scheduler_root_unregister_handle(e1_h);
                pcc_gc_scheduler_root_unregister_handle(e2_h);
                pcc_gc_scheduler_root_unregister_handle(sr_h);
                pcc_gc_scheduler_root_unregister_handle(s_h);
                py_decref(pcc_gc_load_ptr(NULL, &src_root));
                py_decref(pcc_gc_load_ptr(NULL, &set_root));
                pcc_gc_unpin((PyObject *)hash_class);
                pcc_gc_unpin((PyObject *)control_class);
                py_decref((PyObject *)hash_class);
                py_decref((PyObject *)control_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind),
        extra_include_dirs=(REPO_ROOT / "utils" / "fake_libc_include",),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} collect-during-set-ops returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("gc_kind", ALL_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_collect_during_update_and_delete_keeps_container_consistent(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """The insert sibling, for the update and delete paths.

    ``collect_during_insert`` covered publication.  Update walks a source
    across destination callbacks and delete commits a tombstone and releases a
    displaced value, so each has its own window in which a callback-driven
    mark/sweep can act on a half-committed container.  Both were probed only on
    the relocating backend.

    Every pointer the probe needs after a collect is a registered root: the
    collect relocates under backend 4, and reading a moved pointer afterwards
    produces a convincing false finding -- that mistake cost one round on the
    insert probe already.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="collect_during_update_delete_" + gc_kind.lower(),
        source_text=r"""
            #include "Python.h"
            #include <stdint.h>
            #include <stdio.h>

            static PyObject *dst_root;
            static PyObject *src_root;
            static PyObject *del_root;
            static PyObject *k0_root;
            static PyObject *k1_root;
            static PyObject *del_key_root;
            static PyObject *keep_key_root;
            static struct PyClassObject *hash_class;
            static struct PyClassObject *victim_class;
            static struct PyClassObject *control_class;
            static int64_t armed;
            static int64_t update_collects;
            static int64_t delete_collects;
            static int64_t victim_finalized;
            static int64_t control_finalized;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                if (armed == 1 && update_collects == 0) {
                    (void)pcc_gc_collect(0);
                    update_collects++;
                } else if (armed == 2 && delete_collects == 0) {
                    (void)pcc_gc_collect(0);
                    delete_collects++;
                }
                return py_int_from_i64(7);
            }

            /* The value removed by the delete.  Exactly one finalization. */
            static PyObject *probe_victim_del(PyObject *self) {
                (void)self;
                victim_finalized++;
                Py_INCREF(Py_None);
                return Py_None;
            }

            /* Control: only a real tracing sweep can reclaim this cycle.  If it
             * never fires, the collects above did nothing and every assertion
             * below is vacuous. */
            /* One control per armed phase, checked immediately after that
             * phase.  A single control checked at the end only shows that SOME
             * later collect swept, which is not the claim being made. */
            static int make_control(void) {
                PyObject *a = py_dict_new();
                PyObject *b = py_dict_new();
                PyObject *c = py_instance_new(control_class);
                PyObject *l = py_str_new("link", 4);
                PyObject *h = py_str_new("held", 4);
                if (a == NULL || b == NULL || c == NULL
                    || l == NULL || h == NULL) return 0;
                py_dict_set(a, l, b);
                py_dict_set(b, l, a);
                py_dict_set(a, h, c);
                py_decref(c);
                py_decref(a);
                py_decref(b);
                py_decref(l);
                py_decref(h);
                return 1;
            }

            static PyObject *probe_control_del(PyObject *self) {
                (void)self;
                control_finalized++;
                Py_INCREF(Py_None);
                return Py_None;
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;

                hash_class = py_class_new(
                    "CollectingKey", NULL, 0, NULL, 0
                );
                victim_class = py_class_new(
                    "DeleteVictim", NULL, 0, NULL, 0
                );
                control_class = py_class_new(
                    "ControlValue", NULL, 0, NULL, 0
                );
                if (hash_class == NULL || victim_class == NULL
                    || control_class == NULL) return 3;
                pcc_gc_pin((PyObject *)hash_class);
                pcc_gc_pin((PyObject *)victim_class);
                pcc_gc_pin((PyObject *)control_class);
                py_class_add_method(
                    hash_class, "__hash__", (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    victim_class, "__del__",
                    (PyObject *)(uintptr_t)probe_victim_del
                );
                py_class_add_method(
                    control_class, "__del__",
                    (PyObject *)(uintptr_t)probe_control_del
                );

                /* Unreachable cycle holding a finalizable instance. */
                PyObject *cyc_a = py_dict_new();
                PyObject *cyc_b = py_dict_new();
                PyObject *control = py_instance_new(control_class);
                PyObject *link = py_str_new("link", 4);
                PyObject *held = py_str_new("held", 4);
                if (cyc_a == NULL || cyc_b == NULL || control == NULL
                    || link == NULL || held == NULL) return 4;
                py_dict_set(cyc_a, link, cyc_b);
                py_dict_set(cyc_b, link, cyc_a);
                py_dict_set(cyc_a, held, control);
                py_decref(control);
                py_decref(cyc_a);
                py_decref(cyc_b);
                py_decref(link);
                py_decref(held);

                /* ---- update: the source keys collect mid-walk ---- */
                dst_root = py_dict_new();
                src_root = py_dict_new();
                if (dst_root == NULL || src_root == NULL) return 5;
                k0_root = py_instance_new(hash_class);
                k1_root = py_instance_new(hash_class);
                if (k0_root == NULL || k1_root == NULL) return 6;
                py_dict_set(src_root, k0_root, py_int_from_i64(100));
                py_dict_set(src_root, k1_root, py_int_from_i64(101));

                void *dst_h = pcc_gc_scheduler_root_register_handle(&dst_root);
                void *src_h = pcc_gc_scheduler_root_register_handle(&src_root);
                void *k0_h = pcc_gc_scheduler_root_register_handle(&k0_root);
                void *k1_h = pcc_gc_scheduler_root_register_handle(&k1_root);
                if (dst_h == NULL || src_h == NULL || k0_h == NULL
                    || k1_h == NULL) return 7;
                /* Baseline BEFORE the first armed phase: taken later, a root
                 * leaked by the update would be absorbed into it. */
                int64_t roots_before = pcc_gc_scheduler_root_count();

                if (!make_control()) return 90;
                int64_t ctrl_update = control_finalized;
                armed = 1;
                py_dict_update(dst_root, pcc_gc_load_ptr(NULL, &src_root));
                armed = 0;
                if (control_finalized == ctrl_update) {
                    printf("the update callback's collect swept nothing: that "
                           "phase proves nothing\n");
                    return 91;
                }
                dst_root = pcc_gc_load_ptr(NULL, &dst_root);
                src_root = pcc_gc_load_ptr(NULL, &src_root);
                k0_root = pcc_gc_load_ptr(NULL, &k0_root);
                k1_root = pcc_gc_load_ptr(NULL, &k1_root);

                if (py_err_occurred()) {
                    printf("update raised\n");
                    return 8;
                }
                if (update_collects != 1) {
                    printf("update callback did not collect: %lld\n",
                           (long long)update_collects);
                    return 9;
                }
                if (py_dict_len(dst_root) != 2) {
                    printf("update lost entries: len=%lld (expected 2)\n",
                           (long long)py_dict_len(dst_root));
                    return 10;
                }
                PyObject *g0 = py_dict_get(dst_root, k0_root);
                if (py_err_occurred() != NULL) {
                    printf("dict operation left an exception pending\n");
                    return 99;
                }
                PyObject *g1 = py_dict_get(dst_root, k1_root);
                if (py_err_occurred() != NULL) {
                    printf("dict operation left an exception pending\n");
                    return 99;
                }
                if (g0 == NULL || py_int_to_i64(g0, NULL) != 100
                    || g1 == NULL || py_int_to_i64(g1, NULL) != 101) {
                    printf("update entry wrong across collect\n");
                    return 11;
                }
                py_decref(g0);
                py_decref(g1);

                /* ---- delete: the key collects while the tombstone and the
                 *      displaced value release are being committed ---- */
                del_root = py_dict_new();
                if (del_root == NULL) return 12;
                del_key_root = py_instance_new(hash_class);
                keep_key_root = py_str_new("keep", 4);
                PyObject *victim = py_instance_new(victim_class);
                if (del_key_root == NULL || keep_key_root == NULL
                    || victim == NULL) return 13;
                py_dict_set(del_root, keep_key_root, py_int_from_i64(55));
                py_dict_set(del_root, del_key_root, victim);
                py_decref(victim);   /* the dict holds the only reference */

                void *del_h = pcc_gc_scheduler_root_register_handle(&del_root);
                void *dk_h = pcc_gc_scheduler_root_register_handle(
                    &del_key_root
                );
                void *kk_h = pcc_gc_scheduler_root_register_handle(
                    &keep_key_root
                );
                if (del_h == NULL || dk_h == NULL || kk_h == NULL) return 14;

                if (victim_finalized != 0) {
                    printf("victim died before the delete: %lld\n",
                           (long long)victim_finalized);
                    return 15;
                }

                if (!make_control()) return 92;
                int64_t ctrl_delete = control_finalized;
                armed = 2;
                (void)py_dict_del(del_root, pcc_gc_load_ptr(
                    NULL, &del_key_root
                ));
                armed = 0;
                if (control_finalized == ctrl_delete) {
                    printf("the delete callback's collect swept nothing: that "
                           "phase proves nothing\n");
                    return 93;
                }
                if (py_err_occurred() != NULL) {
                    printf("dict operation left an exception pending\n");
                    return 99;
                }
                del_root = pcc_gc_load_ptr(NULL, &del_root);
                del_key_root = pcc_gc_load_ptr(NULL, &del_key_root);
                keep_key_root = pcc_gc_load_ptr(NULL, &keep_key_root);

                if (delete_collects != 1) {
                    printf("delete callback did not collect: %lld\n",
                           (long long)delete_collects);
                    return 16;
                }
                if (py_dict_len(del_root) != 1) {
                    printf("delete left len=%lld (expected 1)\n",
                           (long long)py_dict_len(del_root));
                    return 17;
                }
                PyObject *gone = py_dict_get(del_root, del_key_root);
                if (py_err_occurred() != NULL) {
                    printf("dict operation left an exception pending\n");
                    return 99;
                }
                if (gone != NULL) {
                    printf("deleted key still present\n");
                    return 18;
                }
                PyObject *kept = py_dict_get(del_root, keep_key_root);
                if (py_err_occurred() != NULL) {
                    printf("dict operation left an exception pending\n");
                    return 99;
                }
                if (kept == NULL || py_int_to_i64(kept, NULL) != 55) {
                    printf("surviving entry lost across delete collect\n");
                    return 19;
                }
                py_decref(kept);

                for (int i = 0; i < 8; i++) {
                    (void)pcc_gc_collect(0);
                    for (int j = 0; j < 64; j++) (void)pcc_gc_step(64);
                }
                del_root = pcc_gc_load_ptr(NULL, &del_root);

                if (victim_finalized != 1) {
                    printf("displaced value finalized %lld times "
                           "(expected exactly 1)\n",
                           (long long)victim_finalized);
                    return 20;
                }
                /* Retire the delete phase's own handles first: the baseline
                 * was taken before they existed, so comparing with them still
                 * registered is not like-for-like.  Retiring them here also
                 * means a root leaked by either phase now shows up. */
                pcc_gc_scheduler_root_unregister_handle(dk_h);
                pcc_gc_scheduler_root_unregister_handle(kk_h);
                pcc_gc_scheduler_root_unregister_handle(del_h);
                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 22;
                }

                py_decref(pcc_gc_load_ptr(NULL, &k0_root));
                py_decref(pcc_gc_load_ptr(NULL, &k1_root));
                py_decref(pcc_gc_load_ptr(NULL, &del_key_root));
                py_decref(pcc_gc_load_ptr(NULL, &keep_key_root));
                pcc_gc_scheduler_root_unregister_handle(k0_h);
                pcc_gc_scheduler_root_unregister_handle(k1_h);
                pcc_gc_scheduler_root_unregister_handle(src_h);
                pcc_gc_scheduler_root_unregister_handle(dst_h);
                py_decref(pcc_gc_load_ptr(NULL, &src_root));
                py_decref(pcc_gc_load_ptr(NULL, &dst_root));
                py_decref(pcc_gc_load_ptr(NULL, &del_root));
                pcc_gc_unpin((PyObject *)hash_class);
                pcc_gc_unpin((PyObject *)victim_class);
                pcc_gc_unpin((PyObject *)control_class);
                py_decref((PyObject *)hash_class);
                py_decref((PyObject *)victim_class);
                py_decref((PyObject *)control_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind),
        extra_include_dirs=(REPO_ROOT / "utils" / "fake_libc_include",),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} collect-during-update/delete returned "
        f"{run.returncode}: " + run.stdout + run.stderr
    )


@pytest.mark.parametrize("gc_kind", ALL_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_collect_during_insert_keeps_fresh_entry_alive_on_every_backend(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """A callback that collects mid-insert must not strand the fresh entry.

    This is the class the relocation probes cannot reach, and it applies to
    every collector rather than only the one that moves objects.  The danger is
    that the container is already marked when the user ``__hash__`` runs, the
    callback drives a mark/sweep to completion, and the value published
    immediately afterwards is still white -- reachable only from a black
    object, so nothing will ever mark it, and the sweep frees it while the dict
    still points at it.

    The value carries ``__del__``, so a premature free is observed directly
    rather than inferred from a corrupted read.

    The control arm matters more than the assertion.  An earlier version drove
    ``pcc_gc_step`` alone and passed on both arms; the control showed it had
    collected nothing at all, so "the fresh entry survived" was vacuous.
    ``pcc_gc_collect`` is the entry point that actually marks and sweeps.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="collect_during_insert_" + gc_kind.lower(),
        source_text=r"""
            #include "Python.h"
            #include <stdint.h>
            #include <stdio.h>

            static PyObject *dict_root;
            static struct PyClassObject *hash_class;
            static struct PyClassObject *value_class;
            static int64_t armed;
            static int64_t mark_advances;
            static int64_t value_finalized;
            static int64_t control_finalized;
            static struct PyClassObject *control_class;
            static PyObject *key_root;
            static PyObject *settled_root;
            static PyObject *value_root;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            /* The dict is mid-insert here.  Drive the incremental mark as far
             * as it will go, so if the container is already black the entry
             * published after this call has no marker left to reach it. */
            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                if (armed && mark_advances == 0) {
                    /* pcc_gc_step alone does not complete a tracing cycle --
                     * the control arm below proved that -- so drive the real
                     * mark/sweep entry point from inside the insert.  If
                     * py_dict_set held the graph lock across this callback
                     * this would deadlock, which is itself the finding. */
                    (void)pcc_gc_collect(0);
                    for (int i = 0; i < 64; i++) (void)pcc_gc_step(64);
                    mark_advances++;
                }
                return py_int_from_i64(7);
            }

            /* Must never run: the dict holds the only reference and the dict
             * is a registered root for the whole probe. */
            static PyObject *probe_value_del(PyObject *self) {
                (void)self;
                value_finalized++;
                Py_INCREF(Py_None);
                return Py_None;
            }

            /* Control.  Reachable only from an unreachable dict cycle, so
             * refcounting alone cannot free it and only a real tracing sweep
             * can.  If this never fires, the steps below collected nothing and
             * "the fresh entry survived" would be vacuous. */
            /* Build an unreachable cycle holding a finalizable instance.
             * Refcounting alone cannot reclaim it, so only a real tracing
             * sweep can.  One of these per armed phase, checked immediately
             * after that phase: a single shared control checked at the end only
             * shows that SOME later collect swept, which is not the claim. */
            static int make_control(void) {
                PyObject *a = py_dict_new();
                PyObject *b = py_dict_new();
                PyObject *c = py_instance_new(control_class);
                PyObject *l = py_str_new("link", 4);
                PyObject *h = py_str_new("held", 4);
                if (a == NULL || b == NULL || c == NULL
                    || l == NULL || h == NULL) return 0;
                py_dict_set(a, l, b);
                py_dict_set(b, l, a);
                py_dict_set(a, h, c);
                py_decref(c);
                py_decref(a);
                py_decref(b);
                py_decref(l);
                py_decref(h);
                return 1;
            }

            static PyObject *probe_control_del(PyObject *self) {
                (void)self;
                control_finalized++;
                Py_INCREF(Py_None);
                return Py_None;
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;

                hash_class = py_class_new(
                    "Backend1MarkingKey", NULL, 0, NULL, 0
                );
                value_class = py_class_new(
                    "Backend1TrackedValue", NULL, 0, NULL, 0
                );
                control_class = py_class_new(
                    "Backend1ControlValue", NULL, 0, NULL, 0
                );
                if (hash_class == NULL || value_class == NULL
                    || control_class == NULL) return 3;
                pcc_gc_pin((PyObject *)hash_class);
                pcc_gc_pin((PyObject *)value_class);
                pcc_gc_pin((PyObject *)control_class);
                py_class_add_method(
                    control_class,
                    "__del__",
                    (PyObject *)(uintptr_t)probe_control_del
                );
                py_class_add_method(
                    hash_class,
                    "__hash__",
                    (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    value_class,
                    "__del__",
                    (PyObject *)(uintptr_t)probe_value_del
                );

                dict_root = py_dict_new();
                if (dict_root == NULL) return 4;
                void *handle = pcc_gc_scheduler_root_register_handle(
                    &dict_root
                );
                if (handle == NULL) return 5;

                /* Settle the dict and let the mark reach it, so it is plausibly
                 * black by the time the armed insert runs. */
                settled_root = py_str_new("settled", 7);
                if (settled_root == NULL) return 6;
                py_dict_set(dict_root, settled_root, py_int_from_i64(1));
                for (int i = 0; i < 64; i++) (void)pcc_gc_step(64);

                key_root = py_instance_new(hash_class);
                value_root = py_instance_new(value_class);
                if (key_root == NULL || value_root == NULL) return 7;
                /* Backend 4 relocates during the collect the callback drives,
                 * so these must be roots or the probe reads stale pointers
                 * afterwards and blames the runtime for its own bug. */
                void *key_handle = pcc_gc_scheduler_root_register_handle(
                    &key_root
                );
                void *settled_handle = pcc_gc_scheduler_root_register_handle(
                    &settled_root
                );
                void *value_handle = pcc_gc_scheduler_root_register_handle(
                    &value_root
                );
                if (key_handle == NULL || settled_handle == NULL
                    || value_handle == NULL) return 19;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                if (!make_control()) return 90;
                int64_t ctrl_before_insert = control_finalized;

                armed = 1;
                py_dict_set(dict_root, key_root, value_root);
                armed = 0;
                if (control_finalized == ctrl_before_insert) {
                    printf("the insert callback's collect swept nothing: this "
                           "phase proves nothing about a mid-insert collect\n");
                    return 91;
                }
                dict_root = pcc_gc_load_ptr(NULL, &dict_root);
                key_root = pcc_gc_load_ptr(NULL, &key_root);
                value_root = pcc_gc_load_ptr(NULL, &value_root);
                settled_root = pcc_gc_load_ptr(NULL, &settled_root);
                /* Hand the dict the only reference. */
                py_decref(value_root);

                if (py_err_occurred()) {
                    printf("insert raised\n");
                    return 8;
                }
                if (mark_advances != 1) {
                    printf("callback did not run: advances=%lld\n",
                           (long long)mark_advances);
                    return 9;
                }

                /* Complete several more cycles: a white entry reachable only
                 * from a black container would be swept here. */
                for (int i = 0; i < 8; i++) {
                    (void)pcc_gc_collect(0);
                    for (int j = 0; j < 64; j++) (void)pcc_gc_step(64);
                }

                dict_root = pcc_gc_load_ptr(NULL, &dict_root);
                key_root = pcc_gc_load_ptr(NULL, &key_root);
                value_root = pcc_gc_load_ptr(NULL, &value_root);
                settled_root = pcc_gc_load_ptr(NULL, &settled_root);

                if (value_finalized != 0) {
                    printf("fresh entry finalized while still in the dict: "
                           "%lld\n", (long long)value_finalized);
                    return 10;
                }
                if (py_dict_len(dict_root) != 2) {
                    printf("dict len=%lld (expected 2)\n",
                           (long long)py_dict_len(dict_root));
                    return 11;
                }
                PyObject *got = py_dict_get(dict_root, key_root);
                if (py_err_occurred() != NULL) {
                    printf("py_dict_get raised on a key that must be present\n");
                    return 92;
                }
                if (got == NULL) {
                    printf("fresh entry lost from the dict\n");
                    return 12;
                }
                if (got != value_root) {
                    printf("fresh entry identity drifted: %p != %p\n",
                           (void *)got, (void *)value_root);
                    return 13;
                }
                py_decref(got);
                PyObject *kept = py_dict_get(dict_root, settled_root);
                if (py_err_occurred() != NULL) {
                    printf("py_dict_get raised on the settled key\n");
                    return 93;
                }
                if (kept == NULL || py_int_to_i64(kept, NULL) != 1) {
                    printf("settled entry lost\n");
                    return 14;
                }
                py_decref(kept);
                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 15;
                }

                py_decref(pcc_gc_load_ptr(NULL, &key_root));
                py_decref(pcc_gc_load_ptr(NULL, &settled_root));
                pcc_gc_scheduler_root_unregister_handle(key_handle);
                pcc_gc_scheduler_root_unregister_handle(settled_handle);
                pcc_gc_scheduler_root_unregister_handle(value_handle);
                pcc_gc_scheduler_root_unregister_handle(handle);
                py_decref(pcc_gc_load_ptr(NULL, &dict_root));
                pcc_gc_unpin((PyObject *)hash_class);
                pcc_gc_unpin((PyObject *)value_class);
                pcc_gc_unpin((PyObject *)control_class);
                py_decref((PyObject *)hash_class);
                py_decref((PyObject *)value_class);
                py_decref((PyObject *)control_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind),
        extra_include_dirs=(REPO_ROOT / "utils" / "fake_libc_include",),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} collect-during-insert returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_backend4_dict_update_snapshots_source_across_destination_callbacks(
    tmp_path: Path,
    kind: str,
) -> None:
    """``dict.update`` must snapshot its source before destination callbacks.

    ``py_dict_update`` cached the source ``PyDictObject`` and ``entries_used``
    and re-read ``s->entries`` on every iteration, but each ``py_dict_set``
    runs the destination's user hash/equality.  With two or more source entries
    a callback that relocates the source leaves the next iteration walking a
    stale owner/table.  ``py_set_update`` already snapshots; this is the same
    contract for dict.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="backend4_dict_update_snapshot",
        source_text=r'''
            #include "Python.h"
            #include <stdint.h>
            #include <stdio.h>

            typedef struct ProbeKeyObject {
                PyObject_HEAD
            } ProbeKeyObject;

            static PyObject *src_root;
            static int64_t relocations;
            static int64_t eq_calls;
            static int64_t armed;
            static PyObject *mutate_key;

            extern int64_t pcc_gc_backend4_relocation_set_add(PyObject *obj);
            extern int64_t pcc_gc_backend4_remap_and_retire_stopped_world(void);

            static int relocate(PyObject **slot) {
                PyObject *obj = pcc_gc_load_ptr(NULL, slot);
                pcc_gc_reset_relocation_set();
                if (pcc_gc_backend4_relocation_set_add(obj) != 1) return 0;
                PyObject *target = pcc_gc_relocate_copy(obj, 56);
                if (target == NULL || target == obj) return 0;
                py_decref(target);
                /* Retire the forwarding source so the old copy really goes
                 * away.  Without this the stale table stays readable and a
                 * caller holding it across the callback looks fine. */
                pcc_gc_reset_relocation_set();
                (void)pcc_gc_backend4_remap_and_retire_stopped_world();
                (void)pcc_gc_backend4_remap_and_retire_stopped_world();
                return 1;
            }

            /* Runs while dict.update is walking the source: relocate it. */
            static PyObject *probe_eq(PyObject *self, PyObject *other, int op) {
                (void)self;
                (void)other;
                if (op != Py_EQ) {
                    Py_INCREF(Py_NotImplemented);
                    return Py_NotImplemented;
                }
                eq_calls++;
                /* Only relocate during the update itself.  Relocating while
                 * the source is still being built would move an object that
                 * is not rooted yet and strand the global pointer. */
                if (armed && relocations == 0) {
                    if (relocate(&src_root)) relocations++;
                    /* Also shrink the source: py_dict_update cached
                     * entries_used before the loop, so a delete here makes the
                     * cached bound exceed the live table. */
                    PyObject *cur = pcc_gc_load_ptr(NULL, &src_root);
                    if (mutate_key != NULL) {
                        (void)py_dict_del(cur, mutate_key);
                        py_clear_exception();
                        src_root = pcc_gc_load_ptr(NULL, &src_root);
                    }
                }
                Py_INCREF(Py_False);
                return Py_False;
            }

            static PyTypeObject ProbeKeyType = {
                PyVarObject_HEAD_INIT(NULL, 0)
                .tp_name = "pcc_probe.DictUpdateKey",
                .tp_basicsize = sizeof(ProbeKeyObject),
                .tp_flags = Py_TPFLAGS_DEFAULT,
                .tp_richcompare = probe_eq,
            };

            static PyObject *new_key(void) {
                return (PyObject *)PyObject_New(ProbeKeyObject, &ProbeKeyType);
            }

            int main(void) {
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_COLORED_RELOCATING
                    ) != 0) return 2;
                if (PyType_Ready(&ProbeKeyType) != 0) return 3;

                PyObject *dst = py_dict_new();
                src_root = py_dict_new();
                if (dst == NULL || src_root == NULL) return 4;

                /* A destination entry whose key compares via probe_eq, so the
                 * first source insert triggers the callback. */
                PyObject *seed = new_key();
                if (seed == NULL) return 5;
                py_dict_set(dst, seed, py_int_from_i64(1));

                /* Four source entries: the relocation happens on the first and
                 * the rest must still be read from the reloaded source. */
                PyObject *k[4];
                for (int i = 0; i < 4; i++) {
                    k[i] = new_key();
                    if (k[i] == NULL) return 6;
                    py_dict_set(src_root, k[i], py_int_from_i64(100 + i));
                }
                if (py_dict_len(src_root) != 4) return 7;

                void *src_handle = pcc_gc_scheduler_root_register_handle(
                    &src_root
                );
                if (src_handle == NULL) return 8;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                mutate_key = k[3];
                armed = 1;
                py_dict_update(dst, pcc_gc_load_ptr(NULL, &src_root));
                armed = 0;
                src_root = pcc_gc_load_ptr(NULL, &src_root);

                if (py_err_occurred()) {
                    printf("update raised\n");
                    return 9;
                }
                if (relocations != 1) {
                    printf("source was not relocated: relocations=%lld "
                           "eq_calls=%lld\n",
                           (long long)relocations, (long long)eq_calls);
                    return 10;
                }
                /* seed plus the four source keys. */
                /* The callback removed k[3] from the source mid-update, so
                 * dst gets seed plus k[0..2]; what must NOT happen is reading
                 * a stale table, losing an earlier entry, or crashing. */
                if (py_dict_len(dst) < 4) {
                    printf("dst lost entries: %lld (expected at least 4)\n",
                           (long long)py_dict_len(dst));
                    return 11;
                }
                for (int i = 0; i < 3; i++) {
                    PyObject *got = py_dict_get(dst, k[i]);
                    py_clear_exception();
                    if (got == NULL
                        || py_int_to_i64(got, NULL) != 100 + i) {
                        printf("source entry %d lost or wrong\n", i);
                        return 12;
                    }
                    py_decref(got);
                }
                if (py_dict_len(src_root) != 3) {
                    printf("source damaged: %lld (expected 3 after delete)\n",
                           (long long)py_dict_len(src_root));
                    return 13;
                }
                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 14;
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
        f"{kind} dict update snapshot returned {run.returncode}: "
        + run.stdout + run.stderr
    )
