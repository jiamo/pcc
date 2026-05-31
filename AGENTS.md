# AGENTS.md

This file is for humans and AI agents working in this repository.

## Read Next

Startup route for active goal work:

1. Read this file first for repository rules and safety constraints.
2. Read `codex-goal-prompt.md` for the current goal contract and work protocol.
3. Read `docs/current-goal-state.md` for the current audit state and the
   investigation/doc routing that should be read next.
4. Use `docs/investigations/INDEX.md` to find relevant prior investigations
   before opening or continuing a non-trivial bug.
5. **Task-conditional, required — not optional reference.** The moment the task
   becomes *debugging a failure*, read and follow
   [`docs/debugging-playbook.md`](docs/debugging-playbook.md) before guessing.
   The moment you *open or continue an investigation*, read and follow
   [`docs/investigation-workflow.md`](docs/investigation-workflow.md) first.
   These two were split out of this file to stay under the context budget;
   the split lowered their resident-in-context cost, **not** their authority.

## Project Intent (north star — read before changing direction)

> This section is the top-level design contract. It exists to keep autonomous
> work aligned: when a change would trade away one of the obligations below for
> a local win — a faster benchmark, a greener gate, a smaller diff, a passing
> bootstrap by rewrite — **stop and surface the tradeoff instead of taking it
> silently.** This section is the *why*; `codex-goal-prompt.md` is the *how*
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
`codex-goal-prompt.md`; the one-line form here is the guardrail, and the
parenthetical is where it is actually enforced:

```text
1. Compatibility must be mode-labeled. A claim must say which mode produced it:
     host pcc != pcc1   |   cpython-compat != pcc-native
     libpython != no-libpython   |   LLVM-backed != self-backed
     stage1 != pcc1->pcc2->pcc3 fixed point
   (codex-goal-prompt §0.10 claim hygiene, §9.2 mode boundaries)

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

**Runtime layering: shrink the C runtime to a kernel; do not eliminate it.**
pcc does not aim to eliminate all low-level native runtime code. The long-term
goal is to minimize the C-level runtime into a small ABI kernel — allocation,
object headers, atomics/refcount barriers, platform syscalls, threading
primitives, dynamic loading, C-extension entrypoints, safepoints/stack maps,
and GC primitives — while Python *semantics* migrate into pcc-Python and are
compiled by pcc itself. The C kernel remains as the machine boundary; **it must
not become a second, hand-maintained C version of the Python semantic runtime
running in parallel with the pcc-Python one.** Distinguish four layers (do not
say "C runtime" loosely — it conflates them):

```text
C-level kernel        KEEP (minimize): platform/ABI, alloc, atomics, threads,
                      dlopen, syscalls, safepoints, GC slot/root primitives.
                      Knows no high-level Python semantics (no list/dict/dunder/
                      valueclass/import policy; no `if package == "numpy"`).
C semantic runtime    SHRINK: hand-written C list/dict/str/dunder/exception
                      semantics -> migrate to pcc-Python.
pcc-Python runtime    GROW: the migration target; Python semantics authored in
                      pcc-Python, self-hostable, testable, compiled by pcc.
C-API shim            KEEP but spec/generate: the ABI surface extensions see;
                      != CPython/libpython.
```

This does not contradict no-libpython: no-libpython means not depending on the
CPython runtime, NOT that the final binary contains zero C-level runtime. It
ties directly to the **5-GC Production Equality Rule** (codex-goal-prompt.md,
G-track): all five GC backends, the C kernel, and the pcc-Python mirror must
consume ONE slot-based trace/update contract (`py_obj_visit_slots` /
`py_obj_update_slot` / root + frame + native-handle registration) so there is
never a second parallel set of object-graph rules to drift. The C kernel and
the pcc-Python semantic runtime are connected by a stable, spec'd runtime ABI
(Layer 1) precisely to prevent that drift.

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
  env -u LC_ALL uv run pytest -q
  env -u LC_ALL uv run pcc hello.c
  ```

