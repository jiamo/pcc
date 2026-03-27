import os
import subprocess
import tempfile
import fcntl

import run as pcc_run


_AUTO_CLEAN_TARGET_DIRS = {
    "postgres": ("projects/postgresql-17.4/",),
    "readline": ("projects/readline-8.2/",),
    "zlib": ("projects/zlib-1.3.1/",),
}


def _repo_root():
    return os.path.dirname(__file__)


def _auto_clean_lock_path():
    return os.path.join(tempfile.gettempdir(), "pcc-pytest-auto-clean.lock")


def _acquire_session_shared_lock(config):
    lock_path = _auto_clean_lock_path()
    lockfile = open(lock_path, "w")
    fcntl.flock(lockfile, fcntl.LOCK_SH)
    config._pcc_auto_clean_lockfile = lockfile


def _upgrade_to_session_clean_lock(config):
    lockfile = getattr(config, "_pcc_auto_clean_lockfile", None)
    if lockfile is None:
        return None
    fcntl.flock(lockfile, fcntl.LOCK_UN)
    fcntl.flock(lockfile, fcntl.LOCK_EX)
    return lockfile


def _tracked_dirty_paths(repo_root):
    result = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()

    dirty = set()
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        dirty.add(path)
    return dirty


def _auto_clean_targets(initial_dirty_paths):
    targets = ["generated"]
    for target, prefixes in _AUTO_CLEAN_TARGET_DIRS.items():
        if any(
            dirty_path == prefix.rstrip("/") or dirty_path.startswith(prefix)
            for dirty_path in initial_dirty_paths
            for prefix in prefixes
        ):
            continue
        targets.append(target)
    return tuple(targets)


def pytest_configure(config):
    """Warm the parser cache before xdist workers start."""
    # Only run on the xdist controller (or when not using xdist).
    # Workers have 'workerinput' set; the controller does not.
    if not hasattr(config, 'workerinput'):
        _acquire_session_shared_lock(config)
        config._pcc_auto_clean_targets = _auto_clean_targets(
            _tracked_dirty_paths(_repo_root())
        )
        from pcc.parse.c_parser import CParser

        CParser()


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        return

    lockfile = _upgrade_to_session_clean_lock(session.config)
    targets = getattr(session.config, "_pcc_auto_clean_targets", ("generated",))
    try:
        pcc_run.clean(targets, repo_root=_repo_root())
    finally:
        if lockfile is not None:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
            lockfile.close()
