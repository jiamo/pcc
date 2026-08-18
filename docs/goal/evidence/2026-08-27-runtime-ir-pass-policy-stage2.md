# Independent pcc-Python runtime IR-pass policy

Date: 2026-08-27  
Task: `PERF-P0-STAGE2-COLD-CACHE-REGRESSION`

## Problem and fix

`bootstrap.sh` deliberately sets compiler-module `PCC_PYTHON_IR_PASSES=off`,
but that ambient setting also reached the independently built pcc-Python
runtime archive.  A cold rebuild turned the hot structural-objecthood query
from a 276-byte register-resident body into a 436-byte body with a 112-byte
stack frame.

`PCC_RUNTIME_PYTHON_IR_PASSES` now controls only the pcc-Python runtime make
invocation and defaults to the bounded `default` tier (`mem2reg,sroa`).  An
explicit runtime override such as `off` remains supported.  Stage compiler
modules continue to use `PCC_BOOTSTRAP_PYTHON_IR_PASSES=off`.

Focused policy tests pass 2/2.  The standalone runtime-archive module closure
has a pre-existing `subprocess check=True` failure reproduced on the frozen
pre-change source; aggregate Stage1 is the relevant closure gate.

## Matched module98 A/B

Both ordinary pcc1 compilers use current 32 KiB radix source.  Baseline runtime
passes are off; candidate runtime passes are default.  Frozen module98 SHA-256
is `47289e1d0517d365732a5c9ae7ea67a21b301b3e85c7018ae0b37ffb10030ea`.
Three alternating pairs produced byte-identical assembly:

```text
median wall speedup             1.34880x
median CPU speedup              1.37580x
candidate/base instructions     0.79771
candidate/base cycles           0.72420
candidate/base footprint        0.99920
```

Manifest: `build/runtime-passes-module98-ab-v1/manifest.json`.

The first Stage1 publication barrier observed transient rc134 immediately
after link, but the produced pcc1 subsequently passed `--help`, compiled a
self/no-libpython native program and ran it with output 42.  Its hot query is
the expected 276-byte optimized body.  The transient is not treated as a green
stage result; the later pcc1 -> pcc2 -> pcc3 evidence below proves usability.

## Full cold Stage2 and fixed point

Current pcc1 SHA-256:
`511b9f4e520d14e0916b2f81707bf42b58e2ca3be7ba79a047b5b4936cf8d569`.
It used a verified-empty isolated cache.  Stage2 completed in 890.433 s and
produced pcc2
`c01f0407c57c6fd1c9cb27a2b53831933116a452fae99c7935f3562f04c284dc`.

Compared with the valid current unoptimized-runtime baseline, work counts are
identical: 212 modules, 464 objects, seven oversized and 457 safe.

| phase | runtime passes off | default | delta |
|---|---:|---:|---:|
| Stage2 wall | 1076.793 s | 890.433 s | -186.360 s |
| compiler profile | 1072.209 s | 884.836 s | -187.373 s |
| native emit | 711.927 s | 531.077 s | -180.850 s |
| safe workers | 600.185 s | 444.835 s | -155.350 s |
| oversized workers | 91.261 s | 70.783 s | -20.478 s |
| frontend codegen | 168.235 s | 165.750 s | -2.485 s |
| link driver | 132.223 s | 133.240 s | +1.017 s |

Stage3 completed in 254.571 s; pcc2/pcc3 are byte-identical.

## Gates and boundary

```text
runtime-pass policy tests                  2 passed
granule/provenance/layout, 32KiB nodes    13 passed
bootstrap baseline + IR fallback          10 passed, 2 deselected
fallback baseline                         32 passed in 547.70s
```

Accepted.  This replaces the retracted 793.029 s stale-archive claim with an
explicitly rebuilt, mode-labeled 890.433 s result.  It remains 290.433 s above
the task's 600 s target and does not close Stage2 performance.
