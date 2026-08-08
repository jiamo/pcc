# Investigation: Harness GUI needs a pcc1 that matches the current runtime import policy

## Status
active — exact-current-source self-backend stage 1 exceeded the bounded cold-build gate

## Problem Description

The latest existing canonical compiler, `build/bootstrap-self/pcc1` dated
2026-08-08, compiles and runs the Harness core. The native GUI slice also
pulls the current PCC GUI runtime members. Rebuilding today's production
runtime archive through that compiler stops at `py_gc_telemetry.py` because
the old compiler admits only `pcc.extern` and `pcc.unsafe` imports in a
freestanding module, while current source also admits registered type
scaffolds such as `from pcc import i64`.

The artifact-selection predecessor is
[harness-agent-loop-self-stackmap-err-exit-join.md](harness-agent-loop-self-stackmap-err-exit-join.md).
That report established that the repository-root convenience binary is stale
and selected the then-newest canonical stage 1. This report covers the next
case: a product slice depends on compiler/runtime source newer than every
existing stage-1 artifact.

## Repro

From the PCC repository root:

```bash
gtimeout 600s env -u LC_ALL make -B -C pcc/py_runtime \
  libpy_runtime_pcc_py.a \
  PCC=/Users/jiamo/my/pcc/build/bootstrap-self/pcc1 \
  PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3
```

Observed result:

```text
error: PCC-PY-COMPILE-001: [python-frontend] freestanding modules only support
imports from pcc.extern and pcc.unsafe: pcc
```

## Test [CONFIRMED]

The exact command above failed on 2026-08-14 at
`pcc/py_runtime/py/py_gc_telemetry.py`. Current
`generation_lowering.py` explicitly admits `_TYPE_SCAFFOLD_MODULES`, and the
CPython-hosted current compiler emits the same module successfully. This
isolates the failure to compiler artifact age rather than invalid current
runtime source.

The realistic acceptance is:

```bash
gtimeout 2400s projects/harness/bootstrap-pcc1.sh
gtimeout 300s projects/harness/build.sh
gtimeout 30s projects/harness/build/harness-core --gui-self-check
```

## Proposals

- No.1 Reuse the host-compiler runtime archive with the 2026-08-08 pcc1 [DENIED]
- No.2 Remove the current type-scaffold import from GC telemetry [DENIED]
- No.3 Publish an exact-current-source self-backend pcc1 under the Harness project [BLOCKED BY BUILD COST]
- No.4 Publish the same current frontend as an LLVM stage-1 driver, then compile the product with the self backend [PROPOSED]

## No.1 Reuse the host-compiler runtime archive with the 2026-08-08 pcc1

### DENIED

This can produce a linked Harness executable but leaves CPython-hosted pcc as
the owner of the current runtime archive. It does not prove that pcc1 can
compile the runtime needed by the product and hides the version mismatch from
subsequent slices.

## No.2 Remove the current type-scaffold import from GC telemetry

### DENIED

`i64` is a compile-time annotation marker already supported by current source.
Changing valid runtime source to fit an older binary would regress the
freestanding type contract and make artifact age dictate product design.

## No.3 Publish an exact-current-source pcc1 under the Harness project

### Code Change

`projects/harness/bootstrap-pcc1.sh` invokes the official stage-1 self-backend
bootstrap and publishes only a completed compiler at
`projects/harness/build/pcc1`. `build.sh` prefers that artifact before shared
canonical builds.

### Result

The cold build passed the failing telemetry module and entered parallel
self-backend emission. One huge batch completed, but `huge_0` stayed at
94–100% CPU for more than 34 minutes and the full command reached its
2,400-second bound. The bootstrap correctly left no published project-local
`pcc1`. This is now a measured stage-1 build-cost problem, not evidence of a
compiler correctness failure.

## No.4 Publish the same current frontend as an LLVM stage-1 driver, then compile the product with the self backend

### Code Change

Use the official bootstrap's LLVM stage-1 mode only to emit the exact-current-
source native compiler driver. The resulting `pcc1` must still build Harness
with `--backend self --python-libpython off`; the product artifact remains a
self-backend output and must link neither libpython nor Node. Keep the bounded
self-stage build as a performance follow-up rather than a prerequisite for
every product iteration.

### Result

Pending. This proposal is accepted only if the LLVM stage-1 publish barrier,
the pcc1-driven self-backend Harness build, GUI self-check and linkage audit
all pass.

## Report

The current finding is a compiler/runtime artifact-version mismatch. No
compiler or runtime source workaround has been added. The project-local
stage-1 output is the explicit version-alignment point for product builds.
