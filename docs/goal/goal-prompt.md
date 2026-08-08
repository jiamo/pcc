# PCC goal execution protocol

This is the single, stable, agent-neutral protocol for active goal work. The historical
million-character ledger is preserved at
`docs/archive/goal/codex-goal-prompt-through-2026-07-09.md`; it is evidence and
history, not an executable queue.

## Objective

Continue selecting and completing task-board rows until every row is
`DONE_STRONG` or a real external blocker prevents further progress.
`DONE_WEAK` is unfinished. No other document may own an executable task list.

## 0. Authority and execution

Read `AGENTS.md`, this file, `docs/current-goal-state.md`, and
`docs/goal/task-board.yaml` in that order. The structured task board is the only
executable queue. A new actionable human request must be normalized into a row
before task selection.

Run:

```bash
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py next
```

Selection is milestone- and dependency-aware. `DONE_WEAK` remains unfinished.
Use `DONE_STRONG` only when every listed exit criterion is proven, every required
gate has a final successful result, `open_boundary` is empty, and a small
evidence file is linked from the row.

### 0.1 Status vocabulary

- `DISCOVERED`: observed but not yet shaped into an executable card.
- `TODO_NEEDS_DESIGN`: claim boundary or mechanism is not yet coherent.
- `TODO_READY`: finite, dependency-ready implementation work.
- `IN_PROGRESS`: implementation is active.
- `TESTING`: implementation exists; required evidence is still running.
- `DONE_WEAK`: a real slice passed, but the card's full boundary remains open.
- `DONE_STRONG`: the full finite card is proven with no open boundary.
- `BLOCKED`: use only after the repository's repeated-blocker threshold.
- `CLAIM_RISK` / `BACKEND_PARTIAL`: useful evidence exists but cannot support
  the broad claim implied by the task title.

### 0.2 Evidence

Each completed slice gets one file under `docs/goal/evidence/` naming the task,
source identity, changed behavior, exact commands, observed results, supported
claim, and what is not proven. Logs without a final pytest summary are not green.
Do not rewrite authoritative baselines to absorb a regression.

### 0.10 Claim hygiene

Every claim must label all relevant modes:

```text
host pcc vs pcc1/pcc2/pcc3
cpython-compat vs pcc-native
libpython vs no-libpython
LLVM/llvm_capi vs self backend
single stage vs pcc1 -> pcc2 -> pcc3 fixed point
GC backend 0, 1, 2, 3, or 4
CPU oracle/source proof vs real runtime execution
```

A dirty-worktree truth manifest proves only its recorded worktree fingerprint.
It is not a GitHub commit status. Release claims require a clean manifest with
`claimable_commit=true` and uploaded CI evidence for the claimed SHA.

## 1. North star

PCC exists to provide Python a native, auditable, self-hostable no-libpython
execution path. Performance is a consequence of proven semantics. The five
non-negotiable differentiators are the three-stage fixed point, comparative
five-GC runtime, opt-in identity-free value projection, self backend as an
execution root, and long-running runtime efficiency.

The production runtime, including allocation, headers, atomics, syscalls,
threads, dynamic loading, safepoints, stack maps, all five GC implementations,
and extension ABI entrypoints, migrates to freestanding pcc-Python compiled by
pcc. Compiler-owned raw-memory/syscall/atomic intrinsics are the machine
boundary. Existing C and vendored libc implementations are transition oracles,
not the final production dependency; do not replace host libc with another
permanent C libc. Darwin may retain named libSystem ABI entry calls and must not
be labeled zero-libc; the Linux static target must prove zero C/libc runtime
dependencies in the final artifact.

## 2. Work loop

For one selected card:

1. Read routed investigations and design documents.
2. If debugging, follow `docs/debugging-playbook.md` before guessing.
3. If opening or continuing an investigation, follow
   `docs/investigation-workflow.md` and regenerate its index.
4. Reproduce the smallest failing boundary and compare with the correct oracle.
5. Add a focused regression before changing shared codegen/runtime behavior.
6. Implement one coherent proposal; do not stack unverified speculative edits.
7. Run focused gates, then the card's adjacent/bootstrap gates as required by
   the changed boundary.
8. Record evidence, update the row, validate the board, and check for leftover
   compiler/test children.

All shell commands require explicit timeouts. Use `env -u LC_ALL uv run` for
Python entrypoints. Never commit unless the user asks. Never discard shared
worktree changes.

