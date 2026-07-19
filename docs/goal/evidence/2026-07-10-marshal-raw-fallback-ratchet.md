# Marshal raw fallback ratchet

Date: 2026-07-10

Task id: `M0-HEAD-MARSHAL-RAW-FALLBACK-REGRESSION`

Changed files:

- `pcc/py_frontend/codegen/marshal.py`
- `tests/python/test_fallback_baseline.py`

Failure boundary:

After the independent assignment contextual fix, current HEAD had one fallback
gate failure. The legacy scaffold-off raw compile of
`pcc.py_frontend.codegen.marshal` emitted 341 `py_cpy_*` calls against the
checked baseline of 310 with a five-percent allowance. Scaffold-on remained
zero. CPython 3.13.2 and 3.14.5 reproduced the same 341 count.

Reduction:

The parent compiler emitted 325 calls. The current compiler's 16-call delta
was entirely in `marshal_to_object`: two additional single-element list
literals used as `IRBuilder.call` argument sequences lowered through CPython
`list()+append`. All five single-element call argument sequences had the same
shape. `IRBuilder.call` accepts `Iterable[Value]` and immediately materializes
`list(args)`; llvmlite also accepts a sequence.

Implementation:

The five single-element call argument sequences now use tuple literals. The
runtime values, callee, argument order, and IRBuilder behavior do not change.
The raw scaffold-off count is 316 and scaffold-on remains zero. No baseline was
changed.

Gates:

- Focused raw test before fix -> failed, `341` versus baseline `310`, in
  `0.37s`.
- Source substitution -> scaffold-off `316`, scaffold-on `0`.
- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/python/test_fallback_baseline.py::test_marshal_raw_per_module_fallbacks_stay_under_ratchet tests/python/test_fallback_baseline.py::test_on_mode_assignment_statement_contextual_fallback_zero`
  -> `2 passed in 6.48s`.
- Default LLVM-CAPI parity/end-to-end -> `24 passed in 0.56s`.
- Default marshalling/runtime ABI focused batch -> `6 passed in 7.67s`.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py`
  through the HEAD truth runner -> `21 passed in 188.25s`.

An extra `PCC_USE_LLVMLITE_PY=1` probe reached the pre-existing
`FunctionAttributes._attrs` incompatibility in `runtime_abi.py`; that opt-out
path is not used as evidence for this source-only sequence change. The default
LLVM-CAPI parity gate is green.

Claim:

The current HEAD fallback/no-libpython ratchet is restored without baseline
recapture. This is not bootstrap, five-GC, package, or performance evidence.

Open boundary:

None for this finite marshal raw-count regression. The full HEAD truth manifest
remains open until its heavy gates run on the same source fingerprint.
