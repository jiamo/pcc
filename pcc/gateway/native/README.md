# pcc OpenSSL TLS provider

This directory contains the in-repository implementation of the
`pcc-native-tls-v1` dynamic provider ABI. It turns the generic ABI in
`../include/pcc_tls_provider_v1.h` into OpenSSL calls; it does not put OpenSSL
types or behavior into the gateway scheduler or HTTP implementation.

The minimum supported build and runtime release is OpenSSL 3.0.0. The adapter
source is MIT licensed. OpenSSL is an external, non-vendored Apache-2.0
dependency, and binary distributors must retain its applicable notices. Public
API declarations in `projects/openssl-3.4.1/include/openssl/` and the
nonblocking/SNI shapes in
`~/pcc_refs/nginx-full-depth1/src/event/ngx_event_openssl.c` and
`~/pcc_refs/nginx-full-depth1/src/http/ngx_http_request.c` were used as
reference oracles; no nginx source is copied into this provider. No BoringSSL
source tree is vendored or dynamically discovered. BoringSSL and LibreSSL
deliberately fail the compile-time identity check because neither promises
OpenSSL 3 ABI compatibility. They require separately named and tested provider
implementations.

This implementation is an explicit external-provider boundary, not evidence
for the future Linux zero-libc runtime claim: OpenSSL and this adapter use the
platform C ABI/libc. They do not use libpython or a host Python interpreter.

Python-cc wheels carry this README, the provider manifest, ABI header,
Makefile, and adapter source as reviewed build inputs. They deliberately do
not carry a prebuilt `.so` or `.dylib`: a library built in a developer checkout
must not leak into a wheel for another target. Plain HTTP needs no provider.
HTTPS requires building this adapter (or another separately named provider)
for the deployment target and passing its absolute library path, lowercase
SHA-256 digest, and a finite artifact-size limit in the listener configuration;
there is no fallback to Python `ssl`, plaintext, or a host web server.  The
digest is computed by the freestanding pcc-Python runtime in 32 KiB streaming
chunks before `dynamic_library_open`; unreadable, oversized, malformed-digest,
and mismatched artifacts all fail listener startup.

An explicitly injected `TlsProviderRegistry` remains a caller-owned trust
boundary for tests and custom providers. If its listener declares a library,
digest, and byte limit, all three values plus the provider's verified digest
must match the registry snapshot or startup fails. If it declares no library,
the gateway makes no artifact-authentication claim for that external registry.

Build with a discoverable OpenSSL 3 installation:

```sh
make -C pcc/gateway/native
```

Or select one explicitly:

```sh
make -C pcc/gateway/native OPENSSL_PREFIX=/opt/openssl-3
```

An explicit prefix is also recorded as the provider artifact's runtime search
path; set `OPENSSL_RUNTIME_PATH_FLAGS=` if packaging supplies its own loader
policy.

The output is `build/libpcc_tls_openssl.dylib` on Darwin or
`build/libpcc_tls_openssl.so` on Linux. Configure a listener with that absolute
output path, provider name `pcc-native-tls-v1`, and a `TlsConfig` whose
certificate inputs are absolute paths. Set `tls_provider_library_sha256` to the
64-character lowercase SHA-256 of that exact output. The default
`tls_provider_max_bytes` is 256 MiB and can be lowered by deployment policy.
The certificate file is a PEM chain
(leaf first), the key is an unencrypted PEM private key, and `client_ca` is a
PEM trust-anchor file required only when client-certificate authentication is
enabled. Encrypted keys are rejected without invoking OpenSSL's terminal
password prompt. Files are parsed synchronously while constructing a new
immutable TLS generation; paths and file buffers are not retained. Failed
reloads never publish a partial generation.

The provider receives sockets that pcc has already made nonblocking and whose
owner has established the platform's no-SIGPIPE policy. The provider does not
mutate process signal dispositions. Every OpenSSL operation is attempted once
and translates `SSL_ERROR_WANT_READ` or `SSL_ERROR_WANT_WRITE` to the ABI. It
never polls, sleeps, schedules, closes an fd, handles HTTP, or invokes
Python/libpython. SNI pauses the OpenSSL
ClientHello state machine and returns the hostname to pcc; pcc applies its
generation policy and explicitly resumes the provider with the selected
context. ALPN is required to overlap the listener's offered protocols.

`provider-manifest.json` is the reviewable ownership, provenance, licensing and
certificate-input record. Its relative source/header/build paths resolve from
this directory, including in an installed wheel. It describes these reviewed
inputs; deployment/release evidence must still record the expected artifact
digest and build identity. The current path-based hash-then-load boundary does
not close concurrent replacement by another writer between verification and
`dynamic_library_open`; production deployment must make the artifact and its
parent directory immutable to the gateway identity. A future same-fd/inode
loader seam is required before claiming that race closed. A successful build
alone is not live HTTPS evidence; the provider ABI canary and current-pcc1
HTTPS gate must also complete.
