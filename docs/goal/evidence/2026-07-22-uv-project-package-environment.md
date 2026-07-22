# uv project package environment

Task: `PKG-P0-UV-PROJECT-ENVIRONMENT`

Date: 2026-07-22

## Claim

A uv-installed pcc wheel exposes both the host `pcc` launcher and its native
`pcc1` helper. Under the same uv project, both select the same
compatibility-tagged `.venv/.pcc` overlay. A bare `uv run pcc1 -m pip install`
followed by a bare `uv run pcc1 app.py -o app` installs, compiles, and runs a
generic package without package-path or backend flags.

This proves uv/venv environment ownership and isolation for the current
pcc-native package surface. It does not prove uv.lock projection, dependency
resolution, or general third-party package compatibility; those remain in the
dependent locked-sync task.

## Implemented boundary

- The wheel ships the native `pcc1` script and supports an explicitly verified
  platform pcc1 input for release/CI reuse.
- Native pcc1 locates installed pcc resources from `VIRTUAL_ENV` or its own
  install prefix, without a source-repository environment variable.
- The pcc-emitted runtime archive and target stamp are shipped together. The
  wheel hook forces a rebuild only when compiler/runtime inputs are newer.
- pcc-native package files live below the selected `.venv/.pcc/environments`
  root. The test proves CPython site-packages is not populated with the generic
  package or relabeled pcc-native extensions.
- Recreating `.venv` preserves deterministic environment identity but removes
  overlay contents. A program compiled after recreation raises the expected
  runtime `ImportError`; it does not select the retained user cache or another
  global package site.

## Gates

- Required unit/environment gate: **5 passed in 0.24s**.
- Required real uv wheel integration gate: **1 passed in 6.86s**.
- Required ABI-mode and package-claim gate: **8 passed in 0.21s**.
- Final stage1 rebuild containing installed-prefix discovery and quiet missing
  directory probing: **exit 0 in 160.269s**.

## Open boundary

Empty for this task. `PKG-P1-UV-LOCKED-NATIVE-SYNC` owns uv.lock projection,
transactional sync, provenance, and unchanged second-sync reuse.
