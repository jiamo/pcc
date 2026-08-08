# Tier-1 pproxy service replacement gate source

Mode: test-contract implementation and separate host-CPython oracle smoke. This
is not current-pcc1, five-GC, 30-minute load, or release evidence.

The required Level-1 gate now freezes the unmodified vendored pproxy 1.9.5
tree by SHA-256 and drives one generic pcc1 package/install/module path. The
pcc path poisons both configured and PATH-discovered host Python entrypoints,
uses the provenance-checked pcc-Python runtime archive, and executes under
GC0..4 from one content-addressed compiled-module artifact. The gate checks:

- owned-mode installation and a host-free, no-native-build package receipt;
- simultaneous HTTP and SOCKS5 listener behavior against a local origin;
- sixteen concurrent HTTP requests plus SOCKS5 CONNECT traffic;
- admin status, configuration reload, post-reload HTTP/SOCKS traffic, graceful
  SIGINT drain, and absence of pending-task diagnostics;
- exact behavioral comparison with a separately executed CPython 3.13.2
  oracle;
- no Python descendant in the pcc process tree and no libpython/Python dynamic
  linkage on the cached executable.

Focused commands completed:

```text
gtimeout 30s env -u LC_ALL PYTHONPYCACHEPREFIX=/tmp/pcc-pycache-tier1 uv run python -m py_compile tests/integration/test_pcc1_pure_python_service_replacement.py
PASS

gtimeout 45s env -u LC_ALL uv run python -c '<exercise the HTTP/SOCKS/reload/shutdown helper through the host CPython oracle>'
tier1-oracle-contract:ok

git diff --check -- tests/integration/test_pcc1_pure_python_service_replacement.py
PASS
```

Open boundary: the integration gate has not run through a distributed
current-source pcc1. Darwin/Linux GC0..4 execution, the required 30-minute
performance/resource report, package/process/runtime provenance artifacts,
and the mode-labelled Level-1 release note remain required before this row can
be promoted. The bounded integration gate proves behavior and lifecycle, not
long-running throughput, latency, pause, or RSS stability.
