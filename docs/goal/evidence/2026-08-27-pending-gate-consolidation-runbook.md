# Pending-gate consolidation runbook (post machine-recovery, post kernel-lane)

Date: 2026-08-27
Purpose: eight of today's DONE_WEAK rows defer the same bootstrap-class
evidence.  Run the gates ONCE in this order and map results to rows,
instead of paying a stage chain per row.  Preconditions: (1) machine memory
recovered (vm.swapusage well below total; the 2026-08-27 arms proved even
green shapes segfault-by-jetsam near swap exhaustion), (2) the Indexed
Function Kernel lane reports source-stable (its fixed-point gate
indexed-packed-record-fixed-point-v3-gc0 was in flight at writing time).

## Order of operations

1. Focused pre-flight (minutes, worktree):
   - tests/python/test_native_container_builtin_error_paths.py (18)
   - tests/python/test_native_attr_getattr_ownership.py (3+1 xfail)
   - tests/python/test_self_backend_oversized_admission.py (3)
   - tests/python/gcsubstrate_f_backend4_growth_publication.py
     (30 green + 4 known port-reentry reds -> GC-P1-PORT-LIST-RELOCATION-REENTRY-REDS)
   - tests/python/test_py_multi_file_compile.py + shim quick subset
2. ONE stage chain on the then-current source (bootstrap.sh --backend self),
   profile dir set.  Closes the "stage1/bootstrap residue" boundary for:
   - PY-P1-CONTAINER-BUILTIN-EXCEPTION-PATH-OWNERSHIP
   - PY-P1-ATTR-GETATTR-OWNED-VALUE-UNREGISTERED
   - PY-P1-OWNED-METHOD-CALL-RESULT-LEAK
   - PY-P1-TYPEINFER-LOOP-VAR-SHADOWS-METHOD (its own row asked one stage1)
   - GC-P1-BACKEND4-RELOCATABLE-TAGS-LACK-FRESH-ALLOC (+ dissolved row B)
   Record the stage2 wall and the oversized-lane counter: with admission
   waves the lane's reference is 119.37 s (PERF-P2-OVERSIZED-LANE-PAIRING);
   re-receipt per-item walls if the kernel compiler landed (byte cap stays).
3. Five-GC bootstrap matrix (the standing correctness guarantee), xdist as
   configured, snapshot-gated:
   gtimeout 1800s env -u LC_ALL uv run pytest -q -x -m integration tests/python/gc/test_pcc_bootstrap_full_gc*.py
   Closes the five-GC ride-along boundaries on the same rows + the admission
   row.
4. Compiled-multi shim tests (the two deselected compiled binaries) ride the
   warm cache after step 2.
5. If any stage red appears: the empty-error instrumentation is armed —
   PyPipelineError now carries pcc_message and the formatter falls back to
   it (SELF-P2-EMPTY-PIPELINE-ERROR-TEXT wants the bisect verdict from the
   first real failure).

## Not covered here

- INFRA-P1 oracle warmup: one cold oracle run under the new 1500s budget,
  machine-quiet, any time after step 2.
- PERF wide-cap follow-up stays DENIED unless re-derived from AVAILABLE
  memory (No.80).

## Added: reentry-fix mechanism bisect (healthy machine)

GC-P1-PORT-LIST-RELOCATION-REENTRY-REDS closed DONE_WEAK: the four probes
went green with the FRESH slice in the worktree, red at a pure-HEAD
snapshot.  Two competing explanations must be separated on a healthy
machine, each arm FORCING a fresh port-archive build (wipe the probe
harness's archive cache per arm — the stale-archive trap may be the whole
story if the HEAD-red control hit a poisoned cache key from a mid-slice
build):
  arm 0: pure HEAD, forced-fresh archive        -> red confirms a real HEAD defect
  arm 1: HEAD + exc-publish only                -> green names the fixer
  arm 2: HEAD + FRESH widening (+import fix)    -> green names the fixer
If arm 0 is GREEN, the four reds were cache artifacts end to end and the
row text needs that correction.
