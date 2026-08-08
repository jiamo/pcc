# Runtime diagnostic static convergence — 2026-08-14

Mode: source/mirror contract validation only; no runtime publication or pcc1.

- `gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_exception_chaining_wiring.py -k 'not nested_unhandled_traceback and not unhandled_implicit_chain'`
  — 12 passed, 2 runtime nodes deselected.
- `gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_py_func_fail_closed.py::test_py_func_call_kwargs_fail_closed_contract_is_mirrored`
  — 1 passed.

These non-vacuous source contracts cover C, oracle and pcc-Python symmetry;
callee-owned exception preservation; silent-NULL/status attribution; and
guard-before-cleanup ordering across compiled calls, object/method/constructor
calls, extension slots, protocol/dunder/class/format/copy/pickle and splat
families. Weakref remains an explicit unraisable boundary.

This is intentionally weak evidence: executable traceback/chaining behavior,
C-versus-port runtime parity, the remaining finite pointer-returning audit and
bootstrap qualification wait for the single final current runtime build.
