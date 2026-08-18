# No.97 postmortem and No.98 shared vthread-analysis proposal

## No.97 mechanism

The dense kind was present and correct. LLDB stopped at the first candidate
`Assign` dispatch and read physical slot 1 as `0x2d`, the tagged-small-int
encoding of kind 22. The failure was cost, not activation.

Each dispatch called a 164-byte helper which entered a GC frame, loaded the
borrowed node, read one Python object field, unboxed it with `py_int_to_i64`,
and left the frame. Candidate caller profiling versus No.89 showed:

```text
                                  No.89             No.97
samples                           12,217            12,592
class/MRO leaves              1,136 (9.30%)     1,201 (9.54%)
granule leaves                1,218 (9.97%)     1,225 (9.73%)
AST decode                      670 (5.48%)       772 (6.13%)
stmt dispatch                 7,674 (62.81%)    7,703 (61.17%)
expr dispatch                 5,018 (41.07%)    4,994 (39.66%)
```

The proposal replaced MRO work with boxed-field/GC/unbox work and increased
construction cost. This is the exact architectural boundary: dense metadata
inside the generic Python object projection is not a native data plane.

Two adjacent ideas are denied without code:

- exact-class `py_isinstance` already has a class-pointer early return;
- moving `_never_gc_object_values` to per-Value flags would add initialization
  to 468,643 real Value constructions for only 58,297 queries / 658 hits, an
  8:1 reverse cost ratio before any field-read cost.

Artifacts:

- `build/no97-frontend-worker0-profile-v1/worker.folded`
- `build/no97-frontend-worker0-kind-debug-v1/`
- `build/no98-host-provenance-count-v1/`
- `build/no98-host-value-construction-count-v1/`

## Proposal No.98 — one shared post-hoist vthread analysis kernel

The next measured duplicated group is wholly inside one semantic analysis:

```text
compute_vthread_may_park_callables     455 / 12,217 = 3.72%
classify_vthread_park_boundaries       439 / 12,217 = 3.59%
_function_scope_nodes                  296 / 12,217 = 2.42%
_function_vthread_bindings             467 / 12,217 = 3.82%
```

The two top-level passes run consecutively after closure hoisting and rebuild
the same function/method lists, scope-node lists, lexical binding states,
attribute calls, local class hints, class tables, import aliases and proof
cache. Host instrumentation on frozen `pcc.cli_bootstrap` measured 92.4ms for
compute and 88.9ms for classify, with 11 hoisted functions and final effect
sizes `(0, 0, 0, 0)` / zero rejects. The hoists prohibit an unsafe “no imports,
skip everything” shortcut; sharing the post-hoist evidence is the exact route.

No.98 adds an optional ephemeral analysis cache. Compute fills it once and
publishes an exact-identity, one-shot handoff inside the vthread-analysis
module; the immediately following classify consumes and clears it only when
the module/native-export identities match. Explicit-cache public callers keep
their existing opt-in shape, and callers which do not run the compute→classify
pair still recompute. No AST, export, wire, runtime, ABI, effect rule or
diagnostic changes.

Pre-registered gates before Stage2:

1. a focused differential runs shared and independent compute+classify on
   nested functions, methods, vthread aliases, threading primitives, imported
   may-park callables, shadowing and unresolved receivers; all four effect sets
   and the rejection map must be equal;
2. instrumented tests prove shared mode calls `_function_scope_nodes` once per
   callable while independent mode calls it twice; module/export identity
   mismatch must fail closed to recomputation;
3. frozen host frontend IR and item311 assembly stay byte-identical; strict
   no-libpython closures and the vthread/generator packet pass;
4. one source-frozen pcc1 may then compare shared mode with an explicit tested
   force-unshared diagnostic control in B/C, C/B, B/C order. All outputs must
   be exact; retain only if median wall and CPU B/C are at least 1.025x,
   candidate instructions are at most 0.98x, and footprint is at most 1.02x.

No Stage2, Stage3 or GC1--4 run before that verdict.

### First build invalidated before measurement

The first pcc1 build used a new cross-module combined-analysis entrypoint.
Stage1 succeeded, but the first worker canary failed before function emission:
the new call lowered through `py_cpy_from_pcc_obj(self)`, so strict mode
replaced all of `GenerationLoweringMixin._generate_impl` with an unavailable
stub. No A/B or Stage2 ran.

The corrected v2 restores `generation_lowering.py` byte-for-byte to No.89 and
keeps the sharing entirely inside the two existing vthread compute/classify
APIs via the identity-bound handoff above. Revised gates pass: 29/29 vthread,
real strict bodies for both changed functions, exact shared/unshared/No.89 host
IR, and exact item311. The v2 frozen source differs from No.89 only in
`vthread_effect_analysis.py` plus the two separately proven generic dataclass
correctness files.
