"""Production vthread IO uses kqueue or the explicit live-poll fallback."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).absolute().parents[3]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"


_SOURCE = r"""
#define _POSIX_C_SOURCE 200809L
#include "py_runtime.h"
#include "py_internal.h"
#include "py_io_waitset.h"

#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>

static PyObject *new_vthread(void) {
    return py_virtual_thread_new(py_None);
}

static int finish_one_ready(void) {
    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == NULL) return 0;
    if (((PyVirtualThreadObject *)ready)->io_entry != NULL) return 0;
    if (py_virtual_thread_complete(ready, py_None) != 0) return 0;
    pcc_gc_release(ready);
    return 1;
}

static int check_pipe_ready(void) {
    int fds[2];
    if (pipe(fds) != 0) return 0;
    PyObject *vt = new_vthread();
    if (vt == NULL) return 0;
    if (py_virtual_thread_block_on_fd(vt, fds[0], POLLIN, -1) != 0) return 0;
    if (((PyVirtualThreadObject *)vt)->io_entry == NULL) return 0;
    pcc_gc_release(vt);
    (void)pcc_gc_collect(0);
    if (pcc_gc_scheduler_root_count() != 1) return 0;
    if (write(fds[1], "p", 1) != 1) return 0;
    if (py_virtual_thread_poll_io(0) != 1) return 0;
    if (py_virtual_thread_io_wait_count() != 0) return 0;
    if (pcc_gc_scheduler_root_count() != 1) return 0;
    if (!finish_one_ready()) return 0;
    close(fds[0]);
    close(fds[1]);
    return pcc_gc_scheduler_root_count() == 0;
}

static int check_socket_ready(void) {
    int fds[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, fds) != 0) return 0;
    PyObject *vt = new_vthread();
    if (vt == NULL) return 0;
    if (py_virtual_thread_block_on_fd(vt, fds[0], POLLIN, -1) != 0) return 0;
    pcc_gc_release(vt);
    (void)pcc_gc_collect(0);
    if (write(fds[1], "s", 1) != 1) return 0;
    if (py_virtual_thread_poll_io(0) != 1) return 0;
    if (!finish_one_ready()) return 0;
    close(fds[0]);
    close(fds[1]);
    return pcc_gc_scheduler_root_count() == 0;
}

static int check_timeout(void) {
    int fds[2];
    if (pipe(fds) != 0) return 0;
    PyObject *vt = new_vthread();
    if (vt == NULL) return 0;
    if (py_virtual_thread_block_on_fd(vt, fds[0], POLLIN, 3) != 0) return 0;
    pcc_gc_release(vt);
    (void)pcc_gc_collect(0);
    (void)poll(NULL, 0, 8);
    if (py_virtual_thread_poll_io(0) != 1) return 0;
    if (!finish_one_ready()) return 0;
    close(fds[0]);
    close(fds[1]);
    return pcc_gc_scheduler_root_count() == 0;
}

static int check_same_fd_waiters(void) {
    int fds[2];
    if (pipe(fds) != 0) return 0;
    PyObject *first = new_vthread();
    PyObject *second = new_vthread();
    if (first == NULL || second == NULL) return 0;
    if (py_virtual_thread_block_on_fd(first, fds[0], POLLIN, -1) != 0) return 0;
    if (py_virtual_thread_block_on_fd(second, fds[0], POLLIN, -1) != 0) return 0;
    pcc_gc_release(first);
    pcc_gc_release(second);
    (void)pcc_gc_collect(0);
    if (py_virtual_thread_io_wait_count() != 2) return 0;
    if (pcc_gc_scheduler_root_count() != 2) return 0;
    if (write(fds[1], "m", 1) != 1) return 0;
    if (py_virtual_thread_poll_io(0) != 2) return 0;
    if (py_virtual_thread_ready_count() != 2) return 0;
    if (!finish_one_ready() || !finish_one_ready()) return 0;
    if (py_virtual_thread_poll_ready() != NULL) return 0;
    close(fds[0]);
    close(fds[1]);
    return py_virtual_thread_io_wait_count() == 0
        && pcc_gc_scheduler_root_count() == 0;
}

