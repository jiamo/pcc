# AGENTS.md

This file is for humans and AI agents working in this repository.

## Read Next

Startup route for active goal work and direct human task intake:

1. Read this file first for repository rules and safety constraints.
2. Read `docs/goal/goal-prompt.md` for the single goal contract and work
   protocol.
3. Read `docs/current-goal-state.md` for the current audit state and the
   investigation/doc routing that should be read next.
4. Inspect `docs/goal/task-board.yaml` through `scripts/goal_state.py next` before
   choosing active goal work. This structured task board is the source for
   migrated rows and new actionable tasks; `DONE_WEAK` is still unfinished.
   This applies to directly launched agents too, not only Codex `/goal` or
   Claude loop sessions. If the startup prompt or current conversation contains
   a new actionable task, normalize it into `docs/goal/task-board.yaml` and
   validate the board before selecting active work.
5. Use `docs/investigations/INDEX.md` to find relevant prior investigations
   before opening or continuing a non-trivial bug. Then **read the matching
   file end to end, including every `[DENIED]` verdict and every "did not
   help" note, before writing code** — those sections record fixes already
   written, measured, and disproved, and re-deriving one wastes a full
   rebuild/measure cycle on a change known not to work. See the Investigation
   Workflow section below for what this rule keeps catching.
6. **Task-conditional, required — not optional reference.** The moment the task
   becomes *debugging a failure*, read and follow
   [`docs/debugging-playbook.md`](docs/debugging-playbook.md) before guessing.
   The moment you *open or continue an investigation*, read and follow
   [`docs/investigation-workflow.md`](docs/investigation-workflow.md) first.
   These two were split out of this file to stay under the context budget;
   the split lowered their resident-in-context cost, **not** their authority.

## Goal Task Board

`docs/goal/task-board.yaml` is the structured execution queue for migrated and
new actionable goal tasks. Use it even when the agent was launched directly
instead of through a `/goal` or loop command. `docs/goal/goal-prompt.md` is the
only protocol and claim-hygiene authority; task rows themselves should be
agent-neutral and should not mention Codex, Claude, or any specific runner.

For active goal work:

```bash
env -u LC_ALL uv run python scripts/goal_state.py next
env -u LC_ALL uv run python scripts/goal_state.py validate
```

Add new actionable work as a task row in `docs/goal/task-board.yaml`, with a
priority, status, track, title, open boundary, and required gate commands. Add
one small evidence file under `docs/goal/evidence/` for each completed slice,
then update that task row's `latest_evidence`, `status`, and
`open_boundary`. Promote to `DONE_STRONG` only when the listed gates prove the
full claim and the open boundary is empty.

New-task ingestion rule: when a human asks to "add a P0/P1 task", "package this
into the task board", or describes a new actionable goal, put it in
`docs/goal/task-board.yaml` immediately instead of leaving it only in chat,
`docs/goal/goal-prompt.md`, or `docs/current-goal-state.md`. Once the row exists,
every directly launched agent, Claude loop, or Codex `/goal` run must see it
through `scripts/goal_state.py next`; no extra `/goal` prompt or loop-specific
bootstrap is required for the task to become eligible. Use
`docs/goal/goal-prompt.md` only for protocol and long-form guardrails, not as
the place where new executable tasks live. This is an `AGENTS.md` startup rule, so
the user should not need to copy `docs/goal/goal-prompt.md` into each new agent
session just to make newly added tasks visible.

Direct human-task intake is part of startup state, not a separate loop mode. If
the current conversation or startup prompt contains a new actionable task,
normalize it into `docs/goal/task-board.yaml` first, run
`env -u LC_ALL uv run python scripts/goal_state.py validate`, and then let the
normal priority order choose it. Do not wait for a `/goal` command, cron loop,
or agent-specific bootstrap before recording the task.

If the human gives the task as ordinary prose, first normalize it into a
task-board row with an agent-neutral id, priority, status, track, title,
claim boundary, open boundary, and required gates. Example intent such as "add
a P0 task: fix GPU TVM/TIRx host/device split" is already sufficient input; do
not wait for a separate `/goal` command before recording it.

## Project Intent (north star — read before changing direction)

> This section is the top-level design contract. It exists to keep autonomous
> work aligned: when a change would trade away one of the obligations below for
> a local win — a faster benchmark, a greener gate, a smaller diff, a passing
> bootstrap by rewrite — **stop and surface the tradeoff instead of taking it
> silently.** This section is the *why*; `docs/goal/goal-prompt.md` is the *how*
> (tracks, gates, claim hygiene, prohibitions). If you find yourself weakening
> Python semantics, mislabeling a mode, or special-casing a package to make
> progress, you are off the north star — re-read this section.

**Thesis.** pcc exists to give Python a native, auditable, self-hostable,
no-libpython execution path. The goal is **not** merely to make selected Python
programs faster — it is to make Python execution *ownable*: compiled,
inspectable, self-hostable, package-aware, runtime-extensible, and honest about
every fallback boundary. pcc treats performance as a **consequence of proven
semantics, never a license to weaken Python behavior.**

**What separates pcc from a Python accelerator.** Five things. Without them pcc
is just another speedup tool; with them it is a system rebuilding Python
*execution ownership*. Do not let any of these decay into decoration:

```text
1. pcc1 -> pcc2 -> pcc3 self-hosted fixed point
2. five-GC comparative runtime (refcount/cycle, incremental, concurrent,
   generational, relocating) — a research program, not one collector
3. opt-in value model — identity-free immutable payloads for hot paths, with no
   theft of ordinary-class semantics (Java's Project Valhalla is a conceptual
   reference only, not pcc's brand or design constraint)
4. self-backend as a first-class execution root (LLVM is oracle, not owner)
5. long-running runtime efficiency (pause / RSS / throughput / fragmentation
   over time, not single-shot compile+run speed)
```

**The fixed point is more than a byte compare.** It is evidence that pcc's
Python semantics, runtime, codegen, object model, backend, and diagnostics are
coherent enough to reproduce themselves:

```text
pcc0/host -> pcc1     pcc can produce a compiler
pcc1      -> pcc2     the produced compiler can reproduce the compiler
pcc2      -> pcc3     stable pcc2/pcc3 == a self-hosted fixed point
```

**Seven obligations.** Each is operationalized by a track + gates in
`docs/goal/goal-prompt.md`; the one-line form here is the guardrail, and the
parenthetical is where it is actually enforced:

