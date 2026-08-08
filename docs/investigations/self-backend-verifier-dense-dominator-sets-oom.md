# Investigation: self-backend verifier dense dominator sets OOM (50 GiB stage1 kill)

## Status

active

## Problem Description

`scripts/bootstrap.sh --stage 1` on HEAD `a2031b76` fails: the self-backend
emit batch worker for `self_backend_emit_huge_0.manifest` grows past 50 GiB
RSS and is `Killed: 9` by macOS memory pressure. The user observed the same
50 GiB growth interactively and asked to locate the leak before it reaches
system OOM ("编译pcc1 需要 50g以上？绝对有问题啊 … 请定位内存泄漏").

The `huge_0` batch contains the four largest IR modules; the biggest is
`pcc.py_frontend.codegen._l1_codegen_static_methods` (43 MB of IR), whose
module-top function `_pcc_py_module_top_pcc_py_frontend_codegen__l1_codegen_static_methods`
has **72,100 basic blocks**.

Predecessor investigations:
- [self-backend-sparse-ssa-cache-memory-explosion.md](self-backend-sparse-ssa-cache-memory-explosion.md)
  — same subsystem, same RSS-capped-interrupt localization technique.
- [harness-agent-loop-self-stackmap-err-exit-join.md](harness-agent-loop-self-stackmap-err-exit-join.md)
  — the *other* failure in the same stage1 run (`managed root state disagrees
  at block join 'err.exit'` in `layer1_support`) is a distinct bug with its
  own file; do not conflate. That prior file resolved a stale-artifact case;
  the 2026-08-15 recurrence is in current source (pcc0 host compile).

## Repro [CONFIRMED]

Surviving temp dirs from failed runs hold the manifests and IR inputs
(`/var/folders/.../T/pcc_py_self_*/self_backend_emit_huge_0.manifest`).
Running the single emit worker on the 43 MB module under an 8 GiB RSS cap:

```bash
env -u LC_ALL uv run python <rss_capped_runner> 8 \
  .../pcc_py_self_eme9uslo/self_backend_module_190.ll /tmp/out
```

Observed 2026-08-15: RSS crosses 8.35 GiB at t=9.7 s. The SIGINT traceback
stops at:

```text
File "pcc/backend/self_backend_verify.py", line 252, in _compute_dominators
    dominators = [set(all_blocks) for _index in range(block_count)]
```

## Root Cause [CONFIRMED]

`self_backend_verify.py` (new in `a2031b76`; stage1 never ran it before) uses
the textbook dense iterative dominator algorithm: one Python `set` containing
*all* block indices per block. For N = 72,100 blocks that is N sets of N
entries — the hash tables alone are ~2-4 MB per set, i.e. **hundreds of GiB**
just for initialization. The process was killed at 50 GiB before finishing
line 252. This is not a leak but O(N²) by design; module-top functions of
large generated files (`_l1_codegen_static_methods`) make N² intractable.

The fixed-point loop is equally O(N²) time, so even with unlimited memory the
verifier could not finish.

## Test [CONFIRMED]

- Baseline before fix: `tests/c/test_self_backend_verifier.py` — 9 passed.
- Failure observed under the RSS-capped runner above (never let it reach
  system OOM; cap at 8 GiB).

## Proposals

- No.1 Replace dense dominator sets with idom tree + preorder intervals
  (Cooper–Harvey–Kennedy) [CONFIRMED]

## No.1 Replace dense dominator sets with idom tree + preorder intervals

### Code Change

`pcc/backend/self_backend_verify.py`:

- `_compute_dominators(predecessors, successors)` now computes the immediate
  dominator array via Cooper–Harvey–Kennedy over reverse postorder, then
  assigns preorder entry/exit numbers over the dominator tree. Memory O(N),
  near-linear time. All loops iterative (72k-deep trees would overflow
  recursion).
- Dominance queries (`_definition_dominates_use`, phi-edge check) become
  `dom_in[d] <= dom_in[b] <= dom_out[d]` via `_block_dominates`.
- Unreachable blocks (the parser filters them; only malformed input can
  contain one) get singleton intervals: they dominate only themselves. The
  old code gave predecessor-free blocks `{self}` (same semantics); blocks in
  unreachable cycles previously kept over-full dominator sets (more lenient);
  the new behavior is strictly equal-or-stricter, and unreachable-cycle input
  cannot come from the pipeline.

### Result

- `tests/c/test_self_backend_verifier.py`: 12 passed (9 existing + 3 new:
  dense-oracle dominance parity on branchy/loopy/malformed CFGs, shared
  err.exit join end-to-end, 100k-block chain bounded run in <0.3 s).
- RSS-capped re-run of the 43 MB / 72,100-block module: memory stayed
  bounded (~740 MB RSS, was cap-hit 8.35 GiB at t=9.7 s inside line 252's
  initialization; system kill was >50 GiB). The run then exposed a separate
  emit-time quadratic — see
  [self-backend-stackmap-label-scan-quadratic-emit.md](self-backend-stackmap-label-scan-quadratic-emit.md).
- Downstream stage1 state is recorded in that follow-on investigation and in
  [dict-builtin-module-top-stackmap-err-exit-join.md](dict-builtin-module-top-stackmap-err-exit-join.md)
  (the second stage1 blocker found in the same run).
