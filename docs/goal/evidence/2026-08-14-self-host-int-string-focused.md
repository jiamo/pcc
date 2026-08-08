# Self-host integer string canonicalization focused evidence — 2026-08-14

Mode: host-Python unit/source contract; no current-pcc1 or bootstrap claim.

Command:

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_pipeline_pass_config.py
```

Result: 5 passed. The tests cover zero, positive and negative integers, values
beyond the former 0..20 ladder, and the duration/profile callers. The source
ratchet rejects both an enumerated lookup/branch ladder and a replacement
hand-written decimal loop.

This is weak evidence until the same canonical `str(int)` path compiles and
runs through a source-current strict no-libpython pcc1 and the later sequential
pcc1 -> pcc2 -> pcc3 fixed point.
