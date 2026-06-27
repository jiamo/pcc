from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path


def _compile_and_collect_events(tmp_path: Path, source: str):
    src = tmp_path / "probe.py"
    exe = tmp_path / "probe"
    src.write_text(
        textwrap.dedent(source),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    log_path = tmp_path / "exception.jsonl"
    run_env = {
        **env,
        "PCC_GC_BACKEND": "0",
        "PCC_LOG": "exception,refcount",
        "PCC_LOG_FORMAT": "json",
        "PCC_LOG_FILE": str(log_path),
    }
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=run_env,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "done"

    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_builtin_exception_raise_releases_temporary_after_clear(tmp_path: Path):
    events = _compile_and_collect_events(
        tmp_path,
        """
        def main():
            i = 0
            while i < 50:
                try:
                    raise AttributeError("x")
                except AttributeError:
                    pass
                i += 1
            print("done")
        main()
        """,
    )
    exception_events = [
        event["event"] for event in events if event["category"] == "exception"
    ]
    refcount_events = [
        event for event in events if event["category"] == "refcount"
    ]

    assert exception_events.count("alloc") == 50
    assert exception_events.count("new") == 50
    assert exception_events.count("raise") == 50
    assert exception_events.count("clear") == 50
    assert exception_events.count("dealloc") == 50
    assert (
        sum(
            1
            for event in refcount_events
            if event["event"] == "free" and event["value1"] == 12
        )
        == 50
    )


def test_runtime_stopiteration_helper_releases_temporary_after_clear(
    tmp_path: Path,
):
    events = _compile_and_collect_events(
        tmp_path,
        """
        def main():
            i = 0
            while i < 50:
                it = iter([])
                try:
                    next(it)
                except StopIteration:
                    pass
                i += 1
            print("done")
        main()
        """,
    )
    exception_events = [
        event for event in events if event["category"] == "exception"
    ]
    refcount_events = [
        event for event in events if event["category"] == "refcount"
    ]

    assert sum(
        1
        for event in exception_events
        if event["event"] == "new" and event["value0"] == 8
    ) == 50
    assert sum(
        1
        for event in exception_events
        if event["event"] == "raise" and event["value0"] == 12
    ) == 50
    assert sum(1 for event in exception_events if event["event"] == "clear") == 50
    assert sum(1 for event in exception_events if event["event"] == "dealloc") == 50
    assert (
        sum(
            1
            for event in refcount_events
            if event["event"] == "free" and event["value1"] == 12
        )
        == 50
    )


def test_runtime_attribute_error_helper_releases_temporary_after_clear(
    tmp_path: Path,
):
    events = _compile_and_collect_events(
        tmp_path,
        """
        class C:
            pass

        def main():
            obj = C()
            i = 0
            while i < 50:
                try:
                    obj.missing
                except AttributeError:
                    pass
                i += 1
            print("done")
        main()
        """,
    )
    exception_events = [
        event for event in events if event["category"] == "exception"
    ]
    refcount_events = [
        event for event in events if event["category"] == "refcount"
    ]

    assert sum(
        1
        for event in exception_events
        if event["event"] == "new" and event["value0"] == 6
    ) == 50
    assert sum(
        1
        for event in exception_events
        if event["event"] == "raise" and event["value0"] == 12
    ) == 50
    assert sum(1 for event in exception_events if event["event"] == "clear") == 50
    assert sum(1 for event in exception_events if event["event"] == "dealloc") == 50
    assert (
        sum(
            1
            for event in refcount_events
            if event["event"] == "free" and event["value1"] == 12
        )
        == 50
    )
