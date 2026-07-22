"""Test-suite path shim for migrated test layout.

Tests were moved from ``tests/test_*.py`` to ``tests/{c,python}/test_*.py``.
Legacy code that computes paths via ``os.path.dirname(__file__)`` or
``Path(__file__).resolve().parents[1]`` would otherwise shift by one directory
level, so we normalize those ``*.py`` paths back under ``tests``.
"""

from __future__ import annotations

import fcntl as _gate_fcntl
import importlib.util
import shutil
import subprocess
import sys as _gate_sys
import tempfile
import textwrap
import time as _gate_time
from pathlib import Path
import os


def _legacy_test_parent(path: Path) -> Path | None:
    if path.suffix != ".py":
        return None
    if path.parent.name == "normal":
        tests_root = path.parent.parent
        if tests_root is not None and tests_root.name == "tests":
            return tests_root
        return None
    if (
        path.parent.parent is not None
        and path.parent.name in {"c", "python"}
        and path.parent.parent.name == "tests"
    ):
        return path.parent.parent
    return None


_orig_dirname = os.path.dirname
_ORIG_RESOLVE = Path.resolve


def _patched_dirname(path):
    try:
        original = _orig_dirname(path)
    except TypeError:
        return ""
    try:
        p = Path(path)
    except TypeError:
        return original
    legacy_parent = _legacy_test_parent(p)
    if legacy_parent is not None:
        return str(legacy_parent)
    return original


def _patched_resolve(self: Path, *args, **kwargs):  # type: ignore[override]
    resolved = _ORIG_RESOLVE(self, *args, **kwargs)
    try:
        legacy_parent = _legacy_test_parent(resolved)
    except TypeError:
        return resolved
    if legacy_parent is not None:
        return legacy_parent / resolved.name
    return resolved


os.path.dirname = _patched_dirname
Path.resolve = _patched_resolve


_SELF_HOST_WARMUP_NODEID = (
    "tests/python/test_self_host_oracle_diff.py::"
    "test_000_self_host_oracle_stage_cache_warmup"
)


# --- pcc_gate: opt-in/hardware gates are deselected, never skipped ---------
#
# Policy: a test either runs and verifies, or it is not part of the run.
# ``skip`` is reserved for genuine mid-run environmental aborts. Tests marked
# ``@pytest.mark.pcc_gate(...)`` are deselected at collection when their gate
# is unmet; when explicitly selected with an unmet gate they must fail, so the
# in-test branches behind these gates use pytest.fail, not pytest.skip.

_TSAN_PROBE_CACHE: dict[str, str | None] = {}


def _tsan_unavailable_reason() -> str | None:
    cc = os.environ.get("CC", "clang")
    if cc not in _TSAN_PROBE_CACHE:
        _TSAN_PROBE_CACHE[cc] = _probe_tsan(cc)
    return _TSAN_PROBE_CACHE[cc]


