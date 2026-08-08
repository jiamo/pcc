# Design: absorbing declarative-UI concepts into the pcc GUI stack

Status: reviewed, taskized, and declarative ABI v1 frozen. Production GUI
implementation remains in the downstream task rows. The machine-readable
authority is `pcc/py_runtime/gui_declarative_contract_v1.json`; the pinned,
license-labeled upstream reference note is
`docs/refs_docs/gui-declarative/README.md`.

Review verdict: the direction is reasonable, but the original draft was not
implementation-ready.  It overstated the current kernel, treated temporary
compiler workarounds as language rules, and simultaneously skipped and
required reconciliation.  "Fully absorb" here means the complete selected
mechanism family, not a sampler: React-style queued reducer updates, priority
lanes, keyed render/commit reconciliation and phased effects/lifecycle; Tailwind-style
namespaced tokens, utility generators and class-string parse/compile/cache;
and Tauri-style target listeners, managed state, command request/result/error
separation and lifecycle.  It does not mean wire compatibility or feature
parity with every React, CSS/Tailwind, or Tauri API.

## Goal

Absorb three bounded but complete concept families -- declarative components
and scheduling (React), atomic styling plus utility compilation (Tailwind),
and target-filtered events/managed state/command separation/lifecycle
(Tauri's backend model, implemented in-process without a webview) -- into the
existing pcc GUI, **without a half-finished layer**.
Every abstraction must have a working end-to-end path (component -> node ->
render -> event -> state -> re-render) with tests.

## What exists (the host)

The repository has a promising composition-tree kernel, but not yet one
verified host:

- `pcc/py_runtime/py/pcc_gui_kit.py` contains an append-only node pool,
  stack-v/h plus a not-yet-correct dock branch, bounds, text/colour data,
  hit/bubble helpers, and a rect/text command walk.
- `projects/mac_diff_app/pcc_gui_kit.py` is a project-local shadow used by
  `kit_window.py`; the runtime owner is not in the production runtime module
  list.
- `projects/mac_diff_app/app.py`, the entrypoint used by `build.sh` and the
  tests, inlines another smaller kernel.  The accepted split-line-table and
  changed-row coalescing behavior instead lives in `kit_window.py`.
- Existing tests cover the older `pcc_gui_control_*` ABI and pre-loop diff
  statistics.  They do not directly prove `pcc_gui_kit` render/event behavior,
  a click-to-state loop, current `pcc1`, or window pixels.
- `pcc_gui_binding.py` and `pcc_gui_theme_anim.py` already own production
  binding/command and numeric theme surfaces.  New layers must absorb or
  extend them, not create unsynchronized parallel tables.

The kernel is intended to become the committed render tree.  Canonical
ownership, structural mutation, paint-order hit testing, dock semantics,
public scroll, and real ancestor clipping are prerequisites, not assumed
capabilities.

## Concept mapping (honest)

| React/Tailwind concept | pcc absorption | where |
|---|---|---|
| function component | registered typed callback id invoked through one fixed context-record ABI | new `pcc_gui_components` |
| props (immutable input) | caller-owned bounded record of explicitly typed slots | components |
| state | per-component queued update records (lane + slot + reducer/action); drained by scheduler | components |
| re-render on state change | dirty-component walk updates only that subtree | components |
| lifecycle/effects | before-mutation, mutation, layout and passive phases with registered cleanup/effect ids | components |
| event handlers (onClick) | kernel hit/path -> node owner -> listener id + target filter -> target/bubble -> `set_state` | components |
| virtual DOM diff | no general VDOM; a bounded sibling-key/type reconciler emits insert/move/update/remove effects | components |
| scheduling lanes | discrete/animation/default/background lanes; sync or budgeted yieldable work loop | components |
| Tailwind utility classes | typed helpers plus a bounded class-string parser/compiler/cache over registered utilities | new `pcc_gui_style` |
| theme variables | namespaced ids extending the existing numeric theme table | style |
| Tauri command boundary | typed invoke packet -> registered command -> result/error resolver, with target policy | components/commands |
| component composition | fn returns child descriptors; kernel builds the tree | components |

## Architecture (owned layers plus one command service)

```
app (declarative: components + state)
   |
   |  component layer (pcc_gui_components)   — NEW
   |  descriptor work/commit; reducer queues; lanes; lifecycle; listeners
   |
   |  command service (pcc_gui_commands)      — NEW
   |  typed managed state; invoke registry; result/error resolver
   |
   |  style layer (pcc_gui_style)            — NEW
   |  canonical theme namespaces; utilities; candidate compiler/cache
   |
   |  kernel (pcc_gui_kit)                   — KEEP (host)
   |  nodes/layout/events/render
```

Rule: the component/style layers only translate *descriptions* into node
updates.  Layout, painted hit/path extraction, scrolling and rendering stay in
the kernel; the single component listener registry alone owns target/bubble
dispatch over that path.  The current `pcc_kit_route_event` behavior must be
versioned/superseded so it cannot perform a second, incompatible bubble.  This
does not mean the current kernel code is
unchanged: the prerequisite task adds the missing structural, spacing,
scroll/clipping, and hit-order contracts while preserving existing proven
behavior.

The command service is not another render layer.  It owns application/backend
requests and typed managed state; components consume its resolved results by
enqueueing ordinary state updates.

Canonicality rule: production owners live under `pcc/py_runtime/py/` and are
either explicitly compiled into the production runtime archive or explicitly
app-compiled.  Project-local shadows and inlined kernels may remain as
temporary behavior oracles, but cannot be the implementation selected by the
final build and tests.

## Component model (design, not code)

### Frozen v1 ABI authority

The prose below explains intent. Exact field offsets, sizes, ownership,
callback signatures, limits, lane aging thresholds, replay rules, phase ids,
stable error codes, command/app transition tables and nonclaims live in
`pcc/py_runtime/gui_declarative_contract_v1.json`. In particular:

- component callbacks receive one caller-owned 80-byte render context and
  write only into its bounded caller-owned descriptor arena;
- child identity is the exact tuple `(parent_component_id, key, node_kind)`;
- update records carry one global enqueue sequence, lane, slot, SET/reducer
  action and explicit handle ownership;
- `managed_ref` is described but not admitted until the root/barrier/trace/
  relocation contract passes GC0..4;
- work, completion and app lifecycle transitions are closed tables: an edge
  absent from the table is a stable `INVALID_TRANSITION`, never an implicit
  fallback.

This freeze is a contract/source-guard deliverable only. It does not claim a
working component, scheduler, style, command or app-lifecycle implementation.

- A component is selected by a stable integer callback id.  A generated/direct
  dispatcher invokes a typed module-level callback with one render-context
  address.  The context supplies component id, props/state addresses, a
  caller-owned descriptor arena, and capacity.  The callback returns a
  count/status; it never returns callee `stack_alloc` storage.
- Component id, child key, and kernel node id are distinct.  Child identity is
  `(parent component instance, key, node kind/type)`; keys are unique among
  siblings, and a same-key incompatible type is replaced rather than reused.
- Every component instance owns a subtree in the kernel node pool.
- The first slice allows scalar i64 values and explicitly owned opaque native
  handles only.  Managed Python references are forbidden in raw records until
  a traced slot-kind contract supplies retain/release, barriers, roots, and
  GC4 updates.
- `set_state` enqueues a typed update record containing lane, slot, action
  kind, and operand or registered reducer id.  Enqueue order is global within
  a component queue, not merely FIFO inside each lane.  The component keeps a
  bounded base-state snapshot across its slots plus one skipped-update base
  queue.  When a render skips an update
  whose lane is not selected, it records the state immediately before that
  update and clones the skipped update plus every later processed update for
  replay.  A later lane therefore rebases in original enqueue order: low-lane
  `SET(5)` followed by high-lane `reduce(+1)` may expose the high result first,
  but converges to `6`; low `reduce(+1)` followed by high `SET(5)` converges to
  `5`.  The scheduler selects discrete, animation, default, then background
  work, with an explicit aging rule preventing starvation.
  Bitwise equality supplies the eager no-op check for scalar SET actions;
  reducers run through the fixed callback ABI.  Reducers are pure,
  deterministic, retryable scalar transformations: callback arguments are
  borrowed for the call, the scalar result has no ownership, and side effects
  are forbidden because interruption/rebase may invoke a reducer again.
  Opaque-handle slots initially admit only SET; their queue/base-queue clones
  retain or transfer one explicit owned reference and cancellation/error
  releases it exactly once.  Queue/arena exhaustion or reducer failure leaves
  committed state unchanged, reports a stable error, and never drops an update
  silently.
- Two work loops are required: a blocking sync drain for discrete work and a
  budgeted yield/resume loop for lower lanes.  Only a complete descriptor
  result reaches the atomic commit pass; partial/yielded work never mutates
  the committed kernel tree.
- The main loop each frame:
  1. process events (kernel hit/path -> component listener target/bubble -> set_state)
  2. drain the frame's queued assignments once and mark affected components
  3. render dirty components into descriptors, reconcile keyed children, and
     commit insert/move/update/remove effects
  4. kernel layout + render
- Updates enqueued while rendering or committing are scheduled after the
  active atomic commit, preventing reentrant mutation while still allowing a
  higher-priority lane to invalidate/restart uncommitted lower-priority work.
- Mount/update/unmount callback ids use one specified error convention and
  React-derived phase ordering: before-mutation snapshot; structural mutation
  plus synchronous layout cleanup/destruction; later synchronous layout
  creation; then queued passive cleanup followed by passive creation.
  Replacement/unmount cleanup precedes the corresponding remount/effect.
  Listener ids carry target component + event type, and unmount unregisters
  them.  A node-to-owner map resolves a hit leaf to its component.

## Style model

- Extend `pcc_gui_theme_anim`'s numeric table with namespaced token id ranges
  for colours, fonts, sizes, padding, and gap; do not create a second theme
  owner.
- Atomic helpers: `bg(node, token)`, `tx(node, token, text)`,
  `pad(node, t)`, `gap(node, t)` — set kernel node fields.
- Padding and gap require versioned kernel record fields and measure/arrange
  tests; they are not present in the current 128-byte node record.
- Theme = a token table instance swapped at runtime; swapping it invalidates
  the affected styled components before the next commit.
- Applied by component fns (declarative style at the component site).
- The style compiler accepts a documented bounded class-string grammar,
  parses candidates, treats a negative utility prefix/value policy separately
  from optional `/named-or-arbitrary-modifier` syntax, invokes a registered
  utility generator, and caches the resulting immutable style-op list.  Style
  ops record referenced token/namespace generations, so a theme edit
  invalidates only dependent cache entries and components.  Invalid/ambiguous
  tokens fail deterministically.  Parsing and allocation are forbidden in the
  per-frame apply path after cache warmup.

## Command and managed-state model

- Extend or version-supersede the existing `pcc_gui_binding` owner; do not
  leave its property/command tables live beside a second unsynchronized
  implementation.
- Managed app state uses typed registered slots; the first production values
  are scalar/opaque-handle kinds, and any later managed-object kind must join
  the runtime trace/update contract before use.
- An invoke packet contains request id, command id, target id, payload
  address/length, and policy context.  A static registry resolves it to one
  typed command callback.
- Completion is exactly one result or one structured error.  Sync completion
  and queued async completion use the same resolver table; duplicate or late
  completion fails closed.  Unmount/window teardown cancels pending target
  requests and unregisters listeners.
- This is a real UI/backend command boundary even though transport is local.
  Direct handler-to-state mutation remains valid for purely local UI state,
  but must not be labeled a command or IPC path.

## Application run lifecycle

- The webview-free Tauri-derived run-event subset is explicit: `Ready`,
  `Resumed`, `MainEventsCleared`, native `WindowEvent`, Darwin `Opened` and
  `Reopen`, cancellable `ExitRequested(optional_i32_code)`, and terminal `Exit`.
  `WebviewEvent` is inapplicable by construction; menu/tray payloads remain
  ordinary targeted events rather than hidden lifecycle states.
- One typed app callback receives each event after the event payload has a
  stable owner.  `MainEventsCleared` is the boundary that drains scheduled UI
  work before layout/render.  `ExitRequested` may cancel once; accepted exit
  cancels pending commands/listeners, runs all component/effect cleanup in the
  specified phase order, releases native handles, then emits `Exit` exactly
  once.
- The lifecycle owner includes a versioned adapter at the native window bridge,
  not only a synthetic state machine.  It exports owned/copy-stable native
  `WindowEvent` payloads and Darwin delegate `Opened`/`Reopen` events into the
  typed queue, and records a mode-labeled trace proving those events reach the
  app callback.

## pcc constraints the design must respect (no half-finished traps)

1. Module-level integer initialization is a stripped-runtime-object problem,
   not a universal app-module problem.  Runtime/archive modules must use
   compile-time data or named literal helpers until that mode is fixed; do not
   impose "inline literals everywhere" on app code.
2. The 4+ argument class-method claim was not reproduced.  The contextual
   `pcc_gui_high` tag leak remains tracked by
   `PY-P1-CONTEXTUAL-CLASS-METHOD-ARG-TAGGING`.  Module-level callbacks are a
   narrow raw-ABI choice here, not a permanent Python semantic restriction.
3. Direct recognized unsafe-pointer globals are fixed.  Transitive pointers
   returned through user wrappers still lack complete provenance proof, so
   persistent raw arenas/handles use intentional i64 addresses plus explicit
   allocation/release ownership.  Semantic Python objects must never be hidden
   in i64 slots.
4. A pointer returned from a user function and used as a store base is an
   unverified workaround, not a language law.  The component ABI avoids the
   issue with caller-owned arenas; any general claim needs a focused
   LLVM/self/pcc1 provenance regression.
5. The current indirect unsafe-call surface is a finite catalog of
   shape-specific helpers; it is not a general direct-call or platform-ABI
   arity cap.  Context records keep callback ABIs small and avoid growing that
   catalog for each new component shape.

## Task-board conversion

The executable source is `docs/goal/task-board.yaml`; these ids are ordered by
dependencies, not by prose position:

| Task | Depends on | Finite claim |
|---|---|---|
| `GUI-P2-DECLARATIVE-CONTRACT` | - | Freeze record/callback/ownership/key/state/lane/commit/style-command/error semantics and pinned reference provenance. |
| `GUI-P2-KERNEL-CANONICAL-MUTATION` | contract | Select the production owner; directly test kit layout/hit-path/render; add versioned structural edits, correct paint-order path extraction/dock, public scroll/clipping, and padding/gap fields. |
| `GUI-P2-KEYED-RENDER-COMMIT` | kernel | Reconcile `(parent,key,type)` siblings and atomically commit insert/move/update/remove without stale ids or pool leaks. |
| `GUI-P2-STATE-LANE-SCHEDULER` | reconciler | Queued SET/reducer actions, base-state/skipped-update replay, retry-safe reducer ownership, eager bailout, lane priority/aging, sync and budgeted yield/restart work loops, dirty-subtree invocation proof. |
| `GUI-P2-EVENT-LIFECYCLE` | scheduler | Target-filtered listener ids, node-owner lookup, click -> state loop, and ordered mount/update/unmount cleanup. |
| `GUI-P2-STYLE-TOKEN-UTILITIES` | kernel, reconciler, scheduler | Extend the existing numeric theme owner with namespaces, typed helpers, registered utility generators and selective generation-based theme invalidation. |
| `GUI-P2-STYLE-CANDIDATE-COMPILER` | style utilities | Parse the bounded class-string grammar, compile/cache immutable style ops, reject invalid/ambiguous candidates, and keep parsing out of warm frame paths. |
| `GUI-P2-COMMAND-STATE-BOUNDARY` | scheduler, events | Typed managed state plus invoke registry, target policy, result/error resolver, async completion/cancellation, and teardown. |
| `GUI-P2-APP-RUN-LIFECYCLE` | events, commands | Deliver the selected webview-free run events with cancellable/exactly-once shutdown and full cleanup ordering. |
| `GUI-P2-MAC-DIFF-DECLARATIVE-CANARY` | scheduler, events, style compiler, commands, app lifecycle | Converge `app.py`/`kit_window.py`, preserve accepted diff semantics, then make the canonical app exercise every absorbed layer in one mode-labeled end-to-end loop. |

## Definition of done

- The canonical `projects/mac_diff_app/app.py` is based on the accepted
  `kit_window.py` split-table/change-coalescing behavior and is written as
  state -> descriptors, events -> state, and typed token styles.  No inlined
  kernel and no per-frame app-side node editing remain.
- Deterministic headless tests cover descriptor commits, add/remove/reorder,
  dirty invocation counts, cross-lane rebase/replay, reducer retry/ownership,
  lane preemption/aging/yield/restart, lifecycle
  phase order, app run/exit order, click -> state -> rerender, command result/error/cancellation,
  class-string compile/cache, overflow diagnostics, and theme swap.
- The final app uses a queued reducer action, more than one scheduling lane, a
  keyed structural change, target-filtered listener cleanup, compiled utility
  classes with both negative and slash-modifier candidates, one typed command
  round trip, and a cancellable then accepted exit request; a layer unused by
  the canary is not considered absorbed.
- Host-pcc and current-pcc1 strict self/no-libpython claims are tested
  separately; the headless canary runs on GC0..4.  A Darwin hardware window
  smoke requires an explicit bridge-side render/present acknowledgement, is
  separately labeled, and does not claim pixel correctness while
  permission-gated capture remains unavailable.
- Existing proven kernel behavior remains green after the versioned mutation,
  layout, clipping, and hit/path extensions; listener dispatch has one owner.

## Source reference provenance

The original draft falsely claimed that upstream sources had been copied into
the repository. They were not, and they remain unvendored. The durable
repo-local artifact now present at `docs/refs_docs/gui-declarative/README.md`
records license labels, immutable revisions and exact source anchors without
claiming those untracked checkouts ship with pcc. The review used these local
full-depth checkouts:

| Upstream | Origin and pinned revision | Verified files/symbols |
|---|---|---|
| React | `facebook/react@2042572329425f9ebf35ae6287ea5bab72b2c497` | `packages/react-reconciler/src/ReactFiberHooks.js::{dispatchSetStateInternal,updateReducerImpl}`; `ReactChildFiber.js::{mapRemainingChildren,useFiber,placeChild,reconcileSingleElement}`; `ReactFiberWorkLoop.js::{workLoopSync,workLoopConcurrent}`; `ReactFiberLane.js::{getNextLanes,computeExpirationTime,markStarvedLanesAsExpired}`; `ReactFiberCommitWork.js::{commitBeforeMutationEffects,commitLayoutEffectOnFiber,commitPassiveMountEffects}`; `ReactFiberCommitEffects.js::{commitHookLayoutEffects,commitHookLayoutUnmountEffects,commitHookEffectListUnmount}` |
| Tailwind CSS | `tailwindlabs/tailwindcss@46df7ee2fc4ae822d414d35bbd48be024e5cb1c0` | `packages/tailwindcss/src/theme.ts::{ignoredThemeKeyMap,isIgnoredThemeKey}`; `utilities.ts::{createUtilities,functionalUtility,spacingUtility}`; `candidate.ts::parseCandidate`; `compile.ts::compileCandidates`; `design-system.ts::{parsedCandidates,compiledAstNodes}` |
| Tauri | `tauri-apps/tauri@34ec18ba5e1acabebd66ae79d6fc746f63d8eb96` | `crates/tauri/src/event/{mod.rs,listener.rs}`; `ipc/mod.rs::{Invoke,InvokeResolver,InvokeMessage}`; `state.rs::StateManager`; `app.rs::{run,RunEvent,ExitRequestApi::prevent_exit,on_event_loop_event}` |

`GUI-P2-DECLARATIVE-CONTRACT` must add a durable, license-compatible
repo-local reference note containing these pins and exact source anchors; it
must not pretend untracked `~/pcc_refs` trees ship with the repository.

## What the pinned sources actually establish

### React

- `dispatchSetStateInternal` builds a lane/action update, computes eager state
  when possible, enqueues, and schedules.  It does not synchronously render.
- `workLoopSync` and `workLoopConcurrent` are distinct blocking and yieldable
  work loops.  A dirty bit is therefore not a lane; pcc needs real priority
  classes and a yield/restart contract to claim lane absorption.
- `getNextLanes`, `computeExpirationTime`, and `markStarvedLanesAsExpired`
  establish priority selection and starvation handling.  PCC's four named
  lanes and its aging thresholds are a bounded mapping, not React's exact lane
  bit set or expiration policy.
- Key equality alone is insufficient.  `reconcileSingleElement` checks key and
  compatible fragment/tag/element type; an incompatible same-key child is
  deleted.  `useFiber` creates a work-in-progress clone rather than mutating
  the committed fiber directly.  Placement/deletion effects are recorded for
  a later commit.
- PCC deliberately uses bounded descriptor arenas instead of Fibers, but must
  preserve the semantic separation: work-in-progress render state cannot
  mutate the committed kernel tree before atomic commit.
- `ReactFiberCommitWork` and `ReactFiberCommitEffects` place snapshot work
  before mutation, layout destruction in the mutation phase, layout creation
  later, and passive cleanup/creation later still.  PCC absorbs these phases
  with registered effect ids even though it does not copy Fiber objects or the
  complete React hook API.

### Tailwind CSS

- `theme.ts` implements namespaced keys plus explicit ignored-prefix rules.
- `createUtilities` registers utilities, and `spacingUtility` generates
  declarations from one or more theme namespaces, including negative-value
  policy.
- `parseCandidate` distinguishes utility values, negative roots and `/`
  modifiers; these are different grammar constructs.  `compileCandidates`
  accepts raw candidate strings, invokes the design-system candidate parser,
  and produces style AST.  A direct `pad(node, token)`
  helper alone is therefore not full absorption of the selected Tailwind
  mechanism; pcc also needs the bounded candidate grammar, utility
  registry/compiler, invalid-token behavior, and cache.
- `design-system.ts` owns parsed-candidate and compiled-AST caches.  PCC uses
  schema plus referenced token/namespace generations, not one global theme
  version, so unrelated theme edits stay cached and components stay clean.

### Tauri

- `Event` itself carries listener id and payload, not a target.  One
  `Listeners` owner maps event name -> listener id -> `Handler`; the handler
  carries the target filter, and `unlisten` removes by id.  PCC follows this
  cardinality instead of inventing one table per target.
- `Invoke`, `InvokeMessage`, and `InvokeResolver` form a real command
  request/result/error boundary.  A direct `set_state` call is not IPC and is
  not sufficient evidence for command absorption.
- `StateManager` supplies typed managed state, while `App::run` delivers
  `RunEvent` values; `on_event_loop_event` performs the runtime-to-public event
  conversion, and `ExitRequestApi::prevent_exit` supplies cancellation.  PCC
  maps only the listed Darwin/native subset to typed slot kinds and the native
  app loop.  It preserves the optional i32 exit code but deliberately omits
  Tauri's restart-code exception, webview events, and upstream API/wire
  compatibility.  Any selected exit request may therefore be cancelled once.
  Managed-object slots still require explicit GC tracing before admission and
  exit has explicit cleanup ordering.

## Consolidated complete absorption

1. Registered function components render into bounded work-in-progress
   descriptor arenas.
2. Key + type reconciliation records structural effects and commits them
   atomically to a reclaimable, correctly routed kernel tree.
3. Update queues carry reducer/action and lane; sync and yieldable work loops
   provide eager bailout, priority, aging, interruption, and restart.
4. Namespaced theme tokens feed registered utility generators; class strings
   parse/compile once into cached immutable style operations.
5. Listener ids carry target filters and are removed on unmount; typed managed
   state obeys raw/GC slot ownership.
6. Invoke packets cross a real command registry/resolver boundary with target
   policy, one result-or-error completion, async cancellation, and lifecycle.
7. The app loop delivers the selected run-event lifecycle, including
   cancellable `ExitRequested` and exactly-once terminal `Exit` after cleanup.
8. `mac_diff_app` exercises every item above under current pcc1 strict
   self/no-libpython, with deterministic headless GC0..4 evidence and a
   separately labeled Darwin window smoke.
