# Investigation: Harness agent loop exposes inconsistent self stack-map state at err.exit

## Status
resolved

## Problem Description

The first `projects/harness` product tracer bullet compiles and runs under
CPython but the stale repository-root `pcc1` self backend rejects native emission for
`user_harness_core_AgentLoop_run_turn`. Precise stack-map analysis reports that
managed root state disagrees when multiple error paths join the generated
`err.exit` block. The production requirement is to compile the real method
without libpython or Node fallback; reducing application behavior to avoid the
compiler gap is not an accepted resolution.

The owned-local registration design and shared `err.exit` cleanup predecessor
is [gc-backend1-owned-local-frame-roots.md](gc-backend1-owned-local-frame-roots.md).
The current working tree already contains an unrelated uncommitted stack-map
change that excludes persistent global and caller-owned root arrays from local
machine-frame state. That change is preserved and does not make this Harness
reproducer pass.

## Repro

From the PCC repository root:

```bash
gtimeout 300s projects/harness/harness --self-check
```

Expected result: a current `pcc1` builds a self-backend, no-libpython native
artifact and it prints `HARNESS_CORE_SELF_CHECK_OK`.

Observed result: compilation exits nonzero with:

```text
self precise stack-map analysis in 'user_harness_core_AgentLoop_run_turn':
managed root state disagrees at block join 'err.exit'
```

## Test [CONFIRMED]

The failure was observed on 2026-08-14 with the repository-root `pcc1`, dated
2026-06-24, and the command in `## Repro`. Host behavior is separately green:

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_harness_project.py
```

Observed host result: `3 passed in 0.10s`.

Current PCC source already emits the required call-error LIFO cleanup. The
latest existing canonical stage-1 compiler, `build/bootstrap-self/pcc1` dated
2026-08-08, compiles and runs the realistic project gate, so no new compiler
regression is required for this stale-artifact report.

## Proposals

- No.1 Reduce the generated control flow and repair the owning root-state transition [DENIED — current source already owns and cleans the transition]
- No.2 Select the newest canonical verified pcc1 instead of the stale root artifact [CONFIRMED]

## No.1 Reduce the generated control flow and repair the owning root-state transition

### Code Change

No code change. Inspection of current
`pcc/py_frontend/codegen/unary_call_lowering.py` and exception lowering showed
that call-error edges already receive owned LIFO root slots and release them
before joining the shared error exit. A host compiler built from the current
source compiled the full Harness reproducer and its native artifact printed
`HARNESS_CORE_SELF_CHECK_OK`. Patching the already-correct transition would
not address the observed stale binary.

## No.2 Select the newest canonical verified pcc1 instead of the stale root artifact

### Code Change

`projects/harness/build.sh` now selects an explicit `PCC1`, a project-local
compiler, `build/bootstrap-self/pcc1`, or the shared stage-1 compiler in that
order. It does not fall back to the stale repository-root `pcc1`.

### Result

Confirmed on 2026-08-14:

```text
compiler: /Users/jiamo/my/pcc/build/bootstrap-self/pcc1
HARNESS_CORE_SELF_CHECK_OK
projects/harness/build/harness-core: Mach-O 64-bit executable arm64
/usr/lib/libSystem.B.dylib
```

The ordinary assistant turn and echo-tool turn also completed. `otool -L`
listed no libpython dependency. The issue was artifact selection, not a gap in
the current compiler source.

## Report

The repository-root `pcc1` was an old convenience artifact and must not define
"current pcc1" for product builds. Harness now consumes PCC's canonical
bootstrap outputs and has an explicit script for producing a project-local
compiler when an exact current-source build is required. No compiler or
runtime workaround was added.
