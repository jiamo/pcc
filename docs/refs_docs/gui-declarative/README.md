# Declarative GUI reference pins

This note records the upstream source shapes reviewed for
`pcc.gui.declarative.v1`. The sources are references and oracles only; none is
vendored, linked, imported, or treated as a runtime owner. PCC absorbs a
bounded mechanism family and does not claim upstream API or wire
compatibility.

## React

- Repository/revision: `facebook/react@2042572329425f9ebf35ae6287ea5bab72b2c497`
- License: MIT
- Reviewed anchors:
  - `packages/react-reconciler/src/ReactFiberHooks.js`:
    `dispatchSetStateInternal`, `updateReducerImpl`
  - `packages/react-reconciler/src/ReactChildFiber.js`:
    `mapRemainingChildren`, `useFiber`, `placeChild`,
    `reconcileSingleElement`
  - `packages/react-reconciler/src/ReactFiberWorkLoop.js`:
    `workLoopSync`, `workLoopConcurrent`
  - `packages/react-reconciler/src/ReactFiberLane.js`:
    `getNextLanes`, `computeExpirationTime`, `markStarvedLanesAsExpired`
  - `packages/react-reconciler/src/ReactFiberCommitWork.js`:
    `commitBeforeMutationEffects`, `commitLayoutEffectOnFiber`,
    `commitPassiveMountEffects`
  - `packages/react-reconciler/src/ReactFiberCommitEffects.js`:
    `commitHookLayoutEffects`, `commitHookLayoutUnmountEffects`,
    `commitHookEffectListUnmount`

The pcc contract borrows globally ordered queued updates, retry-safe reducer
replay, key-plus-compatible-type identity, work-in-progress/commit separation,
sync versus yieldable scheduling, starvation prevention, and ordered commit
phases. It does not copy Fibers, Hooks, React lanes, JSX, or the React API.

## Tailwind CSS

- Repository/revision: `tailwindlabs/tailwindcss@46df7ee2fc4ae822d414d35bbd48be024e5cb1c0`
- License: MIT
- Reviewed anchors:
  - `packages/tailwindcss/src/theme.ts`: `ignoredThemeKeyMap`,
    `isIgnoredThemeKey`
  - `packages/tailwindcss/src/utilities.ts`: `createUtilities`,
    `functionalUtility`, `spacingUtility`
  - `packages/tailwindcss/src/candidate.ts`: `parseCandidate`
  - `packages/tailwindcss/src/compile.ts`: `compileCandidates`
  - `packages/tailwindcss/src/design-system.ts`: `parsedCandidates`,
    `compiledAstNodes`

The pcc contract borrows namespaced tokens, registered utility generators, a
negative policy distinct from slash modifiers, bounded candidate compilation,
and a compiled-operation cache. It does not implement CSS, Tailwind plugins,
Tailwind configuration files, or Tailwind class compatibility.

## Tauri

- Repository/revision: `tauri-apps/tauri@34ec18ba5e1acabebd66ae79d6fc746f63d8eb96`
- License: Apache-2.0 OR MIT
- Reviewed anchors:
  - `crates/tauri/src/event/mod.rs`, `event/listener.rs`
  - `crates/tauri/src/ipc/mod.rs`: `Invoke`, `InvokeResolver`,
    `InvokeMessage`
  - `crates/tauri/src/state.rs`: `StateManager`
  - `crates/tauri/src/app.rs`: `run`, `RunEvent`,
    `ExitRequestApi::prevent_exit`, `on_event_loop_event`

The pcc contract borrows one listener registry with target filters, typed
managed state, invoke/result/error separation, exactly-once resolution,
cancellation, and a selected native app-event lifecycle. It deliberately has
no webview, IPC wire compatibility, restart-code exception, or Tauri API.

## PCC contract artifact

The machine-readable authority is
`pcc/py_runtime/gui_declarative_contract_v1.json`. It freezes record offsets,
ownership, callback shapes, capacity limits, lane aging, replay rules, commit
phases, style cache identity, command completion, app transitions, stable
errors, and explicit nonclaims. Downstream implementations must consume or
source-guard that artifact instead of silently creating a second ABI.