- While debugging one failure, prefer `-n0` so xdist does not hide ordering or
  temp-file problems.
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
  specific reason** the run will legitimately take longer (e.g. "full
  pytest suite is 6–8 min → use 600s", "stage1 self-host bootstrap is
  ~4–5 min → use 360s"). "I'll just let it run" is not a reason.
  Without a timeout, a hung collection / codegen infinite loop / runaway
  probe silently burns the foreground turn and can leave zombie children
  pegging CPU for hours (this has happened — ~120 CPU-hours lost once).
  Before ending a session, `ps aux | grep <your-probe-name>` and `kill`
  any leftover children. Prefer the Bash tool's `timeout` field; fall
  back to a `timeout <Ns>` prefix when shelling out from a script.
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
| `pcc/extern/`, `pcc/unsafe/` | Python→C extern decls; compiler-recognized intrinsics |
| `utils/fake_libc_include/` | Fake libc headers (host ABI / decl mismatches surface here) |
| `tests/` | Unit, parity, integration regression coverage |
| `tests/py_corpus/phase*/` | End-to-end Python corpus retained from the earlier phase taxonomy. These tests still run; the phase framework is no longer the active task board. See current priorities in `docs/current-goal-state.md`. |
| `tests/python/test_self_host_oracle_diff.py` | Core Python semantic oracle / pcc1-pcc2 parity ratchet |
| `tests/python/test_pcc_bootstrap_full.py` | Full stage1→stage2→stage3 self-backend bootstrap gate |
| `tests/bootstrap_gate_baseline.json` | **Authoritative bootstrap state** (Issue 1 closure evidence) |
| `tests/fallback_baseline.json` | **Authoritative no-libpython fallback state** |
| `scripts/bootstrap.sh` | macOS arm64 three-stage bootstrap entry |
| `scripts/pcc_multi.py` | Experimental multi-file Python entry |
| `projects/lua-5.5.0/` | Real-program stress target |
| `docs/refs_docs/gc-research/` | Reference impls for the 5 GC backends (Lua, Go, OCaml, ZGC, CPython) |
| `codex-goal-prompt.md` | Active goal contract / work protocol index |
| `docs/current-goal-state.md` | Current goal audit, selected task state, and investigation routing |
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

### Dedicated gates for Python-frontend / bootstrap edits

```bash
env -u LC_ALL uv run pytest tests/python/test_py_multi_file_compile.py tests/python/test_py_multi_file_bootstrap_shim.py -q -n0
env -u LC_ALL uv run pytest tests/c/test_llvm_capi_ir_parity.py tests/c/test_llvm_capi_end_to_end.py -q -n0
env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py -q -n0
env -u LC_ALL uv run pytest tests/python/test_bootstrap_gate_baseline.py -q -n0
```

### Active self-host / package work

The active multi-stage plan is to compile pcc's runtime in pcc-Python and
shrink the libpython surface, while the current active goal prioritizes the
package/import path when it blocks real `pip install` / `import` scenarios.
Use `codex-goal-prompt.md` for the goal contract and
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
  PCC_GC_BACKEND=1 env -u LC_ALL uv run pytest -n0 tests/python/test_gc_*.py
  PCC_GC_BACKEND=2 PCC_WITH_THREADS=1 env -u LC_ALL uv run pytest -n0 tests/python/test_gc_*.py
  PCC_GC_BACKEND=3 env -u LC_ALL uv run pytest -n0 tests/python/test_gc_*.py
  ```
- **Do not let backend X regress backend #0.** `PCC_GC_BACKEND=0` (default) is
  the reference; any new backend must keep stage2 / stage3 green.
- **One backend per PR** with one investigation file under
  `docs/investigations/`. No multi-backend bundle commits.
- **Mirror C and pcc-Python runtimes.** Most GC code has a C source file
  under `pcc/py_runtime/src/` and a pcc-Python port under
  `pcc/py_runtime/py/`. They must stay in sync.

The `layer1.py` split has landed: `layer1.py` is now a facade, with behavior
split across focused `*_lowering.py` mixins plus `native_*.py` modules. When
adding Python lowering, choose the narrowest existing mixin or native module;
do not grow `layer1.py` again.


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

Check in that order before suspecting frontend codegen.


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
bootstrap command or `tests/python/test_pcc_bootstrap_full.py` and do not
claim the fix if stage1→stage2→stage3 is not demonstrated.

Recommended focused gates for high-risk changes:

```bash
env -u LC_ALL uv run pytest tests/c/test_c_parser.py -q -n0
env -u LC_ALL uv run pytest 'tests/c/test_lua.py::test_onelua_compile_and_link' -q -n0
env -u LC_ALL uv run pytest 'tests/c/test_lua.py::test_pcc_runtime_matches_native[math.lua]' -q -n0
env -u LC_ALL uv run pytest tests/c/test_lz4.py -q -n0
env -u LC_ALL uv run pytest tests/integration/test_sqlite.py -q -n0
env -u LC_ALL uv run pytest tests/c/test_unsigned_loads.py -q -n0
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
env -u LC_ALL uv run pytest tests/python/test_bootstrap_gate_baseline.py -q -n0
env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py -q -n0
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
- See the full claim-hygiene table in `codex-goal-prompt.md` §0.10 for related
  distinctions such as host pcc vs pcc1, libpython mode vs no-libpython, fake
  package vs real package, and stage1 vs pcc1→pcc2→pcc3.
- Current package priority and known blockers live in
  `docs/current-goal-state.md`; the active protocol lives in
  `codex-goal-prompt.md`.


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
