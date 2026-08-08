# Build-tool stdlib focused evidence — 2026-08-14

Mode: host CPython differential, provider registry and fail-closed contracts;
the integration compiled-closure node was excluded by the default marker.

`tests/python/test_py_stdlib_build_tool_closure.py` completed with 274 passed
and one integration node deselected.

The first fail-fast run found that `from typing import TYPE_CHECKING, Any`
created false `typing.TYPE_CHECKING` and `typing.Any` module candidates. The
generic import policy now suppresses child expansion for every declared
compile-time-only module while preserving ordinary real child discovery such
as `unittest.mock`. Its exact test and the adjacent initialization-boundary
scanner test passed before the full file was rerun.

The focused non-integration family suites also completed, each independently
with fail-fast serial execution:

- archive closure: 18 passed, one integration node deselected;
- importlib closure: 16 passed, one integration node deselected;
- unittest/mock closure: 14 passed, one integration node deselected;
- developer-tool closure: 9 passed, one integration node deselected;
- compression source contract: 2 passed, one integration node deselected.

This evidence does not include the strict self/no-libpython compiled provider
closure, real Meson/NumPy workload, current pcc1 or fixed-point gate.
