# Stage2 equality and stack-map recovery — 2026-08-26

## Claim

On frozen compiler-source diff identity
`57f1a1611fbbb5e7b9a42e8b07efe256d2b2e830d86c5dba96217358384d6d19`,
the strict Darwin arm64 self/no-libpython bootstrap completes through the
pcc2/pcc3 byte fixed point. This closes the Stage2 correctness failures; it
does **not** claim acceptable performance or five-GC equality.

## Mechanisms fixed

1. Runtime instance equality made generated dataclass `__eq__` reachable for
   unrelated AST dataclass shapes. Synthetic equality now checks concrete
   class before reading fields.
2. pcc1 can produce equal block/SSA-name strings with inconsistent native
   hashes. Precise stack-map CFG, liveness, pointer aliases, spill slots and
   target-final line indexes now use stable integer collision buckets or
   sidecars with explicit text equality.
3. Equality consumers now preserve callback exceptions and commit container,
   iterator, C-API and GC-callback state only after comparison succeeds; the
   right-hand reflected comparison is attempted after left NotImplemented.

## Fixed-point evidence

Artifacts: `build/bootstrap-review-lineindex-20260826/`.

```text
pcc0 -> pcc1  elapsed_ms=340319  rc=0
pcc1 -> pcc2  elapsed_ms=1290895 rc=0
               real 21m27.067s user 90m15.476s sys 5m47.272s
pcc2 -> pcc3  elapsed_ms=384764  rc=0
               real 6m20.956s user 20m41.322s sys 1m49.028s
cmp pcc2 pcc3: OK — byte-identical
```

Stage logs:

- `build/test-logs/bootstrap-review-lineindex-stage2.log`
- `build/test-logs/bootstrap-review-lineindex-stage3.log`

## Focused and ratchet gates

```text
self-backend stackprep/hash-skew/precise-stackmap/unreachable: 35 passed
dataclass unrelated-shape equality:                         1 passed
bootstrap baseline:                                         2 passed, 2 deselected
fallback + IR fallback ratchets:                            40 passed in 563.53s
TLC clean model:                                             22,341 distinct states, no error
TLC victim/early-plan injections:                            intended invariant violations
```

The fallback ratchet recaptures only independent-module OFF/ON action ceilings
for the three self-backend modules (10/205/15). Those probes compile a module
without its sibling export context and therefore count static helper imports
as dynamic actions. The production multi-module strict closure remains zero
bridge/non-bridge fallback, independently enforced by the ratchet and by the
successful `--python-libpython=off` Stage2 chain.

## Performance finding and nonclaim

The cold Stage2 is not green as a performance result. It is 3.35x the hot
Stage3 wall time and consumes 4.36x its user CPU. The Stage2 run also reclaimed
2.346 GB of compiler cache, while Stage3 reused the freshly produced cache.
Every compiler checksum change caused the pcc-Python runtime archive to rebuild
186 objects during Stage1. These observations justify a focused cold-cache /
build-graph P0; they do not yet attribute all 15 minutes of the gap to one
mechanism, and no function-level optimization is accepted without a controlled
profile and receipt-bound A/B.
