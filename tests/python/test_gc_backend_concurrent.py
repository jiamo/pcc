from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

from tests.runtime_build_cache import cached_c_runtime, cached_threaded_c_runtime


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"


def _cc() -> str:
    return os.environ.get("CC", "cc")


def _build_threaded_runtime(tmp_path: Path) -> Path:
    del tmp_path
    return cached_threaded_c_runtime()


def _build_nonthread_runtime(tmp_path: Path) -> Path:
    del tmp_path
    return cached_c_runtime()


def test_concurrent_backend_starts_worker_and_assists_allocations(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_probe.c"
    exe = tmp_path / "cms_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <unistd.h>

            enum {
                CMS_WORKER_STARTS = 11,
                CMS_QUEUE_PUSHES = 12,
                CMS_WORKER_DRAINS = 13,
                CMS_MUTATOR_ASSISTS = 14
            };

            static int wait_for_drain(void) {
                for (int i = 0; i < 200; i++) {
                    if (pcc_gc_telemetry(CMS_WORKER_DRAINS) > 0) return 1;
                    pcc_gc_safepoint();
                    usleep(1000);
                }
                return 0;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                pcc_gc_telemetry_reset();

                if (pcc_gc_set_backend(PCC_GC_KIND_CONCURRENT_MARK_SWEEP) != 0) {
                    return 3;
                }
                if (pcc_gc_backend() != PCC_GC_KIND_CONCURRENT_MARK_SWEEP) {
                    return 4;
                }

                for (int i = 0; i < 300; i++) {
                    PyObject *o = pcc_gc_alloc(64, PY_TYPE_LIST, 0);
                    if (o == 0) return 5;
                    pcc_gc_release(o);
                }

                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_WORKER_STARTS));
                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_QUEUE_PUSHES));
                printf("%d\\n", wait_for_drain());
                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_MUTATOR_ASSISTS));
                printf("%d\\n", pcc_gc_telemetry(PCC_GC_COUNTER_MAX_PAUSE_US) < 10000);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_DEBT_THRESHOLD": "1024",
            "PCC_GC_STEPMUL": "200",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert int(lines[0]) >= 1
    assert int(lines[1]) > 0
    assert lines[2] == "1"
    assert int(lines[3]) > 0
    assert lines[4] == "1"


def test_concurrent_backend_worker_traces_gray_barrier_work(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_trace_probe.c"
    exe = tmp_path / "cms_trace_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>
            #include <unistd.h>

            enum {
                CMS_WORKER_TRACES = 18
            };

            static int wait_for_worker_trace(void) {
                for (int i = 0; i < 200; i++) {
                    if (pcc_gc_telemetry(CMS_WORKER_TRACES) > 0) return 1;
                    pcc_thread_safepoint();
                    usleep(1000);
                }
                return 0;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(PCC_GC_KIND_CONCURRENT_MARK_SWEEP) != 0) {
                    return 3;
                }
                pcc_gc_telemetry_reset();
                if (pcc_stop_the_world() != 0) return 4;

                PyObject *root_a = pcc_gc_alloc(24, PY_TYPE_INT, 0);
                PyObject *root_b = pcc_gc_alloc(24, PY_TYPE_INT, 0);
                PyObject *owner = pcc_gc_alloc(24, PY_TYPE_INT, 0);
                PyObject *child = pcc_gc_alloc(24, PY_TYPE_INT, 0);
                PyObject **slot = (PyObject **)calloc(1, sizeof(PyObject *));
                if (
                    root_a == 0 || root_b == 0
                    || owner == 0 || child == 0 || slot == 0
                ) return 5;
                pcc_gc_pin(root_a);
                pcc_gc_pin(root_b);
                (void)pcc_gc_step(1);

                pcc_gc_store_ptr(owner, slot, child);
                if (pcc_resume_world() != 0) return 6;

                PyObject *ticket = pcc_gc_alloc(24, PY_TYPE_INT, 0);
                if (ticket == 0) return 7;
                pcc_gc_release(ticket);

                printf("%d\\n", wait_for_worker_trace());
                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_WORKER_TRACES));

                pcc_gc_store_ptr(owner, slot, 0);
                pcc_gc_unpin(root_a);
                pcc_gc_unpin(root_b);
                pcc_gc_release(root_a);
                pcc_gc_release(root_b);
                pcc_gc_release(owner);
                pcc_gc_release(child);
                free(slot);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "1"
    assert int(lines[1]) > 0


