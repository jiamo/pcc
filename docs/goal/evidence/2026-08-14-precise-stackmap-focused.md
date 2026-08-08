# Precise stackmap focused evidence — 2026-08-14

Mode: host pcc, self backend, Darwin arm64, serial fail-first.

- `gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_precise_stackmap_abi.py`
  — 25 passed in 0.11s.
- Adjacent self-backend gate: 294 passed in 6.20s; see
  `2026-08-14-self-ir-verifier-focused.md`.

The ABI suite proves deterministic IDs and binary codecs, corruption rejection,
entry/loop/call/exception/continuation records, provenance/liveness checks,
Mach-O semantic stackmap merging, and ELF relocation validation. During the
adjacent full gate, multi-TU object emission exposed and fixed direct
concatenation of complete stackmap payloads: each unit now becomes a
`NativeObject`, and the relocatable linker emits one canonical merged table.

Not proved here: runtime backend3/4 forced relocation, C-versus-pcc-Python
consumer equality, current pcc1/bootstrap, or fixed-point identity. Those gates
are intentionally deferred to the one final runtime/source publication.
