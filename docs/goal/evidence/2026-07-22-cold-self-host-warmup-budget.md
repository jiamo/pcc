# Cold self-host warmer budget closure

Date: 2026-07-22

Mode boundary: this evidence covers the test-owned, lock-serialized pcc1/pcc2/pcc3 oracle warmer under the self backend. It does not claim that concurrent five-GC integration is fast enough; that remaining compiler-performance boundary is tracked separately.

The warmer now selects four inner Python-frontend workers independently of the six pytest-xdist workers. An explicit `PCC_PY_FRONTEND_JOBS` value remains authoritative. The request-scoped CLI environment contract restores both frontend and self-backend worker settings after each host-process invocation, while a compiled pcc1 restart receives the bounded one-job values needed to avoid nested fanout.

Gate results:

- `gtimeout 60s env -u LC_ALL uv run pytest -q -n0 tests/test_test_infrastructure_efficiency.py`: 19 passed.
- Empty-cache `gtimeout 1200s env -u LC_ALL uv run pytest -q -n0 tests/python/test_self_host_oracle_diff.py::test_000_self_host_oracle_stage_cache_warmup`: 1 passed in 727.55 seconds; sampled peak RSS 5.64 GiB; immutable pcc1, pcc2, and pcc3 artifacts were published.
- `gtimeout 1200s env -u LC_ALL uv run pytest`: 9456 passed, 114 skipped in 781.31 seconds.

The subsequent exact integration run did not produce a final summary before its 1800-second watchdog. Its active tail was concurrent GC2/GC3 full self-host rebuilding, not the lock-owned oracle warmer closed by this slice.