def _probe_tsan(cc: str) -> str | None:
    if shutil.which(cc) is None:
        return f"compiler {cc!r} not found"
    with tempfile.TemporaryDirectory(prefix="pcc-tsan-probe-") as tmpdir:
        probe = os.path.join(tmpdir, "tsan_probe.c")
        exe = os.path.join(tmpdir, "tsan_probe.out")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write(
                textwrap.dedent(
                    r"""
                    #include <pthread.h>

                    static void *worker(void *arg) {
                        (void)arg;
                        return 0;
                    }

                    int main(void) {
                        pthread_t thread;
                        if (pthread_create(&thread, 0, worker, 0) != 0) return 1;
                        return pthread_join(thread, 0) == 0 ? 0 : 2;
                    }
                    """
                ).lstrip()
            )
        try:
            build = subprocess.run(
                [cc, "-std=c11", "-pthread", "-fsanitize=thread", probe, "-o", exe],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"ThreadSanitizer probe could not run: {exc}"
        if build.returncode != 0:
            return "ThreadSanitizer runtime is not available for this compiler"
        # macOS TSan startup crashes are ASLR-dependent and nondeterministic;
        # one clean run proves nothing. Any crash in 3 runs = unreliable
        # toolchain, and an unreliable TSan cannot certify anything.
        for _ in range(3):
            run = subprocess.run([exe], capture_output=True, text=True, timeout=30)
            if run.returncode != 0:
                return (
                    "ThreadSanitizer runtime crashes before pcc code runs "
                    f"(exit {run.returncode})"
                )
    return None


_METAL_PROBE_RESULT: list[str | None] = []


def _metal_unavailable_reason() -> str | None:
    """Real-Metal gate: device via MTLCreateSystemDefaultDevice + metal CLI."""
    if not _METAL_PROBE_RESULT:
        _METAL_PROBE_RESULT.append(_probe_metal())
    return _METAL_PROBE_RESULT[0]


def _probe_metal() -> str | None:
    if _gate_sys.platform != "darwin":
        return "real-Metal gates require Darwin"
    try:
        import ctypes
        import ctypes.util

        lib_path = ctypes.util.find_library("Metal")
        if lib_path is None:
            return "Metal framework not found"
        lib = ctypes.CDLL(lib_path)
        lib.MTLCreateSystemDefaultDevice.restype = ctypes.c_void_p
        if not lib.MTLCreateSystemDefaultDevice():
            return "MTLCreateSystemDefaultDevice returned nil"
    except OSError as exc:
        return f"Metal framework probe failed: {exc}"
    finder = subprocess.run(
        ["xcrun", "--find", "metal"], capture_output=True, text=True, timeout=30
    )
    if finder.returncode != 0:
        return "metal compiler not found via xcrun"
    return None


_PCC1_PROVISIONED = False


def _provision_pcc1() -> None:
    """Ensure a fresh stage1 pcc1 exists before pcc1-consumer tests run.

    ``pcc_gate(probe="pcc1")`` never deselects: instead of "stale pcc1 ->
    skip", the session rebuilds pcc1 (content-hash cached: ~16s warm, minutes
    after a pcc/ source change). Build failure is printed loudly and the
    consumer tests then fail on their own asserts — never silently skipped.
    Set PCC_NO_AUTO_PCC1=1 to opt out (CI that stages its own binaries).
    """
    global _PCC1_PROVISIONED
    if _PCC1_PROVISIONED or os.environ.get("PCC_NO_AUTO_PCC1", "").strip():
        return
    _PCC1_PROVISIONED = True
    repo = Path(__file__).resolve().parent.parent
    lock_path = os.path.join(tempfile.gettempdir(), "pcc-pytest-pcc1-provision.lock")
    with open(lock_path, "a+") as lockfile:
        _gate_fcntl.flock(lockfile, _gate_fcntl.LOCK_EX)
        try:
            lockfile.seek(0)
            stamp = lockfile.read().strip()
            now = _gate_time.time()
            if stamp:
                try:
                    if now - float(stamp) < 300:
                        return  # another worker provisioned moments ago
                except ValueError:
                    pass
            _gate_sys.stderr.write(
                "[pcc_gate] ensuring fresh stage1 pcc1 "
                "(scripts/bootstrap.sh --stage 1; ~16s cached, minutes cold)\n"
            )
            env = os.environ.copy()
            env.pop("LC_ALL", None)
            proc = subprocess.run(
                ["bash", str(repo / "scripts" / "bootstrap.sh"), "--stage", "1"],
                capture_output=True,
                text=True,
                timeout=900,
                cwd=str(repo),
                env=env,
            )
            if proc.returncode != 0:
                _gate_sys.stderr.write(
                    "[pcc_gate] pcc1 auto-build FAILED; pcc1-consumer tests "
                    "will fail loudly:\n"
                    + proc.stdout[-2000:]
                    + proc.stderr[-2000:]
                    + "\n"
                )
            else:
                lockfile.seek(0)
                lockfile.truncate()
                lockfile.write(str(now))
        finally:
            _gate_fcntl.flock(lockfile, _gate_fcntl.LOCK_UN)


def _pcc_gate_blocked_reason(item) -> str | None:
    for marker in item.iter_markers("pcc_gate"):
        unavailable = marker.kwargs.get("unavailable")
        if unavailable:
            return str(unavailable)
        env = marker.kwargs.get("env")
        if env and not os.environ.get(env, "").strip():
            return f"env {env} unset"
        dep = marker.kwargs.get("dep")
        if dep and importlib.util.find_spec(dep) is None:
            return f"dependency {dep!r} not installed"
        if marker.kwargs.get("probe") == "tsan":
            reason = _tsan_unavailable_reason()
            if reason:
                return reason
        if marker.kwargs.get("probe") == "metal":
            reason = _metal_unavailable_reason()
            if reason:
                return reason
        if marker.kwargs.get("probe") == "pcc1":
            _provision_pcc1()  # provisioning step, never a deselect reason
    return None


def pytest_configure(config):
    """Publish xdist's outer width so pcc does not multiply parallelism."""

    if not hasattr(config, "workerinput"):
        return
    raw_count = str(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "") or "").strip()
    try:
        worker_count = max(1, int(raw_count))
    except ValueError:
        worker_count = 1
    os.environ.setdefault("PCC_OUTER_PARALLELISM", str(worker_count))


def pytest_collection_modifyitems(config, items):
    """Deselect unmet pcc_gate items; order the self-host warmup first."""

    deselected = [
        item for item in items if _pcc_gate_blocked_reason(item) is not None
    ]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        dropped = {id(item) for item in deselected}
        items[:] = [item for item in items if id(item) not in dropped]

    warmup = [item for item in items if item.nodeid == _SELF_HOST_WARMUP_NODEID]
    if not warmup:
        return
    remaining = [item for item in items if item not in warmup]
    items[:] = warmup + remaining
