# 034 — explicit pre-emit frontend-cycle teardown denied

Date: 2026-09-04

## Proposal

The real `py_ast` PCO caller profile puts roughly 20% of samples in GC0
mark/subtract work. The direct indexed module is frozen before backend emit,
but the worker's first explicit `gc.collect()` ran while caller locals still
retained AST, typed-module, native-export and codegen shells. The candidate:

- explicitly broke Function/Block and CodeGen/ClassLowering cycles;
- cleared module metadata, native exports and string pools;
- dropped singleton-worker AST/export/codegen locals before backend emit;
- moved the first collection after those drops;
- retained the second post-direct-module collection and every output/GC
  semantic boundary.

The intended ceiling was the measured roughly 20% collector share. No user
Python object semantics or collector policy changed.

## Correctness transfer

- focused frontend lifecycle/worker/IR packet: 103 passed;
- direct indexed kernel packet: 15 passed in 46.44s;
- strict `pipeline_frontend_worker_execution.py` no-libpython closure: real
  `_release_direct_frontend_state` and `run_codegen_worker` definitions, zero
  `py_cpy_*` and no strict stub;
- source-frozen v17 pcc1 `9c15b793...` built rc0, links only libSystem and its
  function canary prints 42.

The source-only lifecycle regression was retained in a narrower form after the
candidate removal: it now pins the existing safe contract that top-level
frontend owners are cleared and exactly one collection occurs inside the
release helper.

## Current-pcc1 worker result

Both arms consume the exact same v14 `worker_156` manifest, AST and full export
wire and publish byte-identical PCO SHA-256
`9987edea18e5fde14fd279cfdbfdfefc429eb9bddbae99122102702f4f13d3c0`.

```text
metric                 v14 control          v17 candidate       control/candidate
wall                   41.87s               38.59s               1.085x
user CPU               38.17s               37.29s               1.024x
instructions           561.547658B          561.388153B          1.00028x
max RSS                5,253,480,448 B      5,253,578,752 B      0.99998x
peak footprint         5,209,510,016 B      5,209,526,400 B      1.00000x
```

The wall observation favours the candidate, but deterministic instructions
improve only 0.028%, CPU only 2.4%, and memory is unchanged. The proposed
collector-work deletion therefore did not occur at useful scale; explicit
teardown work merely replaced the collector work. A second noisy wall pair is
not needed to reject that mechanism.

## Verdict

`[DENIED]`. Production lifecycle code was restored by a narrow forward patch,
and the current lifecycle test is green. No Stage2 ran. Do not retry enumerating
more cycle fields in the same helper without evidence for a different bulk
ownership mechanism; field-by-field teardown has a measured ~1.0x instruction
result.
