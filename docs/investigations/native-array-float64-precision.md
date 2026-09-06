# Investigation: native array CLI silently quantizes float64 values

## Status
active — issue #191; source correction and native qualification pending

## Problem Description
The native array CLI represents numeric tokens as integers scaled by one
million. Literal casts, arithmetic, comparisons, creation and reductions lose
precision although reports claim float64 and return no diagnostic. The host
array front door retains the original floating values. This is source-level
semantic divergence, independent of a compiler/runtime miscompile.

Predecessor `pcc1-array-core-literal-empty.md` was read end to end. Its optional
sentinel workaround fixes a different failure and is retained here.

## Repro
Execute the native-shim Python source and the ordinary host front door with
literal `[0.0000001,1.1234567]`: native source reports `[0.0,1.123456]`, while
the host reports `[1e-7,1.1234567]`. Both say float64, ok=true. Division 1/3 is
similarly truncated to 0.333333 in the native source.

## Test [CONFIRMED]
The source differential was observed in the architecture review and issue191.
`tests/python/test_array_numeric_precision.py` now covers the complete numeric
consumer families. Its first focused run failed at literal-float64:0.0 instead
of1e-7 (1 failed in0.17s). Log:
`build/correctness-20260906-a/array-numeric-red.log`.

## Fixed-point inventory
The complete native source inventory before editing includes token parsing and
formatting; dtype casts/astype; binary operations; unary neg/abs/logical_not;
clip; matmul; comparisons; sort/argsort; searchsorted; partition/argpartition;
arange; eye; linspace; sum/prod/mean/min/max/any/all reductions; arg reductions;
count_nonzero/nonzero/argwhere/flatnonzero; cumsum/cumprod. Fill and shape/data
transforms reach the same casting helpers. Replacing only literal conversion
would leave the same six-digit defect active in arithmetic and predicates.

Inventory command: `gtimeout 10s rg -n '1000000|100000|scaled|< 6' pcc/cli_bootstrap_array_core.py`.

## Proposals
- No.1 Share numeric semantics with separate integer and floating operations [pending]

## No.1 Shared numeric owner
### Code Change
`pcc/array_numeric.py` now owns scalar token conversion, floating arithmetic,
comparison and compensated sum, plus wrapping of an already-integer boxed
carrier. Host `array_core` delegates its scalar operations and float reduction
to those same primitives. The native CLI delegates every formerly scaled
consumer; literal values, arithmetic, comparisons, matmul, creation, reduction,
ordering and truth predicates no longer use a1e6/fixed-six-digit lane.

Integer parsing and wrapping have distinct contracts: parsing returns a boxed
integer, and wrapping does not redundantly call int() on it. Width and carrier
preconditions are tested, including signed/unsigned64 limits and negative
values. Numeric errors are reported with explicit diagnostics. Nonfinite
tokens serialize as NaN/Infinity, following the existing host JSON front door;
generic NaN/mixed-number semantics remain owned by issue193. Float32 retains
the host model's existing Python-float value carrier; this is not a new claim
of physical binary32 storage or full NumPy behavior.

### Native capability evidence
The older receipt-bound source-2gdr4ie9 pcc1 compiled a standalone numeric
capability probe through self-backend -o in14.19s. It used its own frozen source
snapshot/runtime, not the current working backend. Finite float(str)/repr,
scientific notation, 1/3, negative zero and integer9007199254740993 are exact.
Two older-artifact limitations were observed: NaN repr/truth and boxed mixed
large-int/float equality differ from CPython. These are not current-source
runtime findings and cannot justify attributing a defect to current runtime
source. Evidence: `build/correctness-20260906-a/array-numeric-capability-01/`.

### Pending
Fresh-current-pcc1 CLI and required bootstrap/fallback gates remain necessary
for closing issue191. The new current-pcc1 test uses the release compiler
freshness verifier and48 numeric cases; it does not accept an older binary.

## Update — source packet and current-native scalar evidence

The final source packet passes57 tests in0.54s; the5 existing non-CLI array
tests pass in0.18s. The complete fixed-point inventory command now returns no
matches for the retired helpers/constants. Source tests include scientific
notation, negative zero, float64 precision, existing float32 carrier behavior,
all numeric consumer families, integer wrap/type controls and nonfinite JSON.

The first older-pcc1 helper gate compiled in29.55s but observed float reduction
and64-bit cast failures as well as the known NaN/mixed-comparison gaps. These
were not attributed to current source from that older artifact. A current
host-pcc/self/no-libpython/C-runtime gate then confirmed the64-bit loss while
the explicit shared float accumulator passed mean and cancellation exactly.

Read-only disassembly of that exact current binary localized the integer loss:
`token_integer` retained the parsed object, but redundant `int(value)` in the
old combined coercion/wrap function emitted `py_int_to_i64` followed by
`py_int_from_i64` at0x100008150/0x100008160. The compiler's generic object
projection routing defect is issue194; this array change does not claim to fix
it. Separating parse/coercion from wrapping removes that unnecessary conversion
by API contract, without manual decimal parsing or narrowing.

The corrected current-native scalar gate compiled in3.20s and executed all
finite/signed64/unsigned64/mean/cancellation/negative-zero controls exactly.
Its C runtime is the independently built archive
`27d29fa72ccea0dce62934d5-c-default/libpy_runtime.a`. Only the explicitly
labeled issue193 mixed comparison remained different at that milestone.
Artifacts: `build/correctness-20260906-a/array-numeric-current-helper-04/`;
the preceding binary/disassembly is under`array-numeric-current-helper-03/`.
This proves current frontend + self-backend + C-runtime scalar execution,
not pcc1 or pcc-Python runtime transfer.

## Update — older-pcc1 copied CLI gate timed out; no retry or acceptance

The exact current CLI/helper bodies were copied into a local `array_subject`
package, with no source rewrite, so the native compiler could import them as
application modules. The mapping and hashes are in
`build/correctness-20260906-a/array-numeric-native-cli-05/source-receipt.json`.
The12-case driver attempted a real -o compile through the older receipt-bound
pcc1, its frozen source helper root and pcc-Python runtime archive.

It hit the unchanged120s watchdog at120.167s, peak tree RSS2.342GB, with empty
stdout/stderr and no binary. The sampler terminated its process group; direct
PID readback found no survivor. No execution or successful CLI claim follows.
The timeout is not a semantic diagnosis, and its watchdog was not widened or
the older compiler replayed. Terminal receipt:
`build/correctness-20260906-a/array-numeric-native-cli-05/compile.result.json`.

The remaining CLI boundary must be tested using the current host-native path
or the fresh compiler after the concurrent generic correctness slices stabilize,
then through the actual rebuilt pcc1 CLI before issue191 can close.

## Checkpoint — remaining float-to-integer capability boundary

Current emitted code still converts a FloatType int() operand through signed
i64 (`fcvtzs` then `py_int_from_i64` in the retained helper03 disassembly).
The boxed int/str object-projection fix tracked in issue194 is distinct from
arbitrary finite float-to-bigint conversion and NaN/inf conversion exceptions.
The array adapter therefore does not yet have a full float-to-integer dtype
claim. An explicit unsupported-range diagnostic was proposed for that boundary,
but has not been implemented or validated. The human requested a temporary
commit/push checkpoint before that further change; implementation is paused.