static int check_complete_cancels_wait(void) {
    int fds[2];
    if (pipe(fds) != 0) return 0;
    PyObject *vt = new_vthread();
    if (vt == NULL) return 0;
    if (py_virtual_thread_block_on_fd(vt, fds[0], POLLIN, -1) != 0) return 0;
    if (pcc_gc_scheduler_root_count() != 1) return 0;
    if (py_virtual_thread_complete(vt, py_None) != 0) return 0;
    if (((PyVirtualThreadObject *)vt)->io_entry != NULL) return 0;
    if (py_virtual_thread_io_wait_count() != 0) return 0;
    if (pcc_gc_scheduler_root_count() != 0) return 0;
    pcc_gc_release(vt);
    if (write(fds[1], "x", 1) != 1) return 0;
    if (py_virtual_thread_poll_io(0) != 0) return 0;
    close(fds[0]);
    close(fds[1]);
    return py_virtual_thread_poll_ready() == NULL;
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    int64_t backend = (int64_t)atoll(argv[1]);
    const char *mode = argv[2];
    if (setenv("PCC_VTHREAD_IO_BACKEND", mode, 1) != 0) return 3;
    if (pcc_gc_set_backend(backend) != 0) return 4;
    int64_t expected = mode[0] == 'p'
        ? PCC_VTHREAD_IO_BACKEND_POLL
        : (pcc_io_waitset_kqueue_available()
            ? PCC_VTHREAD_IO_BACKEND_KQUEUE
            : PCC_VTHREAD_IO_BACKEND_POLL);
    int ok = py_virtual_thread_io_backend() == expected
        && check_pipe_ready()
        && check_socket_ready()
        && check_timeout()
        && check_same_fd_waiters()
        && check_complete_cancels_wait()
        && py_virtual_thread_io_backend() == expected
        && py_virtual_thread_ready_count() == 0
        && py_virtual_thread_io_wait_count() == 0
        && pcc_gc_scheduler_root_count() == 0
        && py_virtual_thread_node_pool_stat(
            PCC_VTHREAD_NODE_IO, PCC_VTHREAD_POOL_REUSES
        ) > 0;
    printf("%lld:%s:%lld:%d\n", (long long)backend, mode,
        (long long)py_virtual_thread_io_backend(), ok);
    return ok ? 0 : (int)(10 + backend);
}
"""


@pytest.fixture(scope="module")
def _io_waitset_runtime_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_vthread_io_waitset_runtime")
    work_runtime = tmp / "py_runtime"
    shutil.copytree(
        RUNTIME_DIR,
        work_runtime,
        ignore=shutil.ignore_patterns(
            "_native", "__pycache__", "build", "build_*", "*.a", "*.a.target"
        ),
    )
    make = subprocess.run(
        ["make", "-B", "-C", str(work_runtime), "libpy_runtime.a"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert make.returncode == 0, make.stdout + make.stderr
    src = tmp / "vthread_io_waitset_runtime.c"
    src.write_text(textwrap.dedent(_SOURCE).lstrip(), encoding="utf-8")
    exe = tmp / "vthread_io_waitset_runtime_bin"
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
    return str(exe)


@pytest.mark.parametrize("backend", ["0", "1", "2", "3", "4"])
@pytest.mark.parametrize("mode", ["auto", "poll"])
def test_production_io_waitset_modes_preserve_roots(
    _io_waitset_runtime_exe, backend, mode
):
    run = subprocess.run(
        [_io_waitset_runtime_exe, backend, mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, (
        f"GC{backend} mode={mode} rc={run.returncode}: "
        f"{run.stdout.strip()} {run.stderr.strip()[:400]}"
    )
    fields = run.stdout.strip().split(":")
    assert fields[:2] == [backend, mode]
    assert fields[-1] == "1"


def test_scheduler_owns_waitset_instead_of_per_entry_poll() -> None:
    source = (RUNTIME_DIR / "src" / "pcc_threads.c").read_text(
        encoding="utf-8"
    )
    header = (RUNTIME_DIR / "include" / "py_runtime.h").read_text(
        encoding="utf-8"
    )
    poll_io = source.split("int64_t py_virtual_thread_poll_io", 1)[1].split(
        "int64_t py_virtual_thread_io_wait_count", 1
    )[0]
    assert "pcc_io_waitset_add" in source
    assert "pcc_io_waitset_wait" in poll_io
    assert "pcc_vthread_fd_ready(entry->fd" not in poll_io
    assert "py_virtual_thread_io_backend" in source
    assert "py_virtual_thread_io_backend" in header