```text
1. Compatibility must be mode-labeled. A claim must say which mode produced it:
     host pcc != pcc1   |   cpython-compat != pcc-native
     libpython != no-libpython   |   LLVM-backed != self-backed
     stage1 != pcc1->pcc2->pcc3 fixed point
   (`docs/goal/goal-prompt.md` §0.10 claim hygiene, §9.2 mode boundaries)

2. Performance must be proven. C-like claims require IR-shape evidence + runtime
   benchmark + a slow path that preserves Python semantics when assumptions fail.
   pcc does not claim arbitrary dynamic Python becomes C-speed — only the parts
   whose semantics are stable enough to lower natively. (C-track, §16)

3. Ecosystem support must be generic. NumPy / PyTorch / pandas / Arrow / SciPy
   are integration targets, never compiler special cases. No `if package ==
   "numpy"`; fix the reusable mechanism (install/import/ABI/buffer/capsule/
   build-surface) and regress the generic feature. (B-track, §9.1, §14)

4. Self-backend must become a first-class execution root, not a forever-LLVM
   dependency. No silent fallback to LLVM after --backend=self. (S-track, §10)

5. The pcc1/pcc2/pcc3 fixed point is a contract. Differences are *classified*
   (semantic / IR-text / class-layout / object-model / backend nondeterminism /
   link metadata / perf-only / diagnostic), not patched around. pcc2/pcc3
   stability is a core correctness signal. (§0.10, §19.2)

6. Runtime design is part of the research goal. The five GC backends are a
   comparative program; none may win by weakening finalizers, weakrefs,
   resurrection, suspended coroutine frames, scheduler queues, C-extension
   refs, or value payloads. Measure efficiency as a long-running property.
   (G-track/§12, T-track/§13)

7. The value model is the performance bridge, not a syntax gimmick. Ordinary
   classes keep identity (id / is / weakref / __dict__ / mutation / subclass /
   finalizer / dynamic attrs). Value classes are opt-in, identity-free payloads
   with explicit boxing/unboxing, identity-escape diagnostics, GC tracing of
   pointer-bearing payloads, and self-backend aggregate/scalar ABI. (The concept
   is the obligation; "Valhalla" is only the reference it was distilled from.)
   What pcc borrows from Valhalla is the PROJECTION model (semantic type vs
   physical representation; value/object projection; boxing bridge; optimization
   never changes semantics) — NOT Java's fixed-width `int` wrap. This applies to
   `int` itself: `int` is a Python arbitrary-precision SEMANTIC type with a value
   projection (tagged small-int lane) and an object projection (boxed bignum);
   value-lane overflow must deopt/promote, never wrap. Raw machine integers are
   the EXPLICIT `pcc.i64`/`pcc.u64` type (where wrap/trap/checked/saturating is
   written in the type), or a proven-in-range internal optimization — never the
   silent default meaning of `int`. (value model / V-track, §11)
```

