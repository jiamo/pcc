"""Contract tests for owned gateway buffers and watermarks.

These tests are intentionally authored with the pcc1 product gate in mind.
They are not a claim that the current worktree has been compiled or executed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pcc.gateway.buffer import (
    BACKPRESSURE_HIGH,
    BACKPRESSURE_LOW,
    BACKPRESSURE_NONE,
    BufferLimitError,
    BufferReleasedError,
    BufferSegment,
    ChannelBuffer,
)
from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]


def test_view_retains_segment_until_last_release() -> None:
    segment = BufferSegment(8)
    assert segment.write(b"abcdef") == 6
    view = segment.view(1, 3)
    assert segment.release() == 0
    assert view.to_bytes() == b"bcd"
    assert view.release() == 1
    assert segment.released


def test_view_double_release_fails_closed() -> None:
    segment = BufferSegment(2)
    segment.write(b"xy")
    view = segment.view()
    segment.release()
    view.release()
    try:
        view.release()
    except BufferReleasedError:
        return
    raise AssertionError("double release must fail")


def test_channel_reports_exact_high_and_low_transitions() -> None:
    channel = ChannelBuffer(
        segment_size=3,
        low_watermark=3,
        high_watermark=6,
        max_bytes=12,
    )
    assert channel.append(b"abcde") == BACKPRESSURE_NONE
    assert channel.append(b"f") == BACKPRESSURE_HIGH
    assert channel.backpressured
    assert channel.consume(2) == BACKPRESSURE_NONE
    assert channel.consume(1) == BACKPRESSURE_LOW
    assert not channel.backpressured
    assert channel.read() == b"def"


def test_channel_peek_retains_storage_across_queue_consume() -> None:
    channel = ChannelBuffer(2, 2, 4, 8)
    channel.append(b"abcd")
    views = channel.peek_views(3)
    assert b"".join(view.to_bytes() for view in views) == b"abc"
    channel.consume(4)
    assert b"".join(view.to_bytes() for view in views) == b"abc"
    for view in views:
        view.release()


def test_channel_limit_is_checked_before_mutation() -> None:
    channel = ChannelBuffer(2, 2, 4, 5)
    channel.append(b"abcd")
    try:
        channel.append(b"ef")
    except BufferLimitError:
        pass
    else:
        raise AssertionError("hard byte limit must reject growth")
    assert len(channel) == 4
    assert channel.read() == b"abcd"


def test_close_releases_all_queued_views_once() -> None:
    channel = ChannelBuffer(2, 2, 4, 8)
    channel.append(b"abcdef")
    assert channel.close() == 3
    assert channel.close() == 0
    assert len(channel) == 0


@pytest.mark.integration
def test_current_pcc1_self_no_libpython_slow_peer_backpressure_gc_matrix(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the gateway buffer gate")
    source = tmp_path / "buffer_app.py"
    source.write_text(
        '''from pcc.gateway.buffer import BACKPRESSURE_HIGH, BACKPRESSURE_LOW, ChannelBuffer

def main() -> int:
    channel = ChannelBuffer(7, 16, 32, 64)
    if channel.append(b"abcdefghijklmnopqrstuvwxyz012345") != BACKPRESSURE_HIGH:
        return 1
    held = channel.peek_views(20)
    if channel.consume(17) != BACKPRESSURE_LOW:
        return 2
    combined = b""
    for view in held:
        combined = combined + view.to_bytes()
        view.release()
    if combined != b"abcdefghijklmnopqrst":
        return 3
    if channel.read() != b"rstuvwxyz012345":
        return 4
    print("PCC1_GATEWAY_BUFFER_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    executable = tmp_path / "buffer_app"
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    environment["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    for backend in range(5):
        run_environment = dict(environment)
        run_environment["PCC_GC_BACKEND"] = str(backend)
        ran = subprocess.run(
            [str(executable)],
            cwd=tmp_path,
            env=run_environment,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert ran.returncode == 0, "GC" + str(backend) + ": " + ran.stdout + ran.stderr
        assert "PCC1_GATEWAY_BUFFER_OK" in ran.stdout
