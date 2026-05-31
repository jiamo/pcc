# Investigation: Python Valhalla value-model status overclaim

## Status
active

## Problem Description
The value-model plan and status report claimed the Python Valhalla-inspired
track was implemented through V6. Code inspection showed that only a V0 slice
is wired into type inference and class lowering, while V1-V6 mostly consist of
host-Python metadata helpers and planning descriptors.

The concrete user report identified these gaps:

- V1 has no direct LLVM struct ABI, marshal helpers, or IR-shape gate proving
  valueclass hot paths avoid object allocation.
- V2 now has a narrow C runtime `PyValueBox` compatibility path for scalar
  payload boundaries, but no full V2 pointer/value flattened payload design or
  complete runtime tracing policy yet.
- V3 has no runtime class-layout metadata for pointer/scalar/value-payload slots
  or backend #3/#4 relocation integration.
- V4 has no `pcc.array[Point]` contiguous payload runtime.
- V5 has no monomorphization, type-tuple cache, or `--explain-specialization`.
- V6 has no real hot-object migration or pcc1 allocation benchmark evidence.

## Evidence
`pcc/py_frontend/py_ast.py` defines `ValueClassType` and
`pcc/py_frontend/type_infer.py` recognizes `@pcc.valueclass`, so the V0 type
marker exists.

`pcc/value_model.py` contains host-side dataclasses named `ValuePayload`,
`ValueBox`, `SpecializedArray`, and `GenericSpecialization`. These remain
planning helpers. A narrow runtime alias-object helper now exists for scalar
payload field movement (`py_valuebox_new`, `py_valuebox_get_field`,
`py_valuebox_set_field`) while full flattened payload metadata and dispatch is
still open.

`rg 'py_valuebox_new|py_valuebox_get_field|py_valuebox_set_field|ValuePayload|valueclass' pcc/py_runtime` finds runtime-backed
`PyValueBox` helpers and instance-like dispatch wiring; flattening/relocation
coverage remains incomplete.

The expected plan tests for V1-V4 were partially absent before this investigation.
In this round, host/runtime regression scaffolding was partially materialized:

- `tests/python/data_model/test_value_class_source_shape.py`
- `tests/python/test_py_value_class_unboxed.py`
- `tests/python/data_model/test_value_class_runtime.py`
- `tests/python/data_model/test_value_class_field_flattening.py`
- `tests/python/test_value_class_array.py`

`tests/python/test_value_model_valhalla.py` still covers status surface +
type-inference smoke plus legacy host checks.

## Fix Applied
The status surface now distinguishes implementation from scaffolding:

- `value_model_status()["implemented_through"] == "V1-direct-scalar-payload-checked-marshal-eq-v2-pointer-boundary-partial"`
- `value_model_status()["scaffolding_through"] == "V6"`
- `value_model_status()["production_runtime"] is False`
- `value_model_status()["not_implemented"]` lists the missing V1-V6 runtime,
  codegen, GC, specialization, and benchmark work.

The plan and status report were corrected to stop claiming V1-V6 completion;
the remaining items now reflect that only a narrow V1/V2 boundary slice is active.

The V0 implementation was strengthened with source-shape diagnostics for:

- untyped valueclass fields,
- untyped `self.x = ...` initializer assignments,
- subclassing,
- `__dict__` / weakref participation,
- `__del__`,
- strict-mode identity escapes through `is` / `is not` and `id()`.

## Remaining Work
V1-V6 remain active implementation work. The current target is V2 boundary hardening:
runtime object aliasing should become a complete value transport abstraction
(flattened payload + GC-aware metadata, pointer payloads, and value-level
equality/hash) before widening to broader marshal coverage.

## Update 2026-05-24: V2 runtime alias path landed (partial)
The prior state that V2 lacked any C runtime `PyValueBox` no longer holds for
current HEAD.

Current V2-anchored runtime path:

- Added `PY_TYPE_VALUEBOX = 200` and runtime ABI exports:
  - `py_valuebox_new`
  - `py_valuebox_get_field`
  - `py_valuebox_set_field`
- Frontend boundary lowering now uses these helpers for scalar-field
  valueclass payload boxing/unboxing across compatibility object boundaries.
- C and pcc-Python runtime now share the helper implementations (`py_class.c`
  and `py/py_class.py`) and route alias-like objects through instance dispatch.
- Tests were updated to assert IR and runtime smoke coverage in this alias path.

Still-open in V2:

