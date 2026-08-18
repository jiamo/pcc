# Per-function text streaming `[DENIED]`

## Proposal

On the no-pass PIDX-to-PCO path, convert each function's emitted instruction
strings to scalar records before emitting the next function.  Preserve two-
space placeholders so target-final stack-map and compact-unwind offsets remain
exact.  The intended ceiling was the 1.02 GiB RSS growth attributed to the
transport phase in evidence 047.

## Correctness

The candidate produced the exact retained PCO for tiny, `py_ast`, medium,
heavy and worst source indices.  Focused structured/codec/driver gates passed
101 tests, and the complete contextual closure passed in 43.04s.  One old
synthetic test expected a positive fallback count; the now-complete scalar
encoder represents all 21 instructions in that case, so the ratchet was
correctly strengthened to require zero fallback.

## Decisive pcc1 result

One adjacent v54 -> v55 pair used the identical frozen
`module_79.direct.pidx` and produced the identical PCO SHA-256
`0ead76f72c90f479810e9c504de5f1b8021cddee7e3612ec983cf94916c19fb0`:

```text
metric                    v54 control          v55 candidate       C/B
wall                      18.16s               18.54s               1.021
user+system               18.03s               18.49s               1.026
instructions              274,640,101,356      281,336,836,737      1.024
cycles                     60,065,779,753       61,548,705,496      1.025
process-tree RSS           1,680,932,864 B      1,859,076,096 B      1.106
peak footprint             1,663,141,520 B      1,836,549,896 B      1.104
```

The candidate is slower and uses materially more memory.  Native phase
counters locate the regression at transport completion: RSS rises from the
v54 1,173MB to 1,357MB and mapped allocator capacity from 1,143MB to 1,328MB.
Scanning/encoding separately per function creates more allocator size-class
churn than the single module scan; early string replacement cannot return
those partially occupied object slabs.

Explicit `del module` changed no live/RSS counter.  Releasing transport fields
after assembly removed only about 13MB live and 9MB RSS, an under-1% ceiling.
Those lifetime-only edits therefore do not justify another build.

## Disposition and refined owner

The per-function publisher, placeholder mode, label-offset API extension and
lifetime-only deletes were forward-removed.  The current `pcc/` source is
byte-for-byte equal to the accepted v54 source snapshot; no git restore/reset
was used.  The zero-fallback test strengthening remains because it reflects
the already accepted complete encoder.

A host inventory of the real structured `py_ast` transport contains 268,909
chunks / 270,571 physical lines: 209,308 blank structured-instruction slots,
53,656 labels, and only about 7,600 remaining directives/metadata lines.  The
1.17GB transport high-water is therefore not primarily retained output text;
it is transient backend analysis/emission object churn.  The next proposal
must either eliminate that construction at its indexed producer or prove that
a quiescent GC release makes the freed capacity reusable before assembly.  Do
not retry per-function post-scanning.
