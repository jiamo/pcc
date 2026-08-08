"""Application-level persistence gates for the PCC Harness port."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "harness"
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from session_persistence import JsonlSessionStore


def _run(entry: Path, home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(entry), "--home", str(home), *arguments],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _identity(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("identity="):
            return line.removeprefix("identity=")
    raise AssertionError("native Harness output did not include identity")


def test_host_application_persists_and_resumes_the_same_projection(tmp_path: Path) -> None:
    home = tmp_path / "home"
    entry = Path(sys.executable)
    app = PROJECT / "app.py"

    first = subprocess.run(
        [
            str(entry),
            str(app),
            "--home",
            str(home),
            "--session",
            "durable",
            "first request",
        ],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    second = subprocess.run(
        [
            str(entry),
            str(app),
            "--home",
            str(home),
            "--session",
            "durable",
            "second request",
        ],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "session=durable format=0 durable=true resumed=false" in first.stdout
    assert "session=durable format=0 durable=true resumed=true" in second.stdout
    assert "title=first request" in second.stdout
    assert "user: first request" in second.stdout
    assert "user: second request" in second.stdout
    assert _identity(first.stdout) == _identity(second.stdout)
    restored = JsonlSessionStore(str(home / "sessions")).load("durable")
    assert restored.projection().completed_turn_count == 2
    assert restored.projection().session_stats.turns == 2
    assert restored.projection().session_stats.steps == 2


@pytest.mark.integration
def test_current_pcc1_persist_resume_and_fork(tmp_path: Path) -> None:
    binary = PROJECT / "build" / "harness-core"
    home = tmp_path / "native-home"

    first = _run(binary, home, "--session", "durable", "native first")
    second = _run(binary, home, "--session", "durable", "native second")
    child = _run(
        binary,
        home,
        "--session",
        "durable",
        "--fork",
        "child",
        "child request",
    )

    assert "session=durable format=0 durable=true resumed=false" in first.stdout
    assert "session=durable format=0 durable=true resumed=true" in second.stdout
    assert "user: native first" in second.stdout
    assert "user: native second" in second.stdout
    assert "session=child format=0 durable=true resumed=true" in child.stdout
    assert _identity(first.stdout) == _identity(second.stdout) == _identity(child.stdout)

    store = JsonlSessionStore(str(home / "sessions"))
    parent = store.load("durable")
    forked = store.load("child")
    assert parent.projection().completed_turn_count == 2
    assert parent.projection().session_stats.turns == 2
    assert parent.projection().session_stats.steps == 2
    assert forked.header.parent_session == "durable"
    assert forked.header.seed_length == parent.count()
    assert forked.projection().completed_turn_count == 3
    assert forked.projection().session_stats.turns == 3
    assert forked.projection().session_stats.steps == 3
    assert forked.projection().title == "native first"