- full marshal coverage for all typed/object boundary combinations,
- flattened payload GC-slot/relocation semantics and pointer-field policy,
- value-hash/value-equality in native runtime,
- non-scalar/nested value payload flattening.

Verification evidence is in existing passes listed in this document plus
`tests/python/test_py_value_class_unboxed.py`, `tests/python/test_value_model_valhalla.py`,
`tests/python/data_model/test_value_class_runtime.py`, and
`tests/python/data_model/test_value_class_field_flattening.py`.

## Update 2026-05-19
The first V0 source-shape diagnostic patch initially regressed the
no-libpython fallback ratchet for `pcc.py_frontend.type_infer`: the independent
module count rose to 951 against the baseline 846 (+5% cap is 888). The cause
was repeated `PyFrontendError(...)` keyword-call construction in each new
diagnostic branch.

The fix keeps diagnostics in `type_infer.py` but routes all frontend diagnostic
construction through one `_raise_frontend_error(span, message, hint)` helper
using positional construction. The current independent fallback count for
`pcc.py_frontend.type_infer` is 851, back inside the ratchet.

Verification:

- `tests/python/test_value_model_valhalla.py`
  `tests/python/data_model/test_value_class_source_shape.py`: 11 passed.
- `tests/python/test_fallback_baseline.py`
  `tests/python/test_ir_py_fallback_baseline.py`: 17 passed.
- `tests/python/test_bootstrap_gate_baseline.py`
  `tests/python/test_cli_launcher.py`
  `tests/python/test_cli_bootstrap_observability.py`: 8 passed, 4 skipped.
- `tests/python/test_py_multi_file_compile.py`
  `tests/python/test_py_multi_file_bootstrap_shim.py`: 73 passed.

## Update 2026-05-19: V1 scalar-payload slices
The first V1 codegen slices are now implemented, but they are intentionally
narrow.

Implemented:

- `tests/python/test_py_value_class_unboxed.py` adds an IR-shape gate for a
  scalar-field valueclass local hot path.
- Direct assignment such as `p = Point(1, 2)` can lower to an LLVM aggregate
  payload when the valueclass fields are all `int`, `float`, or `bool`.
- `p.x` / `p.y` local field reads lower through `extractvalue`.
- Direct function arguments can use the payload ABI for scalar-field
  valueclasses, e.g. `def norm2(p: Point) -> int`.
- Typed constructor returns can return the payload form, e.g.
  `def make_point(...) -> Point: return Point(...)`.
- Direct method receivers can use the payload ABI for scalar-field
  valueclasses, e.g. `p.norm2()`.
- The tested local hot path contains no `call @py_instance_new`.
- The default Python IR pass pipeline now parses aggregate-return function
  headers such as `define { i64, i64 } @make(...)`.

Still not implemented:

- `marshal_value_to_object`,
- `marshal_object_to_value`,
- C runtime `PyValueBox`,
- GC-visible flattened payload tracing,
- value arrays, monomorphization, and hot-object migration benchmarks.

Verification:

- `tests/python/test_value_model_valhalla.py`
  `tests/python/test_py_value_class_unboxed.py`
  `tests/python/data_model/test_value_class_source_shape.py`
  `tests/python/test_ir_scaffold_symbols.py`
  `tests/python/test_ir_mutator.py`: 63 passed.
- Manual default-pass compile/run of `return Point(...)` smoke returned `7`.
- Manual default-pass compile/run of `p.norm2()` smoke returned `25`.
- `tests/python/test_fallback_baseline.py`
  `tests/python/test_ir_py_fallback_baseline.py`: 17 passed.
- `tests/python/test_py_multi_file_compile.py`
  `tests/python/test_py_multi_file_bootstrap_shim.py`: 73 passed.
- `tests/python/test_bootstrap_gate_baseline.py`
  `tests/python/test_cli_launcher.py`
  `tests/python/test_cli_bootstrap_observability.py`: 8 passed, 4 skipped.

## Update 2026-05-19: receiver ABI fallback ratchet fix
The first direct-method receiver patch regressed the raw per-module self-compile
fallback ratchet for `pcc.py_frontend.codegen.class_gen`: the count rose to 241
against the baseline 216 (+5% cap is 226). The cause was calling
`parent._valueclass_payload_ir_type(...)` from `class_gen.py`; in the solo
per-module probe, `parent` does not have a fully typed native host protocol for
that new method, so the call lowered through CPython `getattr` / `call`.

