# Quiescent decoded-module GC `[DENIED]`

## Proposal

After PIDX transport publication, retain only the four transport fields,
release the decoded module and transport shell, and run one GC0 collection at
that quiescent boundary before constructing assembler Sections.  The same v56
pcc1 binary exposed exact `0`/`1` controls so no source or binary difference
confounded the result.

## Result

Both modes consumed the identical frozen `module_79.direct.pidx`, produced
PCO SHA-256 `0ead76f72c90f479810e9c504de5f1b8021cddee7e3612ec983cf94916c19fb0`,
and stayed below the 4 GiB worker circuit breaker:

```text
metric                    GC off               GC on             on/off
wall                      18.03s               18.36s               1.018
user+system               17.99s               18.32s               1.018
instructions              274,347,573,678      275,911,480,263      1.006
cycles                     59,980,070,098       61,008,429,638      1.017
process-tree RSS           1,684,783,104 B      1,683,456,000 B      0.999
peak footprint             1,663,108,752 B      1,663,125,112 B      1.000
```

The native allocator counters are decisive: immediately before and after
`gc.collect()`, live bytes remain about 555,214,000 and mapped capacity remains
1,143,209,984 bytes.  Assembly and encode endpoints are likewise unchanged.
The collection does no useful reclamation and only adds compute.

## Disposition

The GC knob, collection, extracted-field ownership path and tests were
forward-removed.  Current compiler source again equals accepted v54; the v56
binary is retained only as negative evidence.  Do not retry `del` or
quiescent collection at this boundary.  The next diagnostic must inspect the
actual indexed emitter allocation/call paths and eliminate construction, not
attempt to collect still-live or partially occupied object slabs.
