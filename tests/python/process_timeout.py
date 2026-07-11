from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path


def _kill_process_group(proc: subprocess.Popen[str], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _finish_after_process_group_kill(
    proc: subprocess.Popen[str],
    *,
    cmd: Sequence[str],
    timeout: float,
    reason: str,
) -> subprocess.CompletedProcess[str]:
    _kill_process_group(proc, signal.SIGTERM)
    try:
        stdout, stderr = proc.communicate(timeout=2.0)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc, signal.SIGKILL)
        stdout, stderr = proc.communicate()
    stderr = (stderr or "") + (
        f"\n[{reason}] killed process group for {' '.join(cmd)} "
        f"after {timeout:.1f}s\n"
    )
    return subprocess.CompletedProcess(
        list(cmd), 124, stdout=stdout or "", stderr=stderr
    )


def run_process_group_timeout(
    cmd: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if os.name != "posix":
        return subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            # Decode child output as UTF-8 explicitly: pcc/clang emit UTF-8
            # diagnostics (em-dashes etc.). Relying on the parent locale makes
            # this crash with UnicodeDecodeError under LC_ALL=C. errors=replace
            # keeps a stray non-UTF-8 byte from masking the real exit status.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=dict(env) if env is not None else None,
            cwd=str(cwd) if cwd is not None else None,
        )

    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # See note above: UTF-8 child output must not be decoded with the
        # parent's (possibly ascii, under LC_ALL=C) locale.
        encoding="utf-8",
        errors="replace",
        env=dict(env) if env is not None else None,
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=True,
    )
    old_handlers: dict[int, object] = {}

    def _interrupt_handler(signum: int, _frame) -> None:
        _kill_process_group(proc, signal.SIGTERM)
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + signum)

    if threading_is_main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, _interrupt_handler)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(
            list(cmd), proc.returncode, stdout=stdout, stderr=stderr
        )
    except subprocess.TimeoutExpired:
        return _finish_after_process_group_kill(
            proc,
            cmd=cmd,
            timeout=timeout,
            reason="TIMEOUT",
        )
    except BaseException:
        _finish_after_process_group_kill(
            proc,
            cmd=cmd,
            timeout=timeout,
            reason="INTERRUPT",
        )
        raise
    finally:
        for signum, old_handler in old_handlers.items():
            signal.signal(signum, old_handler)


def threading_is_main_thread() -> bool:
    try:
        import threading
    except ImportError:  # pragma: no cover
        return False
    return threading.current_thread() is threading.main_thread()
