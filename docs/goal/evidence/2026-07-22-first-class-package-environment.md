# First-class pcc package environment

Task: `PKG-P0-FIRST-CLASS-PACKAGE-ENVIRONMENT`

Date: 2026-07-22

## Claim

Host pcc, compiled pcc1, package install, and frontend import discovery now use
one self-hostable package-environment resolver. An active `VIRTUAL_ENV` owns a
private compatibility-tagged `.pcc` overlay; without one, pcc selects a durable
per-user data environment. A default install is immediately importable by a
bare follow-up pcc1 compile with no `PCC_PACKAGE_SITE`, no `--target`, and no
libpython edge.

This proves the narrow current NumPy surface (import/version, array creation,
scalar addition, iteration, and scalar conversion), not general NumPy API
compatibility, dependency resolution, or PEP 517 build isolation. uv lock
projection and cross-runtime-profile artifact manifests remain separate task
rows.

## Implemented boundary

- `pcc.package_environment` owns compatibility identity, VIRTUAL_ENV/private
  overlay selection, per-user fallback, legacy package-site precedence, cache
  selection, and JSON/human inspection reports.
- Host and bootstrap CLIs expose `env info`; host installer, compiled installer,
  frontend source discovery, and extension discovery consume the same resolver.
- Divergent `/tmp/pcc-site-packages` and cache-as-install-root defaults are gone.
- Native `str.join` now accepts tuple inputs in both the C runtime and
  pcc-Python mirror; this generic semantic repair was required for the
  self-hosted compatibility tag.
- Default acquisition now uses the owned, hash-verified Simple Repository path.
  Explicit host acquisition remains labeled. This prevents host pip from
  performing a redundant PEP 517 metadata build before pcc's native build.
- The Cython tool wrapper uses `uv run --no-project`, preventing uv from
  discovering and building the acquired source project. In the repeated NumPy
  gate, observed compiler fanout fell from roughly forty processes to the two
  configured pcc build slots; elapsed time fell from 282.09s to 213.02s.
- README's normal NumPy workflow is now the two bare commands: install, then
  compile. `PCC_PACKAGE_SITE` remains only an explicit compatibility override.

## Gates

- Current stage1 rebuild after the final compiled-CLI acquisition change:
  **exit 0 in 3m01s** with two frontend workers.
- Compiled and host acquisition regression with current pcc1:
  **17 passed in 6.90s**.
- Combined package/environment/install/runtime regression batch:
  **82 passed, 2 skipped in 13.28s**.
- Required environment/import gate:
  **42 passed in 3.99s**.
- Required compiled bootstrap-shim gate:
  **93 passed in 365.45s**.
- Required default-environment generic + real NumPy integration after the uv
  project-discovery repair: **2 passed in 213.02s**. The NumPy program was
  installed with bare `pcc1 -m pip install numpy`, compiled with bare
  `pcc1 app.py -o app`, ran under GC0..4, and linked no libpython/Python dylib.

## Open boundary

Empty for this task. The dependent uv project environment, locked sync, and
runtime-profile invariance tasks own the remaining package-environment work.
