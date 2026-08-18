# Native function and block producer streaming

Status: function/block list removal qualified; parent remains IN_PROGRESS.
Task: PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE.
Date: 2026-09-05.
Predecessor: [062](062-native-module-producer.md).

## Implemented boundary

`_emit_function` and `_emit_dense_indexed_function_blocks` now append to the
native scope on unoptimized PCO and return empty list adapters. They no longer
accumulate instruction-bearing function or block output lists. Ordinary ASM
calls retain their prior list path. Seven block append/extend sites and three
packed stack-map append sites were counted and converted within that exact
enclosing function. Packed reloads and cold stubs keep their established order.

Barrier depth/terminal checks were factored out of the existing memory-pair pass
and shared by the native scope. The streaming path removes the pseudo-markers
without weakening volatile/atomic boundaries or maintaining different error
rules. Three unbalanced-marker cases reproduce exact legacy diagnostics.

Helper return lists, their placeholder slots, empty adapter construction,
compact-unwind data lists, residual text, ASM publication and verifier/CFG/
def-use families remain open. This is not full buffer or task closure.

## Evidence before native build

The expanded three-function regression failed before implementation (1 failed
in 0.16s), showing no native sink passed into `_emit_function`. It now observes
three empty native function results and three empty native block results while
requiring exact complete Sections and capture bounds. The nested-emission test
continues to exercise both modes with the new optional sink forwarded.

Focused packet: 242 passed, 1 contextual gate deselected in 3.97s, covering
structured encoding, directive driver, direct indexed kernel, precise stack
maps, cold paths, target passes, and both inventories. Contextual gate:
1 passed in 46.87s, `build/native-function-producer-context.log`; it checks
native aggregate calls and unavailable stubs. The native canary now includes
`fence seq_cst` so the marker path executes in the compiled worker.

## Source and frozen gate

Source SHA256:
`d1deab1741d43e74620cde095ba91d05fccc331a499cc3cbfa53918675b0cd3d`.
Snapshot: `/private/tmp/pcc-native-function-producer-v74`.
Readiness: `build/native-function-producer-v74-readiness.json`.

v74 uses v73's immutable runtime bundle, GC0/threads-off, 7 host workers,
2 self-backend workers and 8 link workers. Expected Stage1 is 165–205s;
watchdogs are 360/410/440s for build/tree/shell. The shared performance-lock
sampler applies an 8GiB tree-RSS breaker and 2GiB launch reserve.
Artifacts: `build/native-function-producer-v74-build-guard/` and
`build/native-function-producer-stage1-v74/`.

Require actual native ASM/PCO, HFA/cold-landing checks and retained py_ast/CLI
comparisons before qualifying this slice. No current-source Stage2, fixed
point or GC1..4 claim exists.

## Native correctness observations

v74 is SUCCEEDED, pcc1 SHA256
`f85cbb2c15011ffe162f9501db1d42c127312807b20b33de1764b35f22932651`.
Stage1 is 166.08s / 685.65 tree CPU seconds. Its outer guard is COMPLETE/rc=0
at 178.59s with sampled peak 5,056,659,456B. Linkage is libSystem-only and
the function smoke prints 42.

The first new fence canary failed in host input construction before pcc1 ran:
direct capture cannot currently publish that fence, and partial text fallback
lost the entry terminator. This is recorded independently as
`PY-P1-DIRECT-INDEXED-FENCE-CAPTURE`. The canary now freezes equivalent complete
IR, which is the relevant input boundary for this worker test. The first full-IR
attempt also needed dedenting for the target-triple parser. Neither failed
invocation counts as native evidence, and no compiler rebuild was needed.

The corrected full-IR fence canary and both HFA/cold-landing native tests pass:
3 passed in 0.36s, actual ASM/PCO bytes match the oracle. Artifacts:
`build/native-function-producer-v74-canary-dedented/`.

Adjacent v73/v74 py_ast PCO replays are COMPLETE/rc=0 with exact SHA256
`2f0f6fa3e03c655403a28b0976efc8f33d6234c07519898125f0e846f257dd56`.
Wall is 16.02→15.52s, CPU 15.91→15.49s, instructions
245.730→238.551B (-2.92%), sampled tree peak
1,337,606,144→1,120,419,840B, and process maximum RSS
1,351,909,376→1,121,812,480B (-17.02%). This is a material memory improvement
on the retained input, not a Stage2/Stage1 completion claim. Both runs used
the shared lock, a 6GiB tree cap and 60s watchdog. Receipts:
`build/native-function-producer-v74-pyast-{control,candidate}/`.

The adjacent CLI ASM comparison is also exact:
`9811ca4cb92aa9a471743bf845528e7005530b83d8c9af160691c8a44677b8ef`.
Wall is 29.37→29.45s, CPU 29.28→29.39s, instructions
429.345→429.125B (-0.05%), sampled tree peak
4,411,703,296→4,416,765,952B. The small wall/CPU spread has no matching
instruction increase; no speed claim is made for ASM. Both process receipts
are COMPLETE/rc=0 in `build/native-function-producer-v74-cli-{control,candidate}/`.

Native function/block instruction-list removal is qualified for continued
structural work. The helper-list/placeholder family, residual text, ASM normal
path, verifier families and final Stage2/fixed-point gates remain open.
