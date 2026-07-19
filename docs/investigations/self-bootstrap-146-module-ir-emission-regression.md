# Investigation: self bootstrap closure and IR growth make one GC gate take tens of minutes

## Status

resolved

## Problem Description

The user reported that `full_gc0` historically completed in seconds but the
current gate spends many minutes in each bootstrap stage and asked that the
regression be optimized rather than hidden behind longer timeouts. Current
profiles confirm a real compiler-performance regression: the stage2 closure
grew from 112 modules and about 84 MB of IR in the 2026-06-02 GC0 profile to
146 modules and about 180 MB in current runs. Native self-emission dominates;
current stage2/stage3 profiles spend 1056--1628 seconds there, with one GC1
stage3 sample at 5287 seconds.

This is not explained by the small final linker or by GC semantics. The native
emitter schedules IR inputs at least 4 MB in size in batches of at most two,
so closure/IR growth creates many long two-worker batches even when the gate
grants twelve self-backend jobs.

## Repro

```text
gtimeout 7200s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py
```

The current-source run rebuilt stage1 for about 9.5 minutes. Stage2 then
continued for more than 8 minutes, with repeated two-worker huge-module emit
batches, before it was deliberately interrupted to avoid spending another
tens of minutes without first diagnosing the regression. All children were
reaped after interruption.

Reference/current profile comparison:

```text
2026-06-02 GC0 stage2: 112 modules, 83,872,536 IR bytes, 17.029s total
2026-07-14 GC1 stage2: 146 modules, 179,568,660 IR bytes, 1182.849s total
2026-07-14 GC4 stage2: 146 modules, 179,568,660 IR bytes, 1492.481s total
```

## Test [CONFIRMED]

The regression is confirmed by checked profile JSON and direct process
observation. The closing gate must retain pcc1-to-pcc2-to-pcc3 normalized
identity and no-libpython/self-backend mode while bringing a single GC0 gate
back to a finite performance envelope. A run interrupted without a pytest
summary is diagnostic evidence only, never a passing gate.

## Proposals

- No.1 Attribute and remove unintended closure/IR expansion [CONFIRMED]
- No.2 Improve huge-module self-emitter scheduling after IR shape is understood [CONFIRMED]
- No.3 Remove self-backend linear scans from known-value hot paths [CONFIRMED]
- No.4 Compile native regular-expression programs once [CONFIRMED]

## No.1 Attribute and remove unintended closure/IR expansion

### Code Change

Emit the current pcc1 closure as combined named LLVM IR without native object
emission. Rank modules by byte size and compare the 146-module current closure
against the 112-module profiled reference contract and current import routing.
Remove only unintended closure edges or duplicated scaffold output; do not
exclude runtime/compiler modules that are semantically reachable.

### CONFIRMED

The retained 2026-06-02 pcc2 and the current pcc2 expose exactly 112 and 146
distinct `__pcc_py_module_fini_*` symbols. Their set difference has 34
additions and zero removals: the first-class `pcc.backend.self_backend*`
closure plus `pcc.cli_bootstrap_array_core`, `pcc.package_schema`, and
`pcc.py_frontend.codegen.builtin_exceptions`. These are semantically reachable
compiler/backend modules, not accidental package imports, so no closure edge
was removed.

A current pcc1 emit-only artifact contains 146 named module sections and
179,766,851 module IR bytes. The 34 additions account for 26,976,531 bytes;
the retained 112 modules account for 152,790,320 bytes. The full current
per-module byte table and artifact SHA-256 are checked in
`docs/goal/evidence/2026-07-14-m1-self-bootstrap-perf-regression.md`.

## No.2 Improve huge-module self-emitter scheduling after IR shape is understood

### Code Change

If the larger closure is semantically required, replace the fixed
`min(2, configured_jobs)` huge-module batching rule with a measured
memory-budgeted scheduler or split the dominant IR modules at safe function
boundaries. Preserve deterministic object ordering and use the same job budget
for pcc2 and pcc3.

### CONFIRMED

Large compiled-stage modules are now split by a short-lived pcc1 splitter
worker that writes a validated `pcc.self_backend.split.v1` manifest. This
keeps the parent from retaining splitter state and produces 43 shards from
nine large modules in the formal profile. Source-hosted stage1 now uses
`python -m pcc` emitter workers when the task set is large enough; tiny inputs
remain in-process.

The first compiled splitter exposed a separate quadratic bug:
`_rename_llvm_global_refs` appended one character at a time. Accumulating
replacement slices and joining once changed a 14.4 MB pcc1 split from 66
seconds / about 22 GB RSS to 1.38 seconds / about 283 MB RSS.

The existing size-class scheduler was retained. A controlled rolling-queue
replacement took 280.62 seconds for the same 180 tasks versus 280.18 seconds
for the existing batches, denying batch barriers as the dominant cause.

## No.3 Remove self-backend linear scans from known-value hot paths

### Code Change

`materialize_value` used the false-hash compatibility lookup before
classifying constants and globals. On the largest generated function, 45,580
real slot hits were mixed with 18,597 globals and 10,609 literals; each known
nonlocal scanned a 22,209-entry slot mapping. Take normal O(1) mapping hits
first, classify constants/globals next, and reserve the linear compatibility
recovery for otherwise unknown locals. Bucket stack-slot used-value lookup by
a deterministic text key while retaining equality checks inside a bucket.

### CONFIRMED

The worst source-mode module fell from 106.30 seconds to 3.44 seconds. The
full source stage1 ultimately completed in 57.742 seconds, down from observed
597-700+ second runs. Focused false-hash regressions and the 277-test self
backend suite pass.

## No.4 Compile native regular-expression programs once

### Code Change

The native `re.Pattern` object captured only `(pattern, kind, flags)`, and
every `.match`/`.search` called `re_compile`; Match construction compiled the
same pattern again to recover group names. Add a bounded 64-entry,
process-wide compiled `ReProg` cache in the standalone C engine. Entries are
append-only and immutable after publication; a C11 atomic lock covers only
lookup/first compilation, while matching runs unlocked. A full cache or
allocation failure uses the original scratch compile path.

### CONFIRMED

The differential regression proves one compile across 100 repeated matches.
All 289 regex differential tests pass, as does a strict self/no-libpython
native Pattern object gate. A same-input 4,686,078-byte emitter worker fell
from 31.00 seconds to 7.83 seconds and produced a byte-identical object.

The formal GC0 profile records 69.721 seconds of stage2 native emission and
69.765 seconds of stage3 native emission, versus 1,055.915 seconds in the
pre-fix current stage2 profile. Stage2/stage3 wall times are 142.936 and
142.930 seconds. The required full gate passes in 287.69 seconds after shared
stage1 and normalized pcc2/pcc3 are byte-identical.

## Closing gates

```text
tests/python/gc/test_pcc_bootstrap_full_gc0.py
1 passed in 287.69s

tests/python/test_re_engine_differential.py
289 passed in 2.45s

tests/c/test_self_backend.py
277 passed

tests/python/test_fallback_baseline.py
20 passed in 225.26s

tests/python/test_ir_py_fallback_baseline.py
3 passed in 1.00s

tests/python/test_py_multi_file_bootstrap_shim.py
86 passed in 307.08s

second synthetic PEP489 strict pcc1 package regression
1 passed in 8.35s
```

No full GCC validation was run.
