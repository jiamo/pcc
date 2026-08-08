# Harness port architecture

## Invariants

1. The executable is compiled by the current PCC `pcc1` self backend with
   libpython disabled; the runtime process has no CPython owner or fallback.
2. Anything visible to the model is reconstructable from the session event
   log.
3. Capability registration is reversible and scoped; a provider owns its
   cleanup.
4. A capability comprises its definition, provider, and consumer path.
5. The native GUI renders from session projections and sends commands to the
   agent runtime; it does not become a second source of session truth.
6. Cross-process and durable data is explicitly validated and versioned.
7. Upstream parity is recorded by upstream commit, PCC commit, task, behavior,
   and verification evidence.
8. A missing general Python or platform capability is implemented in the PCC
   compiler/runtime and proven both by a minimized PCC regression and the
   realistic Harness acceptance path.

## Runtime map

```text
PCC native GUI
    | commands                          session projections
    v                                           ^
Agent registry -> Agent loop -> Session event log -> persistence
       |              |              |
       |              +-> todo/plan projections
       |              |
       +-> settings references -> credential provider -> secret value at use
                      |
                      |              +-> transcript / telemetry / replay
                      v
              prompt + tool registry
                      |
                      v
                LLM provider
                      |
                      v
        PCC virtual-thread HTTP client

Tools -> capability consumers -> PCC providers
       (fs/shell/terminal/LSP/web/subagent/workflow)
```

The current tracer bullet implements the center line with two independent
runtime axes. The Session axis is an append-only, versioned event log with
atomic JSONL storage and deterministic projections. The Cordis axis is a
reactive Fiber graph: a Consumer remains `PENDING` until every injected service
is visible in its selected realm, commits Provider identities while active,
and unloads/reloads when a Provider is withdrawn or replaced. Provider
withdrawal drains dependent Consumers before the Provider scope releases its
resource effects. Explicit private or joined realms isolate same-name services
between subtrees. The event registry implements emit, parallel, serial, bail
and explicitly delegated waterfall semantics; every listener, service and
effect belongs to a scope and is removed during reverse teardown.

The same runnable slice also includes a deterministic model,
settings/credential/identity providers, and logged todo/plan state. The default
app remains keyless and in-process. Every later task replaces or extends one
named Provider without changing the logged turn semantics.

Settings store JSON-compatible user sections separately from secret values.
Configuration carries a credential reference such as `DEEPSEEK_API_KEY`; the
credential provider resolves it at each provider operation. Descriptors,
session events and GUI state expose configured/source/writable facts only.
Local secret files are owner-only and atomically replaced.

## Deliberate differences from upstream

- Cordis source is not embedded. Context inheritance, service realms, reactive
  Fiber activation, committed Provider bindings, dependency diagnostics,
  event modes and owned effects are reimplemented in Python over PCC runtime
  facilities. Async effect iteration, declarative Loader reconciliation/HMR
  and runtime-graph inspection remain separate migration tasks.
- The React/Vite client is replaced by PCC's declarative native GUI.
- Node worker threads are replaced by PCC virtual threads and structured
  task ownership.
- Node-specific PTY, filesystem, watcher, and native-addon integrations are
  replaced by PCC-owned capability providers.

These are implementation differences, not permission to change user-visible
behavior.