def test_concurrent_backend_batches_gray_barrier_flushes(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_wb_buffer_probe.c"
    exe = tmp_path / "cms_wb_buffer_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            enum {
                CMS_QUEUE_PUSHES = 12,
                CMS_WB_FLUSHES = 23
            };

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(PCC_GC_KIND_CONCURRENT_MARK_SWEEP) != 0) {
                    return 3;
                }
                if (pcc_stop_the_world() != 0) return 4;

                PyObject *root_a = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *root_b = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *owner = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject **slots = (PyObject **)calloc(40, sizeof(PyObject *));
                if (root_a == 0 || root_b == 0 || owner == 0 || slots == 0) {
                    return 5;
                }
                pcc_gc_pin(root_a);
                pcc_gc_pin(root_b);
                (void)pcc_gc_step(1);
                pcc_gc_telemetry_reset();

                for (int i = 0; i < 40; i++) {
                    PyObject *child = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                    if (child == 0) return 6;
                    pcc_gc_store_ptr(owner, &slots[i], child);
                    pcc_gc_release(child);
                }
                (void)pcc_gc_step(1);

                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_WB_FLUSHES));
                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_QUEUE_PUSHES));

                for (int i = 0; i < 40; i++) {
                    pcc_gc_store_ptr(owner, &slots[i], 0);
                }
                pcc_gc_unpin(root_a);
                pcc_gc_unpin(root_b);
                pcc_gc_release(root_a);
                pcc_gc_release(root_b);
                pcc_gc_release(owner);
                free(slots);
                (void)pcc_resume_world();
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert int(lines[0]) >= 1
    assert int(lines[1]) >= 32


