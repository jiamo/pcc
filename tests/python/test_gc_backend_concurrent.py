from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

from tests.runtime_build_cache import cached_threaded_c_runtime


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"


def _cc() -> str:
    return os.environ.get("CC", "cc")


def _build_threaded_runtime(tmp_path: Path) -> Path:
    del tmp_path
    return cached_threaded_c_runtime()


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
