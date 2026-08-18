# Accepted No.89 frontend codegen worker profile

## Frozen replay

- Stage2 artifact: worker 0 / module 1 / `pcc.cli_bootstrap`;
- input: 14,287,979-byte AST wire plus the exact native-exports/module table;
- compiler: No.89 pcc1 `b0c6844f...`, GC0, no host Python;
- warmup: 15.376s, one process, 2,322,137,088B peak RSS;
- output: 19,279,474-byte IR, exact Stage2 SHA
  `065100ba25f24b5ef5d423b4ed6246058e5d1f4fe7f1d152c1f4176f574a77a7`;
- profile: 12,217 samples, exact sampled binary, worker/flame rc0.

## Owner

`L1CodeGen.generate` owns 87.0% and `_generate_impl` 82.0%. User-function
emission owns 54.1%; stmt dispatch is about 63% inclusive and expr dispatch
41%. Module top-init is 11.8%, vthread analysis 7.3% and hoist 4.1%. AST and
export decode together are 10.3%, type inference 1.6%, and final IR render 5%.

Frontend leaf aggregation attributes 9.97% to granule lookup, 6.21% to GC
load/store and 9.30% to `strs_eq + class_lookup_in_mro`. Item311 emit shares
the generic GC/provenance tax but not the class/MRO owner.

## Claim boundary

This is a caller-attributed, exact-output frontend worker profile. It supports
auditing a closed-world AST dense-kind projection across whole stmt+expr
dispatch. It does not authorize global class/GC bypass, one-branch micro fixes,
Stage3 or GC1--4.

Artifacts:

- `build/no89-frontend-worker0-replay-v1/warmup/process-tree-result.json`
- `build/no89-frontend-worker0-replay-v1/profile/worker.folded`
- `build/no89-frontend-worker0-replay-v1/profile/worker.svg`
- `build/no89-frontend-worker0-replay-v1/profile/flame.stdout`
- `build/no89-frontend-worker0-replay-v1/profile/worker.tsv`
- `build/no89-frontend-worker0-replay-v1/profile/ir/module_1.ll`
