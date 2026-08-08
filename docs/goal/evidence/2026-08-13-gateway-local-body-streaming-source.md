# Local-handler request-body streaming source checkpoint — unverified

Date: 2026-08-13

This is source evidence only. At the human's explicit direction, no pytest,
compiler, current-pcc1, bootstrap, GC, network, or task-board validation command
was run for this slice.

## Source contract added

- A live transport capability starts a body-bearing local handler at
  `RequestHead`; the host/sans-I/O model continues to dispatch at `RequestEnd`.
- The connection virtual thread remains the only socket and HTTP parser owner.
  It feeds one bounded `BodyStream`; the handler is its single reader and uses
  `Request.read_body()` / `Request.read_body_chunk()` as the canonical compiled
  callback boundary.
- `BodyStream` retains parser `BufferView` slices, accounts unread bytes, parks
  an empty reader on `threading.Event`, wakes on feed/finish/cancel, applies
  high/low-water producer backpressure, and compacts consumed wrappers while
  preserving monotonic `consumed_bytes` for retry safety.
- After `RequestEnd`, the connection joins the handler child rather than waiting
  for another client byte. Cancellation wakes the body waiter before cancelling
  and joining the child. Pending child/request/body/admission owners are taken
  and retired once by the connection owner.
- High-water waits use bounded timer slices and recheck the absolute request
  deadline; a stalled reader cannot hide body timeout behind an unbounded Event
  wait.
- Codec-call and deferred-event ledgers release untransferred `BodyChunk` views
  and queued request admissions best-effort on failure without releasing the
  currently handled chunk twice.
- Terminal framework `Response` values are restricted to status 200..599.
  Local 204 omits Content-Length, 304/HEAD preserve only known representation
  length, and unknown streaming 304/HEAD omit both Content-Length and chunked
  framing.
- Focused source lives in
  `tests/python/test_gateway_local_streaming.py`; the current-pcc1 fixture now
  includes a parked single-reader feed -> finish -> join -> close probe.

## Honest open boundary

- None of this source has executed. In particular, Event/Lock waiter roots,
  cancellation ordering, retained-view relocation, multi-carrier races,
  deadline wakeups, and exact-once cleanup remain unproved under current pcc1
  and GC0..4.
- `Lock.acquire` is deliberately not a standalone `may_park` effect root because
  Python property/descriptor ABI cannot suspend. Small gateway accounting and
  snapshot locks are therefore synchronous carrier-pin critical sections;
  lock-held code must not do I/O, invoke user/provider callbacks, or park, and
  pin telemetry must expose their duration/reason. Methods already resumable
  due to Event/timer operations use the vthread lock path. Pin duration and
  contended behavior still need measurement and a stricter policy.
- Direct dynamic `request.body.read()` is not the canonical compiled handler
  ABI; applications must use the typed `Request.read_body()` or
  `Request.read_body_chunk()` boundary until dynamic receiver proof is widened.
- This checkpoint is not a live listener, pcc1 origin-server, GC-safety,
  performance, security, or nginx-replacement claim.
