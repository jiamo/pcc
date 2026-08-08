# Runtime-profile package-environment invariance

Date: 2026-07-31

Task: `PKG-P1-RUNTIME-PROFILE-ENVIRONMENT-INVARIANCE`

## Source identity

- Base commit: `98e62890963c60515d6f8ddc8c31996b04500f95`, worktree carrying only
  this slice's changes plus the sibling `PKG-P1-UV-LOCKED-NATIVE-SYNC` closure
  docs.
- Changed behavior:
  - New host-side module `pcc/package/runtime_profile.py`: declared
    runtime-policy axes (`RUNTIME_PROFILE_ENV_VARS`: `PCC_GC_BACKEND`,
    `PCC_REFCOUNT_KIND`, `PCC_BACKEND`, `PCC_WITH_THREADS`,
    `PCC_VTHREAD_PARKED`, `PCC_GPU_BACKEND`, `PCC_METAL`, `PCC_DS`),
    `runtime_profile()` report, capability-tagged artifact rows
    (`normalize_capability_artifacts`, `read_capability_artifacts` over a
    package's `pcc-capabilities.json`), and fail-closed
    `select_capability_artifact` with stable diagnostics
    (`PCC-PKG-CAPABILITY-UNKNOWN` / `-UNAVAILABLE` / `-ARTIFACT-MISSING` /
    `-MANIFEST-INVALID` / `-ROW-INVALID`). Payload paths escaping the artifact
    root are rejected; payloads are content-hashed into the manifest rows.
  - `pcc/package/uv_lock_sync.py` records `capability_artifacts` per package
    in the sync report and the published `installed.json` distribution
    manifest; an invalid capability manifest fails the sync with
    `PCC-PKG-UVLOCK-CAPABILITY-INVALID`. Sync/build keys are unchanged, so
    capability payloads and runtime policy never key the environment.
  - Shared test contract
    `tests/python/package_environment_profile_contract.py` plus the gate
    files listed below.

## Commands and results

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_package_runtime_profile_environment.py
5 passed in 0.14s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_package_environment_gc0.py ... gc4.py   (all five)
5 passed in 0.26s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/vthread/test_package_environment_profile.py \
  tests/kernel/test_package_gpu_capabilities.py
10 passed in 0.25s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_package_uv_lock_sync.py
7 passed in 0.26s

gtimeout 420s env -u LC_ALL uv run pytest \
  tests/python/test_fallback_baseline.py \
  tests/python/test_ir_py_fallback_baseline.py -q -n0
27 passed in 263.42s
```

## Exit criteria mapping

1. GC0..4, LLVM/self, threaded-runtime, and scheduler policy switches
   preserve one environment identity and identical installed-artifact
   digests: each axis and the combined profile resolve to byte-identical
   environment reports, and a locked re-sync under the switched profile
   returns the same `sync_key`, same `environment_root`, and identical
   `(name, version, artifact_sha256, build_key)` rows.
2. Metal payloads are capability-tagged artifacts inside the distribution
   manifest: `pcc-capabilities.json` rows land as content-hashed
   `capability_artifacts` in `installed.json` and the payload lives inside
   the same environment root — no new environment dimension.
3. Selecting an unavailable capability raises
   `PCC-PKG-CAPABILITY-UNAVAILABLE` naming the requested capability and the
   available set and refusing fallback; unshipped and unknown capabilities
   fail with their own stable codes. No silent CPU/other fallback path
   exists.
4. Cache assertion: the profile-switch re-sync performs zero downloads, zero
   native builds, and zero installer invocations (installer call counter
   stays at 1).
5. Source-level lock: `pcc/package_environment.py` and
   `pcc/package/uv_lock_sync.py` are asserted to never read any declared
   runtime-policy variable.

## Supported claim

Host-side package-environment contract evidence: package environment
identity, sync keys, and installed-artifact digests are invariant across
declared runtime-policy axes, and optional GPU payloads are fail-closed
capability-tagged manifest artifacts. The invariance matrix uses a counting
installer stub; the real-pcc1 locked path is covered by the sibling
`PKG-P1-UV-LOCKED-NATIVE-SYNC` closure evidence.

## Not proven

- No runtime loader consumes `select_capability_artifact` yet; no real
  package ships a Metal payload. This row delivers the manifest/selection
  contract, not GPU-track completeness.
- Real-hardware Metal execution and the GC/vthread/GPU tracks themselves are
  out of scope, as the task row states.
- The stage1 bootstrap closure is untouched (new module is host-only;
  `uv_lock_sync` is outside the closure), re-proven by the fallback ratchet
  above; no full bootstrap chain was re-run for this slice.
