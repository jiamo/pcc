# Exact compiler-arena field and method ABI

Task: `BUG-P0-COMPILER-ARENA-EXACT-FIELD-ABI`

## Source identity

- Accepted frozen source: `d1b50f5990907e939bccf93ef050cd53874f33c9792f0796c8f3649b55ce2715`
- Accepted pcc1: `650169b5028a46cccb1bdb58375737a91489d1dd0621129c2b1e5e728a7ae06f`
- Build receipt: `build/native-data-plane-stage1-candidate-v73-pep604-exact-receiver/build-receipt.json`
- Runtime bundle: the same frozen v44 GC0 pcc-Python archive used by v67/v72
- Representative input: item311 SHA-256
  `76af6689f079d29a5965733c4e7b365c9d4a8ccc16d0ce8a70e21fea6b65468c`

The v72-to-v73 source manifests differ in one semantic compiler source,
`pcc/parse/py_lift.py`, plus the non-executable working runtime provenance JSON
refreshed by the required runtime rebuild.  Both builds consume the identical
frozen runtime archive.

## Changed behavior

1. Closed-world class exports are flattened after re-export convergence to the
   runtime's inherited-first field order in both serial and parallel frontend
   paths.  Field types follow the same name-indexed order.
2. Early extern-class declarations recover their base graph from the frozen
   export table instead of permanently publishing an empty `bases_ast`.
3. Direct method dispatch is rejected when a compatible runtime subclass in
   the closed-world graph overrides that method; ordinary Python override
   behavior remains dynamic.
4. PEP 604 optionals `T | None` / `None | T` now use the existing
   `Optional[T]` projection rule: object classes/containers retain their native
   schema, while nullable unboxed numerics and arbitrary unions remain Dyn.
5. The former v70/v71 114-byte `append4` failure now completes, and the real
   `_call_instr_from_parts` full-closure machine code calls
   `IndexedFunctionSeed.append_parsed_call` directly instead of binding
   `append_parsed_call` through `py_obj_getattr` + `py_obj_call`.

No module/package/arena-name special case, fallback, unsafe global provenance
bypass, or ordinary-class semantic exemption was added.

## Correctness evidence

- `tests/python/test_cross_module_inherited_field_abi.py`: **5 passed in
  7.74s**.  This includes the minimized inherited index 14→21 red, inherited
  base index 6, PEP 604 exact direct dispatch, PEP 604 subclass override, and a
  four-module transitive 27-slot raw-`pcc.unsafe` arena control.
- Annotation/schema packet: **37 passed in 56.35s**.
- Multi-file/class ABI packet: **68 passed in 114.02s**.
- Indexed call/stackmap/root packet: **54 passed in 0.28s**.
- Strict self/no-libpython closure passed for the five v71 backend modules and
  for `pcc/parse/py_lift.py`; the contextual `class_gen` and method-call
  closure gates passed **2/2 in 45.11s**.
- Current-source compiled repo-main canary: **1 passed in 126.99s**.
- Bootstrap baseline: **2 passed, 2 deselected in 0.82s**.
- Fallback baseline was intentionally sharded to fit measured watchdogs; every
  shard has a final pytest summary: **17/17 in 34.88s**, **6/6 in 259.03s**,
  **2/2 in 33.48s**, **1/1 in 217.03s**, **2/2 in 13.19s**, **1/1 in
  246.04s**, and **5/5 in 308.66s**.  IR fallback baseline: **8/8 in
  1.33s**.
- V73 114-byte GC0 self-emitter canary: exit 0, reached `func begin main`.
- V73 linkage: libSystem only; no libpython or LLVM linkage.

## Performance evidence

Stage1 receipt comparison:

| Source | Wall | CPU | Instructions | Peak footprint |
|---|---:|---:|---:|---:|
| v67 | 281.57s | 1124.05s | 310.085B | 1.660GB |
| v72 field/base repair | 262.35s | 1066.24s | 309.331B | 1.651GB |
| v73 PEP 604 exact call | 299.28s | 1116.44s | 308.470B | 1.665GB |

V73 improves deterministic Stage1 instructions by about 0.52% and CPU by
about 0.68% versus v67.  Its single wall result is slower than v67 and faster
than neither v72 nor a robust bracket, so no Stage1 wall speedup is claimed;
footprint is within 0.3% of v67.

Receipt-bound item311 results:

| Source | Wall | CPU | Instructions | Peak footprint | Assembly |
|---|---:|---:|---:|---:|---|
| v67 | 31.21/31.18s | about 31s | 418.176/418.108B | 3.111GB | `ff943e10...` |
| v72 | 29.21/33.30s | 29.13/30.24s | 401.175/401.193B | 3.111GB | `ff943e10...` |
| v73 | 28.47/28.49s | 28.38/28.39s | 384.116/384.247B | 3.105GB | `ff943e10...` |

The v73 repeats differ by about 0.034% in instructions.  Against the faster
v67 instruction result, v73 removes about 8.1%; against v72 it removes another
about 4.2%.  It also beats the pre-migration v44 ~396.2B instruction envelope
by about 3% while retaining the zero-payload-tuple indexed data plane.

## Supported claim

On frozen GC0, self-backend, no-libpython pcc1, the generic inherited-field and
extern-base ABI is correct, optional exact receivers can use a direct native
method ABI, known subclass overrides remain dynamic, the former 114-byte
failure is fixed, and representative item311 output is byte-identical with a
stable material instruction reduction.

## Not proven

This evidence does not prove a complete Stage2/Stage3 run, pcc2/pcc3 fixed
point, GC1..4 equality, or Stage2 <= Stage1.  Those remain on the parent native
data-plane/task-board route and must not be inferred from this worker slice.
