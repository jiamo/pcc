# No.101 recordless direct instruction order `[DENIED]`

## Claim boundary

The candidate replaced every hot direct/no-text `InstructionRecord` and empty
text entry with one tagged-small-int block-order token carrying final record
and opcode IDs.  Canonical text and unsupported records retained their object
projection.  This tested complete removal of the 329,129-record projection;
it was not the already-denied No.100 slots/lazy-metadata layout.

## Correctness and pcc1 diagnosis

Host direct/kernel/inventory, exact assembly, insertion, terminator, phi,
switch, flag, cast-family, single/multi binary, valueclass, ownership and
10-module contextual gates passed.  The first two Stage1 compilers exposed two
pcc1-only uses of `isinstance(Dyn, int)` that misclassified a tagged token and
then accessed `_direct_record_id` on it.  LLDB localized them independently to
`IRBuilder._bind_direct_record` and `_phi_add_incoming_canonical`; both were
replaced by the already-known direct/no-text projection flag.  The third
source-frozen Stage1 passed the strong function canary (`42`), linked only
libSystem and retired 92.695B instructions in 206.55s.

## Measurements

The clean host prefilter preserved exact `72e2f21a...` assembly and improved
17.27s -> 15.77s, 247.643B -> 239.672B instructions, and 886.97MB ->
806.63MB footprint.  That host result did not transfer to pcc1:

```text
metric                    v13 control       recordless candidate    C / B
wall                         61.61s                66.65s            1.0818
CPU                          61.54s                64.41s            1.0466
instructions                857.478B              860.415B           1.0034
cycles                      207.595B              212.137B           1.0219
peak footprint                6.491GB                6.431GB          0.9907
assembly                    8a1dd249...            8a1dd249...        exact
```

The candidate misses every registered pcc1 transfer requirement: wall/CPU do
not improve by 1.10x, instructions regress, and footprint is nowhere near
0.90x.  No Stage2 or Stage3 ran.  The ten production files and candidate-only
tests were forward-restored byte-for-byte to accepted v13.

## Interpretation

CPython benefits from deleting the record objects, but pcc1 ordinary internal
classes already have fixed physical layouts.  The replacement still pays a
Python list/order publication interface, while pcc's allocator high water does
not fall with shorter object lifetime.  A future attempt needs final-order
publication directly into one native arena, without a per-record Python method
or list-token interface; another object-shell or list-of-tagged-IDs variant is
denied.

Artifacts:

- `build/no101-recordless-host-control/`
- `build/no101-recordless-host-candidate-r2/`
- `build/no101-recordless-host-candidate-r3/`
- `build/no101-recordless-stage1-candidate-v16-r1/`
- `build/no101-recordless-stage1-candidate-v16-r2/`
- `build/no101-recordless-stage1-candidate-v16-r3/`
- `build/no101-recordless-pcc1-module1/`

Accepted timings remain Stage1 212.18s and Stage2 364.616s compile / 380.931s
total.  Stage3 and GC1--4 were not run.
