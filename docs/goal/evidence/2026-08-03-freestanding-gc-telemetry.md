# Freestanding pcc-Python GC telemetry ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree.  Slice fingerprints include
`py_gc_telemetry.py=f281147438c6618f8a4bcf1ad2aa85448756249a6193e4c5ee03e35ba50fbdb9`,
`pipeline.py=17ae7b6b54ef27f246be8c18dbe336e1871d9eb3f891114568440e33c7ade566`,
and
`runtime_abi.py=4ad64c93857dc47b6db4c94c7db41af59ef24ba6d79c77c1491ddcb02c499d44`.

## Claim boundary

The production `pcc_gc_telemetry` dispatcher and its four aggregate score
exports now come from strict freestanding pcc-Python.  Their public counter ABI
matches the retained C oracle over every metric from -1 through 116.  The
object imports only exact raw GC provider/state symbols, and the production
archive uniquely owns the five public symbols through `py_gc_telemetry.o`.

This does not complete the collector migration.  `py_obj_gc.py`, the remaining
`py_gc_backend.py` families, graph semantics, final five-GC fixed point, and
long-running measurements remain open.

## Red evidence and correction

The new extracted-C-oracle differential first failed at the exact ABI boundary:

```text
metric 38: expected 10082, got 10003
1 failed in 1.01s
```

The pcc-Python implementation had reused 32..37 after those values became the
pause histogram ABI.  Scheduler and backend-4 counters now use the public
header values 38..115 directly.  The module also uses ordering-explicit
freestanding atomic loads instead of the removed C atomic helper.

## Cross-emitter and production proof

`tests/python/test_freestanding_gc_telemetry.py` performs four independent
checks:

- LLVM-emitted dispatcher versus the extracted C oracle for -1..116;
- self-emitted dispatcher versus the same oracle;
- exact object closure: five exports and only literal typed GC function/global
  imports;
- production archive ownership plus execution under `PCC_GC_BACKEND=0..4`.

The production run completed:

```text
4 passed in 51.49s
```

The adjacent strict-contract suites also passed:

```text
24 passed in 2.90s   # freestanding module discipline
7 passed in 52.04s  # GC-state closure and production behavior
3 passed, 90 deselected in 0.94s  # telemetry bootstrap-shim API/split checks
```

## Fresh pcc1 proof

Two earlier fresh stage1 attempts compiled and signed a binary but correctly
failed their mandatory publish barrier with `AttributeError: _instance` while
eagerly importing `runtime_abi`.  Raw GC names were separated from managed LLVM
types, and the new `pipeline -> runtime_abi` edge was deferred into the strict
validators.  The focused import-order test is included in the 24-test module
suite above.

The current-source no-libpython/self build then passed its complete stage1
publish/exec-smoke barrier:

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/libc-gc-telemetry-stage1-v3-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/libc-gc-telemetry-stage1-v3 --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=74884 \
  output=build/libc-gc-telemetry-stage1-v3/pcc1
```

That fresh pcc1 executed `--help` and compiled the real module in strict mode:

```text
gtimeout 120s env -u LC_ALL \
  PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  build/libc-gc-telemetry-stage1-v3/pcc1 \
  --ir-scaffold=on --backend self --python-libpython off --python-library \
  --emit-llvm=build/libc-gc-telemetry-stage1-v3/py_gc_telemetry.ll \
  pcc/py_runtime/py/py_gc_telemetry.py

exit 0, 1.81s
```

Compiling the result with clang and inspecting it with `nm` showed exactly the
five expected definitions.  Its undefined set contained only the declared raw
GC providers/state and no `py_cpy_*`, managed exception, libc, or libpython
dependency.  Full scaffold IR contains unused ABI declarations, so the object
symbol table—not textual declaration presence—is the dependency evidence.

## Remaining task boundary

Audit and split the next closed symbol family from `py_obj_gc.py` or
`py_gc_backend.py`, then prove every production GC symbol is pcc-Python-owned.
After all adjacent libc/GC migrations stabilize, run the final five-GC semantic
and pcc1 -> pcc2 -> pcc3 fixed-point matrix once, followed by the required
long-running RSS/fragmentation/pause/throughput measurements.
