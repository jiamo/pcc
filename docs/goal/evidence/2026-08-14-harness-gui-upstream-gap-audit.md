# Harness native GUI vs upstream web UI — source-grounded gap audit

Date: 2026-08-14
Mode label: **source audit plus one same-viewport capture pair**. Sections D1-D7
are source claims citing file/line. Sections V1-V6 come from two real 1280x800
captures taken this session. No pytest gate, no bootstrap and no upstream-pinned
commit checkout was run for this document.

## Compared surfaces

- Upstream: `http://127.0.0.1:3080/`, boot payload `window.__DSH_BOOT__`,
  `rev` `4d7cd5cbf418`, document `lang="zh-CN"`. Captured with a real Chromium
  at viewport 1280x800:
  `docs/goal/evidence/2026-08-14-harness-gui-upstream-1280x800.png`.
  (The in-app preview pane rendered this client blank; that was a tooling
  failure on my side, not an upstream fault.)
- Native port: `projects/harness/gui_app.py` (367 lines),
  `projects/harness/gui_bridge.py` (149), `projects/harness/gui_model.py` (66).
  Captured by running the already-current `build/harness-core` with
  `PCC_HARNESS_GUI_CAPTURE`, window created at the same 1280x800:
  `docs/goal/evidence/2026-08-14-harness-gui-native-1280x800.png`.

## Upstream client surface inventory (from the boot payload)

38 client plugin entries, of which 28 are `@deepseek-ai/dsh-client-ui-*`:

```text
agent-preset  commands  conversation  cordis  deliverables
directory-picker-native  goal  input-trigger  jobs  layout
message-feedback  model-selection  permission-presets  plan  settings
settings-general  settings-models  settings-plugin-inventory  settings-plugins
sidebar  skill  subagent  theme  tool  trajectory  user-questions
workflow-run  workspace
```

Plus `dsh-client-locale`, `dsh-client-hmr`, `dsh-client-modules`,
`dsh-client-connection`, `dsh-client-runtime`, `dsh-cordis-client-runner`,
`dsh-api-gateway`, `dsh-api-remotes`, `dsh-typert-registry`,
`dsh-session-log-export`.

The native app's own declared surface is
`gui_model.HarnessGuiState.visible_regions()` = sidebar, session-navigation,
trajectory, composer, status, settings — six painted regions, all static.

## Defects found in the native GUI source

- **D1 fixed geometry.** `gui_app.py:69-71` pins `WINDOW_WIDTH = 1280`,
  `WINDOW_HEIGHT = 800`, `SIDEBAR_WIDTH = 280`; every node is placed with
  absolute `x/y/w/h` literals. `pcc_kit_layout_tree`, `pcc_kit_layout`,
  `pcc_kit_dock`, `pcc_kit_padding`, `pcc_kit_gap` — all present in
  `pcc/py_runtime/py/pcc_gui_kit.py` — are never called.
- **D2 the window size is never read.** `gui_bridge.init()` resolves nine
  symbols (create/render/show/close/pump/closed/click/text/capture) and does
  **not** resolve `pcc_gui_metal_window_size`, which exists at
  `projects/mac_diff_app/pcc_gui_metal_render_bridge.m:861`. `init` stores the
  create-time width/height in bridge slots 72/80 and passes those same
  constants into every `render_scene` call. The window is created with
  `NSWindowStyleMaskResizable` and the app menu installs Minimize and Zoom
  (same file, lines 384-385 and 222-223), so the user can resize, zoom and
  miniaturize a window whose contents never follow. This is the reported
  "maximize/minimize does not follow" bug.
  `projects/mac_diff_app/declarative_app.py:583,602` already polls
  `window_size` and re-lays out; the Harness app does not.
- **D3 hit-testing by literal pixel ranges.** `gui_app.py:330-338` branches on
  `x >= 14 and x < 266 and y >= 74 and y < 112` (new session) and
  `x >= 1118 and x < 1154 and ((y >= 466 ...) or (y >= 744 ...))` (send). The
  kit's hit-test/handler/focus/key-event API is unused, so any layout change
  silently desynchronizes the clickable regions from the painted ones.
- **D4 no text input at all.** The bridge polls only
  `pcc_gui_metal_window_poll_click`; no key or character event path exists in
  it. The composer is a painted rectangle with a placeholder label, and
  `gui_model.submit_sample()` always sends the literal string
  `hello from pcc gui` and produces a canned reply. The user cannot type.
- **D5 static ceilings.** `kit_init(128)` nodes; `calloc(256, …)` rect/color/
  text buffers; `gui_bridge.render_scene` drives at most 64 CATextLayer slots
  per frame. A real conversation at a maximized window exceeds all three.
- **D6 ASCII-only text, no measurement.** Every string is `cstr(...)` with a
  hand-written byte length, and
  `tests/python/test_harness_gui.py::test_gui_static_text_lengths_match_utf8_payloads`
  enforces those constants. `pcc_gui_kit._measure` derives sizes from declared
  width/height only — there is no font-metric text measurement — so wrapping,
  centering and truncation of CJK/emoji text are not expressible. Upstream
  ships `dsh-client-locale` and renders zh-CN.
