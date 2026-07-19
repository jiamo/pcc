# Investigation: contextual per-module fallback gate for Python self-host

## Status
active

## Problem Description
Raw per-module fallback counts treat mixin files such as
`pcc.py_frontend.codegen.native_math` as standalone classes. That gives
methods a `self` type of `NativeMathLoweringMixin`, while the real
closed-world self-host path executes those methods with `self` bound to
`L1CodeGen`. The raw probe therefore reports `py_cpy_getattr` and
`py_cpy_call*` sites for host methods such as `self._emit_expr(...)`,
even when the real multi-file closure is libpython-free.

## Repro
Run the fallback gate:

```bash
env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py -q -n0
```

The historical failure mode was a per-module ratchet failure in
`layer1.py`, `native_math.py`, `native_modules.py`, or `native_os.py`
despite the multi-file closure reporting zero `py_cpy_*` calls.

## Test [CONFIRMED]
The gate now has three separate models:

```text
multi-file closure fallback count: hard zero
standalone raw per-module count: hard ratchet
contextual mixin per-module count: hard ratchet or hard zero
```

`native_*.py` and `layer1.py` use the contextual model. The contextual
probe builds the same native export table and `derived_class_map` as the
multi-file path before compiling selected modules independently.

## Proposals
- No.1 Keep raw per-module as the only hard gate     [DENIED]
- No.2 Drop per-module gates entirely               [DENIED]
- No.3 Add contextual per-module hard gate          [CONFIRMED]

## No.1 Keep raw per-module as the only hard gate
### Code Change
No code change; continue using `per_module` and `on_mode_per_module` for
all modules.

### DENIED
This uses the wrong semantic model for mixins. It measures missing host
context, not actual fallback debt in the closed-world self-host path.

## No.2 Drop per-module gates entirely
### Code Change
Only assert multi-file total fallback counts.

### DENIED
This preserves correctness for pcc1 but loses the canary for accidental
dynamic idiom creep in standalone modules.

## No.3 Add contextual per-module hard gate
### Code Change
`pcc.py_frontend.pipeline` now exposes closed-world context helpers and
contextual fallback counting:

```text
build_closed_world_context(...)
count_py_cpy_fallback_calls(...)
compile_contextual_per_module_fallback_counts(...)
per_module_probe_policy(...)
contextual_host_for_module(...)
contextual_per_module_modules(...)
l1_codegen_lowering_host_contract(...)
```

The host contract itself lives in
`pcc.py_frontend.codegen.host_contract`; `pipeline.py` re-exports the
public helpers so existing probes have one stable import point.

`tests/python/test_fallback_baseline.py` uses these helpers. The baseline
JSON stores only counts; policy lives in frontend code.

### CONFIRMED
This matches the real self-host model while keeping a hard gate. Contextual
modules must have baseline entries, and stale contextual baseline entries
fail the test.

The host contract is explicit and data-only for now. It names
`pcc.py_frontend.codegen.layer1.L1CodeGen` plus the host attributes and
methods that native lowering mixins depend on. Later type-infer work can
consume this contract as a protocol/stub instead of relying only on the
derived-class map.

## Report
Not closed yet. Follow-up work:

```text
1. Replace the implicit derived-class host model with a formal host interface.
2. Split layer1.py into smaller contextual modules.
3. Require each newly split module to enter the contextual hard-zero gate.
```

First split started with `pcc.py_frontend.codegen.typed_int_abi`, which is
registered as a contextual module with strict-zero fallback baselines.

Second split moved `Import` / `ImportFrom` statement lowering into
`pcc.py_frontend.codegen.import_lowering`. The module is registered as a
contextual module. OFF-mode keeps a hard ratchet baseline for the legacy
builder-dispatch bridge count, while ON-mode is locked at strict zero.
Module teardown, root-enter lifecycle, extern ABI constants, and unsafe
intrinsic lowering remain in `layer1.py`.

Third split moved CPython import fallback state helpers into
`pcc.py_frontend.codegen.cpy_import_state`. This covers module globals,
star-import module maps, star-import lookup, and the per-function
`py_cpy_ensure_init` emission guard. The module is contextual. OFF-mode
keeps a hard ratchet baseline for the legacy builder/runtime bridge count,
while ON-mode is locked at strict zero.

Fourth split moved `pcc.unsafe` intrinsic lowering into
`pcc.py_frontend.codegen.unsafe_lowering`. The unsafe intrinsic name set is
owned by the unsafe module. Import lowering reaches it through the
`_is_unsafe_intrinsic` host method instead of importing the frozenset
constant, because frozenset constants are not native cross-module exports.
The module is contextual. OFF-mode keeps a hard ratchet baseline for the
legacy builder/runtime bridge count, while ON-mode is locked at strict
zero.

