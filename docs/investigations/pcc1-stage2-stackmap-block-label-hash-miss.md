# Investigation: pcc1 stage-2 stack-map CFG lookup misses an equal block label

## Status

resolved

## Problem Description

After the predecessor
[`self-host-stage2-lift-attributeerror-obj.md`](self-host-stage2-lift-attributeerror-obj.md)
was crossed, `pcc1 -> pcc2` reached the self-backend emitter and failed while
planning precise stack maps:

```text
self backend emit worker failed: 'err.frame.791'
```

The current-source stage-2 artifacts reduce this to one 53,562-byte IR shard
containing only
`user_pcc_py_frontend_module_action_dag_PublicSummary_digest`.  Host CPython
emits that exact IR successfully, while the current pcc1 worker fails during
`stack map plans begin`.

`self_backend_analysis.py` and `self_backend_verify.py` already document and
defend the relevant native-bootstrap condition: equal text strings can have
inconsistent native hashes.  `_block_entry_states()` in
`self_backend_precise_stackmaps.py` still builds `blocks` by parsed block-name
objects, queues separately parsed terminator-target strings, and then performs
the unguarded `blocks[name]` lookup.  The failing target and an equal block
definition are both present in the IR.

## Repro

The full boundary was observed from frozen working-tree diff
`dd7b1136b69ba9b970614b3a78e889770fbc0239b9ae3d7b33b05b6a1b8817e4`:

```bash
gtimeout 1320s env -u LC_ALL scripts/bootstrap.sh \
  --out-dir build/bootstrap-review-20260826 --backend self \
  --from-stage 2 --stage 2 --reuse-stage1
# stage=2 elapsed_ms=622190 rc=1
```

The retained current-run IR has a seconds-long deterministic repro:

```bash
gtimeout 60s env -u LC_ALL PCC_DEBUG_SELF_BACKEND_TRACE=1 \
  build/bootstrap-review-20260826/pcc1 \
  --pcc-self-backend-emit-worker \
  /tmp/pcc-err-frame-single-1.ll \
  /tmp/pcc-err-frame-single-1.result \
  /tmp/pcc-err-frame-single-1.s ''
# rc=1; funcs=1; self backend emit worker failed: 'err.frame.791'
```

## Test [CONFIRMED]

The full failure and the single-function reduction were observed on
2026-08-26.  The reduced input passes through the host
`emit_aarch64_darwin_asm()` oracle and fails only through pcc1.

The permanent focused test will construct equal block-name strings with
deliberately inconsistent `__hash__` values and require stack-map CFG
propagation to resolve the successor by equality instead of native hash.

## Proposals

- No.1 Route cross-object block-name lookups through the existing
  `text_key_mapping_get` fallback and regress hash-skewed equal labels
  [CONFIRMED]

## No.1 Route stack-map CFG lookups through the text-key fallback

### Code Change

Replace only cross-object CFG lookups in precise stack-map planning: parsed
terminator target to block, target to entry/live-in state, and target to source
order index.  Keep same-object per-block maps unchanged.

### CONFIRMED

The first block lookup fix exposed three more instances of the same native
bootstrap invariant: pointer-alias lookup, value/alloca spill-slot lookup, and
target-final line-index lookup. The final implementation uses stable integer
collision buckets plus explicit text equality for cross-object block/SSA
names, and keeps value/alloca slot sidecars on `ParsedFunction`; CFG entry and
liveness states are indexed by block number rather than native string hash.

Evidence:

- hash-skewed CFG and pointer-alias regressions plus stackprep/unreachable
  neighbors: 35 passed;
- retained pcc1 worker repros pass: the 53 KiB `err.frame` shard, 70 KiB slot
  shard, 1.26 MiB module 47, four alias shards, and the final-label shard;
- final strict chain: stage2 1,290,895 ms, stage3 384,764 ms, pcc2/pcc3
  byte-identical;
- fallback/fail-closed ratchets: 40 passed. The independent single-module
  OFF/ON action ceilings were recaptured because those probes cannot resolve
  sibling static helpers; production multi-module strict totals remain zero.

## Report

Proposal No.1 is confirmed, expanded only to the other stack-map lookups that
failed under the identical inconsistent-native-hash mechanism. No verifier,
managed-root edge, no-libpython rejection, or LLVM/self boundary was weakened.
The fixed point proves correctness for the frozen source, while the large cold
stage2/hot stage3 gap is explicitly routed to the performance task board.
