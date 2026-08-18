# Deterministic block-range parallel emission denied

## Selected boundary

The complete accepted-No.89 item311 profile contains 10,627 on-CPU samples
from pcc1 `b0c6844f...` and starts 67ms after launch. Dense block emission is
1,575 samples (14.8%). Inventory proves the critical 5.1MB shard is one
function with 9,474 blocks and 59,984 instructions, so function-level
parallelism has zero grain. No.91 tested deterministic contiguous block ranges
after stackmap/regalloc/layout freeze.

## Correctness work

Host tests proved stable weighted coverage, four actual thread owners, exact
serial/parallel small-IR output, exact item311 output across three parallel
repeats, deterministic failure ownership and bounded outer-worker propagation.
Relevant focused packets passed 21, 45, 12 and 42 nodes. Host item311 was exact
`ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`.

The first frozen build produced libSystem-only pcc1 `d07e6e11...` in 287.48s
but failed its mandatory startup smoke because importing the shared parallel
module eagerly imported unsupported `mmap`. That failed source/binary/manifest
is retained. Moving only the optional file-backed mmap import to its owner
made the exact-three-file v2 build and smoke succeed:

```text
pcc1                 5d10244aa8fd6409cc1f598e49a4b9546bbcf59ba64bd5dd63156529b525b574
host                 CPython 3.15.0rc1
runtime              624e1de9... / GC0
mode                 self / no-libpython / libSystem-only
Stage1               311.75s wall / 1124.16 CPU / 178.251B instructions
```

## pcc1 verdict

`PCC_SELF_BACKEND_BLOCK_EMIT_JOBS=auto` fails before assembly with
`failed to start a Mach-O link worker`. More importantly, the shipped
pcc-Python threading runtime explicitly executes Thread targets synchronously;
it has no real pthread dispatch in the default archive. Repairing a constructor
shape cannot provide parallel speedup.

The forced-serial candidate emits exact assembly but also fails its independent
overhead gate:

```text
                             No.89 warmup      candidate off       C/B
instructions                  193.917B          204.101B         1.0525
peak footprint                  1.241GB            1.257GB        1.0125
assembly                      ff943e10...       ff943e10...        exact
registered serial instruction ceiling                            1.0100
```

## Verdict

`[DENIED]`. No third build, formal pair or Stage2 ran. Enabling a genuinely
threaded pcc-Python runtime would be a separate runtime/GC ownership task. The
three production files are byte-identical to accepted No.89 after forward
rollback; GC1--4 remain deferred by the human's ordered gate.
