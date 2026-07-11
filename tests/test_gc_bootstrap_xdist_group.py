from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_full_gc_bootstraps_remain_independently_schedulable(tmp_path: Path) -> None:
    """The bootstrap resource lease, not one xdist group, owns concurrency."""

    record = tmp_path / "workers.txt"
    probe = tmp_path / "test_full_gc_group_probe.py"
    probe.write_text(
        """
import fcntl
import os
from pathlib import Path


def _record_worker() -> None:
    path = Path(os.environ["PCC_XDIST_GROUP_PROBE"])
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(os.environ.get("PYTEST_XDIST_WORKER", "controller") + "\\n")
        stream.flush()
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def test_full_three_stage_bootstrap_probe_gc0() -> None:
    _record_worker()


def test_full_three_stage_bootstrap_probe_gc1() -> None:
    _record_worker()


def test_full_three_stage_bootstrap_probe_gc2() -> None:
    _record_worker()


def test_full_three_stage_bootstrap_probe_gc3() -> None:
    _record_worker()


def test_full_three_stage_bootstrap_probe_gc4() -> None:
    _record_worker()
""",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PCC_XDIST_GROUP_PROBE": str(record),
    }
    env.pop("LC_ALL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n2",
            "--dist=loadgroup",
            "-p",
            "tests.python.gc.conftest",
            str(probe),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    workers = record.read_text(encoding="utf-8").splitlines()
    assert len(workers) == 5
    # Do not collapse all five backends onto one worker: the real bootstrap
    # helper admits GC0 as the cache warmer and then enforces its own bounded
    # resource lease.  Independent xdist scheduling is required for the warm
    # backends to overlap safely.
    assert len(set(workers)) == 2, result.stdout + result.stderr
