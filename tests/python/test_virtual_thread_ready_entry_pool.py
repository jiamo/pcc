from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

from tests.runtime_build_cache import cached_c_runtime


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"
THREADS_C = RUNTIME_DIR / "src" / "pcc_threads.c"


def _slice_between(src: str, start: str, end: str) -> str:
    start_idx = src.index(start)
    end_idx = src.index(end, start_idx)
    return src[start_idx:end_idx]


def test_virtual_thread_ready_entry_pool_source_shape() -> None:
    src = THREADS_C.read_text(encoding="utf-8")

    assert "#define PCC_VTHREAD_READY_ENTRY_POOL_LIMIT 4096" in src
    assert "pcc_vthread_ready_entry_free_head" in src
    assert "pcc_vthread_ready_entry_free_count" in src
    assert "pcc_vthread_ready_entry_alloc_locked" in src
    assert "pcc_vthread_ready_entry_recycle_locked" in src
    assert "calloc(1, sizeof(PccVirtualThreadQueueEntry))" not in src
    assert "#define PCC_VTHREAD_TIMER_ENTRY_POOL_LIMIT 4096" in src
    assert "#define PCC_VTHREAD_POLL_ENTRY_POOL_LIMIT 4096" in src
    assert "pcc_vthread_timer_entry_alloc_locked" in src
    assert "pcc_vthread_poll_entry_alloc_locked" in src
    assert "calloc(1, sizeof(PccVirtualThreadTimerEntry))" not in src
    assert "calloc(1, sizeof(PccVirtualThreadPollEntry))" not in src

    enqueue = _slice_between(
        src,
        "static int pcc_vthread_enqueue_locked",
        "static int pcc_vthread_make_ready_locked",
    )
    assert "pcc_vthread_ready_entry_alloc_locked(1)" in enqueue

    recycle = _slice_between(
        src,
        "static void pcc_vthread_ready_entry_recycle_locked",
        "static void pcc_vthread_ready_entry_release_locked",
    )
    assert "PCC_VTHREAD_READY_ENTRY_POOL_LIMIT" in recycle
    assert "free(entry);" in recycle

    release = _slice_between(
        src,
        "static void pcc_vthread_ready_entry_release_locked",
        "static void pcc_vthread_queue_push_entry_locked",
    )
    unregister_idx = release.index(
        "pcc_gc_scheduler_root_unregister_handle(entry->root_handle);"
    )
    clear_idx = release.index("pcc_gc_store_root(&entry->thread, NULL);")
    recycle_idx = release.index("pcc_vthread_ready_entry_recycle_locked(entry);")
    assert unregister_idx < clear_idx < recycle_idx

    dequeue = _slice_between(
        src,
        "static PyObject *pcc_vthread_dequeue_from_queue_locked",
        "static PyObject *pcc_vthread_dequeue_locked",
    )
    assert "pcc_vthread_ready_entry_release_locked(entry);" in dequeue

    timer_release = _slice_between(
        src,
        "static void pcc_vthread_timer_entry_release_locked",
        "static int pcc_vthread_timer_heap_ensure_locked",
    )
    assert "pcc_vthread_timer_entry_recycle_locked(entry);" in timer_release
    assert "pcc_vthread_ready_entry_recycle_locked" not in timer_release

    poll_release = _slice_between(
        src,
        "static void pcc_vthread_poll_entry_release_locked",
        "static int pcc_vthread_timer_add_locked",
    )
    assert "pcc_vthread_poll_entry_recycle_locked(entry);" in poll_release
    assert "pcc_vthread_ready_entry_recycle_locked" not in poll_release


def _build_runtime(tmp_path: Path) -> Path:
    del tmp_path
    return cached_c_runtime()


def _compile_and_run(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "vthread_ready_entry_pool_probe.c"
    exe = tmp_path / "vthread_ready_entry_pool_probe.out"
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    build = subprocess.run(
        [
            os.environ.get("CC", "cc"),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-lm",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)


def test_virtual_thread_ready_entry_pool_preserves_roots_across_backends(
    tmp_path: Path,
) -> None:
    proc = _compile_and_run(
        tmp_path,
        """
        #include "py_runtime.h"
        #include "py_internal.h"
        #include <stdio.h>

        static int check_backend(int64_t backend) {
            if (pcc_gc_set_backend(backend) != 0) return 0;
            pcc_gc_telemetry_reset();
            if (pcc_gc_scheduler_root_count() != 0) return 0;

            for (int64_t i = 0; i < 192; i++) {
                PyObject *vt = py_virtual_thread_new(py_None);
                if (vt == 0) return 0;
                if (py_virtual_thread_start(vt) != 0) return 0;
                pcc_gc_release(vt);

                if (pcc_gc_scheduler_root_count() != 1) return 0;
                if (py_virtual_thread_ready_count() != 1) return 0;
                (void)pcc_gc_collect(0);

                PyObject *ready = py_virtual_thread_poll_ready();
                if (ready == 0) return 0;
                if (pcc_gc_scheduler_root_count() != 0) return 0;
                if (py_type_of(ready) != PY_TYPE_VIRTUAL_THREAD) return 0;
                if (py_virtual_thread_state(ready) != 2) return 0;
                if (py_virtual_thread_complete(ready, py_None) != 0) return 0;
                py_decref(ready);

                if (py_virtual_thread_ready_count() != 0) return 0;
                if (py_virtual_thread_timer_count() != 0) return 0;
                if (py_virtual_thread_io_wait_count() != 0) return 0;
                if (pcc_gc_scheduler_root_count() != 0) return 0;
            }
            if (py_virtual_thread_node_pool_stat(
                    PCC_VTHREAD_NODE_READY,
                    PCC_VTHREAD_POOL_ALLOCATIONS
                ) <= 0) return 0;
            if (py_virtual_thread_node_pool_stat(
                    PCC_VTHREAD_NODE_READY,
                    PCC_VTHREAD_POOL_REUSES
                ) <= 0) return 0;
            int64_t cached = py_virtual_thread_node_pool_stat(
                PCC_VTHREAD_NODE_READY,
                PCC_VTHREAD_POOL_CACHED
            );
            if (cached <= 0 || cached > 4096) return 0;
            return 1;
        }

        int main(void) {
            for (int64_t backend = 0; backend <= 4; backend++) {
                int ok = check_backend(backend);
                printf("%lld:%d\\n", (long long)backend, ok);
                if (!ok) return (int)(40 + backend);
            }
            return 0;
        }
        """,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().splitlines() == [
        "0:1",
        "1:1",
        "2:1",
        "3:1",
        "4:1",
    ]
