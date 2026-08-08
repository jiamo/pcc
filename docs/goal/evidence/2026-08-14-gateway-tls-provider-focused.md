# Gateway TLS provider — focused and host ABI evidence

Date: 2026-08-14

Task: `GATEWAY-P2-TLS-PROVIDER`

## Focused current-source gate

The fail-fast non-integration TLS/config/package/control set completed:

```text
49 passed, 2 deselected in 0.41s
```

The run fixed the first failures without weakening the provider boundary:

- SHA-256 native ownership assertions now follow the decomposed native-module
  classifier, while preserving exact owned-result checks.
- The CPython host model injects the compiled-only UTF-8 extern seam rather
  than making unsafe/extern intrinsics silently executable on the host.
- malformed SNI wildcard forms now receive a stable wildcard diagnostic and
  only one leading `*.` label is accepted.
- transport-neutral `Cancellation`, `BodyStream`, `Request` and `Response`
  records moved to `pcc.gateway.models`; `pcc.web` re-exports the same class
  objects through the public lower-layer gateway API. The gateway no longer
  imports the framework above it, and the full external-app source closure is
  preserved.
- gateway-control source assertions follow the explicit freestanding `i64`
  annotations while retaining the restore-failure ownership checks.

## Real provider ABI probe

```text
gtimeout 300s env -u LC_ALL PCC_NO_AUTO_PCC1=1 \
  PCC_RUN_OPENSSL_TLS_PROVIDER=1 uv run pytest -q -x -n0 -m integration \
  tests/python/test_gateway_tls_provider.py::test_openssl_provider_build_and_host_loader_probe

1 passed in 0.48s
```

This builds and loads the named OpenSSL 3 provider and probes the reviewed v1
dispatcher ABI. It is not an HTTPS, cryptographic-security or no-libc claim.

## Open boundary

The source-current pcc1 self/no-libpython provider ABI fixture and full HTTPS
product canary remain deferred until the separately owned HARNESS/compiler
work yields a stable source identity. The pre-open digest still does not close
filesystem hash-to-`dlopen` TOCTOU; immutable deployment remains required.
Therefore the row is `DONE_WEAK`.