def test_concurrent_backend_defers_full_wb_flush_until_outer_graph_unlock(
    tmp_path,
):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_wb_outer_graph_lock_probe.c"
    exe = tmp_path / "cms_wb_outer_graph_lock_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            static int wait_for_exact_worker_traces(int64_t expected) {
                for (int i = 0; i < 100000; i++) {
                    int64_t traces = pcc_gc_telemetry(
                        PCC_GC_COUNTER_CMS_WORKER_TRACES
                    );
                    if (traces == expected) return 1;
                    if (traces > expected) return 0;
                    /* Phase-predicate cooperation with the worker's real STW
                     * handshake; no scheduler sleep/yield tuning. */
                    pcc_gc_safepoint();
                }
                return 0;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 3;
                if (pcc_stop_the_world() != 0) return 4;

                PyObject *root_a = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *root_b = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                if (root_a == 0 || root_b == 0) return 5;
                pcc_gc_pin(root_a);
                pcc_gc_pin(root_b);
                (void)pcc_gc_step(1);

                PyObject *children[40] = {0};
                PyObject *expected[40] = {0};
                PyObject *roots[40] = {0};
                for (int i = 0; i < 40; i++) {
                    children[i] = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                    if (children[i] == 0) return 6;
                    expected[i] = children[i];
                    pcc_gc_pin(children[i]);
                }
                /* Remove allocation-work tickets before the observed cycle.
                 * The same-CMS reset synchronously stops and clears the old
                 * worker queue; pinning keeps every child live across it. */
                if (pcc_resume_world() != 0) return 7;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 8;
                if (pcc_stop_the_world() != 0) return 9;
                for (int i = 0; i < 40; i++) {
                    pcc_gc_unpin(children[i]);
                }
                /* Budget one leaves exactly one of the two pinned roots gray.
                 * It later proves that overflow is a whole-gray-set drain,
                 * rather than a budget-one substitute. */
                (void)pcc_gc_step(1);
                py_header_flags_update(
                    py_header(root_a), PY_FLAG_GC_COLOR_MASK, PY_FLAG_GC_GRAY
                );
                py_header_flags_update(
                    py_header(root_b), PY_FLAG_GC_COLOR_MASK, PY_FLAG_GC_BLACK
                );
                for (int i = 0; i < 40; i++) {
                    py_header_flags_update(
                        py_header(children[i]),
                        PY_FLAG_GC_COLOR_MASK,
                        PY_FLAG_GC_BLACK
                    );
                }
                pcc_gc_telemetry_reset();

                for (int i = 0; i < 31; i++) {
                    pcc_gc_store_root(&roots[i], children[i]);
                    pcc_gc_release(children[i]);
                    children[i] = 0;
                }
                int64_t pushes_before = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_QUEUE_PUSHES
                );
                int64_t flushes_before = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_WB_FLUSHES
                );
                if (pushes_before != 0 || flushes_before != 0) return 10;
                if (
                    (py_header_flags_load(py_header(root_a))
                        & PY_FLAG_GC_GRAY) == 0
                ) return 22;
                for (int i = 0; i < 40; i++) {
                    if (root_a == expected[i]) return 23;
                }

                /* The 32nd root barrier fills the TLS WB batch while a real
                 * caller-owned graph-lock scope remains active.  An explicit
                 * nested unlock must not publish it. */
                pcc_gc_root_slot_lock();
                pcc_gc_root_slot_lock();
                pcc_gc_store_root(&roots[31], children[31]);
                pcc_gc_root_slot_unlock();
                int64_t pushes_after_nested = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_QUEUE_PUSHES
                );
                int64_t flushes_after_nested = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_WB_FLUSHES
                );
                for (int i = 32; i < 40; i++) {
                    pcc_gc_store_root(&roots[i], children[i]);
                }
                int64_t pushes_inside = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_QUEUE_PUSHES
                );
                int64_t flushes_inside = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_WB_FLUSHES
                );
                int identities_gray = 1;
                for (int i = 0; i < 40; i++) {
                    if (
                        roots[i] != expected[i]
                        || (
                            py_header_flags_load(py_header(expected[i]))
                            & PY_FLAG_GC_GRAY
                        ) == 0
                    ) {
                        identities_gray = 0;
                        break;
                    }
                }
                pcc_gc_root_slot_unlock();

                if (
                    pushes_after_nested != pushes_before
                    || flushes_after_nested != flushes_before
                ) return 11;
                if (
                    pushes_inside != pushes_before
                    || flushes_inside != flushes_before
                    || !identities_gray
                ) return 12;
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES)
                        != pushes_before + 33
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES)
                        != flushes_before + 1
                ) {
                    fprintf(
                        stderr,
                        "post-unlock pushes=%lld flushes=%lld\\n",
                        (long long)pcc_gc_telemetry(
                            PCC_GC_COUNTER_CMS_QUEUE_PUSHES
                        ),
                        (long long)pcc_gc_telemetry(
                            PCC_GC_COUNTER_CMS_WB_FLUSHES
                        )
                    );
                    return 13;
                }
                for (int i = 31; i < 40; i++) {
                    pcc_gc_release(children[i]);
                    children[i] = 0;
                }

                /* The worker receives 32 identities plus one coalesced rescan
                 * token.  That rescan must trace eight independent overflow
                 * identities and the one deliberately retained gray root. */
                if (pcc_resume_world() != 0) return 14;
                if (!wait_for_exact_worker_traces(41)) return 15;
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WORKER_DRAINS) != 33
                ) return 16;
                if (pcc_stop_the_world() != 0) return 17;
                int identities_black = 1;
                for (int i = 0; i < 40; i++) {
                    if (
                        roots[i] != expected[i]
                        || (
                            py_header_flags_load(py_header(expected[i]))
                            & PY_FLAG_GC_BLACK
                        ) == 0
                    ) {
                        identities_black = 0;
                        break;
                    }
                }
                if (!identities_black) return 18;
                if (
                    (py_header_flags_load(py_header(root_a))
                        & PY_FLAG_GC_BLACK) == 0
                ) return 21;
                if (pcc_resume_world() != 0) return 19;
                if (pcc_gc_set_backend(PCC_GC_KIND_REFCOUNT_CYCLE) != 0) {
                    return 20;
                }
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, (
        "CMS WB full-buffer publication crossed the outer graph lock: "
        f"{result.returncode}: " + result.stdout + result.stderr
    )


