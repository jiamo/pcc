from __future__ import annotations

import __main__
import multiprocessing
import os
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkerProcessResult:
    timed_out: bool
    exitcode: int | None
    payload: Any = None


def _choose_start_method() -> str:
    main_file = getattr(__main__, "__file__", None)
    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("PYTEST_XDIST_WORKER")
        or main_file
    ):
        return "spawn"
    if "fork" in multiprocessing.get_all_start_methods():
        return "fork"
    return "spawn"


def _terminate_process(proc) -> None:
    proc.terminate()
    proc.join(1)
    if not proc.is_alive():
        return
    kill = getattr(proc, "kill", None)
    if kill is not None:
        kill()
        proc.join(1)


def run_worker_process(
    target: Callable[..., None],
    args: tuple[Any, ...],
    timeout: int | float,
) -> WorkerProcessResult:
    ctx = multiprocessing.get_context(_choose_start_method())
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=target, args=(*args, child_conn))
    try:
        proc.start()
        child_conn.close()
        proc.join(timeout)

        if proc.is_alive():
            _terminate_process(proc)
            return WorkerProcessResult(True, proc.exitcode)

        payload = None
        try:
            if parent_conn.poll(0):
                payload = parent_conn.recv()
        except EOFError:
            payload = None

        return WorkerProcessResult(False, proc.exitcode, payload)
    finally:
        parent_conn.close()
        try:
            child_conn.close()
        except OSError:
            pass
        close_proc = getattr(proc, "close", None)
        if close_proc is not None:
            try:
                close_proc()
            except ValueError:
                pass
