# Investigation: Python NaN truth and inequality use ordered predicates

## Status
active — issue193; current-source failure reproduced, focused repair pending

## Problem Description
Python requires a NaN to be truthy and unequal to itself. The Python frontend
emits LLVM ordered inequality for both operations. That predicate is false
when either operand is NaN. The LLVM builder's ordered API is correct; the
Python callers select the wrong predicate. C already selects unordered
inequality and must keep its usual arithmetic-conversion rules.

## Repro
`build/correctness-20260906-a/array-numeric-current-01/observation.json`
records a source-hashed host-pcc -> self/no-libpython/C-runtime program, with
CPython output as control. Typed `bool(nan)` and `nan != nan` both print False
instead of True. Its binary and process receipt were read back successfully.

## Test [CONFIRMED]
The current-source capability program executes the failure. The durable
regression is `tests/python/test_python_numeric_comparison_contract.py`, with
typed/boxed NaN, infinities, signed zero and finite controls. It must execute
through a native binary; emission-only and historical pcc1 evidence do not
close this defect.

## Proposals
- No.1 Select unordered inequality at Python semantic lowering sites [pending]

## No.1 Select unordered inequality at Python semantic lowering sites
### Code Change
Inventory: `coercion_lowering.py` has one float-truth site;
`compare_membership_lowering.py` has two scalar floating-comparison sites.
Float equality inside complex/value payloads stays ordered. The shared LLVM
builder and C lowering retain their own contracts.

### Pending
Run the differential regression after the predicate change. The pcc-Python
formatter's `value != value` NaN guard depends on this repair; a fresh
pcc-Python runtime/pcc1 gate remains a separate required boundary.

## Update — source-bound evidence and history (2026-09-07)

The documentation handoff read the retained program, producing script,
observation JSON and process receipt without rerunning the compiler. The process
receipt is `COMPLETE` with return code 0, and records `PCC_RUNTIME_CC=cc`,
`PCC_RUNTIME_HIGH=c`, and `PCC_SOURCE_ROOT=/Users/jiamo/my/pcc`. The producing
script invokes `compile_python(..., backend='self', libpython_mode='off',
ir_scaffold_mode='on')`, then executes the resulting binary and CPython control.

The retained `probe.py` still matches SHA256
`9f0d15cf7318a26dc5086dfb146e7d9f48b22ad0ab209e67812c1c5eb9dd516b`;
the executable still matches
`5e976dc80554205c805d22d18ae86b30460e62cc73b09a905574ef4f9317536c`.
`observation.json` records `sources_unchanged=true` for its capture. These hashes
identify the pre-repair observation, not a promise that subsequently edited
compiler files still have those bytes.

| Expression/path | CPython | Captured native C-runtime result |
|---|---|---|
| typed `bool(n)` | True | False |
| boxed `bool(boxed.value)` | True | True |
| typed `n == n` | False | False |
| typed `n != n` | True | False |
| typed/boxed `bool(-0.0)` | False / False | False / False |
| `repr(n)` | nan | nan |

The earlier `array-numeric-capability-01/source-receipt.json` explicitly names
the older `source-2gdr4ie9/stage1/pcc1`, its frozen source snapshot and its
`libpy_runtime_pcc_py.a` bundle. It is a different compiler/runtime boundary.
Any formatter observation from that artifact must not be described as a
current C-runtime formatting failure: this capture prints `repr nan` correctly.

Relevant histories were read end to end:
[closed-world float conversion](pcc-py-codegen-float-dyn-closed-world.md),
[finite literal raw-integer scaling](pcc1-float-literal-bignum-scale-raw-int-trap.md),
and [float formatting round-trip](pcc1-float-repr-strtod-17-digit-defect.md).
Their rejected repeated scaling and digit-count-only changes must not be reused
as NaN fixes. This issue changes the predicate selected for Python truth/`!=`;
it does not change float parsing, decimal formatting or C conversion semantics.

## Update — focused predicate execution, not closure (2026-09-07)

The implementation owner's retained
`numeric-nan-predicate-red.{stdout,result.json,pytest.jsonl}` reports one failed
typed-NaN test in 4.55s. After one float-truth site and two scalar comparison
sites selected unordered inequality, the corresponding `green` artifacts
report `COMPLETE`, return code 0 and **3 passed in 5.07s**:

- `test_python_numeric_comparison_contract.py::test_typed_nan_truth_and_inequality_match_python`
- `tests/c/test_float_semantics.py::test_nan_comparisons_follow_c_semantics`
- `tests/c/test_self_backend.py::test_self_backend_runs_unordered_fcmp_semantics_in_ir`

The first test executes a fresh host-pcc/self/no-libpython program against a
private cached C-runtime fixture; the other two guard C semantics and the
shared backend's unordered predicates. These receipts were read back during
the handoff. No tests were rerun by the documentation agent.

No.1 remains pending overall qualification: the current pcc-Python runtime,
fresh pcc1/compiler closure and relevant bootstrap gates are not established
by these three tests. The separate
[boxed equality](python-boxed-nan-value-comparison.md) and
[mixed comparison](python-int-float-lossy-comparison.md) mechanisms remain open.
