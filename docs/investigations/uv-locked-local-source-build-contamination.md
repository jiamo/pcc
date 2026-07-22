# uv locked local source build contamination

Resolved locally on 2026-07-22 for the locked-sync path. A real uv-generated
lock pointing at `projects/numpy-2.4.4` selected the correct local source row,
but `pcc sync --locked` passed that mutable working tree directly to `pcc1 -m
pip install`. The installer then reused `build/pcc-package/meson-build` from an
older CPython 3.14 build and overlaid its `*.cpython-314-*.so` files into the
new pcc-native staging site. The ABI audit correctly rejected the result with
`PCC-PKG-004`.

## Boundary and reproduction

The failing boundary was package materialization, before pcc1 application
compilation:

```text
real uv.lock -> pcc sync projection: passed
local NumPy source digest: passed
pcc1 local-source install: failed
pcc-native ABI audit: rejected copied CPython extension names
```

The focused reproducer is
`tests/integration/test_uv_locked_pcc_sync.py::test_uv_locked_numpy_sync_compiles_and_runs_without_libpython`.
The first failure completed in 22.05 seconds and reported CPython 3.14 artifacts
under the transaction's staged site. Those artifacts came from the source
tree's pre-existing `build/pcc-package/meson-build`, not from the locked source
inputs or the current pcc1 build.

## Root cause

`sync_uv_lock` hashed local sources while intentionally excluding volatile
build directories, but installed from the original mutable directory. Thus the
artifact digest and the actual build input were different: ignored build state
could affect installation. `_overlay_meson_build_payloads` is correct for the
outputs produced by the current pcc-managed build, but cannot distinguish them
from stale outputs already present when the build begins.

## Resolution

Locked local directories are now copied to a transaction-private source
snapshot using the same exclusion policy as the source digest. pcc1 builds the
snapshot, never the user's mutable checkout. The snapshot is outside the
published environment and is removed after success or failure. This preserves
the generic Meson overlay mechanism while making the digest and build inputs
coherent; there is no NumPy-specific branch.

Focused unit coverage plants a stale build payload in a local dependency and
proves that it cannot enter the published site. The real NumPy locked-project
gate proves the full source build, repeat no-op, pcc1 compile, and no-libpython
execution boundary.

