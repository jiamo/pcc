# Investigation: Backend 1 needs codegen roots for owned object locals

## Status
resolved

## Problem Description
Reduction of the Tier 5 backend #1 productionization requirement from
`goal.md`: backend #1 can only safely sweep during allocation-time incremental
work after generated Python functions prove live owned object locals to the
tracing root stack.

`docs/investigations/gc-backend1-auto-step-sweep-debt.md` denied sweeping
existing candidates during automatic steps because current pcc-Python generated
programs did not prove all loop-local container roots to the tracing backend.
This investigation checks the first narrow root-precision slice: ordinary
owned local object slots inside user functions.

## Update
The initial root registration patch exposed a second, implementation-level
failure in the bootstrap compiler: `pcc1` with `PCC_GC_BACKEND=1` segfaulted
while compiling a nested-list Python probe. `lldb` showed the crash inside
`user_py_gc_backend__seed_roots`, with a bogus root count loaded from a stale
stack-allocated frame map. The frame-slot address is intentionally a stack
address, but the frame map metadata must outlive every registered frame node.
The fix below was tightened to use a module-level constant one-slot frame map
and to emit root leaves on the shared `err.exit` path.

## Repro
Run the focused codegen root-shape gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_gc_root_precision.py::test_owned_object_locals_are_registered_as_gc_frame_roots \
  -q -n0
```

Expected pre-fix failure: generated LLVM IR contains owned-local cleanup calls
but no calls to `pcc_gc_frame_enter` / `pcc_gc_frame_leave`.

## Test [CONFIRMED]
Focused gate fails before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_gc_root_precision.py::test_owned_object_locals_are_registered_as_gc_frame_roots \
  -q -n0
```

Observed result: `1 failed in 0.30s`.

Failure:

```text
assert 'call void @pcc_gc_frame_enter' in ir_text
```

## Proposals
- No.1 Register owned local object allocas as one-slot GC frames     [CONFIRMED]

## No.1 Register owned local object allocas as one-slot GC frames
### Code Change
When layer1 marks a function-local `_CSTR` slot as an owned object local, call
`pcc_gc_frame_enter(frame_map, alloca)` with a module-level constant one-slot
frame map. On owned-local cleanup, `del`, normal exits, and the shared
`err.exit` block, call `pcc_gc_frame_leave(alloca)` before dropping the
binding.

Because rooted locals now make automatic backend #1/#2 tracing steps do real
mark work, clear GC debt whenever a tracing step reaches the inactive /
not-requested state, even if the step processed roots. This preserves the
bounded-debt behavior from
`docs/investigations/gc-backend1-auto-step-sweep-debt.md`.

The refcount/default backend keeps frame registration as a no-op. This avoids
making backend #0 pay a heap allocation/free per rooted local during bootstrap
while preserving the same generated IR shape for tracing backends.
### CONFIRMED
Focused gates pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest tests/test_gc_root_precision.py -q -n0

env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest \
  tests/test_gc_root_precision.py \
  tests/test_gc_codegen_write_barrier.py \
  tests/test_gc_backend_incremental.py \
  -q -n0
```

Observed latest result after the static frame-map and `err.exit` patches:
`10 passed in 5.51s`.

The runtime gate
`test_incremental_collect_preserves_live_owned_object_local` compiles a
no-libpython program, selects backend #1, calls `pcc_gc_collect(0)` while a
local list is live, and then reads the list successfully (`42`).

Bootstrap and cross-backend confirmation:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py \
  -q -n0 -rxX

env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  make -B -C pcc/py_runtime PCC='uv run pcc' \
  PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 libpy_runtime_pcc_py.a

env -u LC_ALL /opt/homebrew/bin/timeout 1800s \
  bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-owned-roots-1778171198 \
  --backend self --stage 2
```

Observed results:

- fallback baselines: `11 passed in 49.26s`;
- pcc-Python runtime-high archive rebuilt successfully;
- self bootstrap stage1: `9.181s` real, `PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=9260`;
- self bootstrap stage2: `11.128s` real, `PCC_BOOTSTRAP_STAGE_RESULT stage=2 elapsed_ms=11228`;
- `otool -L` for `pcc1` and `pcc2` lists only `/usr/lib/libSystem.B.dylib`;
- `pcc1 --help` and `pcc2 --help` both pass under `PCC_GC_BACKEND=0..4`;
- `pcc0`, `pcc1`, and `pcc2` each compile and run a nested-list probe under
  backend `0..4`, with every compiled binary printing `42`.

Additional regression gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_py_multi_file_compile.py \
  tests/test_py_multi_file_bootstrap_shim.py -q -n0

env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_llvm_capi_ir_parity.py \
  tests/test_llvm_capi_end_to_end.py -q -n0

env -u LC_ALL /opt/homebrew/bin/timeout 800s \
  uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed results: `70 passed in 143.81s`, `23 passed in 0.22s`, and
`181 passed, 14 xfailed in 157.28s`.

## Report
No.1 landed. Backend #1 now has the first production root-precision slice for
owned function-local object slots: generated no-libpython Python code exposes
live `_CSTR` locals to the tracing root stack, cleans them up on normal and
error exits, and no longer leaves stack-allocated frame-map metadata behind for
the bootstrap compiler to read after return.

This does not make backend #1 fully production. Remaining production criteria
still include complete root coverage for suspended coroutine/task frames,
module/scheduler roots, worker/assist interactions for backend #2, and the
moving/reference-update gates for backends #3/#4.
