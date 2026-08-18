# Indexed shared-analysis closure

Task: `PERF-P0-INDEXED-SHARED-ANALYSIS-DATA-PLANE`

## Accepted source

Accepted source remains v73 (`d1b50f59...`), pcc1 `650169b5...`.  The v74
direct-terminator experiment was denied and all affected compiler sources were
restored byte-for-byte to v73 before this classification.

## Authoritative shared results

One `IndexedFunctionKernel` owns and publishes:

- block IDs and packed instruction start/count/terminator-use facts;
- value IDs, definition block, type ID, slot/alloca IDs and last-use state;
- packed per-instruction destination/use counts and inline/overflow use IDs;
- one deterministic used-value ID order;
- packed PHI incoming spans and terminator successor/value records;
- packed call liveness state IDs consumed by stack-map reload planning;
- packed stack-map locations/reloads and target register/slot assignments.

Verifier, stackprep, precise stack maps, regalloc, target passes and emission
reuse those IDs/spans.  Legacy used-value and last-use mappings are explicit
diagnostic compatibility projections; the supported normal path records zero
instruction/call/type/slot projection.

## Profile-based boundary

A correctly early-attached v73 item311 profile has 9,662 samples.  Parse owns
about 65.3% and verify 32.5%; within verify, `_verify_ordinary_uses` owns about
22% of the worker, while CFG construction, dominators and definitions together
own only about 1.5%.  The verifier is doing required type/dominance validation,
not rebuilding a second consumer data plane.

A delayed v73 profile has 9,216 samples.  Stack-map planning owns about 44%
and final emit about 42%.  `_managed_live_after` owns about 1% and
`_block_entry_states` about 4%; the resulting call-liveness and entry-state
records are published once and reused.  These small local list/set worklists
are construction algorithms, not authoritative retained records.

The remaining temporary CFG/liveness containers are therefore assigned to
`PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE`, whose explicit purpose
is to decide whether each construction container can be fused/removed without
adding a slower publication interface.  They are not mislabelled as the final
data plane, and this task does not spend another full pcc1 cycle on a 1–4%
ceiling without a mechanism that deletes work.

## Gates and performance

- indexed kernel/verifier/stackprep/stackmap/regalloc/emit focused packet:
  547 passed, one frozen-v73 stale control explicitly deselected
- current item311 inventory: 59,984 packed instructions, zero normal
  instruction/call/type/slot projections at verified through emitted stages
- v73 item311: 384.116/384.247B instructions, 28.38/28.39s CPU, 3.105GB,
  exact `ff943e10...` assembly
- strict no-libpython closures, 114-byte pcc1 canary, bootstrap and complete
  sharded fallback/IR ratchets passed
- early and delayed profiles both use v73's own symbol table and exact-output
  worker command

## Deferred claim

This proves one indexed owner and consumer reuse for shared analysis results.
It does not prove every analysis *construction* container is gone, a complete
Stage2, or the fixed point.  Those remain explicit gates of the immediately
dependent object-projection closure; GC1..4 remains the final transfer task.
