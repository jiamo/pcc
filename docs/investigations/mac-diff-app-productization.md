# Investigation: mac_diff_app — pcc-GUI file-compare productization

## Status

active (learning + stabilization), superseding the ad-hoc GUI attempts.

## Problem Description

Goal (user directive): a Beyond-Compare-like **file compare** desktop app as
the first real product on the pcc GUI path — pcc-Python UI compiled with the
self-hosted compiler (`pcc1`, `--python-libpython off`), native macOS window,
real text, real diff, BC-style highlights.  Along the way the GUI layer hit a
series of pcc-compiler and renderer bugs; this document records the root
causes, the fixes (mine + a parallel agent's), and the lessons.

## Learning — root causes found (chronological)

### 1. main() return value ignored by self backend (REAL, FIXED)
`module_lifecycle_lowering._emit_program_main` hardcoded `exit_code = 0` when
`emit_cpy_main_exitcode=False`.  Fix: filter the user's trailing module-level
`main()` call, invoke the user-main native adapter once, unbox via
`py_int_to_i64` (alloca overflow slot, marshal.py pattern).  Verified: host
AND pcc1 both exit 5 for `return 5`.  Implication: earlier GUI "tests" that
asserted exit codes were void — structured stdout (`PCC_MAC_DIFF_SMOKE ...`)
is the truth.

### 2. Cross-backend 64-bit hash divergence (REAL, FIXED in app)
Hash init `0x9E3779B97F4A7C15` is a **u64 > i64 max**; host vs pcc1 truncate
it differently, and plain `h * 31` overflow-multiply also differs between
backends (host kept the wide value, pcc1 produced 0).  Fix: signed init value
+ `wrapping_mul_i64` → host and pcc1 produce identical diffs.

### 3. CAMetalLayer pixel format mismatch → black window (REAL, FIXED)
Window drawables are `BGRA8Unorm` but the pipeline was hardcoded
`RGBA8Unorm` → black bars.  Fix: per-format pipeline cache, window path uses
`drawable.texture.pixelFormat`.

### 4. CAMetalLayer frame pinned to initial size → maximize leaves content
top-left (REAL, FIXED).  Fix: `layer.frame = contentView bounds` before draw.

### 5. Class-method multi-arg passing (OPEN, workaround)
4+ business-arg class methods (anim_start, text_slot) corrupt args
(0x4000000000 object tags leaked into int args).  NOT minimally reproducible
(m7/m8 variants all pass).  Workaround: module-level function API
(`pcc_gui_high`), verified stable.

### 6. GC pins module-level native pointers (REAL, workaround)
Module-level Python vars holding raw pointers (window handle, dlsym fn) get
GC-pinned → `pcc_gc_pin` crash.  Workaround: store as i64 in
`define_global_i64_array` slots.

### 7. Module-level int constants zeroed (RETRACTED)
6+ minimal repros all pass; the original crash was a corrupted mid-edit
file (bad constant-name replaces), not a compiler bug.

### 8. Shared line table between panes → text corruption (REAL, FIXED by
parallel agent)
One `LINES` table stored left+right row offsets — right pane read left
rows (and vice versa), causing missing/garbled text ("middle" losing its
first glyph).  Fix: `LINES_L` / `LINES_R`.

### 9. Bare LCS has no "modified" concept (REAL, FIXED by parallel agent)
BC shows a changed line as ONE modified row (yellow), not delete+insert
pairs.  Fix: `_coalesce` pairs each delete-run with the following
insert-run into `_OPS_CHANGE` rows (min(nd,ni) pairs, extras stay
del/ins), compacted in place.  Result: 13 vs 12 rows → `ops=13 equal=7
deleted=1 inserted=0 changed=5` (was 18 ops with 6 del + 5 ins).

### 10. CATextLayer text instability (PARTIAL)
Text layers over CAMetalLayer vanished / stale after scroll.  Fix: fixed
per-line slots keyed by file line number (left 100+L*2, right 400+R*2).
Long text layers (width 600→1200→400) can span into the other pane —
kept at 400 to avoid overlap; renderer unification (CG into one texture)
attempted but crashed repeatedly (intrinsic/param/state chain) and was
reverted.

### 11. Screen capture verification blocked (TOOLS)
`cacheDisplayInRect` returns black for CAMetalLayer; `CGWindowListCreateImage`
is obsoleted on macOS 15 (ScreenCaptureKit) and permission-gated.  Runtime
pixel verification of the window path is currently manual.

### 12. Parallel-agent runtime edits break auto rebuild (ENV)
A parallel agent mid-editing freestanding GC runtime sources makes pcc's
automatic runtime rebuild fail (freestanding module emitted a managed
reference).  Workaround: `PCC_RUNTIME_ARCHIVE=/abs/libpy_runtime_pcc_py.a`.

## Proposals (ordered)

1. Accept/commit the parallel agent's `_coalesce` + `LINES_L/R` fixes (they
   are the BC "modified" semantics + the shared-buffer bug) and re-verify.
2. Confirm "middle" glyph with the split tables; if still missing, the text
   layer x=52 start is being clipped — move line-text x right or narrow the
   line-number layer.
3. Renderer unification (CG text+rect into one texture) is the right end
   state for stability + BC highlights; needs a 9-arg bridge call or a
   params struct instead of the missing intrinsic.
4. Rebuild pcc1 with the new `call_i64_ptr4_i64_i64` intrinsic so `build.sh`
   doesn't depend on host pcc.
5. Structured GUI smoke: extend `PCC_MAC_DIFF_SMOKE` with per-case expected
   stats (changed=5 fixture) — done in the all-cases test.

## Test [CONFIRMED]

- `ret.py` → exit 5 (host + pcc1).
- `test_mac_diff_app.py` 2 passed (default + all-cases fixture, 13/12/18 ops).
- With `_coalesce`: 13 vs 12 → 13 ops, equal 7, deleted 1, inserted 0,
  changed 5 (BC semantics).
