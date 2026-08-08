"""Focused HTTP/1 sans-I/O, framing-security and pcc1 origin contract."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import pcc.gateway.http1 as http1_module
from pcc.gateway.http1 import (
    BodyChunk,
    Http1Error,
    Http1ResponseEncoder,
    Http1ServerCodec,
    RequestEnd,
    RequestHead,
)
from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]


def _track_body_chunks(monkeypatch):
    created = []
    original = http1_module.BodyChunk

    class TrackingBodyChunk(original):
        def __init__(self, data) -> None:
            super().__init__(data)
            self.release_calls = 0
            created.append(self)

        def release(self) -> int:
            self.release_calls += 1
            return super().release()

    monkeypatch.setattr(http1_module, "BodyChunk", TrackingBodyChunk)
    return created


def test_fragmented_keep_alive_requests_emit_typed_events() -> None:
    codec = Http1ServerCodec()
    assert codec.feed(b"GET /hea") == []
    events = codec.feed(b"lth HTTP/1.1\r\nHost: example\r\n\r\n")
    assert isinstance(events[0], RequestHead)
    assert events[0].method == "GET"
    assert events[0].target == "/health"
    assert events[0].keep_alive
    assert isinstance(events[1], RequestEnd)


def test_fixed_body_streams_without_waiting_for_whole_body() -> None:
    codec = Http1ServerCodec()
    events = codec.feed(
        b"POST /upload HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\nab"
    )
    assert isinstance(events[0], RequestHead)
    assert isinstance(events[1], BodyChunk)
    assert events[1].data == b"ab"
    assert events[1].view.owner.references == 1
    assert events[1].release() in (0, 1)
    events = codec.feed(b"cde")
    assert events[0].data == b"cde"
    events[0].release()
    assert isinstance(events[1], RequestEnd)


def test_chunked_body_and_trailers_are_incremental() -> None:
    codec = Http1ServerCodec()
    events = codec.feed(
        b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"3\r\nabc\r\n2\r\nde\r\n0\r\nDigest: ok\r\n\r\n"
    )
    chunks = [event.data for event in events if isinstance(event, BodyChunk)]
    assert chunks == [b"abc", b"de"]
    ending = [event for event in events if isinstance(event, RequestEnd)][0]
    assert ending.trailers == [("digest", "ok")]


@pytest.mark.parametrize("fragmented", (False, True))
def test_request_codec_rejects_complete_oversized_trailers(
    fragmented: bool,
) -> None:
    codec = Http1ServerCodec(max_header_bytes=128)
    prefix = (
        b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"0\r\nx-test: "
    )
    if fragmented:
        events = codec.feed(prefix + b"a" * 110)
        assert len(events) == 1
        assert isinstance(events[0], RequestHead)
    payload = b"a" * (20 if fragmented else 130) + b"\r\n\r\n"
    with pytest.raises(Http1Error) as caught:
        codec.feed(payload if fragmented else prefix + payload)
    assert caught.value.code == "trailers-too-large"


@pytest.mark.parametrize(
    "size_line",
    (b" 1", b"1 ", b"+1", b"1_0", b"g", b""),
)
def test_request_codec_rejects_non_hexdig_chunk_size(size_line: bytes) -> None:
    codec = Http1ServerCodec()
    with pytest.raises(Http1Error) as caught:
        codec.feed(
            b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
            + size_line
            + b"\r\n"
        )
    assert caught.value.code == "bad-chunk-size"


@pytest.mark.parametrize(
    "extension",
    (
        b";",
        b";=value",
        b";name=",
        b";bad name=value",
        b";name=bad value",
        b";name=\"unterminated",
        b";name=\"bad\\\"",
        b";name=\"value\"junk",
    ),
)
def test_request_codec_rejects_invalid_chunk_extension(extension: bytes) -> None:
    codec = Http1ServerCodec()
    with pytest.raises(Http1Error) as caught:
        codec.feed(
            b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"1"
            + extension
            + b"\r\nx\r\n0\r\n\r\n"
        )
    assert caught.value.code == "bad-chunk-extension"


def test_request_codec_accepts_token_and_quoted_chunk_extensions() -> None:
    events = Http1ServerCodec().feed(
        b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"1;flag;name=value;quoted=\"a b\\\"c\"\r\nx\r\n0\r\n\r\n"
    )
    chunks = [event for event in events if isinstance(event, BodyChunk)]
    assert [chunk.data for chunk in chunks] == [b"x"]
    assert isinstance(events[-1], RequestEnd)
    for chunk in chunks:
        chunk.release()


def test_request_codec_rejects_complete_oversized_chunk_line() -> None:
    codec = Http1ServerCodec()
    with pytest.raises(Http1Error) as caught:
        codec.feed(
            b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"1;name="
            + b"x" * 128
            + b"\r\n"
        )
    assert caught.value.code == "chunk-line-too-long"


@pytest.mark.parametrize(
    ("malformed_tail", "error_code"),
    [
        (b"z\r\n", "bad-chunk-size"),
        (b"2\r\ndeXX", "bad-chunk-ending"),
        (b"0\r\nContent-Length: 1\r\n\r\n", "forbidden-trailer"),
    ],
)
def test_feed_error_releases_undelivered_body_chunk_owner_exactly_once(
    monkeypatch, malformed_tail: bytes, error_code: str
) -> None:
    created = _track_body_chunks(monkeypatch)
    codec = Http1ServerCodec()
    payload = (
        b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"3\r\nabc\r\n2\r\nde\r\n"
        + malformed_tail
    )

    with pytest.raises(Http1Error) as caught:
        codec.feed(payload)

    assert caught.value.code == error_code
    assert len(created) == 2
    for chunk in created:
        assert chunk.release_calls == 1
        assert chunk.released
        assert chunk.view.released
        assert chunk.view.owner.references == 0
        assert chunk.view.owner.released


def test_successful_feed_transfers_body_chunk_owner_without_releasing(
    monkeypatch,
) -> None:
    created = _track_body_chunks(monkeypatch)
    events = Http1ServerCodec().feed(
        b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 3\r\n\r\nabc"
    )

    assert len(created) == 1
    chunk = created[0]
    assert chunk in events
    assert chunk.release_calls == 0
    assert not chunk.released
    assert chunk.view.owner.references == 1
    chunk.release()
    assert chunk.release_calls == 1


def test_pipeline_resets_state_at_message_boundary() -> None:
    codec = Http1ServerCodec()
    events = codec.feed(
        b"GET /a HTTP/1.1\r\nHost: x\r\n\r\n"
        b"GET /b HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"
    )
    heads = [event for event in events if isinstance(event, RequestHead)]
    assert [head.target for head in heads] == ["/a", "/b"]
    assert heads[0].keep_alive
    assert not heads[1].keep_alive


def test_bounded_pipeline_decode_leaves_later_message_buffered() -> None:
    codec = Http1ServerCodec()
    events = codec.feed(
        b"GET /first HTTP/1.1\r\nHost: local\r\n\r\n"
        b"GET /second HTTP/1.1\r\nHost: local\r\n\r\n",
        1,
    )
    assert len(events) == 2
    assert events[0].target == "/first"
    assert isinstance(events[1], RequestEnd)
    assert len(codec.buffer) > 0

    events = codec.feed(b"", 1)
    assert len(events) == 2
    assert events[0].target == "/second"
    assert isinstance(events[1], RequestEnd)
    assert len(codec.buffer) == 0


def _assert_error(payload: bytes, code: str) -> None:
    try:
        Http1ServerCodec().feed(payload)
    except Http1Error as error:
        assert error.code == code
        return
    raise AssertionError("malformed request was accepted")


def test_ambiguous_and_malformed_framing_fails_closed() -> None:
    _assert_error(
        b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx",
        "ambiguous-length",
    )
    _assert_error(
        b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n",
        "ambiguous-framing",
    )
    _assert_error(
        b"GET / HTTP/1.1\r\nHost: x\r\n folded: value\r\n\r\n",
        "obs-fold",
    )


def test_non_origin_request_target_forms_fail_inside_http_boundary() -> None:
    _assert_error(
        b"GET http://example.test/path HTTP/1.1\r\nHost: example.test\r\n\r\n",
        "unsupported-target-form",
    )
    _assert_error(
        b"OPTIONS * HTTP/1.1\r\nHost: example.test\r\n\r\n",
        "unsupported-target-form",
    )
    _assert_error(
        b"CONNECT example.test:443 HTTP/1.1\r\nHost: example.test\r\n\r\n",
        "unsupported-target-form",
    )


def test_limits_fire_before_unbounded_buffering() -> None:
    codec = Http1ServerCodec(max_header_bytes=32)
    try:
        codec.feed(b"GET / HTTP/1.1\r\nHost: " + b"x" * 40)
    except Http1Error as error:
        assert error.status == 431
    else:
        raise AssertionError("oversized headers were buffered")


def test_response_encoder_owns_framing_and_rejects_injection() -> None:
    encoder = Http1ResponseEncoder()
    assert encoder.head(200, [("Content-Type", "text/plain")], 2).endswith(
        b"Content-Length: 2\r\n\r\n"
    )
    assert encoder.chunk(b"abc") == b"3\r\nabc\r\n"
    assert encoder.end_chunks() == b"0\r\n\r\n"
    try:
        encoder.head(200, [("X-Test", "ok\r\nInjected: yes")], 0)
    except Http1Error:
        pass
    else:
        raise AssertionError("response splitting was accepted")
    for name, value in (
        ("Content-Length", "9"),
        ("Transfer-Encoding", "chunked"),
        ("Connection", "keep-alive"),
    ):
        try:
            encoder.head(200, [(name, value)], 2)
        except Http1Error as error:
            assert error.code == "response-framing-owned"
        else:
            raise AssertionError("caller supplied response framing was accepted")


@pytest.mark.integration
def test_current_pcc1_self_no_libpython_http1_origin_and_security_corpus(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the HTTP/1 codec gate")
    source = tmp_path / "http1_codec_app.py"
    source.write_text(
        '''from pcc.gateway.http1 import BodyChunk, Http1Error, Http1ResponseEncoder, Http1ServerCodec, RequestEnd, RequestHead

def main() -> int:
    codec = Http1ServerCodec()
    first = codec.feed(b"POST /x HTTP/1.1\\r\\nHost: local\\r\\nContent-Length: 4\\r\\n\\r\\nab")
    second = codec.feed(b"cdGET /next HTTP/1.1\\r\\nHost: local\\r\\n\\r\\n")
    if len(first) != 2 or not isinstance(first[0], RequestHead) or first[1].data != b"ab":
        return 1
    if len(second) != 4 or second[0].data != b"cd" or not isinstance(second[1], RequestEnd):
        return 2
    if second[2].target != "/next" or not isinstance(second[3], RequestEnd):
        return 3
    encoded = Http1ResponseEncoder().head(200, [("content-type", "text/plain")], 2) + b"ok"
    if b"Content-Length: 2\\r\\n" not in encoded:
        return 4
    rejected = False
    try:
        Http1ServerCodec().feed(b"POST / HTTP/1.1\\r\\nHost: x\\r\\nContent-Length: 1\\r\\nTransfer-Encoding: chunked\\r\\n\\r\\n")
    except Http1Error:
        rejected = True
    if not rejected:
        return 5
    target_rejected = False
    try:
        Http1ServerCodec().feed(b"GET http://local/path HTTP/1.1\r\nHost: local\r\n\r\n")
    except Http1Error as error:
        target_rejected = error.code == "unsupported-target-form"
    if not target_rejected:
        return 6
    print("PCC1_HTTP1_CODEC_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    executable = tmp_path / "http1_codec_app"
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
    ran = subprocess.run(
        [str(executable)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "PCC1_HTTP1_CODEC_OK" in ran.stdout
