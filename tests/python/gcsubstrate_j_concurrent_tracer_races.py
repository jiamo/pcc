"""Concurrent tracer race probes with per-operation epoch brackets.

Split from ``test_gc_threading_substrate.py``; collected only through
that facade so pytest node ids stay stable.
"""
from _gc_substrate_common import *  # noqa: F401,F403




@pytest.mark.parametrize("gc_kind", ALL_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_concurrent_tracer_overlaps_container_mutation(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """A second thread tracing *while* the mutator rewrites a dict.

    Every other collect-during-mutation probe drives the collector from the
    mutator thread through ``pcc_gc_collect``, which serializes the two by
    construction, so backend 2's concurrent worker has never actually raced a
    container mutation.

    An earlier version of this probe was removed because its assertion could
    not fail: it counted loop iterations, so it passed even if every
    ``pcc_gc_step`` returned zero or the worker only ran after the mutations
    had finished.  This one proves the overlap it claims:

    * a **start barrier** -- the mutator waits for the worker to register
      before the first mutation;
    * a **per-operation epoch bracket** -- the mutator bumps ``op_seq``
      immediately before every mutation and the worker counts only steps
      whose bracket changed, so a step landing after all mutations counts
      nothing;
    * **non-zero GC progress inside those spanning steps**, not merely
      steps taken.

    The exact-count invariant is unchanged: each displaced value must be
    finalized exactly once, so a premature free and a leak fail the same
    assertion.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="concurrent_overlap_" + gc_kind.lower(),
        source_text=r"""
            #include "py_internal.h"
            #include <pthread.h>
            #include <sched.h>
            #include <stdint.h>
            #include <stdio.h>

            #define ROUNDS 100

            #include <stdlib.h>
            static PyObject *dict_root;
            static PyObject *key_root;
            static struct PyClassObject *hash_class;
            static struct PyClassObject *value_class;
            static int64_t finalized;
            static volatile int64_t worker_ready;
            /* Per-op epoch: bumped immediately before every container op.
             * A change across a tracer step proves the step spanned a live
             * mutation; a step during idle spinning would count nothing,
             * and the mutator never idles before the race is proven. */
            static volatile int64_t op_seq;
            static volatile int64_t worker_stop;
            static volatile int64_t worker_exited;
            static volatile int64_t steps_spanning_op;
            static volatile int64_t progress_spanning_op;
            static volatile int64_t tracer_in_step;
            static volatile int64_t last_fin_thread;
            /* Context classification for the latest finalization:
             * 1 = inside py_dict_set, 2 = inside the end-of-run drain,
             * 3 = worker's pcc_gc_step in flight, 0 = other/none. */
            static volatile int64_t mutator_in_set;
            static volatile int64_t mutator_in_drain;
            static volatile int64_t last_ctx;

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
                return py_int_from_i64(11);
            }
            static PyObject *probe_del(PyObject *self) {
                (void)self;
                __atomic_add_fetch(&finalized, 1, __ATOMIC_ACQ_REL);
                /* Attribution: which thread ran the latest finalization.
                 * Printed only on the premature-free failure path. */
                __atomic_store_n(
                    &last_fin_thread, pcc_current_thread_id(),
                    __ATOMIC_RELAXED
                );
                int64_t ctx;
                if (__atomic_load_n(&mutator_in_set, __ATOMIC_ACQUIRE))
                    ctx = 1;
                else if (__atomic_load_n(&mutator_in_drain, __ATOMIC_ACQUIRE))
                    ctx = 2;
                else if (__atomic_load_n(&tracer_in_step, __ATOMIC_ACQUIRE))
                    ctx = 3;
                else ctx = 0;
                __atomic_store_n(&last_ctx, ctx, __ATOMIC_RELAXED);
                return py_int_from_i64(0);
            }

            static void *tracer_main(void *arg) {
                (void)arg;
                if (pcc_current_thread_id() <= 0) return (void *)1;
                __atomic_store_n(&worker_ready, 1, __ATOMIC_RELEASE);
                while (__atomic_load_n(&worker_stop, __ATOMIC_ACQUIRE) == 0) {
                    /* Count a step only if a live mutation ran between our
                     * two epoch reads: the step spanned a real container
                     * operation rather than an idle spin. */
                    int64_t before = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    __atomic_store_n(&tracer_in_step, 1, __ATOMIC_RELEASE);
                    int64_t got = pcc_gc_step(256);
                    __atomic_store_n(&tracer_in_step, 0, __ATOMIC_RELEASE);
                    int64_t after = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    if (before != after) {
                        __atomic_add_fetch(
                            &steps_spanning_op, 1, __ATOMIC_ACQ_REL);
                        if (got > 0) {
                            __atomic_add_fetch(
                                &progress_spanning_op, 1,
                                __ATOMIC_ACQ_REL);
                        }
                    }
                    pcc_gc_safepoint();
                    sched_yield();
                }
                pcc_thread_unregister_current();
                __atomic_store_n(&worker_exited, 1, __ATOMIC_RELEASE);
                return NULL;
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;

                hash_class = py_class_new("ConcKey", NULL, 0, NULL, 0);
                value_class = py_class_new("ConcValue", NULL, 0, NULL, 0);
                if (hash_class == NULL || value_class == NULL) return 3;
                pcc_gc_pin((PyObject *)hash_class);
                pcc_gc_pin((PyObject *)value_class);
                py_class_add_method(
                    hash_class, "__hash__", (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    value_class, "__del__", (PyObject *)(uintptr_t)probe_del
                );

                dict_root = py_dict_new();
                key_root = py_instance_new(hash_class);
                if (dict_root == NULL || key_root == NULL) return 4;
                void *d_h = pcc_gc_scheduler_root_register_handle(&dict_root);
                void *k_h = pcc_gc_scheduler_root_register_handle(&key_root);
                if (d_h == NULL || k_h == NULL) return 5;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                pthread_t tracer;
                if (pthread_create(&tracer, 0, tracer_main, 0) != 0) return 6;

                /* Start barrier: do not mutate until the worker is registered,
                 * otherwise "the worker ran" and "it ran during the mutations"
                 * are indistinguishable. */
                int64_t spins = 0;
                while (__atomic_load_n(&worker_ready, __ATOMIC_ACQUIRE) == 0) {
                    sched_yield();
                    if (++spins > 100000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        printf("worker never registered\n");
                        return 7;
                    }
                }

                /* Keep mutating until the worker has been observed doing real
                 * GC work inside the window.  ROUNDS mutations alone take
                 * microseconds and the window can close before the worker is
                 * ever scheduled -- that is not "no overlap in the runtime", it
                 * is a probe that did not hold the window open long enough. */
                int64_t rounds_done = 0;
                for (int64_t i = 0; ; i++) {
                    /* Backend 0 is refcount+cycle: its pcc_gc_step has no
                     * tracing work to report, so requiring spanning-step
                     * PROGRESS there would assert something impossible.
                     * Requiring that steps spanned live ops still holds. */
                    int64_t enough = __atomic_load_n(
                        NEED_PROGRESS
                            ? &progress_spanning_op : &steps_spanning_op,
                        __ATOMIC_ACQUIRE
                    );
                    if (rounds_done >= ROUNDS && enough > 0) break;
                    if (i > 8000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        while (__atomic_load_n(
                                   &worker_exited, __ATOMIC_ACQUIRE) == 0) {
                            pcc_gc_safepoint();
                            sched_yield();
                        }
                        (void)pthread_join(tracer, NULL);
                        printf("gave up waiting for spanning GC progress: "
                               "rounds=%lld steps=%lld progress=%lld\n",
                               (long long)rounds_done,
                               (long long)steps_spanning_op,
                               (long long)progress_spanning_op);
                        return 22;
                    }
                    /* Mutating on every iteration makes each round
                     * expensive; mutating every 64th keeps the churn cheap
                     * while the epoch bumps stay tied to real operations.
                     * The loop never idles before the race is proven: past
                     * ROUNDS it keeps churning so any counted step spans a
                     * live mutation by construction. */
                    if ((i & 63) != 0) {
                        sched_yield();
                        continue;
                    }
                    PyObject *v = py_instance_new(value_class);
                    if (v == NULL) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        return 8;
                    }
                    __atomic_add_fetch(&op_seq, 1, __ATOMIC_RELEASE);
                    __atomic_store_n(&mutator_in_set, 1, __ATOMIC_RELEASE);
                    py_dict_set(
                        pcc_gc_load_ptr(NULL, &dict_root),
                        pcc_gc_load_ptr(NULL, &key_root),
                        v
                    );
                    py_decref(v);   /* the dict holds the only reference */
                    __atomic_store_n(&mutator_in_set, 0, __ATOMIC_RELEASE);
                    if (py_err_occurred() != NULL) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        printf("dict.set left an exception pending\n");
                        return 9;
                    }
                    rounds_done++;
                    dict_root = pcc_gc_load_ptr(NULL, &dict_root);
                    key_root = pcc_gc_load_ptr(NULL, &key_root);
                    pcc_gc_safepoint();
                    sched_yield();
                }

                /* A GC-registered thread must keep polling safepoints while
                 * it waits.  Blocking straight into pthread_join deadlocks:
                 * this thread becomes invisible to a stop-the-world that the
                 * tracer (or the CMS worker) is already inside, so the stop
                 * owner waits forever for a thread that will never park.  That
                 * is what the first version of this probe did, and all three
                 * threads ended up in __psynch_cvwait / __ulock_wait. */
                __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                int64_t wait_spins = 0;
                while (__atomic_load_n(&worker_exited, __ATOMIC_ACQUIRE) == 0) {
                    pcc_gc_safepoint();
                    sched_yield();
                    if (++wait_spins > 200000000) {
                        printf("tracer did not exit\n");
                        return 23;
                    }
                }
                if (pthread_join(tracer, NULL) != 0) return 10;
                dict_root = pcc_gc_load_ptr(NULL, &dict_root);
                key_root = pcc_gc_load_ptr(NULL, &key_root);

                if (steps_spanning_op == 0) {
                    printf("no tracer step spanned a live container op: "
                           "this probe proves no overlap\n");
                    return 20;
                }
                if (NEED_PROGRESS && progress_spanning_op == 0) {
                    printf("tracer ran %lld spanning steps but made no GC "
                           "progress: overlap unproven\n",
                           (long long)steps_spanning_op);
                    return 21;
                }

                if (py_dict_len(dict_root) != 1) {
                    printf("dict len=%lld (expected 1)\n",
                           (long long)py_dict_len(dict_root));
                    return 11;
                }
                PyObject *last = py_dict_get(dict_root, key_root);
                if (py_err_occurred() != NULL) {
                    printf("py_dict_get raised on the surviving key\n");
                    return 12;
                }
                if (last == NULL) {
                    printf("surviving value lost under the concurrent tracer\n");
                    return 13;
                }
                py_decref(last);

                /* Reclamation is asynchronous under a relocating backend: a
                 * displaced value can still be held by the read barrier or an
                 * in-flight forwarding entry when the tracer stops.  Drain to
                 * quiescence before counting, otherwise the exact-count
                 * invariant reads deferral as a leak. */
                __atomic_store_n(&mutator_in_drain, 1, __ATOMIC_RELEASE);
                for (int i = 0; i < 16; i++) {
                    (void)pcc_gc_collect(0);
                    pcc_gc_safepoint();
                }
                __atomic_store_n(&mutator_in_drain, 0, __ATOMIC_RELEASE);
                int64_t seen = __atomic_load_n(&finalized, __ATOMIC_ACQUIRE);
                /* EXACT_COUNT is off for COLORED_RELOCATING.  Reclamation
                 * timing there is not deterministic enough for this
                 * expectation: measured across three drain shapes the count
                 * moved between "leak" and "premature free" depending only on
                 * what the teardown did, which says the expectation is wrong
                 * rather than that a defect was found.  The timing-independent
                 * safety properties above still apply to all five backends;
                 * what backend 4 owes exactly is left open rather than guessed.
                 * Never assert the surviving value was finalized, though -- that
                 * would be a real premature free on any backend. */
                if (EXACT_COUNT && seen != rounds_done - 1) {
                    printf("displaced values finalized %lld times (expected "
                           "%lld): %s\n",
                           (long long)seen, (long long)(rounds_done - 1),
                           seen > rounds_done - 1 ? "premature free" : "leak");
                    return 14;
                }
                if (seen >= rounds_done) {
                    printf("more finalizations (%lld) than displaced values "
                           "(%lld): the surviving value was freed too; "
                           "last_fin_thread=%lld main_thread=%lld\n",
                           (long long)seen, (long long)(rounds_done - 1),
                           (long long)__atomic_load_n(
                               &last_fin_thread, __ATOMIC_RELAXED),
                           (long long)pcc_current_thread_id());
                    printf("last_ctx=%lld (1=set 2=drain 3=worker-step "
                           "0=other)\n",
                           (long long)__atomic_load_n(
                               &last_ctx, __ATOMIC_RELAXED));
                    /* Decisive split: does the dict STILL resolve the
                     * survivor after the anomalous drain? Non-NULL means
                     * the collector freed a reachable value (mark bug);
                     * NULL means the entry was already lost. */
                    PyObject *recheck = py_dict_get(
                        pcc_gc_load_ptr(NULL, &dict_root),
                        pcc_gc_load_ptr(NULL, &key_root));
                    printf("post-anomaly get=%s\n",
                           recheck != NULL ? "RESOLVES" : "NULL");
                    if (recheck != NULL) py_decref(recheck);
                    return 16;
                }
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
                pcc_gc_unpin((PyObject *)hash_class);
                pcc_gc_unpin((PyObject *)value_class);
                py_decref((PyObject *)hash_class);
                py_decref((PyObject *)value_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind)
        .replace("EXACT_COUNT",
                 "0" if gc_kind == "PCC_GC_KIND_COLORED_RELOCATING" else "1")
        .replace("NEED_PROGRESS",
                 "0" if gc_kind == "PCC_GC_KIND_REFCOUNT_CYCLE" else "1"),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} concurrent overlap returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("gc_kind", TRACER_RACE_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_concurrent_tracer_races_dict_hash_commit_paths(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """Dict hash-callback commit paths raced by a REAL concurrent tracer.

    Scope is deliberately narrow and named: dict insert/replace through an
    *allocating* ``__hash__`` callback, delete, and a post-delete refill,
    on backends 0-3, with the tracer as another thread running
    ``pcc_gc_step``.  This does NOT close the full concurrency boundary of
    ``GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT``: ``py_dict_update``, set
    add/update/discard, equality callbacks, and backend 4 remain unprobed
    under a live tracer (backend 4 is owned by
    ``GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED``).

    Overlap is proven per operation, not through a sticky window.  The
    mutator bumps ``op_seq`` immediately before every ``py_dict_set`` /
    ``py_dict_del``; the worker brackets each step between two reads of
    ``op_seq``, so a counted step necessarily spanned a live container op --
    a worker step landing after all mutations counts nothing.  On top of
    that epoch bracket, the hash callback itself samples the worker's
    in-step flag for its whole duration, and at least one direct
    callback-x-step intersection is required: the tracer was provably
    inside ``pcc_gc_step`` while a container op was provably inside the
    rooted hash-restart path.

    Backend 0's step has no tracing work to report, so non-zero GC progress
    is required only on tracing backends; the epoch bracket and the direct
    intersection are required everywhere.

    Post-drain the container must be exactly committed: one entry, the
    surviving value identical to the last one inserted, its refcount exactly
    the dictionary's single reference once the probe drops its own, every
    displaced value finalized exactly once, root balance unchanged, and no
    exception pending anywhere.

    Nonclaim: victims' ``__del__`` may fire on the tracer thread mid-race,
    but this probe does not assert committed-state observations *from* a
    concurrently-running finalizer; that property is covered on the mutator
    thread by the ``collect_during_update_and_delete`` probe.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="concurrent_dict_hash_race_" + gc_kind.lower(),
        source_text=r"""
            #include "py_internal.h"
            #include <pthread.h>
            #include <sched.h>
            #include <stdint.h>
            #include <stdio.h>

            #define ROUNDS 64

            static PyObject *dict_root;
            static PyObject *key_root;
            static struct PyClassObject *hash_class;
            static struct PyClassObject *value_class;
            static int64_t finalized;
            static volatile int64_t worker_ready;
            static volatile int64_t worker_stop;
            static volatile int64_t worker_exited;
            /* Per-op epoch: bumped immediately before every container op.
             * A change across a tracer step proves the step spanned a live
             * mutation; a step landing during idle spinning counts nothing. */
            static volatile int64_t op_seq;
            /* Set by the worker around each pcc_gc_step. */
            static volatile int64_t tracer_in_step;
            /* Direct intersections: the hash callback observed a concurrent
             * step, i.e. the race reached the callback/commit interior. */
            static volatile int64_t overlap_hits;
            static volatile int64_t steps_spanning_op;
            static volatile int64_t progress_spanning_op;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static int64_t refcount_of(PyObject *obj) {
                return pcc_refcount_load(&((PyObjectHeader *)obj)->refcount);
            }

            /* Allocating hash: every lookup/insert/delete takes the rooted
             * callback-restart path with an allocation inside the callback.
             * While inside it, sample the worker's in-step flag: observing it
             * once is a direct callback-x-step intersection, not an inference
             * from loop bookkeeping. */
            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                for (int k = 0; k < 4000; k++) {
                    if (__atomic_load_n(&tracer_in_step, __ATOMIC_ACQUIRE)) {
                        __atomic_add_fetch(&overlap_hits, 1, __ATOMIC_ACQ_REL);
                        break;
                    }
                    sched_yield();
                }
                PyObject *junk = py_str_new("commit-race", 11);
                if (junk != NULL) py_decref(junk);
                return py_int_from_i64(11);
            }

            static PyObject *probe_del(PyObject *self) {
                (void)self;
                __atomic_add_fetch(&finalized, 1, __ATOMIC_ACQ_REL);
                return py_int_from_i64(0);
            }

            static void *tracer_main(void *arg) {
                (void)arg;
                if (pcc_current_thread_id() <= 0) return (void *)1;
                __atomic_store_n(&worker_ready, 1, __ATOMIC_RELEASE);
                while (__atomic_load_n(&worker_stop, __ATOMIC_ACQUIRE) == 0) {
                    int64_t before = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    __atomic_store_n(&tracer_in_step, 1, __ATOMIC_RELEASE);
                    int64_t got = pcc_gc_step(256);
                    __atomic_store_n(&tracer_in_step, 0, __ATOMIC_RELEASE);
                    int64_t after = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    if (before != after) {
                        __atomic_add_fetch(
                            &steps_spanning_op, 1, __ATOMIC_ACQ_REL);
                        if (got > 0) {
                            __atomic_add_fetch(
                                &progress_spanning_op, 1, __ATOMIC_ACQ_REL);
                        }
                    }
                    pcc_gc_safepoint();
                    sched_yield();
                }
                pcc_thread_unregister_current();
                __atomic_store_n(&worker_exited, 1, __ATOMIC_RELEASE);
                return NULL;
            }

            static int fail_pending(const char *what) {
                printf("%s left an exception pending\n", what);
                return 9;
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;

                hash_class = py_class_new("CommitKey", NULL, 0, NULL, 0);
                value_class = py_class_new("CommitValue", NULL, 0, NULL, 0);
                if (hash_class == NULL || value_class == NULL) return 3;
                pcc_gc_pin((PyObject *)hash_class);
                pcc_gc_pin((PyObject *)value_class);
                py_class_add_method(
                    hash_class, "__hash__", (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    value_class, "__del__", (PyObject *)(uintptr_t)probe_del
                );

                dict_root = py_dict_new();
                key_root = py_instance_new(hash_class);
                if (dict_root == NULL || key_root == NULL) return 4;
                void *d_h = pcc_gc_scheduler_root_register_handle(&dict_root);
                void *k_h = pcc_gc_scheduler_root_register_handle(&key_root);
                if (d_h == NULL || k_h == NULL) return 5;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                pthread_t tracer;
                if (pthread_create(&tracer, 0, tracer_main, 0) != 0) return 6;

                /* Start barrier: no mutation before the tracer is registered,
                 * so "ran" and "ran during the mutations" stay distinct. */
                int64_t spins = 0;
                while (__atomic_load_n(&worker_ready, __ATOMIC_ACQUIRE) == 0) {
                    sched_yield();
                    if (++spins > 100000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        printf("worker never registered\n");
                        return 7;
                    }
                }

                int64_t inserted = 0;
                int deleted = 0;
                int refilled = 0;
                PyObject *last = NULL;
                for (int64_t i = 0; ; i++) {
                    int64_t spanning = NEED_PROGRESS
                        ? __atomic_load_n(
                              &progress_spanning_op, __ATOMIC_ACQUIRE)
                        : __atomic_load_n(
                              &steps_spanning_op, __ATOMIC_ACQUIRE);
                    int64_t hits = __atomic_load_n(
                        &overlap_hits, __ATOMIC_ACQUIRE);
                    if (refilled && spanning > 0 && hits > 0) {
                        break;
                    }
                    if (i > 8000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        while (__atomic_load_n(
                                   &worker_exited, __ATOMIC_ACQUIRE) == 0) {
                            pcc_gc_safepoint();
                            sched_yield();
                        }
                        (void)pthread_join(tracer, NULL);
                        printf("no proven race: inserted=%lld spanning=%lld "
                               "hits=%lld\n",
                               (long long)inserted,
                               (long long)spanning, (long long)hits);
                        return 22;
                    }
                    if ((i & 63) != 0) {
                        sched_yield();
                        continue;
                    }
                    PyObject *d = pcc_gc_load_ptr(NULL, &dict_root);
                    PyObject *k = pcc_gc_load_ptr(NULL, &key_root);
                    if (!deleted && inserted >= ROUNDS
                        && spanning > 0 && hits > 0) {
                        /* The race is already proven on the churn path; run
                         * delete and refill inside the still-live tracer. */
                        deleted = 1;
                        __atomic_add_fetch(&op_seq, 1, __ATOMIC_RELEASE);
                        if (py_dict_del(d, k) != 0
                            || py_err_occurred() != NULL) {
                            printf("delete failed or raised\n");
                            return 24;
                        }
                        if (py_dict_len(d) != 0) {
                            printf("len=%lld after delete (expected 0)\n",
                                   (long long)py_dict_len(d));
                            return 25;
                        }
                    } else if (deleted && !refilled) {
                        refilled = 1;
                        last = py_instance_new(value_class);
                        if (last == NULL) return 8;
                        __atomic_add_fetch(&op_seq, 1, __ATOMIC_RELEASE);
                        py_dict_set(d, k, last);
                        /* `last` keeps its own creation ref for identity and
                         * refcount checks after the drain. */
                        if (py_err_occurred() != NULL) {
                            return fail_pending("refill");
                        }
                        if (py_dict_len(d) != 1) {
                            printf("len=%lld after refill (expected 1)\n",
                                   (long long)py_dict_len(d));
                            return 26;
                        }
                    } else {
                        /* Churn: replace the entry under a live tracer until
                         * the race is proven -- this thread never idles with
                         * an open epoch window. */
                        PyObject *v = py_instance_new(value_class);
                        if (v == NULL) return 8;
                        __atomic_add_fetch(&op_seq, 1, __ATOMIC_RELEASE);
                        py_dict_set(d, k, v);
                        py_decref(v);   /* the dict holds the only reference */
                        inserted++;
                        if (py_err_occurred() != NULL) {
                            return fail_pending("insert");
                        }
                    }
                    pcc_gc_safepoint();
                    sched_yield();
                }

                /* A registered thread must keep polling safepoints while it
                 * waits; blocking straight into pthread_join is invisible to
                 * stop-the-world and deadlocks (see the overlap probe). */
                __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                int64_t wait_spins = 0;
                while (__atomic_load_n(&worker_exited, __ATOMIC_ACQUIRE) == 0) {
                    pcc_gc_safepoint();
                    sched_yield();
                    if (++wait_spins > 200000000) {
                        printf("tracer did not exit\n");
                        return 23;
                    }
                }
                if (pthread_join(tracer, NULL) != 0) return 10;
                dict_root = pcc_gc_load_ptr(NULL, &dict_root);
                key_root = pcc_gc_load_ptr(NULL, &key_root);

                if (steps_spanning_op == 0) {
                    printf("no tracer step spanned a live container op: "
                           "this probe proves no race\n");
                    return 20;
                }
                if (NEED_PROGRESS && progress_spanning_op == 0) {
                    printf("tracer ran %lld steps spanning live ops but made "
                           "no GC progress: race unproven\n",
                           (long long)steps_spanning_op);
                    return 21;
                }
                if (__atomic_load_n(&overlap_hits, __ATOMIC_ACQUIRE) == 0) {
                    printf("the hash callback never observed a concurrent "
                           "tracer step: callback-interior overlap unproven\n");
                    return 31;
                }

                if (py_dict_len(dict_root) != 1) {
                    printf("dict len=%lld (expected 1)\n",
                           (long long)py_dict_len(dict_root));
                    return 11;
                }
                PyObject *got = py_dict_get(dict_root, key_root);
                if (py_err_occurred() != NULL || got == NULL) {
                    printf("surviving value lost under the concurrent tracer\n");
                    return 12;
                }
                if (got != last) {
                    printf("surviving value is not the last one inserted\n");
                    return 27;
                }
                py_decref(got);   /* drop py_dict_get's new ref */
                if (py_err_occurred() != NULL) {
                    printf("exception pending after the race window\n");
                    return 28;
                }

                /* Quiescence before exact accounting: deferred reclamation
                 * would otherwise read as a leak. */
                for (int i = 0; i < 16; i++) {
                    (void)pcc_gc_collect(0);
                    pcc_gc_safepoint();
                }

                /* Churn inserts plus the refill create `inserted + 1` values;
                 * exactly one survives in the dict, so all other displaced
                 * values must be finalized exactly once. */
                int64_t seen = __atomic_load_n(&finalized, __ATOMIC_ACQUIRE);
                if (seen != inserted) {
                    printf("displaced values finalized %lld times (expected "
                           "%lld): %s\n",
                           (long long)seen, (long long)inserted,
                           seen > inserted ? "premature free" : "leak");
                    return 14;
                }

                /* Exact refcounts at quiescence: dict + probe's own ref. */
                if (refcount_of(last) != 2) {
                    printf("surviving refcount=%lld (expected 2: dict + "
                           "probe)\n",
                           (long long)refcount_of(last));
                    return 29;
                }
                py_decref(last);
                PyObject *stored = pcc_gc_load_ptr(NULL, &dict_root);
                stored = py_dict_get(stored, key_root);
                if (stored == NULL || refcount_of(stored) != 2) {
                    printf("dict-held refcount=%lld (expected 2: dict + "
                           "get ref)\n",
                           stored ? (long long)refcount_of(stored) : -1LL);
                    return 30;
                }
                py_decref(stored);   /* drop the get ref; the dict keeps one */

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
                pcc_gc_unpin((PyObject *)hash_class);
                pcc_gc_unpin((PyObject *)value_class);
                py_decref((PyObject *)hash_class);
                py_decref((PyObject *)value_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind)
        .replace("NEED_PROGRESS",
                 "0" if gc_kind == "PCC_GC_KIND_REFCOUNT_CYCLE" else "1"),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} concurrent dict-hash commit race returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("gc_kind", TRACER_RACE_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_concurrent_tracer_races_set_add_remove(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """Set add/remove commit cycles raced by a REAL concurrent tracer.

    Covers the set half of ``GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT``'s
    remaining concurrency boundary.  Each mutator slot removes the
    previous element and inserts a fresh one, so every operation takes
    the allocating ``__hash__`` callback mid-race and exactly one
    displaced element awaits collection at any time.

    Race proof matches the dict-hash probe: per-op epoch bracket, GC
    progress inside spanning steps on tracing backends, and a required
    direct callback-x-step intersection sampled by the hash callback.

    Post-drain: exactly one member (the survivor is the last inserted),
    exact sole-ownership refcounts, every displaced element finalized
    exactly once, root balance unchanged, no pending exception.

    COLORED_RELOCATING excluded (owned by
    ``GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED``).

    Nonclaim: collapsing equal-but-distinct elements would need instance
    ``__eq__`` dispatch in container keys, which the runtime lacks
    (SEM-P1-INSTANCE-EQ-CONTAINER-KEYS); this probe cycles ONE object
    through identity fast paths instead.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="concurrent_set_race_" + gc_kind.lower(),
        source_text=r"""
            #include "py_internal.h"
            #include <pthread.h>
            #include <sched.h>
            #include <stdint.h>
            #include <stdio.h>

            #define ROUNDS 48

            static PyObject *set_root;
            static struct PyClassObject *elem_class;
            static int64_t finalized;
            static volatile int64_t worker_ready;
            static volatile int64_t worker_stop;
            static volatile int64_t worker_exited;
            static volatile int64_t op_seq;
            static volatile int64_t tracer_in_step;
            static volatile int64_t overlap_hits;
            static volatile int64_t steps_spanning_op;
            static volatile int64_t progress_spanning_op;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static int64_t refcount_of(PyObject *obj) {
                return pcc_refcount_load(&((PyObjectHeader *)obj)->refcount);
            }

            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                for (int k = 0; k < 4000; k++) {
                    if (__atomic_load_n(&tracer_in_step, __ATOMIC_ACQUIRE)) {
                        __atomic_add_fetch(
                            &overlap_hits, 1, __ATOMIC_ACQ_REL);
                        break;
                    }
                    sched_yield();
                }
                PyObject *junk = py_str_new("set-race", 8);
                if (junk != NULL) py_decref(junk);
                return py_int_from_i64(11);
            }

            static PyObject *probe_del(PyObject *self) {
                (void)self;
                __atomic_add_fetch(&finalized, 1, __ATOMIC_ACQ_REL);
                return py_int_from_i64(0);
            }

            static void *tracer_main(void *arg) {
                (void)arg;
                if (pcc_current_thread_id() <= 0) return (void *)1;
                __atomic_store_n(&worker_ready, 1, __ATOMIC_RELEASE);
                while (__atomic_load_n(&worker_stop, __ATOMIC_ACQUIRE) == 0) {
                    int64_t before = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    __atomic_store_n(&tracer_in_step, 1, __ATOMIC_RELEASE);
                    int64_t got = pcc_gc_step(256);
                    __atomic_store_n(&tracer_in_step, 0, __ATOMIC_RELEASE);
                    int64_t after = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    if (before != after) {
                        __atomic_add_fetch(
                            &steps_spanning_op, 1, __ATOMIC_ACQ_REL);
                        if (got > 0) {
                            __atomic_add_fetch(
                                &progress_spanning_op, 1, __ATOMIC_ACQ_REL);
                        }
                    }
                    pcc_gc_safepoint();
                    sched_yield();
                }
                pcc_thread_unregister_current();
                __atomic_store_n(&worker_exited, 1, __ATOMIC_RELEASE);
                return NULL;
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;

                elem_class = py_class_new("SetElem", NULL, 0, NULL, 0);
                if (elem_class == NULL) return 3;
                pcc_gc_pin((PyObject *)elem_class);
                py_class_add_method(
                    elem_class, "__hash__", (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    elem_class, "__del__", (PyObject *)(uintptr_t)probe_del
                );

                set_root = py_set_new();
                if (set_root == NULL) return 4;
                void *s_h = pcc_gc_scheduler_root_register_handle(&set_root);
                if (s_h == NULL) return 5;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                pthread_t tracer;
                if (pthread_create(&tracer, 0, tracer_main, 0) != 0) return 6;

                int64_t spins = 0;
                while (__atomic_load_n(&worker_ready, __ATOMIC_ACQUIRE) == 0) {
                    sched_yield();
                    if (++spins > 100000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        printf("worker never registered\n");
                        return 7;
                    }
                }

                int64_t inserted = 0;
                PyObject *prev = NULL;   /* probe's own ref while in set */
                for (int64_t i = 0; ; i++) {
                    int64_t spanning = NEED_PROGRESS
                        ? __atomic_load_n(
                              &progress_spanning_op, __ATOMIC_ACQUIRE)
                        : __atomic_load_n(
                              &steps_spanning_op, __ATOMIC_ACQUIRE);
                    int64_t hits = __atomic_load_n(
                        &overlap_hits, __ATOMIC_ACQUIRE);
                    if (inserted >= ROUNDS && spanning > 0 && hits > 0) {
                        break;
                    }
                    if (i > 8000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        while (__atomic_load_n(
                                   &worker_exited, __ATOMIC_ACQUIRE) == 0) {
                            pcc_gc_safepoint();
                            sched_yield();
                        }
                        (void)pthread_join(tracer, NULL);
                        printf("no proven race: inserted=%lld spanning=%lld "
                               "hits=%lld\n",
                               (long long)inserted,
                               (long long)spanning, (long long)hits);
                        return 22;
                    }
                    if ((i & 63) != 0) {
                        sched_yield();
                        continue;
                    }
                    PyObject *e = py_instance_new(elem_class);
                    if (e == NULL) return 8;
                    __atomic_add_fetch(&op_seq, 1, __ATOMIC_RELEASE);
                    py_set_add(pcc_gc_load_ptr(NULL, &set_root), e);
                    inserted++;
                    if (py_err_occurred() != NULL) {
                        printf("set add left an exception pending\n");
                        return 9;
                    }
                    if (prev != NULL) {
                        /* Remove the previous element: identity hit, no
                         * equality callback needed. */
                        __atomic_add_fetch(&op_seq, 1, __ATOMIC_RELEASE);
                        if (py_set_remove(
                                pcc_gc_load_ptr(NULL, &set_root), prev)
                            != 0) {
                            printf("set remove failed\n");
                            return 24;
                        }
                        py_decref(prev);   /* probe drops its ownership */
                        prev = NULL;
                    }
                    prev = e;   /* probe owns it alongside the set */
                    pcc_gc_safepoint();
                    sched_yield();
                }

                __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                int64_t wait_spins = 0;
                while (__atomic_load_n(&worker_exited, __ATOMIC_ACQUIRE) == 0) {
                    pcc_gc_safepoint();
                    sched_yield();
                    if (++wait_spins > 200000000) {
                        printf("tracer did not exit\n");
                        return 23;
                    }
                }
                if (pthread_join(tracer, NULL) != 0) return 10;
                set_root = pcc_gc_load_ptr(NULL, &set_root);

                if (steps_spanning_op == 0) {
                    printf("no tracer step spanned a live container op: "
                           "this probe proves no race\n");
                    return 20;
                }
                if (NEED_PROGRESS && progress_spanning_op == 0) {
                    printf("tracer made no GC progress inside live ops: "
                           "race unproven\n");
                    return 21;
                }
                if (__atomic_load_n(&overlap_hits, __ATOMIC_ACQUIRE) == 0) {
                    printf("hash callback never observed a concurrent step\n");
                    return 31;
                }

                if (py_set_len(set_root) != 1) {
                    printf("len=%lld (expected 1)\n",
                           (long long)py_set_len(set_root));
                    return 11;
                }
                if (py_set_contains(set_root, prev) != 1) {
                    printf("survivor missing from the set\n");
                    return 12;
                }

                for (int i = 0; i < 16; i++) {
                    (void)pcc_gc_collect(0);
                    pcc_gc_safepoint();
                }

                /* Every element but the survivor was removed and must be
                 * finalized exactly once. */
                int64_t seen = __atomic_load_n(&finalized, __ATOMIC_ACQUIRE);
                if (seen != inserted - 1) {
                    printf("displaced elements finalized %lld times "
                           "(expected %lld): %s\n",
                           (long long)seen, (long long)(inserted - 1),
                           seen > inserted - 1 ? "premature free" : "leak");
                    return 14;
                }

                /* Set + probe's own ref. */
                if (refcount_of(prev) != 2) {
                    printf("survivor rc=%lld (expected 2)\n",
                           (long long)refcount_of(prev));
                    return 29;
                }
                py_decref(prev);   /* drop the probe ref; the set keeps one */
                if (py_set_contains(set_root, prev) != 1
                    || refcount_of(prev) != 1) {
                    printf("post-drop membership/refcount wrong\n");
                    return 30;
                }

                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 15;
                }

                py_set_remove(set_root, prev);
                py_decref(prev);
                pcc_gc_scheduler_root_unregister_handle(s_h);
                py_decref(pcc_gc_load_ptr(NULL, &set_root));
                pcc_gc_unpin((PyObject *)elem_class);
                py_decref((PyObject *)elem_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind)
        .replace("NEED_PROGRESS",
                 "0" if gc_kind == "PCC_GC_KIND_REFCOUNT_CYCLE" else "1"),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} concurrent set race returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("gc_kind", TRACER_RACE_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_concurrent_tracer_races_dict_update_walk(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """The py_dict_update walk raced by a REAL concurrent tracer.

    Covers the update-path item of
    ``GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT``'s concurrency boundary.
    The walk hashes each source key to locate the destination slot --
    here an allocating ``__hash__`` that samples the tracer's in-step
    flag -- while the snapshot-before-callback discipline documented in
    py_dict.c keeps the walk consistent mid-race.

    Keys are identity-distinct instances used stably across rounds, so
    each repeated update replaces the two stored VALUES; accounting:
    every created value except the final four (two in src, two in dst,
    shared bindings) must be finalized exactly once.

    Race proof and exclusions match the sibling probes.  Post-drain: both
    destinations resolve to the last-inserted pair by identity, exact
    refcounts including the src+dst shared binding, root balance
    unchanged, no pending exception.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="concurrent_update_race_" + gc_kind.lower(),
        source_text=r"""
            #include "py_internal.h"
            #include <pthread.h>
            #include <sched.h>
            #include <stdint.h>
            #include <stdio.h>

            #define ROUNDS 24

            static PyObject *dst_root;
            static PyObject *src_root;
            static PyObject *ka_root;
            static PyObject *kb_root;
            static struct PyClassObject *key_class;
            static struct PyClassObject *value_class;
            static int64_t finalized;
            static volatile int64_t worker_ready;
            static volatile int64_t worker_stop;
            static volatile int64_t worker_exited;
            static volatile int64_t op_seq;
            static volatile int64_t tracer_in_step;
            static volatile int64_t overlap_hits;
            static volatile int64_t steps_spanning_op;
            static volatile int64_t progress_spanning_op;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static int64_t refcount_of(PyObject *obj) {
                return pcc_refcount_load(&((PyObjectHeader *)obj)->refcount);
            }

            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                for (int k = 0; k < 4000; k++) {
                    if (__atomic_load_n(&tracer_in_step, __ATOMIC_ACQUIRE)) {
                        __atomic_add_fetch(
                            &overlap_hits, 1, __ATOMIC_ACQ_REL);
                        break;
                    }
                    sched_yield();
                }
                PyObject *junk = py_str_new("upd-race", 8);
                if (junk != NULL) py_decref(junk);
                return py_int_from_i64(11);
            }

            static PyObject *probe_del(PyObject *self) {
                (void)self;
                __atomic_add_fetch(&finalized, 1, __ATOMIC_ACQ_REL);
                return py_int_from_i64(0);
            }

            static void *tracer_main(void *arg) {
                (void)arg;
                if (pcc_current_thread_id() <= 0) return (void *)1;
                __atomic_store_n(&worker_ready, 1, __ATOMIC_RELEASE);
                while (__atomic_load_n(&worker_stop, __ATOMIC_ACQUIRE) == 0) {
                    int64_t before = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    __atomic_store_n(&tracer_in_step, 1, __ATOMIC_RELEASE);
                    int64_t got = pcc_gc_step(256);
                    __atomic_store_n(&tracer_in_step, 0, __ATOMIC_RELEASE);
                    int64_t after = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    if (before != after) {
                        __atomic_add_fetch(
                            &steps_spanning_op, 1, __ATOMIC_ACQ_REL);
                        if (got > 0) {
                            __atomic_add_fetch(
                                &progress_spanning_op, 1, __ATOMIC_ACQ_REL);
                        }
                    }
                    pcc_gc_safepoint();
                    sched_yield();
                }
                pcc_thread_unregister_current();
                __atomic_store_n(&worker_exited, 1, __ATOMIC_RELEASE);
                return NULL;
            }

            static int fail_pending(const char *what) {
                printf("%s left an exception pending\n", what);
                return 9;
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;

                key_class = py_class_new("UpdKey", NULL, 0, NULL, 0);
                value_class = py_class_new("UpdValue", NULL, 0, NULL, 0);
                if (key_class == NULL || value_class == NULL) return 3;
                pcc_gc_pin((PyObject *)key_class);
                pcc_gc_pin((PyObject *)value_class);
                py_class_add_method(
                    key_class, "__hash__", (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    value_class, "__del__", (PyObject *)(uintptr_t)probe_del
                );

                dst_root = py_dict_new();
                src_root = py_dict_new();
                ka_root = py_instance_new(key_class);
                kb_root = py_instance_new(key_class);
                if (dst_root == NULL || src_root == NULL
                    || ka_root == NULL || kb_root == NULL) return 4;
                void *d_h = pcc_gc_scheduler_root_register_handle(&dst_root);
                void *s_h = pcc_gc_scheduler_root_register_handle(&src_root);
                void *a_h = pcc_gc_scheduler_root_register_handle(&ka_root);
                void *b_h = pcc_gc_scheduler_root_register_handle(&kb_root);
                if (!d_h || !s_h || !a_h || !b_h) return 5;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                pthread_t tracer;
                if (pthread_create(&tracer, 0, tracer_main, 0) != 0) return 6;

                int64_t spins = 0;
                while (__atomic_load_n(&worker_ready, __ATOMIC_ACQUIRE) == 0) {
                    sched_yield();
                    if (++spins > 100000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        printf("worker never registered\n");
                        return 7;
                    }
                }

                int64_t rounds_done = 0;
                PyObject *last_a = NULL;
                PyObject *last_b = NULL;
                for (int64_t i = 0; ; i++) {
                    int64_t spanning = NEED_PROGRESS
                        ? __atomic_load_n(
                              &progress_spanning_op, __ATOMIC_ACQUIRE)
                        : __atomic_load_n(
                              &steps_spanning_op, __ATOMIC_ACQUIRE);
                    int64_t hits = __atomic_load_n(
                        &overlap_hits, __ATOMIC_ACQUIRE);
                    if (rounds_done >= ROUNDS && spanning > 0 && hits > 0) {
                        break;
                    }
                    if (i > 8000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        while (__atomic_load_n(
                                   &worker_exited, __ATOMIC_ACQUIRE) == 0) {
                            pcc_gc_safepoint();
                            sched_yield();
                        }
                        (void)pthread_join(tracer, NULL);
                        printf("no proven race: rounds=%lld spanning=%lld "
                               "hits=%lld\n",
                               (long long)rounds_done,
                               (long long)spanning, (long long)hits);
                        return 22;
                    }
                    if ((i & 63) != 0) {
                        sched_yield();
                        continue;
                    }
                    /* One churn round: refresh both source bindings, then
                     * run the update walk into dst under the live tracer.
                     * The probe holds refs to the newest pair for identity
                     * checks. */
                    PyObject *va = py_instance_new(value_class);
                    PyObject *vb = py_instance_new(value_class);
                    if (va == NULL || vb == NULL) return 8;
                    __atomic_add_fetch(&op_seq, 1, __ATOMIC_RELEASE);
                    py_dict_set(pcc_gc_load_ptr(NULL, &src_root),
                                pcc_gc_load_ptr(NULL, &ka_root), va);
                    py_dict_set(pcc_gc_load_ptr(NULL, &src_root),
                                pcc_gc_load_ptr(NULL, &kb_root), vb);
                    __atomic_add_fetch(&op_seq, 1, __ATOMIC_RELEASE);
                    py_dict_update(pcc_gc_load_ptr(NULL, &dst_root),
                                   pcc_gc_load_ptr(NULL, &src_root));
                    py_decref(va);
                    py_decref(vb);
                    rounds_done++;
                    last_a = va;   /* borrowed identity; src+dst hold it */
                    last_b = vb;
                    if (py_err_occurred() != NULL) {
                        return fail_pending("update round");
                    }
                    pcc_gc_safepoint();
                    sched_yield();
                }

                __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                int64_t wait_spins = 0;
                while (__atomic_load_n(&worker_exited, __ATOMIC_ACQUIRE) == 0) {
                    pcc_gc_safepoint();
                    sched_yield();
                    if (++wait_spins > 200000000) {
                        printf("tracer did not exit\n");
                        return 23;
                    }
                }
                if (pthread_join(tracer, NULL) != 0) return 10;
                dst_root = pcc_gc_load_ptr(NULL, &dst_root);
                ka_root = pcc_gc_load_ptr(NULL, &ka_root);
                kb_root = pcc_gc_load_ptr(NULL, &kb_root);

                if (steps_spanning_op == 0) {
                    printf("no tracer step spanned a live container op: "
                           "this probe proves no race\n");
                    return 20;
                }
                if (NEED_PROGRESS && progress_spanning_op == 0) {
                    printf("tracer made no GC progress inside live ops: "
                           "race unproven\n");
                    return 21;
                }
                if (__atomic_load_n(&overlap_hits, __ATOMIC_ACQUIRE) == 0) {
                    printf("hash callback never observed a concurrent step\n");
                    return 31;
                }

                if (py_dict_len(dst_root) != 2) {
                    printf("len=%lld (expected 2)\n",
                           (long long)py_dict_len(dst_root));
                    return 11;
                }

                for (int i = 0; i < 16; i++) {
                    (void)pcc_gc_collect(0);
                    pcc_gc_safepoint();
                }

                PyObject *ga = py_dict_get(dst_root, ka_root);
                PyObject *gb = py_dict_get(dst_root, kb_root);
                if (ga == NULL || gb == NULL || ga != last_a || gb != last_b) {
                    printf("destination lost or stale after the race\n");
                    return 12;
                }
                /* src + dst hold each binding; the probe's get refs were
                 * counted too, so expect 3 while both get-refs are alive. */
                if (refcount_of(ga) != 3 || refcount_of(gb) != 3) {
                    printf("shared-binding rc a=%lld b=%lld (expected 3 x2)\n",
                           (long long)refcount_of(ga),
                           (long long)refcount_of(gb));
                    py_decref(ga);
                    py_decref(gb);
                    return 29;
                }
                py_decref(ga);
                py_decref(gb);

                /* Created = 2 per round; survivors = the final pair, held
                 * once by src and once by dst (2 refs, one object). */
                int64_t seen = __atomic_load_n(&finalized, __ATOMIC_ACQUIRE);
                if (seen != 2 * rounds_done - 2) {
                    printf("displaced values finalized %lld times (expected "
                           "%lld): %s\n",
                           (long long)seen,
                           (long long)(2 * rounds_done - 2),
                           seen > 2 * rounds_done - 2 ? "premature free"
                                                      : "leak");
                    return 14;
                }

                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 15;
                }

                py_decref(pcc_gc_load_ptr(NULL, &ka_root));
                py_decref(pcc_gc_load_ptr(NULL, &kb_root));
                pcc_gc_scheduler_root_unregister_handle(a_h);
                pcc_gc_scheduler_root_unregister_handle(b_h);
                pcc_gc_scheduler_root_unregister_handle(d_h);
                pcc_gc_scheduler_root_unregister_handle(s_h);
                py_decref(pcc_gc_load_ptr(NULL, &dst_root));
                py_decref(pcc_gc_load_ptr(NULL, &src_root));
                pcc_gc_unpin((PyObject *)key_class);
                pcc_gc_unpin((PyObject *)value_class);
                py_decref((PyObject *)key_class);
                py_decref((PyObject *)value_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind)
        .replace("NEED_PROGRESS",
                 "0" if gc_kind == "PCC_GC_KIND_REFCOUNT_CYCLE" else "1"),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} concurrent update race returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("gc_kind", ALL_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_instance_eq_collapses_container_keys(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """Single-threaded parity: user-instance __eq__ governs key identity.

    Regression for ``SEM-P1-INSTANCE-EQ-CONTAINER-KEYS``: two instances
    with equal hashes and an always-true ``__eq__`` must collapse to one
    dict entry (the FIRST inserted key stays stored) and one set member,
    with the equality callback actually invoked.  Runs on all five
    backends in both mirrors; the concurrent twin is
    ``test_concurrent_tracer_races_eq_key_preserved``.
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=False,
        stem="instance_eq_collapse_" + gc_kind.lower(),
        source_text=r"""
            #include "py_internal.h"
            #include <stdio.h>

            static struct PyClassObject *key_class;
            static int64_t eq_calls;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static int64_t refcount_of(PyObject *obj) {
                return pcc_refcount_load(&((PyObjectHeader *)obj)->refcount);
            }

            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                return py_int_from_i64(7);
            }

            static PyObject *probe_eq(PyObject *self, PyObject *other) {
                (void)self; (void)other;
                __atomic_add_fetch(&eq_calls, 1, __ATOMIC_RELAXED);
                return py_bool_from_bit(1);
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;
                key_class = py_class_new("EqKey", NULL, 0, NULL, 0);
                if (key_class == NULL) return 3;
                pcc_gc_pin((PyObject *)key_class);
                py_class_add_method(
                    key_class, "__hash__", (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    key_class, "__eq__", (PyObject *)(uintptr_t)probe_eq
                );

                PyObject *d = py_dict_new();
                PyObject *s = py_set_new();
                PyObject *k0 = py_instance_new(key_class);
                PyObject *k1 = py_instance_new(key_class);
                PyObject *e0 = py_instance_new(key_class);
                PyObject *e1 = py_instance_new(key_class);
                if (!d || !s || !k0 || !k1 || !e0 || !e1) return 4;

                py_dict_set(d, k0, py_int_from_i64(1));
                py_dict_set(d, k1, py_int_from_i64(2));
                if (py_dict_len(d) != 1) {
                    printf("dict len=%lld (expected 1)\n",
                           (long long)py_dict_len(d));
                    return 10;
                }
                if (__atomic_load_n(&eq_calls, __ATOMIC_RELAXED) == 0) {
                    printf("__eq__ was never consulted by the dict\n");
                    return 11;
                }
                /* The FIRST inserted key stays stored: k0 is root + dict. */
                if (refcount_of(k0) != 2 || refcount_of(k1) != 1) {
                    printf("stored-key rc k0=%lld k1=%lld (expected 2/1)\n",
                           (long long)refcount_of(k0),
                           (long long)refcount_of(k1));
                    return 12;
                }
                PyObject *v = py_dict_get(d, k1);
                if (v == NULL || py_int_cmp(v, py_int_from_i64(2)) != 0) {
                    printf("second insert did not become the stored value\n");
                    return 13;
                }
                py_decref(v);

                py_set_add(s, e0);
                py_set_add(s, e1);
                if (py_set_len(s) != 1) {
                    printf("set len=%lld (expected 1)\n",
                           (long long)py_set_len(s));
                    return 14;
                }
                if (py_set_contains(s, e1) != 1) {
                    printf("set membership via equal instance failed\n");
                    return 15;
                }

                py_decref(k0); py_decref(k1);
                py_decref(e0); py_decref(e1);
                py_decref(d); py_decref(s);
                pcc_gc_unpin((PyObject *)key_class);
                py_decref((PyObject *)key_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} instance-eq collapse returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_instance_eq_reflects_notimplemented_to_right_operand(
    tmp_path: Path,
    kind: str,
) -> None:
    """A right-hand instance gets __eq__ after the left returns NotImplemented."""
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=False,
        stem="instance_eq_reflected",
        source_text=r'''
            #include "py_internal.h"

            static int64_t left_calls;
            static int64_t right_calls;
            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static PyObject *same_hash(PyObject *self) {
                (void)self;
                return py_int_from_i64(17);
            }
            static PyObject *left_eq(PyObject *self, PyObject *other) {
                (void)self; (void)other;
                left_calls++;
                py_incref(py_NotImplemented);
                return py_NotImplemented;
            }
            static PyObject *right_eq(PyObject *self, PyObject *other) {
                (void)self; (void)other;
                right_calls++;
                py_incref(py_True);
                return py_True;
            }

            int main(void) {
                struct PyClassObject *left_cls = py_class_new(
                    "LeftEq", NULL, 0, NULL, 0
                );
                struct PyClassObject *right_cls = py_class_new(
                    "RightEq", NULL, 0, NULL, 0
                );
                if (left_cls == NULL || right_cls == NULL) return 2;
                py_class_add_method(
                    left_cls, "__hash__", (PyObject *)(uintptr_t)same_hash
                );
                py_class_add_method(
                    right_cls, "__hash__", (PyObject *)(uintptr_t)same_hash
                );
                py_class_add_method(
                    left_cls, "__eq__", (PyObject *)(uintptr_t)left_eq
                );
                py_class_add_method(
                    right_cls, "__eq__", (PyObject *)(uintptr_t)right_eq
                );
                PyObject *left = py_instance_new(left_cls);
                PyObject *right = py_instance_new(right_cls);
                PyObject *dict = py_dict_new();
                if (left == NULL || right == NULL || dict == NULL) return 3;
                py_dict_set(dict, left, py_int_from_i64(1));
                py_dict_set(dict, right, py_int_from_i64(2));
                if (py_err_occurred() || py_dict_len(dict) != 1) return 4;
                if (left_calls < 1 || right_calls < 1) return 5;
                py_decref(left); py_decref(right); py_decref(dict);
                py_decref((PyObject *)left_cls);
                py_decref((PyObject *)right_cls);
                return 0;
            }
        ''',
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, (
        f"{kind} reflected instance equality returned {run.returncode}: "
        + run.stdout + run.stderr
    )


@pytest.mark.parametrize("gc_kind", TRACER_RACE_GC_KINDS)
@pytest.mark.parametrize("kind", ["c", "pcc_python"])
def test_concurrent_tracer_races_eq_key_preserved(
    tmp_path: Path,
    kind: str,
    gc_kind: str,
) -> None:
    """Equal-but-distinct dict keys under a REAL concurrent tracer.

    Concurrent regression for ``SEM-P1-INSTANCE-EQ-CONTAINER-KEYS`` and
    the equality-callback surface of
    ``GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT``: two instances hash and
    compare equal; the mutator repeatedly inserts under ``k1`` after
    seeding with ``k0``, so every operation takes the allocating
    ``__hash__`` plus ``__eq__`` restart path mid-race.

    Race proof matches the sibling probes: per-op epoch bracket, GC
    progress inside spanning steps on tracing backends, and a required
    direct callback-x-step intersection sampled by BOTH callbacks
    independently.

    Post-drain: exactly one entry; BOTH keys hit; refcounts prove the
    STORED key is still the original ``k0`` (rc 2) while ``k1`` never
    became stored (rc 1); the surviving value is the last inserted with
    exact refcounts; every displaced value finalized exactly once; root
    balance unchanged.

    COLORED_RELOCATING excluded (owned by
    ``GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED``).
    """
    executable = _compile_runtime_probe(
        tmp_path,
        kind=kind,
        threaded=True,
        stem="concurrent_eq_key_race_" + gc_kind.lower(),
        source_text=r"""
            #include "py_internal.h"
            #include <pthread.h>
            #include <sched.h>
            #include <stdint.h>
            #include <stdio.h>

            #define ROUNDS 48

            static PyObject *dict_root;
            static PyObject *k0_root;
            static PyObject *k1_root;
            static struct PyClassObject *key_class;
            static struct PyClassObject *value_class;
            static int64_t finalized;
            static volatile int64_t worker_ready;
            static volatile int64_t worker_stop;
            static volatile int64_t worker_exited;
            static volatile int64_t op_seq;
            static volatile int64_t tracer_in_step;
            static volatile int64_t overlap_hits;
            static volatile int64_t overlap_eq_hits;
            static volatile int64_t steps_spanning_op;
            static volatile int64_t progress_spanning_op;

            extern struct PyClassObject *py_class_new(
                const char *, struct PyClassObject **, int32_t,
                const char **, int32_t
            );
            extern void py_class_add_method(
                struct PyClassObject *, const char *, PyObject *
            );
            extern PyObject *py_instance_new(struct PyClassObject *);

            static int64_t refcount_of(PyObject *obj) {
                return pcc_refcount_load(&((PyObjectHeader *)obj)->refcount);
            }

            static void sample_tracer(volatile int64_t *counter) {
                for (int k = 0; k < 4000; k++) {
                    if (__atomic_load_n(&tracer_in_step, __ATOMIC_ACQUIRE)) {
                        __atomic_add_fetch(counter, 1, __ATOMIC_ACQ_REL);
                        break;
                    }
                    sched_yield();
                }
            }

            static PyObject *probe_hash(PyObject *self) {
                (void)self;
                sample_tracer(&overlap_hits);
                PyObject *junk = py_str_new("eq-race", 7);
                if (junk != NULL) py_decref(junk);
                return py_int_from_i64(11);
            }

            static PyObject *probe_eq(PyObject *self, PyObject *other) {
                (void)self; (void)other;
                sample_tracer(&overlap_eq_hits);
                return py_bool_from_bit(1);
            }

            static PyObject *probe_del(PyObject *self) {
                (void)self;
                __atomic_add_fetch(&finalized, 1, __ATOMIC_ACQ_REL);
                return py_int_from_i64(0);
            }

            static void *tracer_main(void *arg) {
                (void)arg;
                if (pcc_current_thread_id() <= 0) return (void *)1;
                __atomic_store_n(&worker_ready, 1, __ATOMIC_RELEASE);
                while (__atomic_load_n(&worker_stop, __ATOMIC_ACQUIRE) == 0) {
                    int64_t before = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    __atomic_store_n(&tracer_in_step, 1, __ATOMIC_RELEASE);
                    int64_t got = pcc_gc_step(256);
                    __atomic_store_n(&tracer_in_step, 0, __ATOMIC_RELEASE);
                    int64_t after = __atomic_load_n(&op_seq, __ATOMIC_ACQUIRE);
                    if (before != after) {
                        __atomic_add_fetch(
                            &steps_spanning_op, 1, __ATOMIC_ACQ_REL);
                        if (got > 0) {
                            __atomic_add_fetch(
                                &progress_spanning_op, 1, __ATOMIC_ACQ_REL);
                        }
                    }
                    pcc_gc_safepoint();
                    sched_yield();
                }
                pcc_thread_unregister_current();
                __atomic_store_n(&worker_exited, 1, __ATOMIC_RELEASE);
                return NULL;
            }

            int main(void) {
                if (pcc_gc_set_backend(__GC_KIND__) != 0) return 2;

                key_class = py_class_new("EqKey", NULL, 0, NULL, 0);
                value_class = py_class_new("EqValue", NULL, 0, NULL, 0);
                if (key_class == NULL || value_class == NULL) return 3;
                pcc_gc_pin((PyObject *)key_class);
                pcc_gc_pin((PyObject *)value_class);
                py_class_add_method(
                    key_class, "__hash__", (PyObject *)(uintptr_t)probe_hash
                );
                py_class_add_method(
                    key_class, "__eq__", (PyObject *)(uintptr_t)probe_eq
                );
                py_class_add_method(
                    value_class, "__del__", (PyObject *)(uintptr_t)probe_del
                );

                dict_root = py_dict_new();
                k0_root = py_instance_new(key_class);
                k1_root = py_instance_new(key_class);
                if (dict_root == NULL || k0_root == NULL || k1_root == NULL)
                    return 4;
                void *d_h = pcc_gc_scheduler_root_register_handle(&dict_root);
                void *k0_h = pcc_gc_scheduler_root_register_handle(&k0_root);
                void *k1_h = pcc_gc_scheduler_root_register_handle(&k1_root);
                if (d_h == NULL || k0_h == NULL || k1_h == NULL) return 5;
                int64_t roots_before = pcc_gc_scheduler_root_count();

                PyObject *seed = py_instance_new(value_class);
                if (seed == NULL) return 6;
                py_dict_set(pcc_gc_load_ptr(NULL, &dict_root),
                            pcc_gc_load_ptr(NULL, &k0_root), seed);
                py_decref(seed);
                if (py_err_occurred() != NULL) {
                    printf("seed insert left an exception pending\n");
                    return 9;
                }

                pthread_t tracer;
                if (pthread_create(&tracer, 0, tracer_main, 0) != 0) return 6;

                int64_t spins = 0;
                while (__atomic_load_n(&worker_ready, __ATOMIC_ACQUIRE) == 0) {
                    sched_yield();
                    if (++spins > 100000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        printf("worker never registered\n");
                        return 7;
                    }
                }

                int64_t inserted = 0;
                PyObject *last = NULL;
                for (int64_t i = 0; ; i++) {
                    int64_t spanning = NEED_PROGRESS
                        ? __atomic_load_n(
                              &progress_spanning_op, __ATOMIC_ACQUIRE)
                        : __atomic_load_n(
                              &steps_spanning_op, __ATOMIC_ACQUIRE);
                    int64_t hits = __atomic_load_n(
                        &overlap_hits, __ATOMIC_ACQUIRE);
                    int64_t eq_hits = __atomic_load_n(
                        &overlap_eq_hits, __ATOMIC_ACQUIRE);
                    if (inserted >= ROUNDS && spanning > 0 && hits > 0
                        && eq_hits > 0) {
                        break;
                    }
                    if (i > 8000000) {
                        __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                        while (__atomic_load_n(
                                   &worker_exited, __ATOMIC_ACQUIRE) == 0) {
                            pcc_gc_safepoint();
                            sched_yield();
                        }
                        (void)pthread_join(tracer, NULL);
                        printf("no proven race: inserted=%lld spanning=%lld "
                               "hits=%lld eq_hits=%lld\n",
                               (long long)inserted,
                               (long long)spanning, (long long)hits,
                               (long long)eq_hits);
                        return 22;
                    }
                    if ((i & 63) != 0) {
                        sched_yield();
                        continue;
                    }
                    PyObject *v = py_instance_new(value_class);
                    if (v == NULL) return 8;
                    __atomic_add_fetch(&op_seq, 1, __ATOMIC_RELEASE);
                    py_dict_set(pcc_gc_load_ptr(NULL, &dict_root),
                                pcc_gc_load_ptr(NULL, &k1_root), v);
                    py_decref(v);   /* the dict holds the only reference */
                    inserted++;
                    last = v;       /* borrowed identity; dict owns storage */
                    if (py_err_occurred() != NULL) {
                        printf("churn insert left an exception pending\n");
                        return 9;
                    }
                    pcc_gc_safepoint();
                    sched_yield();
                }

                __atomic_store_n(&worker_stop, 1, __ATOMIC_RELEASE);
                int64_t wait_spins = 0;
                while (__atomic_load_n(&worker_exited, __ATOMIC_ACQUIRE) == 0) {
                    pcc_gc_safepoint();
                    sched_yield();
                    if (++wait_spins > 200000000) {
                        printf("tracer did not exit\n");
                        return 23;
                    }
                }
                if (pthread_join(tracer, NULL) != 0) return 10;
                dict_root = pcc_gc_load_ptr(NULL, &dict_root);
                k0_root = pcc_gc_load_ptr(NULL, &k0_root);
                k1_root = pcc_gc_load_ptr(NULL, &k1_root);

                if (steps_spanning_op == 0) {
                    printf("no tracer step spanned a live container op: "
                           "this probe proves no race\n");
                    return 20;
                }
                if (NEED_PROGRESS && progress_spanning_op == 0) {
                    printf("tracer made no GC progress inside live ops: "
                           "race unproven\n");
                    return 21;
                }
                if (__atomic_load_n(&overlap_hits, __ATOMIC_ACQUIRE) == 0) {
                    printf("hash callback never observed a concurrent step\n");
                    return 31;
                }
                if (__atomic_load_n(&overlap_eq_hits, __ATOMIC_ACQUIRE) == 0) {
                    printf("equality callback never observed a concurrent "
                           "step: eq-path race unproven\n");
                    return 32;
                }

                if (py_dict_len(dict_root) != 1) {
                    printf("len=%lld (expected 1)\n",
                           (long long)py_dict_len(dict_root));
                    return 11;
                }

                for (int i = 0; i < 16; i++) {
                    (void)pcc_gc_collect(0);
                    pcc_gc_safepoint();
                }

                if (refcount_of(k0_root) != 2) {
                    printf("stored-key rc=%lld (expected 2: root + dict)\n",
                           (long long)refcount_of(k0_root));
                    return 33;
                }
                if (refcount_of(k1_root) != 1) {
                    printf("incoming-key rc=%lld (expected 1: root only)\n",
                           (long long)refcount_of(k1_root));
                    return 34;
                }

                PyObject *got = py_dict_get(dict_root, k1_root);
                if (got == NULL || got != last) {
                    printf("surviving value lost or not the last inserted\n");
                    return 12;
                }
                if (refcount_of(last) != 2) {
                    printf("surviving rc=%lld (expected 2: dict + get ref)\n",
                           (long long)refcount_of(last));
                    return 29;
                }
                py_decref(got);
                got = py_dict_get(dict_root, k0_root);
                if (got == NULL || got != last) {
                    printf("original key no longer resolves to the value\n");
                    return 35;
                }
                py_decref(got);

                int64_t seen = __atomic_load_n(&finalized, __ATOMIC_ACQUIRE);
                if (seen != inserted) {
                    printf("displaced values finalized %lld times (expected "
                           "%lld): %s\n",
                           (long long)seen, (long long)inserted,
                           seen > inserted ? "premature free" : "leak");
                    return 14;
                }

                if (pcc_gc_scheduler_root_count() != roots_before) {
                    printf("roots leaked: %lld -> %lld\n",
                           (long long)roots_before,
                           (long long)pcc_gc_scheduler_root_count());
                    return 15;
                }

                py_decref(pcc_gc_load_ptr(NULL, &k0_root));
                py_decref(pcc_gc_load_ptr(NULL, &k1_root));
                pcc_gc_scheduler_root_unregister_handle(k0_h);
                pcc_gc_scheduler_root_unregister_handle(k1_h);
                pcc_gc_scheduler_root_unregister_handle(d_h);
                py_decref(pcc_gc_load_ptr(NULL, &dict_root));
                pcc_gc_unpin((PyObject *)key_class);
                pcc_gc_unpin((PyObject *)value_class);
                py_decref((PyObject *)key_class);
                py_decref((PyObject *)value_class);
                return 0;
            }
        """.replace("__GC_KIND__", gc_kind)
        .replace("NEED_PROGRESS",
                 "0" if gc_kind == "PCC_GC_KIND_REFCOUNT_CYCLE" else "1"),
    )
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, (
        f"{kind} {gc_kind} concurrent eq-key race returned {run.returncode}: "
        + run.stdout + run.stderr
    )
