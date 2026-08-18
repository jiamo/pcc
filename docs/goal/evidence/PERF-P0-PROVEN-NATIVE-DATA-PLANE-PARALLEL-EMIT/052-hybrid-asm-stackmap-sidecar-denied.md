# Hybrid ASM plus packed stack-map sidecar `[DENIED]`

## Proposal

For the 31 large ASM-lane modules, retain textual code/data but move the
stack-map section into a packed sidecar.  On `cli_bootstrap` this shrinks ASM
61.1->39.9MB and 2.82M->1.34M physical lines; the sidecar payload is 5.32MB.

## Same-pcc1 measurement

v59 contains both artifact modes.  The identical frozen module1 PIDX was run
under the performance lock and 8 GiB breaker:

```text
metric                   ASM control          hybrid candidate
emit wall                35.74s               38.00s
emit CPU                 32.39s               37.12s
instructions             424.459B             521.499B
tree RSS                   4.558GB              4.472GB

host assembly wall         7.94s                6.05s
host assembly CPU          7.73s                5.98s
combined wall             43.68s               44.05s
combined CPU              40.12s               43.10s
```

Timestamped native markers show hybrid transport itself completes in 25.99s,
but generic PCO encoding/final validation costs another 4.19s.  The 1.89s
host-assembler saving does not recover this cost, and the split would add a
second decode/link input.  The candidate misses both wall and CPU gates; its
about 2% RSS reduction is insufficient.

## Disposition

`ASM_STACKMAP`, undefined-only native-object acceptance, and their tests were
forward-removed.  Current pcc source again matches the accepted v58 snapshot;
v59 is retained only as negative evidence.  Do not implement the deferred
pair-publication protocol for this codec shape.  A future compact sidecar
would need an independently measured encoding path that removes, rather than
adds, the 4.19s producer validation cost.