## 3. Active milestone order

The durable order is encoded in the board:

```text
M0 truth/control plane
M1 pcc1 pcc-native real extension-package canary across self + GC0..4
M2 NumPy L4 import and L5 array behavior
M3 value projection and C-like performance proof
M4 virtual-thread runtime scale
M5 deferred GPU/TIRx, distributed, ds4, and breadth research
```

Before M1 is strong, M5 accepts regression maintenance and claim correction,
not new P0 breadth.

## 4. Gate ownership

`scripts/head_truth_manifest.py` owns the command registry. The light CI path
runs fallback and control-plane ratchets on every push and pull request. The
reusable heavy path runs the complete LLVM/self/five-GC truth matrix manually,
nightly, and before a release. `docs/goal/head-truth-manifest.json` is a checked
local truth record; CI artifacts are the clean-commit publication authority.

## 9. Implementation requirements

### 9.1 Generic mechanisms first

Packages are integration targets, never compiler special cases. Fix reusable
install/import/ABI/buffer/capsule/build mechanisms and add a second synthetic
regression. Source guards must reject package-name branches such as
`if package == "numpy"`.

### 9.2 Mode boundaries

Reject incompatible artifacts with stable diagnostics. Never report a
`cpython-*`/`abi3` artifact as pcc-native, a host-assisted build as no-host, an
LLVM fallback as self backend, or a pcc1 smoke test as a fixed point.

## 10. Self-backend rules

`--backend self` must not silently invoke LLVM. The authoritative fixed-point
shape is pcc0/host -> pcc1, pcc1 -> pcc2, pcc2 -> pcc3, followed by normalized
pcc2/pcc3 equality and linkage inspection. Classify drift instead of patching
bytes around it.

## 11. Value-model rules

Ordinary classes retain identity, mutation, dynamic attributes, weakrefs,
subclassing, and finalization. Value classes are opt-in identity-free payloads
with explicit boxing and GC tracing. Python `int` is arbitrary precision with a
tagged-small-int value projection and boxed-bignum object projection; overflow
promotes or deoptimizes, never silently wraps. Machine wrap semantics belong to
explicit `pcc.i64`/`pcc.u64` types or proven-in-range internal lanes.

## 12. Five-GC rules

All five collectors must consume one slot/root/frame/native-handle graph
contract through `py_obj_visit_slots`, `py_obj_update_slot`, and registration
APIs. Backend #4 relocation must not maintain a second hand-written object
layout switch. A backend cannot pass by weakening finalizers, weakrefs,
resurrection, suspended frames, scheduler roots, extension refs, or value
payloads.

## 13. Virtual-thread rules

Scale work proceeds through pooled ready/waiter/timer/io nodes, a real timer
structure, a platform waitset, runtime-effect events, and finally the 1M gate.
Logical scheduler models are not runtime scale proof. Every scheduler-owned
object must remain updateable across GC0..4.

## 14. Package and ecosystem rules

The pcc-native canary must be a pinned real third-party source package built by
pcc1 with host Python and host pcc disabled. Its extension tag and manifest must
declare pcc-native, link no libpython, load under the self backend, cross at least
one function and one object/container boundary, match a CPython oracle, and run
under GC0..4. A fixture alone is not completion.

NumPy progress is measured only by the first missing module, symbol, or semantic
mismatch moving forward. Build-only success, CPython compatibility, or correct
rejection of a CPython ABI artifact is not NumPy L4/L5 success.

## 16. Performance proof

A C-like claim requires all of: stable semantic assumptions, optimized IR-shape
evidence, runtime benchmark evidence, and a slow path preserving Python
semantics. Run LLVM and self backends where the claim covers both. Single-shot
compile/run speed does not prove long-running pause, RSS, throughput, or
fragmentation behavior.

## 19.2 Fixed-point classification

Classify pcc2/pcc3 differences as semantic, IR text, class layout, object model,
backend nondeterminism, link metadata, performance-only, or diagnostic. Do not
normalize away semantic differences or weaken no-libpython/runtime ownership to
obtain byte equality.

## Historical reference

The pre-M0 ledger is read-only at
`docs/archive/goal/codex-goal-prompt-through-2026-07-09.md`. Use it only when an
investigation or evidence file explicitly routes there; never select work from
its old tables.
