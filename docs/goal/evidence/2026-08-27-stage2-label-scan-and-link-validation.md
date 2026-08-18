# Stage2 target-label scan and owned-link validation evidence

Status: paused at the human's request after the current measurement round.
`PERF-P0-STAGE2-COLD-CACHE-REGRESSION` remains `IN_PROGRESS`; this file does
not claim the <=600 s exit criterion.

## Current valid fixed point before the retained link-validator change

Proposal No.67 added an emitter-owned ordinary-instruction fast path to
`_aarch64_text_label_offsets`.  Normal target instructions add four bytes
without allocating a stripped copy or entering directive parsing; labels,
directives, comments and data regions retain the prior path.

The frozen item302 pcc1 A/B was byte-identical at wall 1.06781x, CPU 1.06304x,
instructions 0.94217x, cycles 0.93990x and footprint 0.99552x.  The subsequent
verified-empty isolated GC0/self/no-libpython build completed:

```text
Stage2 wall                         686.160 s
compiler profile                    681.246 s
frontend codegen                    134.700 s
native emit                         373.081 s
  oversized workers                 61.729 s
  safe workers                     296.913 s
pcc-owned link driver               121.440 s
Stage3 wall                         240.840 s
pcc1 SHA-256  f1526b0262cd17fe9c289d02f0c134f168b17b01606897bc5050937dcdd85f5b
pcc2 SHA-256  a18940193db4f3628bf620e6e19e39f7e1d4d715d188da27ab33f7437948f34d
pcc3 SHA-256  a18940193db4f3628bf620e6e19e39f7e1d4d715d188da27ab33f7437948f34d
```

The workload remained 212 modules / 464 native objects / seven oversized /
457 safe.  Relative to the preceding valid runtime-pass build (890.433 s),
the complete Stage2 improved by 203.590 s; native emit improved by 157.996 s,
frontend by 31.050 s and link by 11.800 s.  pcc2/pcc3 are byte-identical.

## Link localization and No.70 verdict

The exact 464 cached Stage2 assembly inputs total 1,011,914,799 bytes.  A fresh
owned-link replay took 122.69 s and published all 464 `.pco` payloads within
18 seconds; the final image followed about 85 seconds later.  A control with
incremental state disabled took 113.20 s and produced identical bytes, so
incremental keying/persistence was denied as the owner (only 1.084x).

Coordinator cProfile, with assembly excluded, attributed the two dominant
groups to 464 public NativeObject decode/validation calls and final merged
stack-map materialization/validation.  The final validation-only call was
replaced by a raw v2 wire validator which memoizes shared location slices by
`(location_index, location_count, frame_size)` while retaining every semantic
check.  On the real 89,480,328-byte pcc2 table:

```text
materializing decoder   31.585 s, 31.752 s
raw final validator      2.437 s,  2.433 s, 2.432 s
```

The broader trusted NativeObject transport was denied after three alternating
pairs: median wall 1.85265x and RSS ratio 0.84587, but median CPU only 1.44138x
against the pre-registered 1.50 requirement.  All six outputs had SHA-256
`baac72710663dfd1e77a8184df92472c66e762dde480886a9c6e0c93a25caf00`
and ran `--help`.  The private seam and its validation shortcut were removed;
only the independently proven raw final validator remains.

Post-reversion focused command:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_object_fastpath.py \
  tests/python/test_macho_incremental_link.py \
  tests/python/test_self_link_argument_contract.py \
  tests/python/test_precise_stackmap_abi.py
83 passed in 0.55 s
```

Closure classification: current `precise_stackmap.py` and
`macho_assemble_worker.py` pass strict no-libpython emit.  `native_object.py`
and `macho_exec.py` fail their standalone closure checks, but the frozen
pre-No.70 source files fail with the exact same diagnostics, so those are
pre-existing closure gaps rather than regressions from this slice.

## Honest open boundary at pause

The last full current-source Stage2 evidence is still 686.160 s, 86.160 s
above target.  The retained raw final validator has not yet been transferred
through a new Stage1/Stage2 build, so no complete-stage speedup is claimed.
On resume, first re-read Update No.70 and this evidence.  Do not restore the
denied private NativeObject transport, do not attribute the invalid stale-
archive 793.029 s run, and do not rerun broad gates before a new focused
mechanism is accepted.  The current candidate profile points to the host
assembler operand parser as a separate possible investigation, not as an
accepted continuation of No.70.
