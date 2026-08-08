# GC4 trashcan and zpage source-contract evidence (2026-08-14)

Mode: source-contract only. No current runtime archive, GC4 executable,
throughput/RSS sample, or bootstrap was run.

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_trashcan.py::test_cc_trashcan_state_is_thread_local \
  tests/python/test_freestanding_gc_zpage_lifecycle.py::test_zpage_lifecycle_preserves_cache_and_forwarding_safety_contract \
  tests/python/test_freestanding_gc_zpage_lifecycle.py::test_backend4_free_path_never_scans_all_zpage_lists_for_origin \
  tests/python/test_freestanding_gc_forwarding_retirement.py::test_forwarding_retirement_releases_only_after_two_remap_epochs
4 passed in 0.11s
```

The current source retains thread-local trashcan state, removes the
O(live-pages) free-origin scan, defers unsafe zpage recycle, and physically
releases a retained generation only after the declared two-remap quarantine.
The deep-chain no-overflow, 200 MiB RSS ceiling, throughput and sequential GC4
bootstrap remain current-source runtime gates.
