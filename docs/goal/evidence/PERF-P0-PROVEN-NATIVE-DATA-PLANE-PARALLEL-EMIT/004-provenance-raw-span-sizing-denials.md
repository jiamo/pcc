# Provenance and raw-span sizing denials

## Claim boundary

This is source-free sizing against the accepted No.72 pcc1/profile. It closes
three tempting implementation branches before another Stage1 build. It does
not change GC semantics, the runtime ABI, parser source, or the native data
plane.

## Global managed-refcount bypass remains denied

The accepted item311 profile attributes 7.99% to the complete granule
object-start predicate, spread across ordinary dict/list/regex/object
operations. Exact `CompilerIntArena` raw loads/stores already use
`pcc.unsafe` and do not ask object provenance.

A GC0-only upper-bound micro kept one list's original owner alive and compared
500,000 checked `pcc_gc_retain/release` pairs with direct refcount primitives:

```text
checked path     828,814,010 instructions
direct bound     623,081,406 instructions
ratio                  1.330x
```

The direct arm deliberately omits forwarding, finalizer, immortality,
underflow, logging, and GC3/4 semantics, so it is an impossible production
upper bound. Applying its 25% instruction reduction even to the whole 7.99%
granule share cannot be the Stage2 factor. No ABI/source change was made. The
old container-wide `py_incref_managed/py_decref_managed` crash remains the
authoritative denial for any unproven Dyn/container slot.

## Raw text span does not replace the bulk string runtime

Equal-output programs scanned 4,440,000 bytes and reported
`4380000 / 60000`:

```text
implementation                         wall    instructions   footprint
native str.splitlines + len            0.02s     0.330B        17.96MB
semantic-int raw byte loop             0.39s     5.639B         6.54MB
freestanding pcc.i64 raw byte kernel   0.29s     4.342B         6.72MB
same kernel with default IR tier       0.29s     4.342B         6.60MB
```

The freestanding kernel is a host-pcc0-generated self-backend oracle, not a
pcc1 claim. Several earlier single-file attempts produced no executable due to
correct raw-type/import diagnostics and are excluded. The valid result proves
that a Python-authored per-byte loop is 13x more instruction-heavy than the
existing bulk runtime helper. A parser may still need a future dedicated bulk
intrinsic, but an intermediate raw span/arena plus semantic second decode would
also repeat the already-denied V22/V25 parser scratch shape. No production
change is supported.

## Current liveness batch helpers are not the owner

The accepted caller flamegraph attributes all `CompilerIntArena` frames to
1.50% inclusive. The proposed bulk methods are individually tiny:

```text
converge_liveness_row_unchecked   0.09%
or_prefix_from_unchecked          0.09%
zero_prefix_unchecked             0.04%
copy_prefix_from_unchecked        0.01%
append_state_words                0.23%
```

Moving these methods alone to new intrinsics cannot reach a 5% worker line.
The larger root-state plane is 5.56% total and requires an end-to-end fused
state/transition/location design; per-word/raw-getter variants remain denied
by V85.

## Next boundary

Retain accepted No.72 exactly and capture one complete source-frozen cache-off
Stage2. Use its phase/CPU/process-tree receipts to choose a lifecycle or
dataflow owner with a whole-stage ceiling, not another local leaf.