The fix keeps the V1 direct receiver ABI but moves the tiny scalar valueclass
payload-type calculation into `_classgen_valueclass_payload_ir_type()`, a local
class-gen helper that mirrors the current V1 scalar payload subset. This keeps
the emitted method signatures and receiver slots native while avoiding a new
CPython fallback in the bootstrap compiler's own raw module probe. The current
single-module `class_gen` fallback count is 223, inside the ratchet.

Verification:

- `pcc.py_frontend.codegen.class_gen` single-module fallback count: 223.
- `tests/python/test_value_model_valhalla.py`
  `tests/python/test_py_value_class_unboxed.py`
  `tests/python/data_model/test_value_class_source_shape.py`
  `tests/python/test_ir_scaffold_symbols.py`
  `tests/python/test_ir_mutator.py`: 63 passed.
- `tests/python/test_fallback_baseline.py`
  `tests/python/test_ir_py_fallback_baseline.py`: 17 passed.
- `tests/python/test_py_multi_file_compile.py`
  `tests/python/test_py_multi_file_bootstrap_shim.py`: 73 passed.
- `tests/python/test_bootstrap_gate_baseline.py`
  `tests/python/test_cli_launcher.py`
  `tests/python/test_cli_bootstrap_observability.py`: 8 passed, 4 skipped.

## Update 2026-05-19: direct scalar payload equality
V1 now includes a narrow `value_payload_eq` slice for same-type scalar-field
valueclasses in direct payload form. `p == q` and `p != q` lower before class
dunder dispatch, so the hot path no longer emits an invalid payload-argument
call to the autogenerated pointer-ABI `Point.__eq__` helper. The lowering
extracts each payload field and combines integer/bool `icmp` or float ordered
`fcmp` results with `and`.

Implemented:

- same-type scalar-field valueclass payload `==` / `!=`;
- IR-shape gate proving the `main` hot path avoids `Point.__eq__`,
  `py_obj_eq`, and `py_instance_new`;
- default IR-pass compile/run smoke returning the expected equality result.

Still not implemented:

- `marshal_object_to_value`,
- identity escape / object-boundary equality,
- non-scalar or nested valueclass payload equality,
- C runtime `PyValueBox`,
- GC-visible flattened payload tracing,
- value arrays, monomorphization, and hot-object migration benchmarks.

Verification:

- `tests/python/test_py_value_class_unboxed.py`: 7 passed.

## Update 2026-05-19: scalar payload boxing at Dyn boundary
V1 now includes a narrow `marshal_value_to_object`-style slice for direct
scalar-field payloads. When a valueclass payload crosses selected object
boundaries, codegen allocates an ordinary pcc instance with `py_instance_new`
and stores boxed scalar fields with `py_instance_set_field`. This is an
intentional compatibility box, not the full V2 flattened payload C-runtime
`PyValueBox`.

Implemented:

- scalar-field valueclass payload boxing in `_emit_as_object`;
- PCC/CPython bridge object conversion path;
- `DynType` coercion, including a direct `Any` function-argument boundary;
- print/multi-print object conversion path.

Still not implemented:

- full `marshal_value_to_object` coverage for every object boundary,
- full `marshal_object_to_value` coverage for every typed boundary,
- identity escape / object-boundary equality,
- non-scalar or nested valueclass payload boxing,
- C runtime `PyValueBox`,
- GC-visible flattened payload tracing,
- value arrays, monomorphization, and hot-object migration benchmarks.

Verification:

- `tests/python/test_py_value_class_unboxed.py`: 8 passed.

## Update 2026-05-19: scalar payload unboxing at typed boundary
V1 now includes the matching narrow `marshal_object_to_value`-style slice for
ordinary pcc instances that need to flow back into direct scalar payload ABI.
When a boxed valueclass reaches a typed `Point` boundary, codegen reads fields
with `py_instance_get_field`, unboxes scalar field objects, and rebuilds the
LLVM payload aggregate before the direct typed call.

Implemented:

- ordinary pcc instance to scalar-field valueclass payload unboxing;
- direct `Any -> Point` function-argument boundary after a prior valueclass
  boxing step.

Still not implemented:

- full `marshal_value_to_object` / `marshal_object_to_value` coverage for
  every object/typed boundary,
- identity escape / object-boundary equality,
- non-scalar or nested valueclass payload boxing/unboxing,
- C runtime `PyValueBox`,
- GC-visible flattened payload tracing,
- value arrays, monomorphization, and hot-object migration benchmarks.