def test_concurrent_backend_invalidates_partial_wb_on_switch_and_restart(
    tmp_path,
):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_wb_epoch_restart_probe.c"
    exe = tmp_path / "cms_wb_epoch_restart_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>

            static void force_one_gray_cycle(
                PyObject *gray_anchor,
                PyObject *black_anchor,
                PyObject **values,
                int count
            ) {
                (void)pcc_gc_step(1);
                py_header_flags_update(
                    py_header(gray_anchor),
                    PY_FLAG_GC_COLOR_MASK,
                    PY_FLAG_GC_GRAY
                );
                py_header_flags_update(
                    py_header(black_anchor),
                    PY_FLAG_GC_COLOR_MASK,
                    PY_FLAG_GC_BLACK
                );
                for (int i = 0; i < count; i++) {
                    py_header_flags_update(
                        py_header(values[i]),
                        PY_FLAG_GC_COLOR_MASK,
                        PY_FLAG_GC_BLACK
                    );
                }
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 3;
                if (pcc_stop_the_world() != 0) return 4;

                PyObject *old_anchor_a = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *old_anchor_b = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *old_values[31] = {0};
                PyObject *old_roots[31] = {0};
                if (old_anchor_a == 0 || old_anchor_b == 0) return 5;
                pcc_gc_pin(old_anchor_a);
                pcc_gc_pin(old_anchor_b);
                for (int i = 0; i < 31; i++) {
                    old_values[i] = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                    if (old_values[i] == 0) return 6;
                    pcc_gc_pin(old_values[i]);
                }

                /* Clear allocation tickets before constructing the partial
                 * barrier batch whose stale identity is under test. */
                if (pcc_resume_world() != 0) return 7;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 8;
                if (pcc_stop_the_world() != 0) return 9;
                for (int i = 0; i < 31; i++) pcc_gc_unpin(old_values[i]);
                force_one_gray_cycle(
                    old_anchor_a, old_anchor_b, old_values, 31
                );
                pcc_gc_telemetry_reset();
                for (int i = 0; i < 31; i++) {
                    pcc_gc_store_root(&old_roots[i], old_values[i]);
                    pcc_gc_release(old_values[i]);
                    old_values[i] = 0;
                }
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 0
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 0
                ) return 10;

                /* Switching away stops and invalidates CMS before graph reset.
                 * Clearing these sole roots makes stale TLS addresses unsafe
                 * to reuse after the switch back. */
                if (pcc_resume_world() != 0) return 11;
                if (pcc_gc_set_backend(PCC_GC_KIND_REFCOUNT_CYCLE) != 0) {
                    return 12;
                }
                for (int i = 0; i < 31; i++) {
                    pcc_gc_store_root(&old_roots[i], 0);
                }
                pcc_gc_unpin(old_anchor_a);
                pcc_gc_unpin(old_anchor_b);
                pcc_gc_release(old_anchor_a);
                pcc_gc_release(old_anchor_b);

                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 13;
                if (pcc_stop_the_world() != 0) return 14;
                PyObject *new_anchor_a = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *new_anchor_b = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *new_values[32] = {0};
                PyObject *new_roots[32] = {0};
                if (new_anchor_a == 0 || new_anchor_b == 0) return 15;
                pcc_gc_pin(new_anchor_a);
                pcc_gc_pin(new_anchor_b);
                for (int i = 0; i < 32; i++) {
                    new_values[i] = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                    if (new_values[i] == 0) return 16;
                    pcc_gc_pin(new_values[i]);
                }
                for (int i = 0; i < 32; i++) pcc_gc_unpin(new_values[i]);
                force_one_gray_cycle(
                    new_anchor_a, new_anchor_b, new_values, 32
                );
                pcc_gc_telemetry_reset();

                /* If the stale count=31 survived the epoch transition, this
                 * single fresh identity would immediately publish 32 tickets. */
                pcc_gc_store_root(&new_roots[0], new_values[0]);
                pcc_gc_release(new_values[0]);
                new_values[0] = 0;
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 0
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 0
                ) return 17;
                for (int i = 1; i < 32; i++) {
                    pcc_gc_store_root(&new_roots[i], new_values[i]);
                    pcc_gc_release(new_values[i]);
                    new_values[i] = 0;
                }
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 32
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 1
                ) return 18;

                int64_t starts_before = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_WORKER_STARTS
                );
                int64_t stops_before = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_WORKER_STOPS
                );
                if (pcc_resume_world() != 0) return 19;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 20;
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WORKER_STARTS)
                        != starts_before + 1
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WORKER_STOPS)
                        != stops_before + 1
                ) return 21;
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, (
        "CMS stale TLS WB state survived switch/restart: "
        f"{result.returncode}: " + result.stdout + result.stderr
    )