- **D7 no scrolling.** `pcc_kit_scroll_container` / `pcc_kit_scroll_by` exist
  and are unused; conversation content sits at fixed `y`.

## What the two captures actually show

- **V1 no filled shape appears in the native capture.** Upstream paints a grey
  sidebar with a right border, an outlined white New Session button, a grey
  selected-session row, a white rounded composer card with border and shadow, a
  blue circular send button and a pale-blue Preview pill. The native capture is
  pure white with floating text: not one `kit_rect` fill is visible, although
  `gui_app.build_scene` emits at least nine of them and the self-check asserts
  a rect count. Two hypotheses remain open and this audit does not choose
  between them: either the rect command list never reaches the screen, or
  `pcc_gui_metal_window_capture` snapshots only the CATextLayer overlay and
  misses the Metal drawable. Until this is resolved, **every screenshot-based
  parity claim in this project is unsafe**, including the one
  `HARNESS-P0-NATIVE-GUI-SHELL` requires for promotion.
- **V2 text metrics are visibly wrong.** In the native capture the title
  "DeepSeek Harness" runs straight into the "Preview" label and overlaps it;
  the placeholder, "Read-only", "DeepSeek" and the "^" send glyph sit at
  unrelated positions with no enclosing box. This is D6 made visible: positions
  were computed from guessed widths because no font measurement exists.
- **V3 upstream is a live product surface, ours is two frozen lines.** Upstream
  sidebar shows a real session list with a selected row, a Chinese session
  title, a relative timestamp, a workspace row with a folder icon and three
  icon buttons. The native side shows the literal strings "pcc" and
  "PCC Harness" and nothing else.
- **V4 the native GUI has no icon or bitmap channel at all.** Upstream draws a
  brand logo, a HARNESS badge, folder/search/filter/settings/attachment icons
  and a chevron set. The kit exposes only filled rects and text runs, so none
  of this is expressible today.
- **V5 the native GUI has no rounded corners, borders, shadows or font
  weights.** Upstream's shell is built almost entirely out of those. This is a
  large share of the "looks completely different" gap and was missed by the
  source-only pass.
- **V6 upstream composer is a control cluster.** Rounded card, placeholder
  "Describe what you want to build", and a bottom row of attach, "Workspace
  Write", model "DeepSeek-V4-Pro", effort "High" and a circular send button,
  each a menu or action. The native composer is a placeholder string plus three
  detached labels. Upstream's hero line is also a rotating welcome phrase
  ("Into the Unknown"), not the fixed product name we paint.

## Task-board coverage check

Already owned:

- live session content, switching, streaming → `HARNESS-P0-NATIVE-GUI-SHELL`
  open boundary and `HARNESS-P1-GUI-SESSIONS-STREAMING`
- tool/terminal/diff/approval rendering → `HARNESS-P1-GUI-TOOLS-APPROVALS`
- settings, credentials, workspaces, profiles → `HARNESS-P1-GUI-SETTINGS-PROFILES`
- pixel/keyboard/resize **measurement against upstream** →
  `HARNESS-P1-UI-PARITY-ACCESSIBILITY`

Not owned by any row before this audit:

- D1, D2, D3, D5, D7 — viewport-driven layout and geometry-derived interaction.
  `HARNESS-P1-UI-PARITY-ACCESSIBILITY` names "resize" only as a tolerance
  measurement, ranks 63, and depends on the three GUI feature rows, so nothing
  in the board made the app read its own window size.
- D4, D6 — keyboard/character input, IME composition, CJK text measurement.
  No row in the board mentions text input, key events, IME or fonts.
- The 28-plugin upstream client-UI surface at plugin granularity. `TASKS.md`
  maps "upstream Web UI/product states" to four rows as one line; surfaces such
  as deliverables, message-feedback, goal, input-trigger, jobs, workflow-run,
  subagent, skill, plan, permission-presets, agent-preset, theme, locale,
  commands, directory-picker-native, cordis inspector, session-log-export,
  layout and hmr have no named owner.

Also not owned before this audit, and only visible from the captures:

- V1 — whether the rect command list reaches the screen at all, and whether the
  capture path is trustworthy as evidence.
- V4, V5 — icons/bitmaps, rounded corners, borders, shadows and font weights as
  paint primitives.

## Rows added from this audit

- `HARNESS-P0-GUI-FILLS-AND-CAPTURE-TRUTH` (V1)
- `HARNESS-P0-GUI-WINDOW-VIEWPORT` (D1, D2, D3, D5, D7)
- `HARNESS-P0-GUI-TEXT-INPUT-IME` (D4, D6, V2)
- `HARNESS-P1-GUI-PAINT-PRIMITIVES` (V4, V5)
- `HARNESS-P1-GUI-UPSTREAM-SURFACE-INVENTORY` (V3, V6, plugin-granular mapping)

## Open boundary of this audit

The captures prove the current painted result at one viewport on this machine;
they prove nothing about resize behavior, which remains a source-level finding
(D2), and nothing about any gate. The five rows above are unverified until
their own gates run. The upstream inventory is read from one live boot payload
at rev `4d7cd5cbf418`; it is not the pinned upstream commit inventory that
`HARNESS-P1-UPSTREAM-CONVERGENCE` owns, and the two must be reconciled before
any parity claim.
