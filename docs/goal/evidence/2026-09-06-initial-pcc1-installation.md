# Initial shared pcc1 installation — 2026-09-06

Task: `A-INSTALL-P0-INITIAL-PCC1-BASELINE`.

## Installed boundary

Stable command: `/Users/jiamo/.local/bin/pcc1`.
Target: `/Users/jiamo/.local/share/pcc/toolchains/v84-baseline-c1f4342696e9/bin/pcc1`.
The wrapper executes its private `libexec/pcc1` and pins helper sources, runtime
archive and a separate helper Python environment. It does not point into a
core build directory. Versioned source/runtime files are copied and hash-checked.

This is the historical v84 Stage1/Stage2 baseline, not python-cc 0.1.8 and not
full current-source/default/integration/Stage3 qualification. New gateway/GUI
features may require a newer compiler. The current correctness/release tasks
remain unfinished.

Compiler SHA-256:
`c1f4342696e9d45b36deb17434160f702f082dd2b558973f2d75964802ab4090`.
Successful provenance: `build/heapsort-stage1-v84/build-receipt.json` and
`build/heapsort-stage2-v84/manifest.json`. The Stage2 input/output compiler
hashes were checked before installation. Failed v85 Stage2 was not selected.

## Validation

- `tests/python/test_install_pcc1_toolchain.py`: 5 passed in 0.08 seconds;
  includes refusal to overwrite existing/dangling entries, changed/unverified
  candidates, mismatched copy evidence and successful atomic entry creation.
- `scripts/install_pcc1_toolchain.py --stage1-dir build/heapsort-stage1-v84
  --stage2-dir build/heapsort-stage2-v84 --name v84-baseline`, under the shared
  performance lock and a 240-second process-group watchdog: exit 0.
- Installed compiler compiled a real `def add(a: int, b: int)` to a native
  executable; execution returned exactly `42`. `otool -L` lists libSystem only.
- `command -v pcc1` from both application checkouts returns the stable path.
- `pcc1 --help` returns 0. No-argument invocation returns the explicit existing
  unsupported-REPL diagnostic (exit 2); it is not a missing-file error.

Durable installation/canary receipt and logs:
`~/.local/share/pcc/toolchains/v84-baseline-c1f4342696e9/installation.json` and
`evidence/`. Outer run logs are in
`build/correctness-20260906-a/install-baseline-01.*`.

## Remaining boundary

Later promotion/rollback and release qualification belong to
`INSTALL-P0-VERIFIED-PCC1-TOOLCHAIN`, `CHECK-P0-CURRENT-PCC-PCC1-CORRECTNESS`
and `DIST-P0-PYTHON-CC-0-1-8`. The initial installer intentionally refuses all
replacement. No PyPI release, Git commit or source rewind was performed.