Verification:

- `tests/python/test_py_value_class_unboxed.py`: 9 passed.

## Update 2026-05-19: recursive payload rejection
V1 now rejects recursive and mutually-recursive valueclass payload graphs before
codegen. This closes the plan item "Reject recursive/self-containing value
classes in V1" for the current scalar-payload subset; it does not implement
recursive value payload layout, nullable value fields, or boxed recursive edges.

Implemented diagnostics:

- direct self field, e.g. `child: 'Bad'`;
- mutually-recursive same-module valueclasses;
- container-mediated self reference, e.g. `children: list['Bad']`.

Still not implemented:

- non-scalar valueclass payload lowering,
- boxed recursive edge syntax or runtime policy,
- nullable flattened value fields,
- C runtime `PyValueBox`,
- GC-visible flattened payload tracing,
- value arrays, monomorphization, and hot-object migration benchmarks.

Verification:

- `tests/python/data_model/test_value_class_source_shape.py`: 10 passed.
- `tests/python/test_value_model_valhalla.py`
  `tests/python/test_py_value_class_unboxed.py`
  `tests/python/data_model/test_value_class_source_shape.py`
  `tests/python/test_ir_scaffold_symbols.py`
  `tests/python/test_ir_mutator.py`: 70 passed.
- `tests/python/test_fallback_baseline.py`
  `tests/python/test_ir_py_fallback_baseline.py`: 17 passed.
- `tests/python/test_py_multi_file_compile.py`
  `tests/python/test_py_multi_file_bootstrap_shim.py`: 73 passed.
- `tests/python/test_bootstrap_gate_baseline.py`
  `tests/python/test_cli_launcher.py`
  `tests/python/test_cli_bootstrap_observability.py`: 8 passed, 4 skipped.

## Update 2026-05-19: checked scalar payload unboxing failure path
The previous object-to-value payload slice rebuilt scalar-field payloads from
ordinary pcc instances, but it did not first prove the boxed object had the
expected valueclass. A wrong dynamic object could reach a typed `Point`
boundary and be consumed through `py_instance_get_field`.

The unbox path now loads the expected class object, calls `py_obj_isinstance`,
and raises `TypeError` before field reads when the dynamic object is not an
instance of that valueclass. This keeps the V1 partial boundary aligned with
the compatibility rule: fail loudly instead of silently reading the wrong
object shape.

Implemented:

- runtime class check before scalar-field valueclass payload unboxing;
- TypeError branch for wrong `Any`/`Dyn` object at a typed valueclass boundary;
- regression that `ident(123)` passed into `def total(p: Point)` exits
  nonzero with `TypeError`, not a native crash.

Still not implemented:

- complete `marshal_object_to_value` coverage for every typed boundary,
- object-boundary equality and identity-sensitive dynamic operations,
- non-scalar or nested valueclass payload boxing/unboxing,
- C runtime `PyValueBox`,
- GC-visible flattened payload tracing,
- value arrays, monomorphization, and hot-object migration benchmarks.

Verification:

- `tests/python/test_py_value_class_unboxed.py`: 10 passed.
- `tests/python/test_value_model_valhalla.py`
  `tests/python/test_py_value_class_unboxed.py`
  `tests/python/data_model/test_value_class_source_shape.py`
  `tests/python/test_ir_scaffold_symbols.py`
  `tests/python/test_ir_mutator.py`: 71 passed.
- `tests/python/test_fallback_baseline.py`
  `tests/python/test_ir_py_fallback_baseline.py`: 17 passed.
- `tests/python/test_py_multi_file_compile.py`
  `tests/python/test_py_multi_file_bootstrap_shim.py`: 76 passed.
- `tests/python/test_bootstrap_gate_baseline.py`
  `tests/python/test_cli_launcher.py`
  `tests/python/test_cli_bootstrap_observability.py`: 8 passed, 4 skipped.
- `scripts/bootstrap.sh --backend self --stage 1 --out-dir build/bootstrap-pytest-self`
  rebuilt current pcc1.
- `PCC_REQUIRE_CURRENT_PCC1=1 PCC_CURRENT_PCC1=build/bootstrap-pytest-self/pcc1`
  package pcc1 hard gate: 92 passed, 1 skipped.