def test_concurrent_backend_reset_fails_closed_for_stw_owner_and_no_park(
    tmp_path,
):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_reset_guard_probe.c"
    exe = tmp_path / "cms_reset_guard_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>

            static int wait_for_exact_worker_traces(int64_t expected) {
                for (int i = 0; i < 100000; i++) {
                    int64_t traces = pcc_gc_telemetry(
                        PCC_GC_COUNTER_CMS_WORKER_TRACES
                    );
                    if (traces == expected) return 1;
                    if (traces > expected) return 0;
                    pcc_gc_safepoint();
                }
                return 0;
            }

            static int state_is_unchanged(
                int64_t starts,
                int64_t stops,
                int64_t pushes,
                int64_t flushes
            ) {
                return
                    pcc_gc_backend() == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    && pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WORKER_STARTS)
                        == starts
                    && pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WORKER_STOPS)
                        == stops
                    && pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES)
                        == pushes
                    && pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES)
                        == flushes;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 3;
                if (pcc_stop_the_world() != 0) return 4;

                PyObject *anchor_a = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *anchor_b = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *values[32] = {0};
                PyObject *roots[32] = {0};
                if (anchor_a == 0 || anchor_b == 0) return 5;
                pcc_gc_pin(anchor_a);
                pcc_gc_pin(anchor_b);
                for (int i = 0; i < 32; i++) {
                    values[i] = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                    if (values[i] == 0) return 6;
                    pcc_gc_pin(values[i]);
                }

                if (pcc_resume_world() != 0) return 7;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 8;
                if (pcc_stop_the_world() != 0) return 9;
                for (int i = 0; i < 32; i++) pcc_gc_unpin(values[i]);
                (void)pcc_gc_step(1);
                py_header_flags_update(
                    py_header(anchor_a),
                    PY_FLAG_GC_COLOR_MASK,
                    PY_FLAG_GC_GRAY
                );
                py_header_flags_update(
                    py_header(anchor_b),
                    PY_FLAG_GC_COLOR_MASK,
                    PY_FLAG_GC_BLACK
                );
                for (int i = 0; i < 32; i++) {
                    py_header_flags_update(
                        py_header(values[i]),
                        PY_FLAG_GC_COLOR_MASK,
                        PY_FLAG_GC_BLACK
                    );
                }
                pcc_gc_telemetry_reset();
                if (pcc_resume_world() != 0) return 10;

                for (int i = 0; i < 31; i++) {
                    pcc_gc_store_root(&roots[i], values[i]);
                    pcc_gc_release(values[i]);
                    values[i] = 0;
                }
                int64_t starts = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_WORKER_STARTS
                );
                int64_t stops = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_WORKER_STOPS
                );
                int64_t pushes = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_QUEUE_PUSHES
                );
                int64_t flushes = pcc_gc_telemetry(
                    PCC_GC_COUNTER_CMS_WB_FLUSHES
                );
                if (pushes != 0 || flushes != 0) return 11;

                pcc_gc_root_slot_lock();
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != -1) return 22;
                if (!state_is_unchanged(starts, stops, pushes, flushes)) {
                    return 23;
                }
                pcc_gc_root_slot_unlock();
                if (!state_is_unchanged(starts, stops, pushes, flushes)) {
                    return 24;
                }

                pcc_thread_no_park_enter();
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != -1) return 12;
                if (
                    pcc_thread_no_park_depth() != 1
                    || !state_is_unchanged(starts, stops, pushes, flushes)
                ) return 13;
                pcc_thread_no_park_exit();
                if (pcc_thread_no_park_depth() != 0) return 14;

                if (pcc_stop_the_world() != 0) return 15;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != -1) return 16;
                if (
                    pcc_thread_owns_stopped_world() != 1
                    || !state_is_unchanged(starts, stops, pushes, flushes)
                ) return 17;

                pcc_gc_store_root(&roots[31], values[31]);
                pcc_gc_release(values[31]);
                values[31] = 0;
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 32
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 1
                ) return 18;
                if (pcc_resume_world() != 0) return 19;
                if (!wait_for_exact_worker_traces(32)) return 20;
                if (pcc_gc_set_backend(PCC_GC_KIND_REFCOUNT_CYCLE) != 0) {
                    return 21;
                }
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )
    assert result.returncode == 0, (
        "CMS reset did not fail closed for STW/no-park caller: "
        f"{result.returncode}: " + result.stdout + result.stderr
    )


