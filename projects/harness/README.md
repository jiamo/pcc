# PCC DeepSeek Harness

This directory is the pcc-native Python port of
[`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness).
It is not a Python wrapper around the Node runtime. It is a product-scale proof
that self-hosted `pcc1` can replace CPython as the execution owner of a real,
long-running Python application. The production target is a native `pcc1`
self-backend executable with no libpython, Node.js, browser, Electron, or
WebView dependency.

## Current runnable slice

The first tracer bullet implements one complete deterministic agent turn:

```text
input -> durable-style event log -> prompt/tool assembly -> model decision
      -> optional tool call -> assistant response -> transcript projection
```

Build and run it from the PCC repository root:

```bash
projects/harness/harness
projects/harness/harness "hello"                 # explicit CLI turn
projects/harness/harness "/tool echo native pcc" # explicit CLI tool turn
projects/harness/harness --self-check
projects/harness/harness --gui-self-check
```

`harness` rebuilds `build/harness-core` with `pcc1` when project sources
change, then executes that native artifact. Compiler selection prefers an
explicit `PCC1`, then a project-local `build/pcc1`, then PCC's canonical
`build/bootstrap-self/pcc1` and shared stage-1 artifact. It never silently
falls back to the stale repository-root binary. Run `bootstrap-pcc1.sh` to
refresh the project-local compiler from the current PCC source tree. Stage 1
is constructed with the faster LLVM backend by default; the resulting `pcc1`
is backend-agnostic and `build.sh` uses its PCC self backend for Harness. Set
`PCC_HARNESS_BOOTSTRAP_BACKEND=self` only when validating stage-1 construction
through the self emitter itself. The project-local compiler is bound to
`build/pcc1-source.json`; `build.sh` rejects a changed compiler artifact or
stale PCC source digest instead of silently building with an older `pcc1`.

With no arguments, `harness` opens the native PCC AppKit/Metal GUI. The first
shell mirrors the upstream light-theme column geometry and exposes the
sidebar, session navigation, Chat/Trajectory views, composer, status and
settings regions. Clicking the composer send control submits the deterministic
sample through PCC's typed GUI command registry into the same logged agent
core; **New Session** clears it. This is the first parity shell, not a claim of
complete Web UI parity yet.

The runtime now also includes a reactive Cordis-style Fiber graph with pending
Consumers, scoped service realms, Provider-identity reload, committed teardown
bindings, dependency diagnostics, five event dispatch modes and reversible
effects. Session state uses atomic JSONL persistence; settings are revisioned
and atomically persisted; credential references stay secret-safe; anonymous
identity, whole-list `todo_write`, and logged plan mode are assembled into the
same runtime. The executable still selects a deterministic local model by
default. Async Loader/HMR parity, real PCC HTTP/TLS Provider wiring, complete
capability composition and native GUI pixel parity remain open; the runnable
slice is not a full-port claim.

## Target architecture

The port preserves the upstream domain split while using PCC-native owners:

| Upstream domain | PCC port owner |
|---|---|
| session event log and projections | Python core, then PCC persistence |
| system prompt, todo/plan and tool registry | Python core |
| agent loop and live events | `pcc.virtual_thread`-driven Python runtime |
| LLM streaming | PCC HTTP client and virtual-thread I/O |
| web host | `pcc.gateway` / `pcc.web` where a server surface is needed |
| Web React client | PCC declarative native GUI; no browser compatibility layer |
| filesystem, shell, terminal and LSP | PCC capability providers |
| settings, credentials and identity | validated Python providers over PCC filesystem primitives |
| profile/bundle composition | Python reactive Fiber/realm/effect runtime, then declarative Loader |

The final `projects/harness/harness` command will open the PCC native GUI. Its
layout, visible state, interactions, streaming trajectory, approvals,
settings, session navigation, and error states must match the upstream Web UI.
Implementation technology is intentionally different; observable behavior and
pixels are the parity target.

Harness requirements are allowed to drive general PCC development. Missing
Python semantics, stdlib modules, compiler lowerings, runtime/GC behavior,
virtual-thread facilities, networking, persistence, GUI primitives and native
ABI support belong in PCC with focused compiler/runtime tests. The port will
not simplify product behavior merely to stay inside PCC's current subset.

## Task tracking

The canonical PCC task board is `../../docs/goal/task-board.yaml`; this
project's rows have the `HARNESS-` prefix.
[`TASKS.md`](TASKS.md) is the project-local complete index for the pinned
upstream commit and maps every upstream package domain to a finite board row.
It includes PCC compiler/runtime prerequisites, native GUI parity, all
capabilities and protocols, release closure, commit-ledger enforcement and
scheduled upstream discovery.

## Upstream tracking

[`migration/upstream.json`](migration/upstream.json) pins the official remote,
branch, and last audited commit. [`migration/README.md`](migration/README.md)
defines the commit-to-commit migration ledger. A later task adds automated
upstream discovery and task generation; until that lands, update the pin only
after an audited comparison.
