# 2026-07-16 V-P1-VAL completion evidence

Task: `V-P1-VAL`

## Focused value-model result

The three finite slices are implemented and focused-green:

- VP-S1: proven-range raw integer lane with checked re-entry to Python's
  arbitrary-precision semantic `int`;
- VP-S2: selected 1–7-field self-backend aggregate ABI, adapter
  boxing/unboxing, and recursive pointer-bearing slot schema;
- VP-S3: stable diagnostics for direct identity escapes while preserving the
  boxed object-projection boundary.

The closing focused valueclass gate passed:

```text
110 passed in 22.10s
```

Slice-level implementation and gate details remain in:

- `2026-07-16-v-p1-val-s1-range-lane-reentry.md`;
- `2026-07-16-v-p1-val-s2-aggregate-slot-schema.md`;
- `2026-07-16-v-p1-val-s3-identity-escape.md`.

## Five-GC closure

The remaining runtime/bootstrap dependency was closed by
`M3-FIVE-GC-MATRIX-PERF`.  Its strict resumable aggregate gate finished with:

```text
5 passed in 368.45s (0:06:08)
```

Each cache hit revalidated no-libpython, the successful stage records and
binary hashes; complete backend hits additionally revalidated normalized
pcc2/pcc3 identity.  The final process scan was empty.

## Claim boundary

This closes the task's selected semantic-int projection, aggregate ABI, slot
schema, and identity-escape diagnostic contract across the five self-backend
GC configurations.  It does not claim arbitrary valueclass layouts are
unboxed, that all identity escapes can remain allocation-free, or that the
next zero-allocation hot-loop performance task is complete.