def test_concurrent_backend_nonthread_full_wb_batch_still_drains(tmp_path):
    work_runtime = _build_nonthread_runtime(tmp_path)

    src = tmp_path / "cms_wb_nonthread_drain_probe.c"
    exe = tmp_path / "cms_wb_nonthread_drain_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdint.h>

            int main(void) {
                if (pcc_threads_enabled() != 0) return 2;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 3;

                PyObject *root_a = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *root_b = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                if (root_a == 0 || root_b == 0) return 4;
                pcc_gc_pin(root_a);
                pcc_gc_pin(root_b);
                (void)pcc_gc_step(1);

                PyObject *children[32] = {0};
                PyObject *roots[32] = {0};
                for (int i = 0; i < 32; i++) {
                    children[i] = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                    if (children[i] == 0) return 5;
                }
                pcc_gc_telemetry_reset();
                for (int i = 0; i < 32; i++) {
                    pcc_gc_store_root(&roots[i], children[i]);
                    pcc_gc_release(children[i]);
                    children[i] = 0;
                }
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 32
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 1
                ) return 6;
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, (
        "nonthread CMS full WB batch did not drain: "
        f"{result.returncode}: " + result.stdout + result.stderr
    )


def test_concurrent_backend_unregister_clears_partial_wb_tls(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_wb_unregister_probe.c"
    exe = tmp_path / "cms_wb_unregister_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>

            typedef struct {
                PyObject **old_values;
                PyObject **old_roots;
                PyObject **fresh_values;
                PyObject **fresh_roots;
            } ProbeCtx;

            static void *run_probe(void *raw) {
                ProbeCtx *ctx = (ProbeCtx *)raw;
                int64_t first_thread_id = pcc_current_thread_id();
                if (first_thread_id <= 0) return (void *)(intptr_t)1;
                for (int i = 0; i < 31; i++) {
                    pcc_gc_store_root(
                        &ctx->old_roots[i], ctx->old_values[i]
                    );
                }
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 0
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 0
                ) return (void *)(intptr_t)2;

                /* The legal teardown contract is graph depth zero.  With
                 * capacity available, unregister first delivers the partial
                 * prefix, then clears every TLS ownership field. */
                pcc_thread_unregister_current();
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 31
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 1
                ) return (void *)(intptr_t)3;

                /* Explicitly re-register this exact pthread before any more
                 * runtime work; the new identity must start with clean TLS. */
                int64_t second_thread_id = pcc_current_thread_id();
                if (
                    second_thread_id <= 0
                    || second_thread_id == first_thread_id
                ) return (void *)(intptr_t)4;
                pcc_gc_store_root(
                    &ctx->fresh_roots[0], ctx->fresh_values[0]
                );
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 31
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 1
                ) return (void *)(intptr_t)5;
                for (int i = 1; i < 32; i++) {
                    pcc_gc_store_root(
                        &ctx->fresh_roots[i], ctx->fresh_values[i]
                    );
                }
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 63
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 2
                ) return (void *)(intptr_t)6;
                return 0;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 3;
                if (pcc_stop_the_world() != 0) return 4;

                PyObject *anchor_a = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *anchor_b = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                PyObject *old_values[31] = {0};
                PyObject *old_roots[31] = {0};
                PyObject *fresh_values[32] = {0};
                PyObject *fresh_roots[32] = {0};
                if (anchor_a == 0 || anchor_b == 0) return 5;
                pcc_gc_pin(anchor_a);
                pcc_gc_pin(anchor_b);
                for (int i = 0; i < 31; i++) {
                    old_values[i] = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                    if (old_values[i] == 0) return 6;
                    pcc_gc_pin(old_values[i]);
                }
                for (int i = 0; i < 32; i++) {
                    fresh_values[i] = pcc_gc_alloc(24, PY_TYPE_NONE, 0);
                    if (fresh_values[i] == 0) return 7;
                    pcc_gc_pin(fresh_values[i]);
                }

                if (pcc_resume_world() != 0) return 8;
                if (pcc_gc_set_backend(
                        PCC_GC_KIND_CONCURRENT_MARK_SWEEP
                    ) != 0) return 9;
                if (pcc_stop_the_world() != 0) return 10;
                for (int i = 0; i < 31; i++) pcc_gc_unpin(old_values[i]);
                for (int i = 0; i < 32; i++) pcc_gc_unpin(fresh_values[i]);
                (void)pcc_gc_step(1);
                py_header_flags_update(
                    py_header(anchor_a),
                    PY_FLAG_GC_COLOR_MASK,
                    PY_FLAG_GC_GRAY
                );
                py_header_flags_update(
                    py_header(anchor_b),
                    PY_FLAG_GC_COLOR_MASK,
                    PY_FLAG_GC_BLACK
                );
                for (int i = 0; i < 31; i++) {
                    py_header_flags_update(
                        py_header(old_values[i]),
                        PY_FLAG_GC_COLOR_MASK,
                        PY_FLAG_GC_BLACK
                    );
                }
                for (int i = 0; i < 32; i++) {
                    py_header_flags_update(
                        py_header(fresh_values[i]),
                        PY_FLAG_GC_COLOR_MASK,
                        PY_FLAG_GC_BLACK
                    );
                }
                pcc_gc_telemetry_reset();
                if (pcc_resume_world() != 0) return 11;

                ProbeCtx ctx = {
                    old_values,
                    old_roots,
                    fresh_values,
                    fresh_roots
                };
                PccThreadHandle *thread = 0;
                if (pcc_thread_start(&thread, run_probe, &ctx) != 0) return 12;
                void *thread_result = 0;
                if (pcc_thread_join(thread, &thread_result) != 0) return 13;
                if ((intptr_t)thread_result != 0) {
                    return 20 + (int)(intptr_t)thread_result;
                }
                if (
                    pcc_gc_telemetry(PCC_GC_COUNTER_CMS_QUEUE_PUSHES) != 63
                    || pcc_gc_telemetry(PCC_GC_COUNTER_CMS_WB_FLUSHES) != 2
                ) return 30;
                if (pcc_gc_set_backend(PCC_GC_KIND_REFCOUNT_CYCLE) != 0) {
                    return 31;
                }
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, (
        "CMS unregister left stale TLS WB state: "
        f"{result.returncode}: " + result.stdout + result.stderr
    )


