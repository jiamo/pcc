# Investigation: first-install runtime copy loses static port ABI exports

## Status
resolved

## Problem Description
The new source installer must work after a clean clone. Its first actual run
failed building `py_substrate.o`, before Stage1, with `pcc.unsafe global
definition intrinsics require integer literals or statically imported integer
constants`. The failed runtime copy was named `runtime/py`, outside the
compiler source tree. The frontend's existing isolated-runtime contract is
`py_runtime/py` (or `py_runtime_*/py`). An unrecognized location loses both
the port ABI export table and the special runtime-library ownership policy.

## Repro
`build/correctness-20260906-a/first-install-01.stderr.log` identifies the run.
Its actual worker stderr is
`~/.cache/pcc/installations/source-h5ir6ez_/runtime.stderr`.
Replaying `host-pcc --python-library --emit-llvm=... py_substrate.py` under a
30-second watchdog fails for the `runtime/py` copy, but succeeds for the
identical file in the frozen `source/pcc/py_runtime/py` directory.

## Test [CONFIRMED]
The complete cold runtime build fails in 30.52 seconds. Both direct emission
replays above were observed. No Stage1/Stage2 build or installed-entry change
was attempted after that failed stage.

## Proposals
- No.1 Preserve the existing isolated runtime directory contract [CONFIRMED]

## No.1 Preserve the existing isolated runtime directory contract
### Code Change
`prepare_runtime_source()` now creates a writable `py_runtime` copy. It does
not change compiler path classification, ABI constants, GC/ownership semantics
or runtime source. Failed installer stages also expose their retained stderr
in the outer error rather than only a CalledProcessError command string.

### Pending full installation
The focused test compiles a copied runtime port defining a global header from
`PY_FLAG_IMMORTAL` and verifies the emitted symbol. All 7 installer tests pass
in 0.12 seconds. The complete clean installation must now run again, stopping
at the first failing stage.

## Report
The second clean runtime build completed successfully before entering Stage1:
`~/.cache/pcc/installations/source-9t8s3b_t/runtime.result.json` reports
COMPLETE / returncode 0. This closes the copied-runtime ABI binding defect.
The subsequent Stage1 watchdog issue is separate: its 8-GiB admission budget
selected 3 host workers instead of the historical 7. Frontend artifacts were
complete at 324.12 seconds; a separate source-bound link replay completed in
52.14 seconds. It does not reopen this runtime-layout finding or prove full
installation success.