**One mission, not two.** Industrial failures are research data (import failure
-> C-API/ABI gap; Linux deploy failure -> self-backend target gap; long-running
service regression -> GC/runtime benchmark; perf miss -> value-model gap), and
research artifacts are industrial trust (fixed-point bootstrap -> reproducibility;
five-GC matrix -> runtime credibility; valueclass benchmarks -> performance
proof; package ABI reports -> ecosystem trust). The industrial thesis ("adopt
pcc where native artifacts, no-libpython deploy, package-aware diagnostics, and
hot-path specialization beat CPython") and the academic thesis ("a
Python-authored compiler self-hosts into a no-libpython fixed point while
exposing a disciplined runtime laboratory") reinforce each other. **Every claim
must say exactly what it proves and what it does not prove.**

**Accelerator execution is an extension of the ownership thesis, not a sixth
mission — and not the overclaim it is easy to make.** The repo already contains
a real GPU/Metal thread (`pcc/kernel_ir/`, `pcc/gpu_gc/`, `pcc/dist/`) that the
five pillars above did not name; this paragraph gives it an honest home so the
intent stops lagging the code. It belongs to the same "ownable execution"
thesis: the target is **native accelerator execution ownership** — a
host/device-split kernel IR, no-libpython device launch, and GC-aware external
resource lifetime — with **TVM/TIRx and TileLang used as oracles/reference
shapes, never as runtime owners** (the same relationship the value model has to
Valhalla and the self-backend has to LLVM). What is actually proven today is
narrow and must be stated at its claim level (`pcc/kernel_ir/gpu_claims.py`
levels 0-6): a real Metal kernel-IR path with on-device result proofs for small
fixed-shape kernels, **local-machine and hardware-gated**. What is **not**
provided: whole-program GPU, executing `import tvm` / `import tilelang`
(only a fail-closed parser of a TileLang-*shaped* DSL subset exists), external
framework interop, and any real distributed/ds4 runtime (`gpu_gc`/`dist` are
CPU oracles). This thread **must not displace the self-host -> 5-GC -> value ->
runtime-efficiency spine**: it is M5 breadth, and calling a GPU slice "done"
requires the same mode-labeled claim hygiene as every pillar.

**Runtime layering: the production runtime is authored in pcc-Python, including
the low-level kernel.** The long-term goal is not a smaller hand-written C
runtime. Allocation, object headers, atomics/refcount barriers, platform
syscalls, threading primitives, dynamic loading, extension ABI entrypoints,
safepoints/stack maps, and all five GC implementations migrate to a strict
freestanding pcc-Python subset and are compiled by pcc into native code. The
machine boundary is compiler-owned raw-memory/syscall/atomic intrinsics and a
specified ABI, not a permanently hand-maintained C kernel. Existing C and
vendored libc sources are transition implementations and differential oracles;
they are not the final production dependency. Distinguish these layers:

```text
compiler intrinsics   KEEP: raw memory, atomics, syscall/host-ABI entry and
                      machine operations; no Python object semantics.
freestanding pcc-Py   GROW: allocator, threads, safepoints, GC, libc-like
                      substrate and ABI shims; no heap/boxing/GC dependency
                      while bootstrapping those facilities themselves.
semantic pcc-Python   GROW: list/dict/str/dunder/exception/import semantics.
C/libc sources        REMOVE from the production dependency after differential
                      and fixed-point gates; retain only as attributed oracles.
```

This is stronger than no-libpython: the final Linux zero-libc claim requires no
production C/libc runtime dependency either. Darwin may still enter the OS
through named libSystem ABI calls and must not be labeled zero-libc. The
**5-GC Production Equality Rule** (`docs/goal/goal-prompt.md`, G-track) still
requires every backend to consume ONE slot-based trace/update contract
(`py_obj_visit_slots` / `py_obj_update_slot` / root + frame + native-handle
registration). During migration the C oracle and pcc-Python implementation must
stay differential-equal; completion removes the C implementation from the
production link rather than preserving two implementations indefinitely.

## Project Summary

`pcc` is two compilers and one runtime in one repo:

1. A **C frontend** built on `pycparser`, LLVM (`llvmlite` and `pcc/llvm_capi`),
   and a fake-libc layer. This is the most mature path; it runs real
   third-party projects (Lua, SQLite, PostgreSQL `libpq`, zlib, lz4, zstd,
   PCRE, OpenSSL, readline, nginx).
2. An experimental **typed-Python frontend** (`pcc/py_frontend/` +
   `pcc/py_runtime/`) used for the self-host / bootstrap track.
3. A **runtime** (`pcc/py_runtime/src/*.c` and pcc-Python ports under
   `pcc/py_runtime/py/`) with five pluggable GC backends.

Most fixes in this repository are not parser bugs; they are **semantic** bugs
that only show up when expressions are combined, lowered to LLVM IR, and then
exercised by real programs. The C-side and Python-side both follow this
pattern.

The fastest way to get useful results:

1. Reproduce with a tiny program when possible.
2. Compare against a known-good reference (native compiler for C; CPython for
   Python; `llvmlite` for `llvm_capi` parity).
3. Add one small regression test and one realistic integration confirmation.
4. Do not consider a real-project bug "fixed" until the minimized regression
   exists in `tests/` and passes without the project harness.

The fastest way to create expensive regressions is the opposite:

1. touch `c_codegen.py`, any `pcc/py_frontend/codegen/*_lowering.py` mixin, or
   any `pcc/py_frontend/codegen/native_*.py` file with a broad speculative
   change
2. skip the smallest reproducer
3. delay regression checks until after several edits have stacked up

Do not do that here.


## Environment Rules

- Use `uv run ...` for Python entrypoints. Do not rely on bare `python`; local
  `pyenv` state may not match the repository.
- Use `env -u LC_ALL` in command examples. It is required for Codex because
  Codex may set `LC_ALL=C`, which can break Python locale handling; it is
  harmless for other users. See https://github.com/openai/codex/issues/14723.

  ```bash
  gtimeout 120s env -u LC_ALL uv run pytest -q -x
  env -u LC_ALL uv run pcc hello.c
  ```

- While debugging one failure, prefer `-n0` so xdist does not hide ordering or
  temp-file problems. This is a debugging rule, not a blanket validation rule.
  For independent bootstrap / GC matrix validation, do **not** serialize the
  whole matrix with `-n0`; let pytest-xdist use its configured workers, or run
  one explicitly chosen backend file with `-n0` when localizing that backend.
  A five-backend bootstrap matrix should normally look like:

  ```bash
  gtimeout 1800s env -u LC_ALL uv run pytest -q -x -m integration tests/python/gc/test_pcc_bootstrap_full_gc*.py
  ```

  Use the single-backend form only for focused diagnosis:

  ```bash
  gtimeout 360s env -u LC_ALL uv run pytest -q -x -n0 -m integration tests/python/gc/test_pcc_bootstrap_full_gc4.py
  ```

  Long bootstrap gates must provide meaningful progress updates: name the exact
  command, backend/stage if known, elapsed time, and whether new output has
  appeared. If a long gate is interrupted or times out, immediately check for
  and terminate leftover `pytest`, `bootstrap.sh`, `pcc`, `pcc1`, `pcc2`, and
  `pcc3` children. A run without a final pytest summary is not green evidence,
  no matter how many progress dots were printed.
- **Every pytest invocation must stop at the first failure or error.** Pass
  `-x` (or the exact equivalent `--maxfail=1`) on every pytest command,
  including focused, default, integration, bootstrap, and GC gates. Do not let
  a suite continue after the first `F` or `E`. The only exception is an
  explicit human request in the current conversation to collect multiple
  failures; a task document, historical command, or desire for broader
  evidence is not enough to override this rule. With xdist, stop scheduling
  new work at the first reported failure and preserve the first failing node's
  traceback before doing anything else.
- **Never spend a long pytest run producing dots-only evidence.** Before any
  pytest command expected to run longer than 60 seconds, arrange durable,
  incremental failure evidence outside pytest's end-of-session summary. At a
  minimum, run with node IDs visible (for example `-vv`) and persist the live
  output to a named artifact; for a diagnostic run also use `-x --tb=short` so
  the first current failure is reported promptly. A broad run that may reach
  its watchdog must use an incremental report hook/logger that records each
  failed node ID and traceback as the report arrives. If that facility is not
  available, shard the suite into commands that fit their watchdog instead of
  launching the broad run. `F`/`E` counts and percentage progress without node
  IDs and tracebacks are not useful failure evidence and must never be reported
  as if they were. Leave enough outer-watchdog grace for pytest to flush its
  report and summary; if the measured suite cannot finish within that budget,
  stop and shard it before rerunning rather than merely widening the timeout.
- **Broad suites are final evidence, never a discovery mechanism or a way to
  decide what to implement next.** Do not start the default suite, full
  integration suite, five-GC matrix, or a full `pcc1 -> pcc2 -> pcc3` chain
  until the implementation slice being closed has met all of these readiness
  conditions: its known code changes are complete; its focused regression has
  passed with `-x`; every agent editing an overlapping subsystem has reported
  source-stable; no relevant source or generated artifact is changing; and the
  exact frozen source identity plus expected time/cache envelope has been
  recorded. If any condition is false, continue implementation or run one
  focused diagnostic only. Never launch a broad gate merely because a task
  card lists it, because progress feels slow, or because accumulated edits need
  "a general check." After readiness, build bootstrap stages in dependency
  order (`pcc0 -> pcc1`, then `pcc1 -> pcc2`, then `pcc2 -> pcc3`) and stop at
  the first failed stage; do not hide stage causality inside an unrelated full
  pytest run.
- **A completed tool call is not ongoing work.** Once no shell/tool/agent job is
  running, send a concrete progress update or end the turn within 60 seconds.
  Do not remain silent in model-side analysis; if the next action is not ready,
  report the exact completed state and yield. A multi-hour `Model interrupted
  to submit` interval with no live child process is an agent stall, not task
  progress, and must never be described as compilation or investigation time.
- **Do not use the full five-GC bootstrap matrix as a diagnostic loop.** Any
  change under `pcc/` invalidates the content-addressed bootstrap source hash
  and can force five cold `pcc1 -> pcc2 -> pcc3` chains. Before launching the
  matrix, read the routed bootstrap-performance/timeout investigations, inspect
  existing stage profiles and success manifests, and measure one explicitly
  chosen backend or an emit-only/profile probe. If one backend exceeds its
  documented budget, or the projected cold matrix cannot fit its outer
  watchdog, optimize/localize that regression first. Do not repeat the matrix
  or widen its timeout to compensate. Run the full matrix once, only after the
  focused fix and scheduler tests are green, as final cross-backend evidence.
- Use ripgrep (`rg`) or your agent's built-in code search for source discovery.
- Do not leave temporary `.c`/`.py` files inside real project directories.
  Directory-based source collection can accidentally compile them.
- **Never drop scratch files in the repo root.** When you need a throwaway
  artifact, in order of preference: (1) write a real test under `tests/` —
  that captures the case as a regression and survives; (2) use an existing
  fixed build/cache directory (e.g. the per-test artifact dir under
  `~/.cache/pcc/test-artifacts/`, or `cache_dir` in the function you're
  working on); (3) only if neither fits, use `/tmp/`. The top-level
  directory is never the right place — it pollutes `git status`,
  pre-commit hooks pick it up, and other agents see the noise.
- **Every Bash invocation must have an explicit timeout. No exceptions.**
  This applies to **all** runs — `uv run pytest`, `uv run pcc`,
  `scripts/bootstrap.sh`, `/tmp/<name>` probes, LLDB sessions, anything
  that forks a child process. The default is a small timeout you expect
  the command to finish well under; **only widen it when you can name a
  specific reason** the run will legitimately take longer (e.g. "the current
  9517-case non-integration suite reached only 93% at 900s on a cold
  current-source run → use 1200s", "stage1 self-host bootstrap is ~4–5 min →
  use 360s"). "I'll just let it run" is not a reason. Re-measure these
  envelopes after build/cache changes; an old shorter estimate is not a reason
  to repeat a gate that cannot produce a final summary.
  Without a timeout, a hung collection / codegen infinite loop / runaway
  probe silently burns the foreground turn and can leave zombie children
  pegging CPU for hours (this has happened — ~120 CPU-hours lost once).
  Before ending a session, `ps aux | grep <your-probe-name>` and `kill`
  any leftover children. Prefer the Bash tool's `timeout` field when
  available. When shelling out on macOS, do not assume GNU `timeout`
  exists; use `gtimeout` only if it is installed, or use a wrapper that
  keeps a parent watchdog alive, forks the target into its own process
  group, and kills that process group on expiry.
- **Do not use `perl -e 'alarm shift; exec @ARGV' ...` or any
  alarm-then-`exec` wrapper as a timeout for heavy commands.** That pattern
  has already failed in this repository after `exec`, leaving an already
  exec'd `pcc1` running for 11+ minutes. A valid fallback timeout wrapper
  must keep the watchdog parent alive, create a child process group
  (`setpgrp`/`setsid`), and on expiry send `TERM` then `KILL` to the child
  process group. After any timed-out bootstrap/compiler run, verify with
  `ps` that no `pcc`, `pcc1`, `pytest`, or named probe child survived.
- **Never `git revert`, `git checkout -- <path>`, `git restore`, `git reset
  --hard`, `git clean -f`, `git stash`, branch switches that discard local
  edits, or any `--force`/`--hard` flag that drops uncommitted state — unless
  the user explicitly asks for it.** Multiple agents and humans share this
  working directory; a unilateral rewind silently deletes another agent's
  in-flight work that has not yet been committed. If you believe a previous
  change is wrong, leave it in place and discuss with the user, or stage a
  *new* commit that supersedes it.
- **Do not perform file-overwrite operations via git (including revert/restore
  checkout/reset --hard/clean/stash or equivalent) unless explicitly requested
  by the user.** This applies to all working-tree files touched in this
  repository.
- **Do not `git commit` unless the user asks.** During investigation you may
  *describe* a sequence ("commit investigation file → run experiment → revert
  → record outcome → commit again"), but you may not run the commits without
  explicit user approval. The Investigation Workflow's "save before each
  experiment" idiom is a prescription for the user to follow, not for the
  agent to execute autonomously.
- **Do not delete documents, plans, investigations, tests, or project
  directories unless the user explicitly names the files/directories to
  remove.** Treat `rm`, scripted deletion, and bulk cleanup of docs as
  destructive operations, even when git could recover them.


## Interrupting tasks

A short operational request received while another task is unfinished is an
interrupting subtask, not a replacement.

- Push it onto the active task stack.
- Complete and verify the interrupting task.
- Automatically resume every unfinished parent task.
- Do not end the turn until the parent task is answered.
- Replace or abandon the parent task only when the user explicitly says:
  “stop”, “cancel the previous task”, or “switch to this instead”.
- Before the final response, list all active requests and verify each is complete.


## Repository Map

| Path | Role |
|---|---|
| `pcc/pcc.py`, `pcc/cli_core.py` | CLI entrypoints |
| `pcc/cli_bootstrap.py` | Bootstrap-stage CLI used by `pcc1`/`pcc2`/`pcc3` |
| `pcc/api.py` | `build(...)` / `module(...)` Python API for C |
| `pcc/project.py` | Directory collection, `--sources-from-make`, TU selection |
| `pcc/evaluater/c_evaluator.py` | C preprocess/parse/IR/optimize/execute |
| `pcc/codegen/c_codegen.py` | Main C semantic lowering (~most C bugs land here) |
| `pcc/parse/c_parser.py` | C parser (PLY-based; bump cache version on grammar/lexer changes) |
| `pcc/py_frontend/` | Python typed-frontend; type infer + native lowering |
| `pcc/py_frontend/codegen/layer1.py` | Thin facade for Python frontend lowering |
| `pcc/py_frontend/codegen/*_lowering.py` | Main Python lowering mixins; add focused behavior here instead of growing `layer1.py` |
| `pcc/py_frontend/codegen/native_*.py` | Native module lowering (gc, threading, asyncio, system, os, math, modules, weakref, ...) |
| `pcc/py_runtime/src/*.c` | Native runtime in C (objects, GC, threads, exceptions, ...) |
| `pcc/py_runtime/py/*.py` | pcc-Python runtime ports (mirror of C; for self-host) |
| `pcc/py_runtime/include/py_runtime.h` | Public runtime header: object header, type tags, `PCC_GC_KIND_*` enum |
| `pcc/py_runtime/src/py_internal.h` | Runtime-internal object layouts such as `PyClassObject` |
| `pcc/llvm_capi/` | In-repo LLVM-C builder; fallback path is `llvmlite` |
| `pcc/backend/` | Experimental LLVM-free self backend (AArch64 Darwin, x86_64 Linux subsets) |
| `pcc/kernel_ir/` | Kernel-only GPU IR, TIRx-like freeze, Metal finalization, launch packages, HMM/fence, and DLPack/tensor ownership slices |
| `pcc/gpu_gc/` | GPU-GC metadata/oracle and external-resource lifetime seam; CPU-only unless a focused gate proves runtime integration |
| `pcc/dist/` | Local-only distributed session, transport, collective, sharding, and KV oracles; not localhost TCP or multi-Mac execution by default |
| `pcc/extern/`, `pcc/unsafe/` | Python→C extern decls; compiler-recognized intrinsics |
| `utils/fake_libc_include/` | Fake libc headers (host ABI / decl mismatches surface here) |
| `tests/` | Unit, parity, integration regression coverage |
| `tests/py_corpus/phase*/` | End-to-end Python corpus retained from the earlier phase taxonomy. These tests still run; the phase framework is no longer the active task board. See current priorities in `docs/current-goal-state.md`. |
| `tests/python/test_self_host_oracle_diff.py` | Core Python semantic oracle / pcc1-pcc2 parity ratchet |
| `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py` | Full stage1→stage2→stage3 self-backend bootstrap gate, one file per GC backend (shared helpers live in `tests/python/test_pcc_bootstrap_full.py`) |
| `tests/bootstrap_gate_baseline.json` | **Authoritative bootstrap state** (Issue 1 closure evidence) |
| `tests/fallback_baseline.json` | **Authoritative no-libpython fallback state** |
| `scripts/bootstrap.sh` | macOS arm64 three-stage bootstrap entry |
| `scripts/pcc_multi.py` | Experimental multi-file Python entry |
| `projects/lua-5.5.0/` | Real-program stress target |
| `docs/refs_docs/gc-research/` | Reference impls for the 5 GC backends (Lua, Go, OCaml, ZGC, CPython) |
| `docs/goal/goal-prompt.md` | Single active goal contract and work protocol |
| `docs/current-goal-state.md` | Current goal audit, selected task state, and investigation routing |
| `docs/design/pcc-gpu-next-work.md` | Durable GPU / TVM-TIRx / Metal / GPU-GC / distributed / ds4 route contract, reference pins, and GPU claim levels |
| `docs/investigations/INDEX.md` | Index of investigation docs; keep it current when investigation docs change |


## Compile Modes

Be explicit about which mode you are debugging.

- **Single-file**: compile exactly one `.c` / `.py` file.
- **Merged directory** (default for directory inputs): concatenates selected
  `.c` files into one large translation unit.
- **`--separate-tus`**: compile each `.c` as its own TU, link at the LLVM /
  module layer.
- **`--sources-from-make GOAL`**: use `make -nB GOAL` to discover `.c` files.
  Recovers preprocessor flags only when the build system actually emits them;
  flags only documented in headers are not inferable.

Use the Lua commands from the current test or investigation context instead of keeping long examples in startup memory.


## Python Frontend / Bootstrap

The Python frontend is a separate subsystem from the mature C path. C-side
debugging rules do not automatically cover Python lowering, runtime fallback,
or bootstrap behavior.

### Bootstrap stage names

`scripts/bootstrap.sh --backend self` builds a staged compiler chain:

- `pcc0`: host Python running repository source.
- `pcc1`: first native compiler binary produced by `pcc0`.
- `pcc2`: compiler binary produced by `pcc1`.
- `pcc3`: compiler binary produced by `pcc2`.

The full self-host gate requires `pcc1` to compile `pcc2`, `pcc2` to compile
`pcc3`, and the stage outputs/baselines to match the relevant bootstrap
contracts. Do not describe a self-host fix as complete from a local toy repro
alone.

### Critical CLI knobs

| Flag | Default | Meaning |
|---|---|---|
| `--python-libpython` | `off` | `off`: hard error if codegen would need a CPython fallback. `auto`: link `libpython` only when fallback was needed. `on`: always allow/link the fallback surface. |
| `--ir-scaffold` | `on` | `on`: closed-world lowering used by the strict self-host work. `off`: older lowering escape hatch. `auto`: legacy mixed mode. |
| `--backend` | `llvm` | `llvm`, `llvm_capi`, or `self`. Strict self-host requires `--backend self`. |

Strict no-libpython self-host invocation:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o /tmp/pcc1_probe
```

Multi-file/bootstrap entry:

```bash
env -u LC_ALL uv run python scripts/pcc_multi.py --entry pkg.main \
  --out out_bin pkg/main.py ...
```

### Authoritative current state (do not invent)

- **Bootstrap byte-identity** (Issue 1 closure 2026-05-01):
  [`tests/bootstrap_gate_baseline.json`](tests/bootstrap_gate_baseline.json)
- **No-libpython fallback ratchet**:
  [`tests/fallback_baseline.json`](tests/fallback_baseline.json)
- These JSON files are the source of truth. `docs/issues/open-bootstrap-issues.md`
  is a historical tracker that may lag.

### Bootstrap regression discipline

When a gate that has been green for a long time regresses, treat that as
evidence that the current work is suspect until proven otherwise. Do not answer
with a local patch story before doing the causality audit.

Required sequence for bootstrap / pcc1 / no-libpython regressions:

1. Identify the first failing boundary in mode-labeled terms
   (`pcc0 -> pcc1` fallback, `pcc1 -> pcc2` runtime crash,
   `pcc2/pcc3` byte drift, no-host link failure, etc.).
2. List the recent touched subsystems that could plausibly own that boundary
   before changing more code. For codegen/runtime changes, assume your recent
   change is a prime suspect until IR/source/debugger evidence rules it out.
3. Separate stacked failures. If fixing the first boundary exposes a second
   crash, write them as two failures with two evidence chains; do not collapse
   them into one guessed root cause.
4. Do not weaken runtime or GC semantics to localize a bootstrap failure. In
   particular, do not disable GC tracking, barriers, owned-local cleanup,
   finalizers, or libpython rejection just to make a stage pass. Such changes
   are semantic changes, not diagnostics.
5. For ownership failures, verify the callee/caller reference contract before
   touching cleanup. Function calls return owned references; returning a
   borrowed local, parameter, module global, field, or singleton must retain in
   the callee rather than making the caller stop releasing owned results.
6. Host-side tests are not bootstrap proof. A Python-frontend/codegen/runtime
   fix that affects `pcc/py_frontend/codegen/`, `pcc/py_frontend/type_infer.py`,
   `pcc/py_runtime/`, or bootstrap entrypoints must include a focused
   regression and a pcc1/bootstrap gate appropriate to the touched path before
   it is described as fixed.
7. Debug instrumentation must be clearly tagged, recorded in the investigation,
   and removed or promoted to a deliberate tested feature before finishing. Do
   not leave temporary runtime probes that change archive staleness, link
   shape, or stage behavior.

### Dedicated gates for Python-frontend / bootstrap edits

```bash
gtimeout 120s env -u LC_ALL uv run pytest tests/python/test_py_multi_file_compile.py tests/python/test_py_multi_file_bootstrap_shim.py -q -x -n0
gtimeout 120s env -u LC_ALL uv run pytest tests/c/test_llvm_capi_ir_parity.py tests/c/test_llvm_capi_end_to_end.py -q -x -n0
gtimeout 120s env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py -q -x -n0
gtimeout 120s env -u LC_ALL uv run pytest tests/python/test_bootstrap_gate_baseline.py -q -x -n0
```

### Active self-host / package work

The active multi-stage plan is to compile pcc's runtime in pcc-Python and
shrink the libpython surface, while the current active goal prioritizes the
package/import path when it blocks real `pip install` / `import` scenarios.
Use `docs/goal/goal-prompt.md` for the goal contract and
`docs/current-goal-state.md` for the current selected task, evidence, and
investigation routing. Older plans such as
`docs/plans/python-runtime-no-c-plan.md` are background, not the active task
board.

### Subprocess vs in-process boundaries

Some bootstrap host queries intentionally use subprocess boundaries
(`PCC_HOST_PYTHON` or `python3`) instead of in-process libpython calls. In
particular, `_link_with_self_backend` must not reintroduce compiled-stage
imports/calls of `pcc.backend.*`; that brings `py_cpy_*` back into the
stage1 closure. The long-term target is to compile those backend modules
natively, not to grow in-process CPython fallback again.


## Runtime & GC Backends

The runtime ships **five GC backend slots** selected at runtime via
`PCC_GC_BACKEND` (enum at `pcc/py_runtime/include/py_runtime.h::PCC_GC_KIND_*`,
values 0..4). The current default decision is recorded in
`docs/investigations/gc-backend-selection-matrix.md`: backend #0 remains the
default and rollback reference.

Detailed backend status lives in `docs/current-goal-state.md` and routed GC investigations. Startup needs only the invariants below.


Rules when working on GC code:

- **Read the reference before patching.** `docs/refs_docs/gc-research/<lang>/` has
  the full upstream implementation; the pcc port is meant to mirror it. Do
  not re-derive — read.
- **Each backend gets its own focused gate**, not a shared one. Example:
  ```bash
  gtimeout 120s env -u LC_ALL PCC_GC_BACKEND=1 uv run pytest -x -n0 tests/python/test_gc_*.py
  gtimeout 120s env -u LC_ALL PCC_GC_BACKEND=2 PCC_WITH_THREADS=1 uv run pytest -x -n0 tests/python/test_gc_*.py
  gtimeout 120s env -u LC_ALL PCC_GC_BACKEND=3 uv run pytest -x -n0 tests/python/test_gc_*.py
  ```
- **Do not let backend X regress backend #0.** `PCC_GC_BACKEND=0` (default) is
  the reference; any new backend must keep stage2 / stage3 green.
- **One backend per PR** with one investigation file under
  `docs/investigations/`. No multi-backend bundle commits.
- **Keep migration mirrors differential-equal, then remove the C production
  dependency.** Most GC code currently has a C source file under
  `pcc/py_runtime/src/` and a pcc-Python port under `pcc/py_runtime/py/`.
  They must stay in sync while both exist; the end state links the
  freestanding pcc-Python implementation and retains C only as an oracle.

The `layer1.py` split has landed: `layer1.py` is now a facade, with behavior
split across focused `*_lowering.py` mixins plus `native_*.py` modules. When
adding Python lowering, choose the narrowest existing mixin or native module;
do not grow `layer1.py` again.


## Evidence Discipline (read before any measure/verify loop)

Every rule here was written after the same failure repeated in one session. The
common root is **treating something that looks like evidence as evidence**. Each
rule names the cheap check that would have caught it.

- **Never infer about new code from an old artifact.** A binary, `.o`, `.ll`,
  manifest, cache entry, or temp directory older than your edit tells you
  nothing about your edit. Check the timestamps before drawing a conclusion:
  ```bash
  stat -f '%Sm %N' -t '%d %H:%M' build/bootstrap/pcc1 <the-file-you-changed>
  ```
  Cost when skipped: a batch manifest produced at 12:51 by a `pcc1` built at
  12:17 was used to conclude that a fix made at 12:55 "did not take effect".
  See also `pcc/py_runtime/build_py/*.o`, which is keyed by SOURCE hash and
  does not invalidate when the compiler changes.

- **Never trial-and-error through a tool that swallows errors.** Get the error
  out FIRST, then debug. `scripts/bootstrap.sh` discards worker stderr and
  reports an unrelated downstream symptom ("linker has no inputs"); eight
  hypotheses were tested against it at 6-10 minutes each, all wrong, while the
  real message — `no-libpython function unavailable: run_worker_commands` —
  appeared instantly when `pcc1` was invoked directly. If a failure path has no
  diagnostic, **adding the diagnostic is the first fix**, not a detour.

- **Host-green is not pcc1-green, and it never will be.** Anything under
  `pcc/py_frontend/`, `pcc/backend/`, or `pcc/py_runtime/py/` is compiled by pcc
  itself and obeys a smaller language. Measured in one session: `wait -n` (macOS
  ships bash 3.2), `isinstance(x, str)` / `"\0" in x` rejecting plain decimal
  strings, a comprehension reusing a name already planned as an exact-int local,
  and `tempfile` / `shlex` / `os.mkfifo` simply being unavailable in the
  no-libpython closure. **Run the 30-second closure check before anything
  longer:**
  ```bash
  env -u LC_ALL uv run pcc --backend self --python-libpython=off \
      --ir-scaffold=on --python-library --emit-llvm=/tmp/chk.ll <the-file>
  ```
  If it fails, first re-run it on the `git show HEAD:<file>` version — several
  files do not compile in the closure at HEAD either, and that is not your bug.

- **Build the fast loop before the slow one.** In escalating cost:
  ```text
  ~30 s   closure check (above)
  ~5 s    pcc1-compiled microbenchmark for a cost-model question
  ~2 min  ./build/bootstrap/pcc1 ... pcc/__main__.py   (real errors, no wrapper)
  ~10 min scripts/bootstrap.sh --stage 1
  40 min+ full cold chain -- FINAL CONFIRMATION ONLY
  ```
  Never answer a question at a higher tier than it needs. Batch several verified
  changes into one expensive run rather than paying it per change.

- **Look up the historical number before calling a current one normal.** The
  repo records its own baselines (`docs/goal/task-board.yaml` baseline_metric,
  `docs/investigations/*.md`, `docs/issues/performance-gaps.md`). A stage2 at
  90 minutes and a cold stage1 at 589 s were both treated as "how slow it is"
  until a search found 434 s and 311 s recorded days earlier — they were
  regressions, and knowing that changes where you look.

- **Do not edit source a running measurement depends on.** Workers spawned
  later import the edited module mid-run and silently corrupt the result. Wait,
  or prototype in a scratch copy and apply afterwards.

- **A smoke input that omits the feature proves nothing.** The chained pcc1
  smoke check used `print(1)`. A module with no function has no phi node, which
  turned out to be the *only* shape that still compiled -- pcc1 could not build
  any program containing a `def` for a full day, invisibly, because the smoke
  input dodged the broken path. Every pcc1 gate also stopped at `--emit-llvm`,
  which returns *before* the self-backend, so the emit path had no pcc1-side
  coverage at all. A smoke input must contain a function definition and must
  compile through to a binary with `-o`, then run.

- **Never run pcc1/bootstrap tests while a bootstrap stage is in flight.** They
  trigger a runtime-archive `make` and fight over `build_py/`. The compile
  "failure" that comes back is contention, not a defect, and it can corrupt the
  concurrent stage.

- **An `id()`-keyed cache must keep its keyed objects alive.** Free one and the
  allocator can reuse the address, so a stale fingerprint *hits* and returns
  another key's answer. "Distinct objects just miss, so it can only lose an
  optimization" is wrong, and was written into a comment here before the bug was
  found. Store the keys with the answer: `entry = (answer, tuple(keys))`. Expect
  host-green/pcc1-red from any such structure -- the host often keeps the keys
  alive incidentally.

- **When the only surface is an empty error message, fix the message first.**
  `PCC-PY-COMPILE-001 ... exception_type=Exception` with no text cost four
  probed-and-denied hypotheses before the cause was reachable. Two small
  diagnostics made it a five-minute job: `str(exc) or type(exc).__name__` at the
  pipeline's re-raise sites, and `PCC_DEBUG_SELF_BACKEND_TRACE` phase markers in
  the emitter. Prefer cheap probes against the *existing* binary (seconds) over
  a rebuild (~8 min) when testing a hypothesis about pcc1 behavior.

- **`sample` names the leaf; only `cProfile` names the caller.** A stripped pcc1
  gives `sample` nothing but C leaves, and acting on them is how you optimize the
  wrong function: the leaf `_dot_numeric_text_key_id` was genuinely 100M calls,
  but memoizing it measured 1.6x because the real defect was a caller scanning a
  whole dict on every miss (fixed: 1.89x, byte-identical output). Get Python-level
  attribution on a representative input before editing. A microbenchmark that
  contradicts a profile is evidence, not noise -- record the denial.

- **One change per expensive verification.** Three changes landed together cost
  a full bisection when the run failed. If a batch is unavoidable, make each
  change individually revertible and bisect by disabling, not by rewriting.

## Debugging Playbook

**Before debugging any non-trivial failure, read and follow
[`docs/debugging-playbook.md`](docs/debugging-playbook.md) — required
procedure, not optional reference.** The 12 titles below are the index so you
know what exists and so `§N` references resolve; the linked file holds the
actual procedure (commands, oracle steps, LLDB recipe). Splitting it out of
this always-loaded file lowered its context cost, not its authority. Section
numbers are stable, so `§9` / `§12` referenced from investigations still
resolve. The techniques:

1. Make the failure deterministic first
2. Compare `pcc` against a reference from the same source
3. Use `llvmlite` as an oracle for `llvm_capi` parity
4. Treat short fallback / IR traces as locators only
5. Shrink the reproducer in stages
6. Test hypotheses by substitution, not only inspection
7. Avoid harness mistakes (these look like compiler bugs and are not)
8. Use LLDB for native crash triage, not guesswork
9. Do not stack unverified edits in shared codegen
10. Separate data-layout bugs from expression-semantics bugs
11. Prefer downstream-sensitive regression tests
12. Treat compile-time constant folding as a semantic subsystem


## C Codegen Invariants — Signedness

The repository lowers both `int` and `unsigned int` to LLVM `i32`. Signedness
is tracked **separately** in `pcc/codegen/c_codegen.py`.

Helpers:

- `_tag_unsigned`
- `_clear_unsigned`
- `_is_unsigned_val`
- `_convert_int_value`
- `_usual_arithmetic_conversion`
- `_shift_operand_conversion`

When you add or change an expression form, ask:

1. Does this produce an integer result?
2. If yes, should the result remain unsigned?
3. Will that result later feed `%`, `/`, `>>`, comparisons, or another
   arithmetic conversion?

Classic failure mode: the immediate value bits are correct but the returned
IR value is no longer marked unsigned, so a later operator uses `sdiv`,
`srem`, `ashr`, or signed comparison. This is easy to miss with toy tests
and shows up quickly in Lua, libc-heavy code, and control-flow-heavy
programs.


## Python Codegen / Runtime Invariants

The Python side has its own object-shape invariants that bugs cluster
around. Confirm them in source before guessing.

### Object header

- All heap objects start with `PyObjectHeader` (defined in
  `pcc/py_runtime/include/py_runtime.h` /
  `pcc/py_runtime/src/py_internal.h`).
- `refcount` at `obj + 0`, `type_tag` at `obj + 8` (int32). Type tag values
  in `enum PyTypeTag` (e.g. `PY_TYPE_STR == 4`, `PY_TYPE_LIST`,
  `PY_TYPE_DICT`, `PY_TYPE_INSTANCE`, `PY_TYPE_TASK`).
- Header `flags` carries `PY_FLAG_FINALIZED`, `PY_FLAG_GC_TRACKED`,
  `PY_FLAG_IMMORTAL`, etc. Checking and writing these is `__atomic_*` under
  `PCC_WITH_THREADS=1`.

### `PyClassObject` layout

- 120 bytes total. `del_method` is at offset 96, `attrs` at offset 104, and
  `metaclass` at offset 112. The pcc-Python mirror in
  `pcc/py_runtime/py/py_class.py` must match the C `PyClassObject` in
  `pcc/py_runtime/src/py_internal.h` exactly. Layout drift between them is a
  recurring class of bug.

### Refcount / GC discipline

- Read pointer slots through `pcc_gc_load_ptr()` and write them through
  `pcc_gc_store_ptr()`. These are barriers for backend #3 (generational
  forwarding) and #4 (relocation read barrier). Plain `obj->slot = x` works
  on backend #0 but breaks #3/#4.
- For generational backend #3 / colored-relocating #4, eager slot rewrite
  for owned items lives next to the per-type promotion code in
  `py_gc_backend.c`; do not invent a parallel path.
- `py_user_del_dispatch()` is invoked from `py_instance_dealloc()`. Setting
  `PY_FLAG_FINALIZED` after dispatch is what prevents resurrection cycles
  from re-entering the finalizer.

### Exception model

- `py_raise(exc)` stores in TLS and returns normally. Generated code must
  check `py_err_occurred()` after calls that may raise and branch to the
  error path. There is **no** Itanium-style stack unwinding; missing the
  check turns into "compile succeeded with no output" — see
  `docs/investigations/python-self-host-no-libpython-runtime-holes.md`.

### When this bites you

If a real-program failure looks like "object has no attribute X but the
class clearly defines X", it is almost always one of:

1. layout drift between C `PyClassObject` and `py_class.py`
2. missing `pcc_gc_load_ptr()` barrier on backend #3/#4
3. missing `py_err_occurred()` check after a raising call
4. the instance's **class object was freed by a stray over-release** — a
   self-host codegen ownership bug decrefs a *borrowed* reference as if owned,
   and because `py_class_new` does **not** mark user classes `PY_FLAG_IMMORTAL`
   (only the `object` root is immortal), the class hits refcount 0 and is
   `py_class_dealloc`'d + zeroed. Then `_lookup_field_index` reads `n_fields==0`
   and every `getattr` on any instance of that class fails ("attr not found"),
   or a moving backend segfaults in `class_lookup_in_mro`. Diagnose by
   watchpointing the class object's `n_fields` (`cls+72`, where
   `cls = *(inst+16)`) for a 119→0 transition and reading the `py_decref` stack.
   Fatal on all backends; the host compiler hides it. See
   `docs/investigations/pcc1-tuple-unpack-self-host-str-counter-corruption.md`.

Check in that order before suspecting frontend codegen. When one self-host
over-release corrupts a *shared* object (a class, an interned singleton), the
first visible symptom is usually far from the buggy release — chase the freed
object, not the innocent reader.


## Common Pitfalls

The recurring failure classes are: signedness metadata loss in C codegen, static/incomplete-array lowering, struct/union tag reuse, casted function-pointer globals, object-layout drift between C and pcc-Python runtime mirrors, missing GC barriers, missing `py_err_occurred()` checks, stale parser caches, and directory-mode probes accidentally compiled as source. Use `docs/investigations/INDEX.md` for the detailed historical routing instead of keeping the full table in startup memory.



## Testing & Definition of Done

For every semantic bug fix:

1. Add a focused regression test in `tests/`.
2. Confirm the original realistic reproducer is fixed.
3. Run the focused gates that cover the touched subsystem. Full-suite runs are
   optional by default and should be reserved for broad shared-path changes,
   release/commit qualification, or explicit user request.
4. Any commit-related completion must pass the bootstrap gate tests listed below.

For changes in shared parser/codegen paths, a stricter gate:

1. Run the smallest reproducer first.
2. Run one existing sensitive integration check for the same bug class
   before making another edit.
3. Only then continue or broaden the patch.

For critical bootstrap paths, the self-host must be proven green before the
work is considered fixed. This includes changes to `pcc/parse/py_lift.py`,
`pcc/parse/py_parse.py`, `pcc/py_frontend/py_ast.py`,
`pcc/py_frontend/pipeline.py`, `pcc/py_frontend/type_infer.py`,
`pcc/py_frontend/codegen/`, `pcc/cli_bootstrap.py`, the self backend, or any
runtime object/class/dataclass semantics used by those paths. A local
minimized reproducer is necessary but not sufficient: run the relevant
bootstrap command or `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py` and
do not claim the fix if stage1→stage2→stage3 is not demonstrated.

Recommended focused gates for high-risk changes:

```bash
gtimeout 120s env -u LC_ALL uv run pytest tests/c/test_c_parser.py -q -x -n0
gtimeout 120s env -u LC_ALL uv run pytest 'tests/c/test_lua.py::test_onelua_compile_and_link' -q -x -n0
gtimeout 120s env -u LC_ALL uv run pytest 'tests/c/test_lua.py::test_pcc_runtime_matches_native[math.lua]' -q -x -n0
gtimeout 120s env -u LC_ALL uv run pytest tests/c/test_lz4.py -q -x -n0
gtimeout 120s env -u LC_ALL uv run pytest tests/integration/test_sqlite.py -q -x -n0
gtimeout 120s env -u LC_ALL uv run pytest tests/c/test_unsigned_loads.py -q -x -n0
```

Python frontend / bootstrap gates: see *Python Frontend / Bootstrap* above.

GC backend gates: see *Runtime & GC Backends* above.

**Definition of Done** — before stopping, all of:

- [ ] smallest reproducer passes
- [ ] original integration scenario passes
- [ ] temporary debug edits and probe files removed
- [ ] regression test exists in `tests/`
- [ ] focused gates covering the touched subsystem pass; if a full suite is
  skipped, say exactly which subset was run and why it is sufficient for the
  current change
- [ ] bootstrap validation commands required for the submission were run successfully

Commit-level bootstrap validation (mandatory before considering work complete):

```bash
gtimeout 120s env -u LC_ALL uv run pytest tests/python/test_bootstrap_gate_baseline.py -q -x -n0
gtimeout 120s env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py -q -x -n0
```

Repair principle: prefer elegant fixes (API/ABI correctness, boundary semantics, readability, and maintainability). When possible, prioritize bootstrap-driven capability first and then add compatibility patches as follow-up.


## Package / NumPy Claim Hygiene

`B-P0-PKG` work is about real package install/import behavior, not a command
shape that only looks similar.

- `pcc1 -m pip install numpy ...` succeeds only when the real package artifact
  is installed into the target site and its package metadata is usable.
- `import numpy` is a separate gate. Do not claim NumPy support from install
  success alone, from an array-core-only test, or from a synthetic package
  named `numpy`.
- Do not add package-name special cases such as `if package == "numpy"` to make
  a gate pass. Fix the generic package/import/ABI/lowering behavior and add a
  focused regression for the generic feature.
- Native extension ABI compatibility and no-libpython behavior are separate
  claims. Keep `pcc-native` rejection, `cpython-compat` acceptance, and
  `PCC_HOST_PYTHON=/bin/false` evidence distinct.
- See the full claim-hygiene table in `docs/goal/goal-prompt.md` §0.10 for related
  distinctions such as host pcc vs pcc1, libpython mode vs no-libpython, fake
  package vs real package, and stage1 vs pcc1→pcc2→pcc3.
- Current package priority and known blockers live in
  `docs/current-goal-state.md`; the active protocol lives in
  `docs/goal/goal-prompt.md`.


## Platform Gotchas (macOS)

Keep platform-specific command details in focused tests or investigations. Startup reminder: macOS has no `/dev/full`, Lua builds need macOS flags, and Darwin multi-TU MCJIT teardown crashes can be lifecycle issues after correct runtime output.


## IR Fix Policy

Semantic bugs belong in parser / codegen source logic.
`postprocess_ir_text()` is acceptable **only** for narrow lowering gaps that
the IR builder cannot directly express.

- Do not hide CFG, type, or signedness bugs with text rewrites.
- Currently, the only acceptable remaining text-level lowering is the
  `va_arg` path. Anything else is a source-level compiler bug, fix it
  before serializing IR.
- The system-link path no longer hands LLVM IR text to the system compiler
  — `run_translation_units_with_system_cc()` optimizes each module with
  repository-managed LLVM and emits native objects directly. If you ever
  reintroduce a text-IR handoff, centralize attribute stripping (`nuw`,
  `nneg`, `range()`, `initializes()`, `dead_on_unwind`) in
  `postprocess_ir_text()`.


## Investigation Workflow (mandatory for any non-trivial bug)

Open a written investigation for anything more involved than a one-line fix,
so the next agent can pick it up without re-reading the chat log. **Before you
create or continue any `docs/investigations/*.md` file, read and follow
[`docs/investigation-workflow.md`](docs/investigation-workflow.md) first** — it
holds the mandatory-sections template and the three modes (Repro / Continue /
Report), and they are required, not optional. The enforceable guardrails, kept
inline so they bind even if the linked file is skipped:

- **Discover first.** Scan [`docs/investigations/INDEX.md`](docs/investigations/INDEX.md)
  before opening a new file; if the symptom matches, continue that file via a
  `## Update` block or link to it as predecessor. One investigation = one file
  under `docs/investigations/<specific-slug>.md`; existing files are historical
  record — never delete or rewrite them.
- **Read the matching investigation END TO END before writing any code, and
  read its `[DENIED]` and "did not help" sections first.** They are the most
  valuable part of the file and the part agents skip. Every one of them is a
  fix somebody already wrote, measured, and disproved — re-deriving it costs a
  full rebuild/measure cycle and produces a change that is known not to work.
  These docs are long on purpose. Skimming the headings is not reading them.
  If you are about to make a change that an investigation already recorded as
  refuted, you must either cite new evidence that overturns that verdict, or
  not make the change. Concrete recurring examples this rule exists for:
  - A `sample`/profiler "direct child" is often a **grandchild** whose parent
    frame the tail-call pass elided. Read the real lowered IR for the function
    (from the bootstrap's own `self_backend_module_*.ll`, not a standalone
    `--python-library` emit, which can lower a closure to a `raise` stub)
    before believing a caller attribution or optimizing the wrong callee.
  - A profile tells you **where to look**, not what to change. Changes made
    from profile shape alone have repeatedly measured zero or negative;
    the wins came from reading the code and the IR at the spot the profile
    pointed to.
  - `pcc/py_runtime/build_py/*.o` is keyed by **source** hash and does not
    invalidate when the compiler changes, so a codegen change measured without
    wiping the objects measures nothing.
- **Regenerate the index** after adding/editing any `docs/investigations/*.md`:
  ```bash
  env -u LC_ALL uv run python scripts/regen_investigations_index.py
  ```
- **Agents must not `git commit` unless the user explicitly asks** — the
  "save before each experiment" idiom is for the user-driven workflow.
- **One proposal at a time**, run to a `[CONFIRMED]`/`[DENIED]` verdict; no
  silent "while I was in there" fixes.
- `## Test [CONFIRMED]` means the failure was observed under the listed
  command, not optimism. `## Status` is mandatory and final. Two distinct
  bugs → two files.


## Maintainer Notes

Packaging/release notes are maintainer workflow, not active agent startup context. Consult project docs or ask before changing release metadata.