def test_concurrent_backend_worker_traces_positive_allocation_work(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_positive_work_probe.c"
    exe = tmp_path / "cms_positive_work_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <unistd.h>

            enum {
                CMS_WORKER_DRAINS = 13,
                CMS_WORKER_TRACES = 18
            };

            static int wait_for_worker_trace(void) {
                for (int i = 0; i < 200; i++) {
                    if (pcc_gc_telemetry(CMS_WORKER_TRACES) > 0) return 1;
                    pcc_thread_safepoint();
                    usleep(1000);
                }
                return 0;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(PCC_GC_KIND_CONCURRENT_MARK_SWEEP) != 0) {
                    return 3;
                }
                pcc_gc_telemetry_reset();

                PyObject *root = pcc_gc_alloc(24, PY_TYPE_INT, 0);
                if (root == 0) return 4;
                pcc_gc_pin(root);

                PyObject *ticket = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (ticket == 0) return 5;
                pcc_gc_release(ticket);

                printf("%d\\n", wait_for_worker_trace());
                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_WORKER_DRAINS));
                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_WORKER_TRACES));

                pcc_gc_unpin(root);
                pcc_gc_release(root);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "1"
    assert int(lines[1]) > 0
    assert int(lines[2]) > 0


def test_concurrent_backend_worker_reaches_mark_termination_without_mutator_gc_step(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_mark_termination_probe.c"
    exe = tmp_path / "cms_mark_termination_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <unistd.h>

            enum {
                CMS_WORKER_DRAINS = 13
            };

            static int wait_for_worker_sweep_debt(void) {
                for (int i = 0; i < 200; i++) {
                    if (pcc_gc_has_tracing_sweep() != 0) return 1;
                    pcc_thread_safepoint();
                    usleep(1000);
                }
                return 0;
            }

            static int wait_for_worker_drain(void) {
                for (int i = 0; i < 200; i++) {
                    if (pcc_gc_telemetry(CMS_WORKER_DRAINS) > 0) return 1;
                    pcc_thread_safepoint();
                    usleep(1000);
                }
                return 0;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(PCC_GC_KIND_INCREMENTAL_TRICOLOR) != 0) {
                    return 3;
                }

                PyObject *cycle = py_list_new(1);
                if (cycle == 0) return 4;
                py_list_append(cycle, cycle);
                pcc_gc_release(cycle);

                pcc_gc_telemetry_reset();
                if (pcc_gc_set_backend(PCC_GC_KIND_CONCURRENT_MARK_SWEEP) != 0) {
                    return 5;
                }

            PyObject *ticket = pcc_gc_alloc(64, PY_TYPE_INT, 0);
            if (ticket == 0) return 6;
            pcc_gc_release(ticket);
            if (!wait_for_worker_drain()) return 7;

            PyObject *ticket2 = pcc_gc_alloc(64, PY_TYPE_INT, 0);
            if (ticket2 == 0) return 8;
            pcc_gc_release(ticket2);

            printf("%d\\n", wait_for_worker_sweep_debt());
            printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_WORKER_DRAINS));
            printf("%lld\\n", (long long)pcc_gc_collect_tracing());
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "1"
    assert int(lines[1]) > 0
    assert int(lines[2]) > 0