## Update 2026-05-24: valuebox runtime tag survives dynamic boundary
The scalar valuebox object-boundary path regressed because the pcc-Python
runtime `py_valuebox_new` wrote `PY_TYPE_VALUEBOX` through a module-level
constant. In the runtime compilation path that constant did not preserve the
expected literal value, so the boxed object reached `py_obj_isinstance` and
`py_obj_getattr` with header `type_tag == 0`. Dynamic `Any` boundaries then
raised `AttributeError` or rejected a valid `Point` valuebox.

The pcc-Python runtime now writes the literal `200` (`PY_TYPE_VALUEBOX`) into
the object header, matching the public runtime tag enum and keeping valuebox
objects on the instance-compatible dispatch path.

Implemented:

- pcc-Python runtime valuebox tag writeback uses literal `200`;
- `py_isinstance` / `py_obj_isinstance` normalize class/instance pointers
  through the relocation read barrier before header checks;
- focused dynamic boundary regressions for valueclass boxing, unboxing, and
  wrong-type rejection pass again.

Still not implemented:

- non-scalar or nested valueclass payload boxing/unboxing;
- identity-sensitive dynamic operations for escaped valueclass boxes;
- GC-visible flattened payload tracing beyond the current scalar object-box
  path;
- value arrays, monomorphization, and hot-object migration benchmarks.

Verification:

- `tests/python/test_py_value_class_unboxed.py`: 10 passed.
- `tests/python/data_model/test_value_class_runtime.py`
  `tests/python/test_value_model_valhalla.py`: 8 passed.
- `tests/python/data_model/test_value_class_field_flattening.py`: 3 passed.

## 2026-05-24 update: pointer payload boundary is implemented but still weakly verified

The valueclass payload ABI now accepts object-pointer fields such as `list` in addition to the earlier scalar `int`/`float`/`bool` subset. The lowering continues to reject nested valueclasses so the implementation does not silently claim recursive flattening or deep Valhalla semantics.

The new regression shape boxes `Bag(items: list, count: int)` through an `Any`/dynamic boundary, forces `gc.collect()`, then reads the pointer field back through dynamic attribute access and a typed valueclass function. This proves the current valuebox boundary can preserve a non-scalar payload pointer for that case.

This does not yet close the full Valhalla/value-model objective. Remaining weak areas include broader identity-escape coverage, deeper GC tracing guarantees beyond the covered pointer payload case, nested valueclass payloads, and post-fix fallback baseline verification.

### Additional staged regressions

Two additional runtime regressions have been staged for the pointer-payload slice: one for `str` payload roundtrip and one for mutable `list` identity preservation through a dynamic valuebox boundary. They intentionally remain described as staged, not proven, until the focused runtime and fallback gates are re-run.

### Pointer payload equality staged

Direct payload equality for selected pointer fields now calls `py_obj_eq` instead of comparing raw object pointers. A staged regression checks that two valueclass payloads containing independently allocated but equal lists compare equal, while changed fields compare unequal. This remains staged until the focused runtime test is executed.

### Boxed valueclass equality staged

`py_obj_eq` now has a `PY_TYPE_VALUEBOX` branch in both runtime implementations. It compares the runtime valueclass class first, then compares each payload slot with `py_obj_eq` using GC-aware slot loads. A staged regression covers boxed valueclass equality after an `Any` boundary, including different-class inequality for same-shaped payloads.

### Boxed valueclass hash staged

`py_obj_hash` now has a `PY_TYPE_VALUEBOX` branch in both runtime implementations. It mixes payload-field hashes loaded through GC-aware slot reads so equal boxed valueclasses have matching hashes for selected hashable payloads. A staged regression covers dictionary lookup with a separately boxed but payload-equal valueclass key after `gc.collect()`.

## 2026-05-24 validation update: pointer/object valuebox boundary

The pointer/object payload slice now has focused validation:

- Runtime valueclass regression file: `10 passed in 6.91s`.
- Valueclass unboxed/codegen regression file: `11 passed in 4.27s`.
- Value model status/field-flattening regressions: `7 passed in 0.99s`.
- Fallback baseline pair: `17 passed in 89.59s`.

Validation exposed and fixed an inline constructor escape bug: `to_dyn(Bag(...))` previously produced a normal user object tag (`104`) instead of a `PY_TYPE_VALUEBOX` (`200`). The argument ABI lowering now emits the valueclass constructor payload before boxing when the target parameter is `Any`/object.

Validation also exposed a fallback cleanliness issue caused by dynamic valueclass attribute probing emitting the receiver before it knew whether the attribute name matched any valueclass field. Candidate selection now happens first, so unrelated native module attributes do not introduce CPython imports.
