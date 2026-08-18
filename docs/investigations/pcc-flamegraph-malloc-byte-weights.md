# Investigation: malloc flamegraph weighted allocation counts as bytes

## Status

resolved

## Problem Description

`scripts/pcc_flamegraph.py heap/peak` claimed to weight call paths by bytes,
but a `malloc_history` row contains two numbers: allocation count followed by
cumulative size, for example `28350 (1902M)`. The shared `_ROW` parser retained
only `28350`, so heap graphs ranked allocation counts while labelling them
bytes. This blocked attribution of the current Stage2 coordinator footprint.

## Repro

Fold this synthetic call tree in allocation mode:

```text
Call graph:
    10 (2.0K) root
    + 4 (512) child
```

The old tool had no allocation-byte mode and returned count weights `6/4`.
The correct self weights are `1536/512` bytes.

## Test [CONFIRMED]

`tests/python/test_pcc_flamegraph_tool.py::test_malloc_call_tree_weights_reported_bytes_not_allocation_count`
was red with `TypeError: _fold() got an unexpected keyword argument
'allocation_bytes'`. It also pins the real `1 byte` / `128 bytes` spellings.

## Proposals

- No.1 Parse the parenthesized allocation amount for heap/peak [CONFIRMED]

## No.1 Parse the parenthesized allocation amount for heap/peak

### Code Change

Capture the optional parenthesized amount, parse unscaled bytes plus binary
K/M/G/T/P suffixes and `byte/bytes`, and let `_fold` select allocation bytes or
sample counts explicitly. CPU mode remains count-weighted. Heap/peak main mode
selects bytes and fails closed on unknown/missing amount syntax. Self weight is
clamped at zero against display-rounding differences between a parent and its
children.

### CONFIRMED

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_pcc_flamegraph_tool.py
15 passed in 0.09s
```

The retained frontend `malloc_history` report now parses to 1.863GiB of
stack-attributed live allocations rather than 28,350 allocation-count units.
The report separately names 4.4GiB of earlier VM regions without stacks; those
bytes remain unattributed and are not silently assigned.

## Report

Heap/peak graphs now measure the byte quantity their UI and AGENTS tool entry
promise. Existing CPU flamegraphs retain their sample estimator. No compiler,
runtime, GC, or generated-program behavior changed.

