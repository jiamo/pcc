# Investigation: Linux x86_64 self-backend docker harness has rotted

## Status
RESOLVED 2026-06-12 (same day) — harness REVIVED: fresh image +
`PCC_BUILD_SKIP=1` in the docker-run env, and ALL SEVEN gates pass
(7 passed in 438.94s). NOT caused by the day's worktree changes
(clean-HEAD worktree failed identically-classed in the old container).
Follow-ups remain as proposals No.4/No.5.

## Resolution (2026-06-12)

Two stacked causes, two fixes:

1. The in-container editable-build hook self-compiles pcc and builds
   the port archive in hatchling's ISOLATED build env, which has no
   llvmlite — and the linux self-backend subset cannot self-compile
   the full compiler, so both backends failed at install time. FIX:
   `-e PCC_BUILD_SKIP=1` in
   `scripts/run_self_backend_linux_x86_64_docker.sh` — the documented
   hook knob defers building to lazy first-run inside the PROJECT
   venv, where llvmlite exists.
2. Host-built Darwin arm64 archives leak into the container through
   the bind mount; GNU ld silently SKIPS wrong-architecture archive
   members, producing walls of `undefined reference` (the misleading
   first signature). Interim handling: wipe `pcc/py_runtime/*.a` +
   build dirs around container runs; the real fix is proposal No.5.

Baseline established (claim-scoped): 7/7 docker gates green = the
C-frontend subset + self-backend smoke + c-testsuite buckets on
x86_64-linux under docker. NOT Python self-host on Linux, NOT
no-libpython Linux deploys.

## Problem Description

`S-P2-LINUX` was filed as "no parity gate, host/CI strategy undecided".
Both halves are stale: the strategy WAS decided and implemented —
`tests/integration/test_self_backend_x86_64_linux.py` holds SEVEN
docker-gated tests (build+run smoke for llvm and self backends, amd64
alias triple, direct-call/binop smoke, c-testsuite buckets, strict
exact bucket) driven by
`scripts/run_self_backend_linux_x86_64_docker.sh` +
`docker/self-backend-linux-x86_64.Dockerfile`. But the gates are
excluded from default pytest runs (`-m 'not integration'`) and have
rotted unobserved.

## Test [CONFIRMED]

Observed 2026-06-12 under
`uv run pytest tests/integration/test_self_backend_x86_64_linux.py::test_linux_x86_64_docker_self_backend_smoke_can_build_and_run -n0 -m integration`:
the in-container `uv run` editable build fails before any test logic
runs. Two failure signatures, same family (in-container runtime-archive
build for linux-x86_64):

```text
current worktree:  bundled archive libpy_runtime_pcc_py.a link fails —
                   undefined reference to py_tuple_set_item /
                   pcc_gc_release (port objects missing from the
                   in-container archive)
clean HEAD (git worktree add probe, same container):
                   make libpy_runtime_pcc_py.a (backend=self) failed;
                   llvm retry fails: "pass pipeline failed for module
                   'py_tuple': libLLVM-C not found"
```

Image vintage: `docker image inspect` Created=2026-04-22 — the harness
image predates the entire 5-GC/port-archive expansion arc.

## Reading

The in-container editable build hook self-compiles pcc and builds the
pcc-Python PORT archive for x86_64-linux. That path needs either (a)
the self backend's x86_64-linux subset to compile all PY_MODULES port
IR (far beyond the current ~2.4k-line subset's verified surface), or
(b) the llvm fallback, which needs a working llvmlite/libLLVM inside
the image — absent in the 2026-04-22 image.

## Proposals
- No.1 Rebuild the image from the current Dockerfile (PCC_SELF_BACKEND_DOCKER_REBUILD=1) and re-measure which of the seven gates pass   [DONE — image 2026-06-12; necessary but not sufficient]
- No.2 Provide a working build path inside the container   [DONE via PCC_BUILD_SKIP=1 in the harness env — defers to lazy first-run in the project venv (llvmlite present); no Dockerfile change needed]
- No.3 Run the full seven-gate suite and record the S-P2-LINUX baseline   [DONE — 7 passed in 438.94s, 2026-06-12]
- No.4 Add a cheap NON-docker assemble-only gate (clang -target x86_64-unknown-linux-gnu -c on emitted asm) to the default suite so the Linux subset gets SOME default-run coverage and rot is visible earlier   [DONE 2026-06-12 — `tests/c/test_self_backend_linux_assemble.py`, 3 known-supported emitter shapes cross-assembled to ELF per default run (3 passed, 0.29s; clang-presence skip gate)]
- No.5 Arch-isolate runtime build artifacts so host Darwin archives and container Linux archives stop corrupting each other through the bind mount (GNU ld skips wrong-arch members SILENTLY)   [DONE 2026-06-12 via stamp-guarding the LAST unguarded build path: the pipeline's lazy path ALREADY stamps archives with `sys.platform:machine:triple` and `make -B`s on mismatch; `hatch_build.py` (the path that originally linked the wrong-arch archive) now runs the same check — `_discard_wrong_target_archives` deletes mismatched archives+stamps AND the build/build_pcc/build_py/build_libpython objdirs (else make re-links from wrong-arch .o), and `_write_archive_target_stamp` records the id after a successful make. Stamp format verified byte-identical to the pipeline's (parity probe). Gates: host lazy rebuild green (strict self-backend compile + stamps written), docker smoke re-confirmed green (4.83s). Full arch-suffixed naming remains a possible future hardening but is no longer needed for correctness.]

## Notes

Claim hygiene: docker-gated green ≠ Linux production support; the
seven gates cover the C-frontend subset + self-backend smoke, NOT the
strict no-libpython Python self-host on Linux (that needs the port
archive to compile natively — currently the failing step itself).
