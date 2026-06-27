# Test package fixtures

Small, real, redistributable package artifacts used by pinned pcc-native
package end-to-end gates (`tests/integration/test_pcc_native_package_e2e.py`).
These are unmodified upstream artifacts, vendored so the gates are
self-contained (no index/network at test time).

- `wheel-0.45.1-py3-none-any.whl` — the upstream `wheel` project (PyPI),
  MIT-licensed, pure-Python. Used as a second, non-numpy distribution to prove
  the acquire/build/run package pipeline is generic.
