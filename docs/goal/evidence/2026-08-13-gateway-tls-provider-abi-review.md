# Gateway TLS provider ABI review — implementation source only

Date: 2026-08-13

## Scope and claim level

This is a manual source/ABI boundary review for
`GATEWAY-P2-TLS-PROVIDER`. It records an implementation-only checkpoint. At
the human's explicit request, no pytest, compiler, current-pcc1, native-link,
TLS interoperability, certificate, network, GC, bootstrap, or task-board
validation command was run for this gateway slice. The task remains
`TODO_READY` and unproved until its exact gates run.

## Finite pcc-owned ABI

`pcc/gateway/tls.py` defines provider ABI v1. A provider supplies opaque
certificate contexts and connection sessions plus only:

- context/session create and exactly-once free operations;
- nonblocking handshake, read, write, and close-notify operations;
- synchronous SNI context installation and selected-ALPN reporting;
- fixed readiness/status results and stable bounded error codes.

`pcc.gateway` retains ownership of listener admission, socket flags, virtual-
thread parking, absolute deadlines, HTTP parsing/routing, configuration
publication, certificate-generation references, cancellation, drain, and
telemetry. A provider must not block or become a scheduler/server framework.
The source rejects unknown status/error/count combinations instead of guessing
provider behavior.

## Provider, link, license, and security boundary

Every registry entry must label `name`, ABI version, `link_boundary`,
`license_id`, `security_boundary`, and `production_ready`. Those values are
snapshotted into generation/channel metadata so later evidence can name the
exact provider and native boundary.

The repository now contains the source for a production ABI adapter and an
OpenSSL 3 provider in `pcc/gateway/native/openssl_provider.c`. The provider is
an external C/libc/OpenSSL component: it is not a pcc-Python runtime owner and
does not satisfy the Linux zero-libc claim. Its source implements TLS 1.2/1.3,
PEM certificate/key loading, optional mTLS CA configuration, SNI handoff,
HTTP/1.1 ALPN, nonblocking read/write/close-notify, and stable provider
failures. It has not been compiled, interoperated, fuzzed, or security-audited
in this implementation-only phase.

The scripted providers remain test-only and the registry refuses them when a
production provider is required. Thus source presence proves a finite ABI and
ownership design, not cryptographic correctness or safe Internet exposure.

The production adapter also requires a lowercase expected SHA-256 and a
bounded provider-library byte limit. A pcc-owned streaming hash checks the
declared path before `dlopen`; the activated provider snapshot must match the
declared path, expected/verified digest, and byte limit. Reload cannot replace
those identity fields. An explicitly injected registry with no declared
artifact remains a caller-owned trust boundary and is not described as
artifact-authenticated.

## SNI, ALPN, reload, deadlines, and cleanup

- SNI uses normalized ASCII DNS names, exact matching before a one-label
  wildcard, bounded certificate/name tables, and a configurable fail-closed
  unknown-name policy. Providers request selection with `SELECT_SNI`; pcc
  selects and installs an immutable generation context.
- ALPN is bounded and duplicate-free. A provider-selected protocol not offered
  by the generation is a stable `alpn` failure.
- Reload builds a replacement generation completely before publication. Old
  channels retain the previous generation until close; context destruction is
  reverse-order and exactly once.
- Every data-path operation accepts an absolute monotonic deadline. Expiry is
  checked before entering the provider and maps to stable `deadline`.
- Provider exceptions, invalid result shapes, malformed SNI, cancellation,
  protocol failures, unrecognized names, and cleanup failures have distinct
  bounded mappings. Provider detail is local-only and capped at 160 characters.
- Close-notify is a readiness-driven operation. Force-close never performs
  blocking I/O and releases session plus generation once even if provider
  cleanup reports an error.

## Integration boundary and remaining work

`GatewayServer` now wires a named production provider through accept,
handshake, SNI/ALPN, TLS read/write, bounded close-notify, immutable certificate
reload, and generation-owned cleanup. Missing symbols/capabilities, an
unverified declared artifact, unsupported ALPN, or a test-only provider fail
closed; there is no host `ssl`/`asyncio` or plaintext fallback.

The pre-open path hash does not cryptographically bind the bytes later opened
by path. Concurrent writable-path replacement remains a hash-to-`dlopen`
TOCTOU deployment boundary; immutable provider files and parent directories
are required until a same-handle/inode loader is implemented. Linux SIGPIPE
policy, provider compilation, key-material/vulnerability policy, real
certificate/SNI/ALPN/malformed-peer/timeout/reload/close interoperability, the
current-pcc1 self/no-libpython HTTPS gate, and GC0..4 lifetime proof all remain
open. No source-only result is a TLS security verdict.
