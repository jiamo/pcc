# PERF-P2-CODE-IDENTIFIED-PGO focused evidence — 2026-08-14

Mode: host-source schema/matcher/emitter tests; no benchmark or fixed-point
claim.

Command/result:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_code_identified_pgo.py
# 5 passed in 0.08s
```

Identity matching, stale/corrupt/skew rejection, deterministic no-profile
ordering, and AArch64/x86 emitter consumption are focused-green.  Open:
representative/adversarial multi-sample metrics and sequential fixed point.