def test_concurrent_backend_worker_stops_and_restarts_on_backend_switch(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)

    src = tmp_path / "cms_lifecycle_probe.c"
    exe = tmp_path / "cms_lifecycle_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <unistd.h>

            enum {
                CMS_WORKER_STARTS = 11,
                CMS_WORKER_DRAINS = 13,
                CMS_WORKER_STOPS = 22
            };

            static int wait_for_drain(void) {
                for (int i = 0; i < 500; i++) {
                    if (pcc_gc_telemetry(CMS_WORKER_DRAINS) > 0) return 1;
                    pcc_thread_safepoint();
                    usleep(1000);
                }
                return 0;
            }

            static int drive_work(void) {
                PyObject *root = pcc_gc_alloc(24, PY_TYPE_INT, 0);
                if (root == 0) return 0;
                pcc_gc_pin(root);
                PyObject *ticket = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (ticket == 0) return 0;
                pcc_gc_release(ticket);
                int drained = wait_for_drain();
                pcc_gc_unpin(root);
                pcc_gc_release(root);
                return drained;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                pcc_gc_telemetry_reset();

                if (pcc_gc_set_backend(PCC_GC_KIND_CONCURRENT_MARK_SWEEP) != 0) {
                    return 3;
                }
                if (!drive_work()) return 4;

                if (pcc_gc_set_backend(PCC_GC_KIND_REFCOUNT_CYCLE) != 0) {
                    return 5;
                }
                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_WORKER_STOPS));

                if (pcc_gc_set_backend(PCC_GC_KIND_CONCURRENT_MARK_SWEEP) != 0) {
                    return 6;
                }
                if (!drive_work()) return 7;
                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_WORKER_STARTS));

                if (pcc_gc_set_backend(PCC_GC_KIND_REFCOUNT_CYCLE) != 0) {
                    return 8;
                }
                printf("%lld\\n", (long long)pcc_gc_telemetry(CMS_WORKER_STOPS));
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env["PCC_GC_DEBT_THRESHOLD"] = "1048576"
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert int(lines[0]) >= 1
    assert int(lines[1]) >= 2
    assert int(lines[2]) >= 2
