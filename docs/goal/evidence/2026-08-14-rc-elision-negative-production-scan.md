# Refcount pair elision — focused correctness and production-shape scan

Mode: host pcc, text IR pass, `PCC_GC_BACKEND=0`; no bootstrap or runtime
publication was performed.

The bounded correctness file passed fail-fast:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_refcount_pair_elision.py
7 passed in 0.24s
```

The pass was then applied to generated L1 IR for six deliberately selected
owned-reference shapes and to the first 80 small modules in
`tests/py_corpus/`.  All 80 corpus modules parsed, inferred and generated; the
pass reported zero rewrites in every module.  The six direct shapes also
reported zero rewrites.

This is negative design evidence, not a performance success.  It proves that
the synthetic legality fixtures work, but does not prove that current codegen
emits either candidate shape.  Do not wire the pass into the default
backend-0 tier until two real codegen fixtures show a measurable redundant
pair and the root/refcount event threshold is recorded.
