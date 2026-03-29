import os
import subprocess
import tempfile
import fcntl

import run as pcc_run


_AUTO_CLEAN_TARGET_DIRS = {
    "postgres": ("projects/postgresql-17.4/",),
    "readline": ("projects/readline-8.2/",),
    "zlib": ("projects/zlib-1.3.1/",),
    "nginx": ("projects/nginx-1.28.3/",),
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
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        config._pcc_skip_auto_clean = True
        lockfile.close()
        config._pcc_auto_clean_lockfile = None
        return None
    return lockfile


def _status_paths(repo_root):
    result = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()

    paths = set()
    for line in result.stdout.splitlines():
        if len(line) < 3:
            continue
        if line.startswith("?? "):
            paths.add(line[3:])
            continue
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.add(path)
    return paths


def _paths_touch_target(paths, prefixes):
    return any(
        path == prefix.rstrip("/") or path.startswith(prefix)
        for path in paths
        for prefix in prefixes
    )


def _auto_clean_targets(initial_status_paths, current_status_paths):
    targets = ["generated"]
    for target, prefixes in _AUTO_CLEAN_TARGET_DIRS.items():
        if _paths_touch_target(initial_status_paths, prefixes):
            continue
        if not _paths_touch_target(current_status_paths, prefixes):
            continue
        targets.append(target)
    return tuple(targets)


def pytest_configure(config):
    """Warm caches before xdist workers start."""
    # Only run on the xdist controller (or when not using xdist).
    # Workers have 'workerinput' set; the controller does not.
    if not hasattr(config, 'workerinput'):
        _acquire_session_shared_lock(config)
        config._pcc_initial_status_paths = _status_paths(_repo_root())
        from pcc.parse.c_parser import CParser

        CParser()

        # Ensure nginx is configured so all xdist workers discover the
        # same parametrized test set.
        nginx_dir = os.path.join(_repo_root(), "projects", "nginx-1.28.3")
        nginx_makefile = os.path.join(nginx_dir, "objs", "Makefile")
        if os.path.isdir(nginx_dir) and not os.path.isfile(nginx_makefile):
            from tests.test_nginx import _ensure_nginx_configured

            _ensure_nginx_configured()


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        return

    lockfile = _upgrade_to_session_clean_lock(session.config)
    if getattr(session.config, "_pcc_skip_auto_clean", False):
        return
    initial_status_paths = getattr(session.config, "_pcc_initial_status_paths", set())
    targets = _auto_clean_targets(initial_status_paths, _status_paths(_repo_root()))
    try:
        pcc_run.clean(targets, repo_root=_repo_root())
    finally:
        if lockfile is not None:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
            lockfile.close()
