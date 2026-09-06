# Investigation: shared Python target omitted from native provider closures

## Status
active — host/native execution passes; fresh pcc1/bootstrap validation pending

## Problem Description
The user requested a coherent Python 3.15 target instead of package selection
defaulting to 3.11 while runtime introspection and version-condition folding
reported 3.13. Literal target constants now have one owner,
`pcc/python_target.py`. Native stdlib providers importing that module failed
because provider discovery deliberately excluded arbitrary `pcc.*` internals.

## Repro
The function-bearing version/branch/sysconfig canary in
`tests/python/python_target_canary.py`, compiled through host pcc with self
backend, no libpython and C runtime, compiled and linked but exited 1:
`ImportError: No module named 'pcc.python_target'` from sysconfig.py.
`build/correctness-20260906-a/python-target-host-v1.stdout` retains the first
failure (5.76s) and its JSONL report.

## Test [CONFIRMED]
`test_python_target_contract.py::test_host_pcc_emits_native_python_target_contract_with_c_runtime`
observed the failure. The explicit shallow platform/sys provider test later
reproduced the same omission from sys.py after the recursive path was fixed.
`python-target-followups-v1.*` retains that second boundary.

## Proposals
- No.1 Admit registered shared components through both provider closures [CONFIRMED]

## No.1 Admit registered shared components through both provider closures
### Code Change
The existing pcc-owned component registry explicitly includes the literal
target module. Recursive and required/shallow provider expansion consume that
registry; other compiler internals remain excluded. A negative closure test
keeps pcc.cli_core outside application provider discovery. This does not open
arbitrary pcc internals or inline separate copies of the version constants.

### CONFIRMED
Focused closure checks cover recursive and shallow admission. After the
separate sys/platform fallback repair, the full three-case execution packet
passed in 8.57s: self/no-libpython version canary, LLVM-backed starred version
unpack, and self/no-libpython explicit top-level platform/sys providers.
The process-tree receipt is COMPLETE/rc0 in 9.20s with 323MB peak tree RSS:
`build/correctness-20260906-a/python-target-native-packet-v1.result.json`.
All use C runtime for this focused boundary. Fresh pcc1 and bootstrap remain
required before closing this shared frontend change.

The target change does not establish complete Python 3.15 semantics. The
prior package-selection limitation is recorded in
[package-acquisition-target-python.md](package-acquisition-target-python.md).