Fifth split moved `pcc.extern` scaffold lowering into
`pcc.py_frontend.codegen.extern_lowering`. This covers extern declaration
registration, extern call emission, and scalar ABI coercion. The module is
contextual. OFF-mode keeps a hard ratchet baseline for the legacy
builder/runtime bridge count, while ON-mode is locked at strict zero.

Sixth cleanup consolidated import policy helpers into
`pcc.py_frontend.codegen.import_lowering`. This moved scaffold module
classification, compile-time-only import filtering, annotation-only import
checks, test facade import checks, runtime-use scanning, and CPython module
value emission out of `layer1.py`. `L1CodeGen` keeps class-local copies of
the import policy constants because stage-compiled pcc1 does not yet
reliably resolve class attributes through mixin bases from host
orchestration code.

Seventh cleanup moved native module object/export helper lowering into
`pcc.py_frontend.codegen.native_modules`. This covers
`importlib.import_module` literal recognition, native module object export
lookup, native module export value emission, `hasattr` / `getattr` on
native module objects, `type(module).__name__`, and native `inspect`
helpers. OFF-mode `native_modules` contextual fallback increased only as a
legacy bridge ratchet; ON-mode remains strict zero.

Eighth split moved typing/protocol helper lowering into
`pcc.py_frontend.codegen.typing_lowering`. This covers native
`typing.TypeVar` name extraction, `typing.Optional` alias name handling,
typing alias `__name__`, and protocol `isinstance` static checks. The
module is contextual. OFF-mode keeps a hard ratchet baseline for the
legacy builder/runtime bridge count, while ON-mode is locked at strict
zero.

Ninth split moved enum/dynamic type helper lowering into
`pcc.py_frontend.codegen.dynamic_type_lowering`. This covers enum member
`name` / `value` attribute lowering and dynamic `type(name, bases, ns)`
constructor lowering. The module is contextual. OFF-mode keeps a hard
ratchet baseline for the legacy builder/runtime bridge count, while
ON-mode is locked at strict zero.

Tenth split moved native lambda callback object lowering into
`pcc.py_frontend.codegen.lambda_callback_lowering`. This covers generation
of a zero-capture adapter function for simple lambdas that can be wrapped
as pcc function objects. The module is contextual. OFF-mode keeps a hard
ratchet baseline for the legacy builder/runtime bridge count, while
ON-mode is locked at strict zero.

Eleventh split moved async context-manager lowering into
`pcc.py_frontend.codegen.async_with_lowering`. This covers native
`async with` lowering through `__aenter__`, `__aexit__`, and `py_await`.
The module is contextual. OFF-mode keeps a hard ratchet baseline for the
legacy builder/runtime bridge count, while ON-mode is locked at strict
zero.

Twelfth split moved exception/error-exit helper lowering into
`pcc.py_frontend.codegen.exception_lowering`. This covers `raise` lowering,
`try`/`except` dispatch, builtin exception construction, AttributeError
branches, except-class references, traceback frame attachment, post-call
error checks, and function error-exit epilogues. The module is contextual
and locked at strict zero in ON-mode. OFF-mode keeps a hard ratchet
baseline for the legacy builder/runtime bridge count.

Thirteenth split moved small core helper utilities into
`pcc.py_frontend.codegen.core_helpers`. This covers terminated-block
detection, zero-value construction for LLVM types, and IR type equality
checks. The module is contextual. OFF-mode keeps a hard ratchet baseline
for the legacy builder/runtime bridge count, while ON-mode is locked at
strict zero.

Fourteenth split moved IR scaffold lowering into
`pcc.py_frontend.codegen.ir_scaffold_lowering`. This covers detection and
closed-world lowering for `self.builder.*`, IRBuilder helper methods,
`ir.*` constructors, and the `ScaffoldUnsupportedError` diagnostics used
by `--ir-scaffold=on`. The module is contextual. ON-mode remains the
strict zero-fallback correctness gate; OFF-mode is retained as a hard
legacy bridge ratchet.

After fixing the split-local helper dependencies, these gates passed:

```bash
env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py -q -n0
env -u LC_ALL uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0
```

## Update 2026-07-10: assignment walker host-dataclasses regression

Current HEAD added literal self-method dictionary dispatch analysis to
`assignment_statement_lowering`. Its recursive AST-use walker called
`dataclasses.is_dataclass()` and `dataclasses.fields()` directly, producing 17
ON-mode contextual `py_cpy_*` calls. A focused regression reproduced `17 != 0`.

## No.4 Use the existing self-host-safe dataclass field-name protocol

### Code Change

Replace host dataclasses reflection with the convention already used by other
lowering walkers: `getattr(node, "__dataclass_fields__", None)` followed by
`fields.keys()` and direct field access.

### CONFIRMED

The focused contextual count is zero after the change. Literal self-method
dispatch runtime/IR gates pass (`3 passed`). The full fallback gate then moved
from six assignment-related failures to one distinct legacy scaffold-off
marshal raw-count failure, tracked separately in
`fallback-baseline-marshal-raw-ratchet-2026-07-10.md`.
