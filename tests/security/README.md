# `tests/security` — low-level software security tests

These tests are derived from **[Low-Level Software Security for Compiler
Developers](https://github.com/llsoftsec/llsoftsecbook)** (llsoftsecbook). They
exercise the security-relevant behavior of the code pcc actually emits. pcc
lowers **both** its C frontend and its no-libpython **Python** frontend down to
machine instructions, so the same classes of low-level hazard apply to both —
hence the suite covers C *and* Python.

Philosophy (mirrors the book): a compiler must lower security-sensitive
semantics exactly, and should provide hardening for the binaries it produces.
Tests assert the **secure** behavior. Where pcc already does the right thing the
test passes (and is a regression guard); where pcc has a gap the test is
`xfail(strict=True)` so that *closing* the gap flips it to XPASS and forces the
marker — and the matching task — to be removed.

## How tests run

* **C** (`test_c_*.py`): `CEvaluator().evaluate(src)` compiles `main` and
  returns its exit code; `llvmdump=True` exposes the emitted assembly for
  hardening checks. Fast (in-process JIT).
* **Python** (`test_py_*.py`): `conftest.py` compiles a program through the
  strict no-libpython path (`libpython_mode="off"`, `ir_scaffold_mode="on"`) in
  an isolated child process, runs the native binary, and diffs against a CPython
  oracle. The `backend` argument selects the LLVM path or pcc's own LLVM-free
  `self` backend.

```bash
env -u LC_ALL uv run pytest tests/security/ -q -n0 -rxX
```

## Book topic → test mapping

| Book topic | Frontend | Test | Status |
|---|---|---|---|
| Integer overflow / truncation / signedness (size & bounds computations) | C | `test_c_integer_safety.py` | PASS — pcc lowers C integer semantics correctly |
| Division / `INT_MIN/-1` UB (no UBSan guard) | C | `test_c_division_trap.py` | PASS on AArch64 (documents the no-guard hazard) |
| Stack buffer overflow → stack canary (`-fstack-protector`) | C | `test_c_stack_protection.py::test_stack_canary_emitted...` | PASS — `__stack_chk_guard`/`__stack_chk_fail` in emitted code (LLVM path) |
| Code-reuse / CFI: pointer authentication (`pac-ret`) + BTI | C | `test_c_stack_protection.py::test_control_flow_protection...` | PASS — AArch64 functions get PAC/BTI branch-protection attrs |
| Sensitive-data clearing (memset DSE, CWE-14) | C | `test_c_stack_protection.py::test_secret_clearing...` | PASS — zeroing `memset` lowers to volatile LLVM memset |
| Integer overflow must promote, never wrap (`int` arbitrary precision; obligation 2/7) | Python | `test_py_integer_safety.py` (LLVM + `self`) | PASS |
| Out-of-bounds read: list / dict / str / bytes bounds | Python | `test_py_memory_safety.py` | PASS (IndexError / KeyError raised) |
| Arithmetic-fault safety (`/ // %` by zero, negative shift) | Python | `test_py_arithmetic_safety.py` | PASS (ZeroDivisionError / ValueError) |
| Type confusion (float vs pointer; JIT-vuln chapter) | Python | `test_py_local_type_confusion.py` | PASS |

## Confirmed gaps (summarized as tasks in `docs/goal/task-board.yaml`)

* **SEC-P1-UBSAN** (documented, not a failing test) — no
  `-fsanitize=undefined`-style instrumentation for signed overflow,
  divide-by-zero, or out-of-range shifts in the C frontend.

The tests do **not** modify any pcc source; per the task that created them they
only add coverage and route the gaps to the goal board.
