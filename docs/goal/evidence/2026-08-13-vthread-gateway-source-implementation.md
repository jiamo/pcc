# Virtual-thread gateway source implementation — unverified

Date: 2026-08-13

This records an implementation-only checkpoint. At the human's explicit
request, no pytest, compiler, current-pcc1, bootstrap, GC, socket, Metal or task
board validation command was run after writing this source. None of the rows
below is promoted to done by this document.

## Source now present

- closed-world virtual-thread `may_park` propagation and continuation lowering;
- concrete local and sibling-module method `may_park` metadata, generator ABI
  and child delegation, with dynamic/implicit receiver paths rejected;
- bounded pcc-Python multi-carrier scheduler state and pin accounting;
- nonblocking freestanding socket observation ABI, SO_ERROR connect completion,
  Darwin kqueue and Linux x86_64 epoll source with generation tokens,
  absolute deadlines and an interrupt channel;
- retained segmented buffers with byte watermarks;
- bounded incremental HTTP/1 request/response codecs and retained request-body
  views rather than whole-body concatenation;
- live body-bearing local routes start a single handler child at RequestHead;
  the connection remains the sole socket/parser owner and feeds a bounded
  single-reader BodyStream whose Event waits, high/low-water backpressure,
  absolute-deadline slices, cancellation and terminal join are explicit;
- live HTTP/1 decoding stops after one complete request, preserves later
  pipeline bytes, and cannot let a malformed later request erase or overtake a
  valid predecessor response;
- the declarative sequential `pcc.web` framework;
- listener/connection virtual-thread loops, bounded streaming responses,
  global connection/request/upstream/buffer admission, deadlines and drain;
- reverse-proxy policy plus bounded live nonblocking connect/read/write driving;
- static proxy routes begin at `RequestHead`: fixed/chunked body fragments and
  trailers move upstream before `RequestEnd`, `Expect: 100-continue` is owned
  once by the gateway, request-upload/header/body deadlines are distinct, and
  consumed bodies are not retried without a replay owner;
- downstream EOF/protocol failure is observed during DNS/connect/write/read
  parks in bounded 25 ms readiness slices and converges on one request/body/
  admission owner rather than adding a second proxy response;
- connected-UDP asynchronous DNS with TCP fallback, bounded resolver/hosts
  configuration reads, cache, absolute deadlines, rotation, retry and rebinding
  policy, wired into proxy address selection; resolver/cache/policy/address
  rotation and shared-endpoint admission mutations are synchronized;
- the fixed `pcc-native-tls-v1` ABI, listener handshake/read/write/close-notify,
  SNI/ALPN, immutable certificate reload, and an in-repository OpenSSL 3 adapter
  source/build/manifest whose external C/libc/OpenSSL boundary is explicit;
- generation, TLS context/session, server connection, lifecycle, metrics,
  upstream pool and signal-control state have explicit synchronized or
  single-owner teardown; cleanup takes native owners before callbacks and
  continues best-effort while preserving the first failure;
- accept-owner failure is observable, shutdown cancels and boundedly observes
  it before stopping carriers, and the listener fd is closed only after that
  registration owner is terminal;
- deadline shutdown is structured and fail-closed: the control owner cancels
  request/body/handler waits and shuts down connection descriptors, but only
  each connection continuation may join its child and release request/body/
  admission ownership. A separate task ledger waits beyond resource release
  for the surrounding connection vthread's terminal outcome. A nonterminal
  accept or connection preserves the live carrier pool, listener, ledgers and
  GC roots and returns a retryable error;
- carrier-pool stop disposes epoll/kqueue/eventfd ownership only after all
  carriers join; failed cleanup preserves rooted parked I/O for a retry, and a
  later start rebuilds registrations from those roots;
- a fail-closed product-canary source combines real TLS/DNS/proxy/reload/drain,
  GC0..4 markers and process/link/resource-closure assertions;
- current-pcc1 self/no-libpython test source for the finite local/core paths.

## Claims explicitly not made

- No source compiled or test passed in this implementation phase.
- Linux epoll source currently targets x86_64 only; Linux AArch64 fails closed.
- Dynamic method receivers and implicit parking dunder/property calls remain
  rejected until their concrete continuation contracts are proved.
- The OpenSSL adapter has not been compiled or security-tested, and no live
  current-pcc1 HTTPS wire canary has run. It is not a zero-libc component.
- DNS has no parallel Happy Eyeballs and Darwin does not yet consume platform
  split-DNS/SCDynamicStore policy.
- Scheduler interrupt, split-wait and repeated waitset-lifecycle source is now
  present, but no multi-carrier race, cancellation or restart behavior is
  proved by execution.
- Downstream cancellation currently uses bounded periodic readiness slices,
  not one atomic multi-fd wait; this is not scalable idle-efficiency evidence.
- Host/sans-I/O local dispatch, bodyless routes and middleware-controlled local
  dispatch still begin at `RequestEnd`. Only live transport-capable,
  body-bearing local routes use the unverified pre-RequestEnd child path.
- Plain Lock critical sections remain synchronous carrier-pin regions because
  property descriptors cannot suspend. Source forbids I/O, user/provider hooks
  and other parks while those locks are held; contention and pin telemetry are
  not yet proved by execution.
- Repeated carrier-pool lifecycle has a source contract but still needs its
  focused/current-pcc1/GC execution proof; source inspection is not fd-closure
  evidence.
- A handler/iterator that never reaches a pcc cancellation or safepoint can
  outlive the configured drain deadline. That condition now retains all owners
  and fails shutdown instead of pretending cleanup succeeded; an embedding
  owner must retry `GatewayServer.shutdown()` after termination or terminate
  the isolated worker process.
- The product canary, signal-owned graceful shutdown, live listener/proxy/TLS/
  DNS execution, GC0..4, performance and nginx workload comparison remain
  required gates. Source presence is not a no-nginx product result.

The authoritative open work and exact gates remain in
`docs/goal/task-board.yaml`.
