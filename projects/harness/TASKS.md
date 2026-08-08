# Harness migration tasks

The canonical machine-readable task state is
`../../docs/goal/task-board.yaml`. This file is the project-local index for
all `HARNESS-` rows. The inventory is exhaustive for upstream
`deepseek-ai/deepseek-harness` commit
`47f943859bef60e4160492346772ded9b24f765a` at package-domain level. A task
may produce smaller implementation rows when its first source audit identifies
independently releasable slices.

New upstream commits are not silently absorbed into these rows.
`HARNESS-P1-UPSTREAM-CONVERGENCE` classifies every intervening commit and
adds finite follow-up rows before advancing the audited pin.

## Foundation and execution ownership

| Task | Upstream or PCC surface |
|---|---|
| `HARNESS-P0-MIGRATION-INVENTORY` | Complete package-domain inventory and dependency graph |
| `HARNESS-P0-MIGRATION-LEDGER-GATE` | Commit-to-upstream migration ledger schema and validation |
| `HARNESS-P0-NATIVE-CORE` | Core turn, event log, prompt, tools and agent loop tracer bullet |
| `HARNESS-P0-CURRENT-PCC1` | Current-source project-local pcc1 and no-libpython/self build closure |
| `HARNESS-P0-NATIVE-GUI-SHELL` | PCC declarative AppKit/Metal shell and GUI command path |
| `HARNESS-P0-GUI-FILLS-AND-CAPTURE-TRUTH` | Missing rect fills in the captured window; render-vs-capture separation |
| `HARNESS-P0-GUI-WINDOW-VIEWPORT` | Live viewport-driven layout, geometry hit-testing, scrolling and capacity policy |
| `HARNESS-P0-GUI-TEXT-INPUT-IME` | Keyboard/character input, editable composer, IME composition and measured CJK text |
| `HARNESS-P1-PLUGIN-EFFECT-KERNEL` | Cordis-style plugin scopes, services, effects and disposal |
| `HARNESS-P1-SESSION-EVENT-COMPAT` | Complete logged model-visible event vocabulary and replay |
| `HARNESS-P1-PCC-SQLITE-DURABILITY` | Reusable PCC SQLite/transaction/migration foundation |
| `HARNESS-P1-SESSION-COMPOSITION` | Session persistence, projection, resume and fork |
| `HARNESS-P1-SETTINGS-IDENTITY-CREDENTIALS` | settings, identity and credential references |
| `HARNESS-P1-PRESET-BUNDLE-SELF-MOD` | presets, bundles, boot composition and self-modification |

## Models, networking and API

| Task | Upstream or PCC surface |
|---|---|
| `HARNESS-P1-MODEL-PROVIDER-REGISTRY` | LLM definition/provider/consumer registry |
| `HARNESS-P1-PCC-HTTP-VTHREAD` | PCC virtual-thread DNS/TLS/HTTP/SSE client foundation |
| `HARNESS-P1-DEEPSEEK-STREAMING` | DeepSeek request, streaming, cancellation, retry and errors |
| `HARNESS-P1-CONTEXT-COMPACTION-GUARD` | request context, compaction and loop/tool guards |
| `HARNESS-P1-API-TYPERT-RPC` | Typert graph, JSON-RPC, BFF and PCC WebGateway server |
| `HARNESS-P1-PYTHON-SDK-PROTOCOL` | Python SDK and bundled-runtime protocol compatibility |
| `HARNESS-P1-ACP-HOOKS-AUTOMATION` | ACP server and Claude Code/Codex hook bridges |
| `HARNESS-P1-CLI-BOOT-EXAMPLES` | CLI/headless boot profiles and runnable examples |

## Capabilities

| Task | Upstream or PCC surface |
|---|---|
| `HARNESS-P1-FS-SHELL-SUBPROCESS` | filesystem policy, shell and process-tree providers |
| `HARNESS-P1-SANDBOX-PERMISSION` | approval policy, Landlock/native sandbox and audit |
| `HARNESS-P1-TERMINAL-LSP` | persistent terminal and language-server lifecycle |
| `HARNESS-P1-WEB-SEARCH-FETCH` | search/fetch providers on PCC HTTP/WebGateway |
| `HARNESS-P1-SKILL-CATALOG` | skill provider registry, loader and catalog tool |
| `HARNESS-P1-INTERACTION-APPROVALS` | interaction, ask-user, approvals and commands |
| `HARNESS-P1-TODO-PLAN` | logged todo and plan-mode tools |
| `HARNESS-P1-SUBAGENT-WORKFLOW-JOBS` | subagents, worker jobs and workflow consumers |
| `HARNESS-P1-E2B-SANDBOX` | E2B sandbox and remote FS/subprocess adapters |
| `HARNESS-P1-CAPABILITY-SEAMS` | assembled definition/provider/consumer conformance |

## Native product surface and release

| Task | Upstream or PCC surface |
|---|---|
| `HARNESS-P1-GUI-SESSIONS-STREAMING` | session navigation, live chat and trajectory |
| `HARNESS-P1-GUI-TOOLS-APPROVALS` | generic/terminal/diff tool rendering and approvals |
| `HARNESS-P1-GUI-SETTINGS-PROFILES` | settings, credentials, workspaces and profiles |
| `HARNESS-P1-GUI-PAINT-PRIMITIVES` | corner radius, borders, shadows, icon channel and font weight scale |
| `HARNESS-P1-GUI-UPSTREAM-SURFACE-INVENTORY` | plugin-granular mapping of every upstream client UI surface to an owner |
| `HARNESS-P1-UI-PARITY-ACCESSIBILITY` | viewport, interaction, pixel and accessibility parity |
| `HARNESS-P1-SNAPSHOT-E2E-CONFORMANCE` | keyless snapshots, real API e2e and protocol fixtures |
| `HARNESS-P2-PERFORMANCE-SOAK` | latency, memory, cancellation and leak soak gates |
| `HARNESS-P1-PACKAGING-RELEASE` | installable native distribution and update metadata |
| `HARNESS-P1-DOCS-OPERATIONS` | architecture, operator, migration and contributor docs |
| `HARNESS-P1-UPSTREAM-CONVERGENCE` | recurring upstream range classification and task creation |
| `HARNESS-P1-UPSTREAM-WATCH-AUTOMATION` | scheduled discovery, deduplicated reports and notifications |
| `HARNESS-P1-FULL-PORT-EXIT` | final pinned-upstream feature/parity/release closure |

