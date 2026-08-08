# Non-integration heavy xdist lane repair

Date: 2026-07-27

Task: `PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE`

Mode boundary: current source on Darwin arm64, host pytest with the repository
default six-worker `--dist=loadgroup` scheduler. The affected compiler commands
use the existing LLVM/self frontend modes and GC2..4 selections. No compiler,
runtime, GC semantic, backend, or subprocess-timeout value changed.

Reported boundaries:

- pcc-Python runtime-oracle `os_basics` and `path_basics` compiles exceeded
  their 120-second limits in the complete non-integration suite.
- the LLVM / GC4 metadata subset exceeded its 300-second inner limit.
- `docs/current-goal-state.md` did not match its structured sources.

Cause and change:

- The runtime oracle and each GC metadata backend exposed independent xdist
  groups. Six workers could therefore launch the runtime compiler workload and
  every nested GC pytest workload concurrently.
- GC metadata now uses two frontend-shaped xdist groups:
  `pcc_heavy_llvm` for complete LLVM slices and `pcc_heavy_self` for reduced
  self slices. The runtime oracle shares the reduced self lane.
- `docs/current-goal-state.md` was regenerated from the structured board and
  manifest.

Focused evidence:

- Scheduler contract was RED before the marker change because the matrix
  exposed `gc_meta_2`, `gc_meta_3`, and `gc_meta_4` instead of two aggregate
  lanes.
- Isolated original nodes:
  - `os_basics`: `1 passed in 1.86s`.
  - `path_basics`: `1 passed in 1.36s`.
  - LLVM / GC4: `1 passed in 35.01s`.
- Affected files through the normal six-worker scheduler:
  `34 passed in 73.50s`.
- Test-infrastructure contracts: `19 passed in 0.62s`.
- Goal-state/startup contracts: `10 passed in 0.44s`.
- Structured task board: `OK: 192 tasks validated`.

Broad evidence, intentionally not claimed green:

- `gtimeout 900s env -u LC_ALL uv run pytest` reached 50%, emitted two failure
  markers without tracebacks, and ended with watchdog exit 124 and no final
  summary. No child survived.
- A bounded `-x --tb=short` follow-up passed beyond both earlier marker
  locations, reached 54% with no failure, and ended at its 600-second watchdog
  without a summary. No child survived.
- Both runs spent roughly four minutes rebuilding/provisioning stage1 before
  pytest progress began. Complete-suite completion inside 900 seconds therefore
  remains open, and the two marker-only failures are not named or claimed fixed.

Supported claim: the four user-reported boundaries have focused current-source
evidence; the generated-state drift is repaired, and the affected runtime/GC
workloads pass together under the new two-lane six-worker schedule without
relaxing timeouts.

Not proven: the exact complete non-integration or integration suite inside 900
seconds, a final verdict on the two marker-only transient failures, clean
release truth, or any compiler/runtime semantic change.
