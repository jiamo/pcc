# GC3 explicit full-heap collection source contract (2026-08-14)

Mode: source-contract only. No GC3 runtime workload, RSS measurement, or
bootstrap was run.

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_freestanding_gc_public_collection.py::test_public_collection_has_one_strict_source_owner \
  tests/python/test_freestanding_gc_public_collection.py::test_public_collection_preserves_config_and_collection_order
2 passed in 0.09s
```

The production pcc-Python owner and retained C oracle both implement explicit
`gc.collect()` as a stop-the-world full sweep with signed `INT64_MAX`; the old
1024 budget is rejected. The 10k two-node cycle count, steady-state RSS,
finalizer behavior and sequential GC3 bootstrap remain runtime gates.