## Rules

- Every implementation commit updates one
  `migration/commits/NNNN-*.md` ledger entry with upstream domains, PCC
  compiler/runtime changes, tests and remaining boundaries.
- Missing reusable functionality is implemented in PCC and receives focused
  PCC tests; Harness-local compatibility shadows are not accepted.
- A task reaches `DONE_STRONG` only after its current-source
  `pcc1 --backend self --python-libpython off` acceptance and listed gates
  pass.
- The full-port exit does not close while an upstream package domain is
  unmapped, an accepted upstream commit lacks a disposition, or the native UI
  still requires Node.js, JavaScript, a browser, WebView or CPython.

## Pinned upstream path map

| Upstream path/domain | Owning task ids |
|---|---|
| `vendor/` Cordis | `HARNESS-P1-PLUGIN-EFFECT-KERNEL` |
| `packages/core/session`, `system-prompt`, `tools`, `agent`, `agent-loop` | `HARNESS-P0-NATIVE-CORE`, `HARNESS-P1-SESSION-EVENT-COMPAT`, `HARNESS-P1-MODEL-PROVIDER-REGISTRY`, `HARNESS-P1-CONTEXT-COMPACTION-GUARD` |
| `packages/api/`, `packages/typert/`, `packages/sdk/` | `HARNESS-P1-API-TYPERT-RPC`, `HARNESS-P1-PYTHON-SDK-PROTOCOL` |
| `packages/llm/` | `HARNESS-P1-MODEL-PROVIDER-REGISTRY`, `HARNESS-P1-DEEPSEEK-STREAMING` |
| `packages/e2b/` | `HARNESS-P1-E2B-SANDBOX` |
| `packages/shell/`, `packages/subprocess/`, `packages/fs/` | `HARNESS-P1-FS-SHELL-SUBPROCESS`, `HARNESS-P1-SANDBOX-PERMISSION` |
| `packages/terminal/`, `packages/lsp/` | `HARNESS-P1-TERMINAL-LSP`, `HARNESS-P1-GUI-TOOLS-APPROVALS` |
| `packages/skill/` | `HARNESS-P1-SKILL-CATALOG` |
| `packages/web/` | `HARNESS-P1-PCC-HTTP-VTHREAD`, `HARNESS-P1-WEB-SEARCH-FETCH` |
| `packages/compaction/`, `packages/context/`, `packages/guard/` | `HARNESS-P1-CONTEXT-COMPACTION-GUARD` |
| `packages/subagent/`, `packages/workflow/` | `HARNESS-P1-SUBAGENT-WORKFLOW-JOBS` |
| `packages/todo/`, `packages/plan/` | `HARNESS-P1-TODO-PLAN` |
| `packages/preset/`, `packages/bundle/`, `packages/boot/`, `packages/self-modification/` | `HARNESS-P1-PRESET-BUNDLE-SELF-MOD`, `HARNESS-P1-CLI-BOOT-EXAMPLES` |
| `packages/hooks/`, `packages/acp/` | `HARNESS-P1-ACP-HOOKS-AUTOMATION` |
| `packages/session/` | `HARNESS-P1-SESSION-EVENT-COMPAT`, `HARNESS-P1-PCC-SQLITE-DURABILITY`, `HARNESS-P1-SESSION-COMPOSITION` |
| `packages/identity/`, `packages/settings/`, `packages/credentials/` | `HARNESS-P1-SETTINGS-IDENTITY-CREDENTIALS` |
| `packages/interaction/` | `HARNESS-P1-INTERACTION-APPROVALS`, `HARNESS-P1-SANDBOX-PERMISSION` |
| `packages/examples/`, root `examples/`, `packages/support/`, `packages/util/` | `HARNESS-P1-CLI-BOOT-EXAMPLES`, `HARNESS-P1-SNAPSHOT-E2E-CONFORMANCE` |
| `python/` SDK and bundled runtime | `HARNESS-P1-PYTHON-SDK-PROTOCOL`, `HARNESS-P1-PACKAGING-RELEASE` |
| `native/` Landlock runner | `HARNESS-P1-SANDBOX-PERMISSION`, `HARNESS-P1-PACKAGING-RELEASE` |
| upstream Web UI/product states | `HARNESS-P0-NATIVE-GUI-SHELL`, `HARNESS-P0-GUI-WINDOW-VIEWPORT`, `HARNESS-P0-GUI-TEXT-INPUT-IME`, the three P1 GUI feature rows above, `HARNESS-P1-UI-PARITY-ACCESSIBILITY`; plugin-granular ownership is still open and owned by `HARNESS-P1-GUI-UPSTREAM-SURFACE-INVENTORY` |
| `docs/`, `website/`, `.agents/` process notes | `HARNESS-P1-DOCS-OPERATIONS`, `HARNESS-P0-MIGRATION-LEDGER-GATE` |
| `scripts/` gates and generators | `HARNESS-P1-SNAPSHOT-E2E-CONFORMANCE`, `HARNESS-P1-PACKAGING-RELEASE`, `HARNESS-P1-UPSTREAM-CONVERGENCE` |
