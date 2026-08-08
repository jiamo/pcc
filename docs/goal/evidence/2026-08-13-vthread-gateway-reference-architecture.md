# GATEWAY-P2 virtual-thread gateway reference architecture — 2026-08-13

## Source identity and scope

- Visible repository HEAD: `24711209ba8f65f6fb8aecbd7208c700c98d58cb`.
- The worktree was already dirty in unrelated Python-frontend, runtime-archive
  and test files. This slice did not edit or qualify those implementation
  changes.
- Scope: reference-source study, current pcc seam audit, design contract and
  task decomposition. No gateway implementation, HTTP interoperability,
  throughput, nginx parity or current-pcc1 service result is claimed.

## Pinned primary references

The exact revisions, license labels, local locations, reviewed source anchors,
absorbed mechanisms and rejected ownership/API choices are recorded in
`docs/refs_docs/gateway-research/README.md`:

- nginx `8d9666701d61105d46baed32eba2939599132d7e`;
- OpenJDK Loom `b9778ccb475891efd6347f7645b9a53c011f70fd`;
- Go runtime/net/http `6a0f8b7c91664a458339a7f47a07dda512845fde`;
- Netty `dd7c9dc102bda163cd4966b6e9a45c57f42ebfb9`;
- Helidon `c5c14d66a98a3a9f438fd2708d2f9fb1b2222580`;
- h11 `62c5068c971579d61fa1b55373390e12f25fd856`;
- Starlette `398e5a3430eb1ddd33e1d48d766efe41426e231f`;
- FastAPI `f336ff831c4af3d4f625c2593a27b1e0cae93eb7`.

Full or sparse upstream checkouts live under `~/pcc_refs`. They are source and
differential oracles only; none is a proposed build, link, subprocess, import
or runtime dependency.

## Current pcc audit verdict

Pcc already has a substantial C-runtime virtual-thread scheduler, GC-rooted
continuations, timer heap, Darwin kqueue/poll waitsets and freestanding socket
syscall/ABI surfaces. That is a useful base, but it is not yet a pcc1 gateway:

- ordinary non-generator call chains cannot transparently freeze and resume at
  `block_current_on_fd`; only an already-resumable generator yields there;
- the pcc-Python carrier-pool surface is still a single-carrier compatibility
  implementation;
- accepted sockets are changed back to blocking mode, outbound connect blocks,
  Linux epoll and general async DNS are absent, and the nonblocking receive
  helper assumes one static 1 KiB buffer;
- the small asyncio TCP loop scans global collections and has a relay-specific
  shortcut; it is an oracle/compatibility path rather than the production root;
- the current HTTP runtime is a client/downloader and socket TLS wrapping is
  unimplemented.

Therefore the existing C-runtime million-virtual-thread measurement does not
prove current-pcc1, self-backend, no-libpython gateway execution.

## Frozen architecture

`docs/design/pcc-vthread-gateway.md` defines two pcc-owned layers:

1. `pcc.gateway`: virtual-thread-aware sockets, channel buffers, backpressure,
   HTTP/1 codec, routing, reverse proxy, DNS/TLS boundaries, admission,
   immutable generations, graceful drain and telemetry.
2. `pcc.web`: typed static routes, request/response types, parameter binding,
   middleware, errors, streaming and lifespan above the gateway kernel.

The first implementation prerequisite is compiler-visible, transitive
`may_park` analysis and heap-owned resumable frames produced by current pcc1.
Netty-style pipelines/buffers remain internal; the public programming model is
sequential virtual-thread code. The design contains a G0–G6 claim ladder. Only
G5+ permits the bounded statement that the covered deployment can run without
nginx; universal nginx replacement remains prohibited.

## Taskization and gates

The task board contains twelve finite rows from reference architecture through
park-effect lowering, pcc1 carrier parity, nonblocking socket/waitset,
buffer/backpressure, HTTP/1, `pcc.web`, reverse proxy, async DNS, TLS provider,
lifecycle/observability and the final product canary. Every implementation row
requires execution from a current pcc1 self-backend/no-libpython artifact.
Host models and C-runtime measurements remain oracle evidence only.

Observed board validation before closing this architecture row:

```text
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate
OK: 285 tasks validated
```

No pytest, bootstrap, GC matrix, network interoperability or performance gate
was run because this was a documentation/reference slice.

## Supported and unsupported claims

Supported: the reference pins are identified and license-labeled; reviewed
concepts have explicit absorbed/rejected boundaries; current pcc seams and
known gaps are mapped; a pcc1-rooted architecture and finite dependency graph
exist and the task board validates.

Not supported: a gateway or HTTP framework exists; ordinary pcc1 functions can
already park transparently; the pcc-Python runtime has multi-carrier parity;
HTTP/TLS/DNS/proxy behavior works; nginx can be removed from any deployment;
the new future test paths named in task gates exist or pass.
