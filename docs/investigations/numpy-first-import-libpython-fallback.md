# Investigation: Real NumPy first import still emits libpython fallback

## Status

active

## Problem Description

The active `B-P0-PKG` first-import tracer for the repository-local NumPy 2.4.4 package no longer matches the stale status/test expectation. Current worktree evidence shows that the pcc1 no-host compile of a script containing `import numpy` reaches multi-file codegen, then fails the strict no-libpython boundary with `requires libpython fallback for multi-file compile`. Debug tracing shows remaining `py_cpy_*` helper emission across the NumPy import closure.

The previously recorded blockers, `numpy.lib._function_base_impl` `NoneType` marshal and `codegen[numpy.f2py.symbolic]` `Layer 1 cannot coerce ClassType to int`, are superseded by the current observed boundary until they are reproduced again in the current worktree.

## Repro

Run the opt-in boundary gate:

```bash
PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 PCC_NUMPY_ARTIFACT=$PWD/projects/numpy-2.4.4 \
  env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in -q -n0 --maxfail=1
```

The gate installs the local NumPy source fixture into a temporary site directory, then asks the fresh pcc1 artifact to compile a program containing `import numpy` with:

```bash
PCC_HOST_PYTHON=/usr/bin/false
PCC_PYTHON_IR_PASSES=off
PCC_PACKAGE_SITE=<temporary site>
build/bootstrap-pytest-self/pcc1 --backend self --python-libpython=off --ir-scaffold=on <main.py> -o <exe>
```

Expected current compile failure marker:

```text
PCC-PY-COMPILE-001
multi-file compile
imports still lower through CPython fallback
generated IR still calls py_cpy_* helpers
numpy.f2py.symbolic
numpy.f2py.func2subr
```

The stable public marker used by the opt-in pytest gate is:

```text
PCC-PY-COMPILE-001
requires libpython fallback for multi-file compile
numpy.f2py.symbolic
numpy.f2py.func2subr
```

## Test [CONFIRMED]

Before this investigation file was opened, the opt-in gate failed because it still asserted the old `Layer 1 cannot marshal NoneType to CPython yet` marker. The captured compile output instead showed the current no-libpython fallback-scan marker above.

A direct rerun with fresh `build/bootstrap-pytest-self/pcc1` and the installed temporary package site also exited 1 with `PCC-PY-COMPILE-001` and reported the multi-file NumPy closure ending in `numpy.f2py.symbolic` and `numpy.f2py.func2subr`.

## Proposals

- No.1 Resync the boundary gate and active status docs [pending]
- No.2 Shrink the first remaining `py_cpy_*` fallback source in the NumPy import closure [in progress]
- No.3 Keep parallel closed-world export workers semantically equivalent to the serial path [confirmed]

## No.1 Resync the boundary gate and active status docs

### Code Change

Update `tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in` so a non-zero compile must match the current strict no-libpython public fallback boundary rather than the superseded `NoneType` marshal blocker. Update `docs/current-goal-state.md` and `codex-goal-prompt.md` so the active blocker points at the current fallback surface.

### CONFIRMED

The resynced opt-in boundary gate now passes while preserving the failure as the active blocker:

```bash
PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 PCC_NUMPY_ARTIFACT=$PWD/projects/numpy-2.4.4 \
  env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in -q -n0 --maxfail=1
```

Observed result: `1 passed in 63.42s`.

This confirms only that the active boundary is recorded correctly. It does not prove successful `import numpy` or no-libpython NumPy execution.

## No.2 Shrink the first remaining `py_cpy_*` fallback source in the NumPy import closure

### Code Change

The first short debug trace was insufficient because it only showed two IR
lines before `py_cpy_ensure_init()`. A full IR dump with the libpython gate
bypassed for diagnosis showed the actual first helper edge in:

```text
define external ptr @user_numpy___getattr__(ptr %.1)
call void () @py_cpy_ensure_init()
call ptr (ptr) @py_cpy_import(...)
```

The imported module was `warnings`, from `numpy.__getattr__` in
`numpy/__init__.py`. The nearby `%cpy.kwv.warn.*` allocas were not the root
cause; they were entry-block allocas for later `warnings.warn(...)` calls in
the same function.

`import warnings` now registers as a native builtin module alias, and focused
`warnings.warn`, `warnings.filterwarnings`, and `warnings.simplefilter` calls
lower to the same no-op/`None` behavior as the existing narrow
`pcc/py_stdlib/warnings.py` shim. This removes the first generic source-level
fallback without adding NumPy-specific branches.

Changed files:

```text
pcc/py_frontend/codegen/import_lowering.py
pcc/py_frontend/codegen/method_call_expression_lowering.py
tests/python/test_recursive_stdlib_import_codegen.py
```

### CONFIRMED

Focused host-source IR regression:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 120 \
  uv run pytest tests/python/test_recursive_stdlib_import_codegen.py::test_default_off_mode_auto_routes_warnings_native -q -n0
```

Observed result: `1 passed in 0.74s`.

Related stdlib import routing file gate:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  uv run pytest tests/python/test_recursive_stdlib_import_codegen.py -q -n0
```

Observed result: `7 passed in 1.74s`.

This confirms the source tree no longer emits `py_cpy_import("warnings")` for
the reduced import/call shape and does not regress the nearby stdlib import
routing tests. It does not prove that the existing
`build/bootstrap-pytest-self/pcc1` artifact contains the change, and it does
not yet prove that the real NumPy first-import boundary moved. A fresh pcc1 /
bootstrap validation is still required before claiming pcc1 or NumPy-closure
progress beyond this focused ratchet.

### Diagnostic guardrail

Do not use the two-line `PCC_DEBUG_BOOTSTRAP_TRACE` context as root-cause
evidence for future fallback shrinkage. It is only a locator. Before changing
code, confirm the enclosing `define`, the actual `py_cpy_*` call, and the
helper argument source from full IR or an equivalent minimized reproducer.

### CONFIRMED 2026-05-27 textwrap literal-dedent shrinkage

Added literal-only native `textwrap.dedent(...)` constant folding for both
`import textwrap` module-attribute calls and `from textwrap import dedent`
aliases. The lowering only accepts one string literal argument and no kwargs;
dynamic strings still fall back to CPython instead of claiming full `textwrap`
compatibility.

Evidence:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  uv run pytest tests/python/test_native_textwrap_dedent.py -q -n0 --maxfail=1
```

Observed result: `5 passed in 1.02s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0 --maxfail=1
```

Observed result: `1 passed in 47.42s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  env PCC_CURRENT_PCC1=/Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc1 \
      PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 \
      PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4 \
      uv run pytest tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in -q -n0 --maxfail=1
```

Observed result: `1 passed in 16.07s`.

Strict-closure diagnostic `/tmp/pcc_numpy_textwrap_diag.dXXS4T/ir` dumped 149
IR modules before the known self-backend generator emission failure and
measured `@.cpy.attr.dedent` = 7, down from 48 in
`/tmp/pcc_numpy_native_alias_diag3.jIWYWa/ir`. The same diagnostic measured
`@.cpy.mod.textwrap` = 9, `@.cpy.attr.split` = 45, and kept
`codecs`/`fileinput` module loads at zero. This remains fallback-surface
shrinkage only; it does not prove successful NumPy import or ecosystem
readiness.

## No.3 Keep parallel closed-world export workers semantically equivalent to the serial path

### Code Change

After the frontend-worker performance work, the fresh pcc1 real NumPy gate no
longer reached the fallback scan. It first failed in the parallel export worker
with:

```text
pcc frontend worker failed: no expr lifter for tuple
```

The failing module was `numpy._typing._dtype_like`, specifically the generic
Python class-header shape:

```python
class _DTypeDict(_DTypeDictBase, total=False):
    ...
```

The full lifter already treats parser class-header kwargs as
`("__pcc_kwarg__", name, value, line)`. The shallow closed-world export lifter
used by parallel export workers incorrectly sent that tuple to `lift_expr()`.
The shallow lifter now preserves class keywords just like the full lifter.

The next fresh pcc1 run then exposed a second parallel-worker-only regression:
cross-worker export JSON marked non-literal defaults as if they were missing.
NumPy signatures such as `dtype=int`, `axis=-1`, and
`keepdims=np._NoValue` therefore became required parameters after
serialization, causing codegen errors such as:

```text
missing required argument 'dtype' ... while resolving call to 'indices'
missing required argument 'axis' ... while resolving call to 'diff'
missing required argument 'keepdims' ... while resolving call to 'any'
```

The export wire format now preserves `Name`, `Attr`, and `UnaryOp` default
expressions so `has_default=True` survives the parallel export/codegen
boundary.

Changed files:

```text
pcc/py_frontend/pipeline.py
tests/python/test_py_frontend_ir_pass_pipeline.py
```

### CONFIRMED

Focused host-source regressions:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 120 \
  env -u LC_ALL uv run pytest \
  tests/python/test_py_frontend_ir_pass_pipeline.py::test_closed_world_shallow_lift_preserves_class_keywords \
  tests/python/test_py_frontend_ir_pass_pipeline.py::test_native_export_wire_preserves_expression_defaults \
  -q -n0 --maxfail=1
```

Observed result: `2 passed in 0.38s`.

Fresh full bootstrap after the shared pipeline change:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 360 \
  zsh -lc 'PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc_numpy_default_wire_profile \
  bash scripts/bootstrap.sh --backend self --out-dir /tmp/pcc_numpy_default_wire_boot/out --stage 3'
```

Observed result: stage1 `13139ms`, stage2 `20948ms`, stage3 `21139ms`;
pcc2/pcc3 signature-normalized byte-identical.

Fresh pcc1 real NumPy boundary:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  env PCC_CURRENT_PCC1=/tmp/pcc_numpy_default_wire_boot/out/pcc1 \
      PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 \
      PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4 \
      env -u LC_ALL uv run pytest \
      tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in \
      -q -n0 --maxfail=1
```

Observed result: `1 passed in 26.63s`.

A direct compile with the same fresh pcc1 confirms the active public boundary
is again strict no-libpython fallback scanning:

```text
requires libpython fallback for multi-file compile
```

with `numpy.f2py.symbolic`, `numpy.f2py.func2subr`, and `numpy.f2py.cfuncs`
present in the module list. This does not prove successful `import numpy`.

### Next fallback shape

An auto-libpython diagnostic compile with full IR dump after No.3 wrote 149 IR
inputs under `/tmp/pcc_numpy_next_fallback/ir`. The first inspected concrete
fallback sources are no longer `warnings` or cross-worker defaults:

```text
numpy.distutils.fcompiler.environment.EnvironmentConfig.clone
```

lowers:

```python
self.__class__(distutils_section=self._distutils_section, **self._conf_keys)
```

through `py_cpy_getattr("__class__")` plus `py_cpy_call_kwdict_plus`.

Another visible source is:

```text
numpy.distutils.log.Log._log / Log.good
```

where `sys.stdout.flush()` and `from distutils.log import *` names still lower
through CPython fallback. The next shrink should choose one generic mechanism
from this set, preferably with a minimized fixture before touching shared
call/attribute lowering.

## No.4 Native missing same-package optional import

### Hypothesis

The NumPy import closure contains package-local optional imports shaped like:

```python
try:
    from . import _distributor_init_local
except ImportError:
    pass
```

When the package itself is compiled natively and the optional sibling module is
absent, the no-libpython path should emit a native, catchable `ImportError`
instead of routing through `py_cpy_import` / `py_cpy_getattr`.

### Change

`pcc/py_frontend/codegen/import_lowering.py` now handles only the narrow
generic shape `from . import name` where:

- the current package is present in the native export table
- `name` is not a compiled native submodule
- `name` is not an exported native attribute of the package

It emits a native `ImportError` for that missing optional import. The fast path
is deliberately not applied to concrete sibling-module imports such as
`from .py_ast import BoolType`, because those must continue to bind native
exports. An initial overly broad version broke fresh pcc1 startup with
`cannot import name 'BoolType' from 'pcc.py_frontend.py_ast'`; the final guard
keeps the missing-optional case separate from native sibling-module exports.

### Evidence

Focused regression:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  env -u LC_ALL uv run pytest \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_missing_native_relative_import_raises_importerror_without_libpython \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_native_relative_import_from_concrete_module_still_binds_export \
  -q -n0 --maxfail=1
```

Observed result: `2 passed in 1.90s`.

Fresh off-mode bootstrap after this import-lowering change and the IR hot-path
optimization:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 360 \
  zsh -lc 'PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc_ir_hotpath_boot_profile.xtxQJd \
  PCC_PYTHON_IR_PASSES=off bash scripts/bootstrap.sh --backend self \
  --out-dir /tmp/pcc_ir_hotpath_boot_out.OgGfMW --stage 3'
```

Observed result: stage1 `7625ms`, stage2 `15779ms`, stage3 `14598ms`;
pcc2/pcc3 signature-normalized byte-identical.

Fresh pcc1 real NumPy boundary:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  zsh -lc 'PCC_CURRENT_PCC1=/tmp/pcc_ir_hotpath_boot_out.OgGfMW/pcc1 \
  PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 \
  PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4 \
  env -u LC_ALL uv run pytest \
  tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in \
  -q -n0 --maxfail=1'
```

Observed result: `1 passed in 24.90s`.

### Remaining boundary

This shrinks one generic optional-import fallback shape. It still does not prove
successful `import numpy`: the public boundary remains strict no-libpython
fallback scanning, and the observed fallback module list is broader than the
three representative f2py modules asserted by the opt-in gate.

## No.5 Typing markers and current performance boundary

### Hypothesis

NumPy typing helper modules contain runtime-dead typing-only branches and
metadata-only `TypeVar` declarations. These should not require CPython fallback
when compiling under strict no-libpython:

```python
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterator

_T_co = TypeVar("_T_co", covariant=True)
```

`typing.TYPE_CHECKING` is a compile-time false marker for this frontend, and
`TypeVar(..., covariant=True)` is metadata-only for the currently supported
typing-lowering surface.

### Change

The Python frontend now:

- registers `typing.TYPE_CHECKING` aliases from `import typing` and
  `from typing import TYPE_CHECKING`
- folds `if TYPE_CHECKING:` and `if typing.TYPE_CHECKING:` at codegen time
- returns native `False` for `typing.TYPE_CHECKING`
- accepts keyword metadata on lowered `TypeVar(...)` calls when the first
  positional argument is the type variable name

This is a generic typing marker change, not a NumPy-specific branch.

### Evidence

Focused typing regression batch:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  zsh -lc 'env -u LC_ALL uv run pytest \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_typing_type_checking_branches_are_compile_time_false \
  tests/python/test_py_stdlib_typing_types.py \
  tests/python/test_py_class_decorators_typing.py \
  -q -n0 --maxfail=1'
```

Observed result: `7 passed in 1.21s`.

Current-source NumPy diagnostic confirmed the specific module-level effect:

- before the typing marker change, `numpy._typing._nested_sequence` had 10
  actual `py_cpy_*` call sites
- after `TYPE_CHECKING` folding, it dropped to 6 call sites
- after `TypeVar(..., covariant=True)` lowering, it dropped to 0 call sites

Final off-mode bootstrap for this source state:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 360 \
  zsh -lc 'PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc_final_off_profile.LF6nW4 \
  PCC_PYTHON_IR_PASSES=off bash scripts/bootstrap.sh --backend self \
  --out-dir /tmp/pcc_final_off_out.qnM0E3 --stage 3'
```

Observed result: stage1 `6388ms`, stage2 `14491ms`, stage3 `14219ms`;
pcc2/pcc3 signature-normalized byte-identical.

Final pass-on bootstrap:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 360 \
  zsh -lc 'PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc_final_pass_on_profile.q9m1Cs \
  PCC_PYTHON_IR_PASSES=on bash scripts/bootstrap.sh --backend self \
  --out-dir /tmp/pcc_final_pass_on_out.dWkvYI --stage 3'
```

Observed result: stage1 `8381ms`, stage2 `15146ms`, stage3 `14154ms`;
pcc2/pcc3 signature-normalized byte-identical.

Final pcc1 real NumPy boundary:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  zsh -lc 'PCC_CURRENT_PCC1=/tmp/pcc_final_off_out.qnM0E3/pcc1 \
  PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 \
  PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4 \
  env -u LC_ALL uv run pytest \
  tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in \
  -q -n0 --maxfail=1'
```

Observed result: `1 passed in 24.58s`.

LLVM CAPI focused gate after the IR call-text change:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 240 \
  zsh -lc 'env -u LC_ALL uv run pytest \
  tests/c/test_llvm_capi_ir_parity.py \
  tests/c/test_llvm_capi_end_to_end.py -q -n0 --maxfail=1'
```

Observed result: `24 passed in 0.16s`.

### Performance finding

The current success-path bottleneck is not strict no-libpython early failure and
not file count alone. With the persistent self-backend object cache hot, final
off-mode stage2/stage3 profiles show:

- `multi_frontend_codegen_worker_commands`: `9114ms` / `8744ms`
- `multi_frontend_export_parallel`: `2379ms` / `2506ms`
- `link_self_emit_objects_host`: `1227ms` / `1225ms`

This means the remaining large cost is frontend/codegen work in the compiled
workers. A tested self-backend unused-declaration pruning experiment reduced
theoretical IR input from `67.8MB` to `55.9MB` by deleting about `128k`
unreferenced `declare` lines, but it made stage1 jump to `28295ms`, so the
experiment was removed. Future performance work should target large-module
frontend lowering/IR emission or a true persistent worker design, not another
Python-level whole-IR scan.

### Remaining boundary

This removes a generic typing-marker fallback source and preserves the real
NumPy first-import boundary gate. It still does not prove successful
`import numpy`: the public boundary remains strict no-libpython fallback
scanning, and the observed fallback module list is broader than the three
representative f2py modules asserted by the opt-in gate.

## 2026-05-27 performance follow-up: rejected bootstrap closure trim

The next performance diagnosis tested whether the remaining pass-on cost was
primarily worker count, object emission, IR passes, or a large-module frontend
hotspot.

Findings:

- `PCC_PY_FRONTEND_JOBS=20` was worse than auto-10: stage1/stage2 pass-on moved
  from about `5.4s`/`12.0s` to `6.1s`/`14.8s` profile totals. More frontend
  workers are not the main lever.
- The old max stage1 worker module was `pcc.cli_bootstrap`; a trial change made
  `pcc/__main__.py` import a new `pcc.cli_bootstrap_min`, removing the legacy
  11k-line bootstrap CLI from the self-host closure.
- Focused strict entry gate after the change:
  `tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_compiled_repo_main_auto_closes_same_package_absolute_imports`
  -> `1 passed in 14.04s`.
- Current off-mode bootstrap:
  `/tmp/pcc_mincli_off_profile.FGDfcV`, `/tmp/pcc_mincli_off_out.bpqBUR` ->
  stage1 `6139ms`, stage2 `13523ms`, stage3 `13311ms`; pcc2/pcc3
  signature-normalized byte-identical.
- Current pass-on bootstrap:
  `/tmp/pcc_mincli_pass_on_profile.WkacDG`, `/tmp/pcc_mincli_pass_on_out.qs3njt`
  -> stage1 `5719ms`, stage2 `13115ms`, stage3 `13495ms`; pcc2/pcc3
  signature-normalized byte-identical.
- Profile-level pass-on total moved from about `29.4s` to `28.3s`; stage2/3 IR
  size moved from about `70.2MB` to `62.1MB`.

Rejection reason: the trial entry switch broke the active no-host package gate.
Fresh pcc1 from `/tmp/pcc_mincli_off_out.bpqBUR/pcc1` failed
`test_pcc1_real_numpy_first_import_boundary_opt_in` immediately because
`pcc1 -m pip install ...` delegated to `PCC_HOST_PYTHON=/usr/bin/false` instead
of using the native package shim. The entry switch and new file were removed.

Restored-entry validation:

- focused entry IR gate:
  `tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_compiled_repo_main_auto_closes_same_package_absolute_imports`
  -> `1 passed in 15.76s`
- current pass-on bootstrap:
  `/tmp/pcc_revert_pass_on_profile.BHR9br`, `/tmp/pcc_revert_pass_on_out.ZZoHFa`
  -> stage1 `7148ms`, stage2 `13961ms`, stage3 `13521ms`; pcc2/pcc3
  signature-normalized byte-identical
- current off-mode bootstrap:
  `/tmp/pcc_revert_off_profile.HjN0Lq`, `/tmp/pcc_revert_off_out.P4uJtR`
  -> stage1 `5913ms`, stage2 `12763ms`, stage3 `13017ms`; pcc2/pcc3
  signature-normalized byte-identical
- restored package boundary:
  fresh pcc1 `/tmp/pcc_revert_off_out.P4uJtR/pcc1` with
  `PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1` and
  `PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4` ->
  `1 passed in 23.65s`

Conclusion: the closure trim proves that `pcc.cli_bootstrap` is a real large
single-module cost, but a usable version must be package-aware. The true
remaining success-path bottleneck is compiled pcc frontend/codegen work on
large single modules (`pcc.cli_bootstrap`, `pcc.py_frontend.pipeline`, and
`pcc.py_frontend.codegen.hoist_lowering`). Further performance work should
target large-module lowering/IR emission, module splitting, or a true
persistent worker/package-aware minimal entry design rather than more frontend
workers or another small cache.

## 2026-05-27 optional external import fold and rejected worker experiments

The next generic fallback shrink added a strict no-libpython fold for missing
external optional imports assigned to `None` in an `ImportError` handler. This
lets the compiler erase a dead `alias is not None` branch without emitting
`py_cpy_import` / `py_cpy_getattr` for that optional dependency.

Focused validation:

- `tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_missing_external_optional_import_folds_to_none_without_libpython`
  -> `1 passed in 0.68s`
- Related import batch -> `2 passed in 1.88s`

The performance diagnosis rechecked the success path before keeping any larger
optimization:

- stage2/stage3 are still dominated by compiled frontend workers:
  `multi_frontend_codegen_worker_commands` plus
  `multi_frontend_export_parallel`;
- `PCC_PY_FRONTEND_JOBS=12` and `=8` were both worse than auto-10;
- a persistent export+codegen worker experiment removed one parse boundary but
  did not improve compiled pcc1 wall time, so it was removed;
- exact-type statement/expression dispatch improved CPython cProfile but
  worsened compiled pcc1, so it was removed.

Fresh validation for the retained source state:

- off-mode bootstrap: `/tmp/pcc_bootstrap_off_profile.o48sRY`,
  `/tmp/pcc_bootstrap_off_out.IsnxYF` -> stage wall
  `8.5s / 15.4s / 13.7s`, pcc2/pcc3 signature-normalized byte-identical;
- `PCC_PYTHON_IR_PASSES=on` bootstrap:
  `/tmp/pcc_bootstrap_pass_on_profile.qkXstG`,
  `/tmp/pcc_bootstrap_pass_on_out.9M9J73` -> stage wall
  `8.3s / 16.4s / 14.6s`, pcc2/pcc3 signature-normalized byte-identical;
- real NumPy boundary with
  `PCC_CURRENT_PCC1=/tmp/pcc_bootstrap_off_out.IsnxYF/pcc1` and
  `PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4`
  -> `1 passed in 24.50s`.

This preserves the public boundary: strict no-libpython still reaches the NumPy
import closure and records remaining fallback, not successful native
`import numpy`.
## 2026-05-27 — generic `os.path.getsize` lowering and runtime-high guard

The real NumPy import closure hits `os.path.getsize(filename)` in the generic
f2py `crackfortran.openhook` path. Added native lowering for
`os.path.getsize(path)` without a NumPy-specific branch. The C runtime helper
uses `stat().st_size`; the pcc-Python runtime mirror intentionally avoids a
new `pcc.unsafe` stat-size intrinsic and computes the size through existing
file/read/string-length helpers so pcc1 does not gain another compiler
intrinsic surface.

During validation, the full stage gate failed at the stage1 publish barrier:
newly generated pcc1 could print `--help` but failed compiling the smoke input
with `AttributeError: __truediv__`. Isolating `PCC_RUNTIME_HIGH=c` vs
`PCC_RUNTIME_HIGH=py` showed the failure belonged to the pcc-Python runtime
mirror, not to the target program. The root was `py_time_monotonic()` using
true division in `pcc/py_runtime/py/py_os_substrate.py`; pcc1 executes the
compiler timing path while compiling any input. The mirror now uses
multiplication by `0.000001`, matching the C helper's seconds conversion
without requiring true-div support on the pcc-Python runtime path.

Evidence:

- `tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_os_path_getsize_lowers_without_libpython -q -n0` -> 1 passed in 2.91s.
- `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0` -> 1 passed in 33.88s.
- `PCC_CURRENT_PCC1=$PWD/build/bootstrap-pytest-self/pcc1 PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 PCC_NUMPY_ARTIFACT=$PWD/projects/numpy-2.4.4 tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in -q -n0` -> 1 passed in 28.00s.

This shrinks one generic package/import fallback source and preserves the
pcc1/pcc2/pcc3 bootstrap gate. It does not prove full NumPy import success,
native extension execution, or ecosystem readiness.

## 2026-05-27 — generic pathlib suffix lowering

`numpy.f2py.crackfortran` still used CPython fallback for the generic package
shape `Path(fname).suffix.lower()` / `Path(currentfilename).suffix.lower()`.
The fix registers `from pathlib import Path, PurePath` as native builtin value
aliases and lowers direct `.suffix` attribute reads through the existing native
`py_os_path_splitext` helper plus `py_tuple_get(..., 1)`. This deliberately
does not model full pathlib objects; it only removes the generic suffix access
used by package import code.

Evidence:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  uv run pytest \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_os_path_getsize_lowers_without_libpython \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_pathlib_path_suffix_lowers_without_libpython \
  -q -n0 --maxfail=1
```

Observed result: `2 passed in 1.21s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0 --maxfail=1
```

Observed result: `1 passed in 40.74s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  env PCC_CURRENT_PCC1=/Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc1 \
      PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 \
      PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4 \
      uv run pytest tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in \
      -q -n0 --maxfail=1
```

Observed result: `1 passed in 22.36s`.

A diagnostic `--python-libpython=auto` IR dump at
`/tmp/pcc_numpy_fallback_pathlib_auto.Fi4Sfc` showed
`user_numpy_f2py_crackfortran_is_free_format` lowering the suffix check to:

```llvm
%.6 = call ptr (ptr) @py_os_path_splitext(ptr %fname.320.5)
%.7 = call ptr (ptr, i64) @py_tuple_get(ptr %.6, i64 1)
call void (ptr) @pcc_gc_release(ptr %.6)
%.9 = call ptr (ptr) @py_os_path_splitext(ptr %fname.321.8)
%.10 = call ptr (ptr, i64) @py_tuple_get(ptr %.9, i64 1)
call void (ptr) @pcc_gc_release(ptr %.9)
%.11 = call ptr (ptr) @py_str_lower(ptr %.10)
```

This is fallback-surface shrinkage only. The real NumPy boundary still reports
`requires libpython fallback for multi-file compile`, so the next slice should
attack the next generic `py_cpy_*` source rather than claiming NumPy import
success.

## 2026-05-27 — direct re flags/search fallback shrink

The next measured generic fallback source in `numpy.f2py.crackfortran` was the
stdlib `re` surface. Full `re.compile(...).search` remains a larger object
model problem, but direct `re.match/search(pattern, text[, flags])` and the
`re.I` / `re.S` constants can be handled by the existing native regex subset
without pretending compiled-pattern objects exist.

Code change:

- Register `from re import match, search` as native builtin value aliases.
- Lower `re.match` and `re.search` with two or three arguments to native
  runtime helpers.
- Lower `re.I`, `re.IGNORECASE`, `re.S`, `re.DOTALL`, `re.M`, and
  `re.MULTILINE` to native integer constants.
- Add C runtime and pcc-Python runtime mirror helpers for `py_re_match_flags`,
  `py_re_search`, and `py_re_search_flags`, sharing the small ASCII regex
  subset and adding ignore-case / dot-all handling.

Evidence:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  uv run pytest tests/python/test_native_re_match.py -q -n0 --maxfail=1
```

Observed result: `2 passed in 5.13s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0 --maxfail=1
```

Observed result: `1 passed in 44.09s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  env PCC_CURRENT_PCC1=/Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc1 \
      PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 \
      PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4 \
      uv run pytest tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in \
      -q -n0 --maxfail=1
```

Observed result: `1 passed in 22.02s`.

A diagnostic `--python-libpython=auto` IR dump at
`/tmp/pcc_numpy_fallback_re.SxdcdZ` showed `numpy.f2py.crackfortran` fallback
helpers dropping from the previous 1555 count to 1302. In that module,
`@.cpy.attr.I` and `@.cpy.attr.S` are now zero, while native `py_re_match`,
`py_re_match_flags`, `py_re_search`, and `py_re_search_flags` calls are present.

This is still fallback-surface shrinkage only. The public NumPy boundary still
reports `requires libpython fallback for multi-file compile`. The next large
`re` source is `re.compile` / compiled-pattern methods, which should not be
faked without a real native compiled-regex object boundary.

## 2026-05-27 — native re.compile bound match/search methods

The direct `re.match/search` slice left module-level bound methods such as
`re.compile(...).search` and `re.compile(...).match` on the CPython fallback
path. This is common in `numpy.f2py.crackfortran`, for example
`_has_f_header = re.compile(...).search`.

Code change:

- Lower direct `re.compile(pattern[, flags]).match` and
  `re.compile(pattern[, flags]).search` attribute reads to a native runtime
  callable.
- The runtime helper `py_re_compile_method(pattern, flags, method_kind)` returns
  a real `PY_TYPE_FUNC` object. Its captures store the pattern, flags, and
  method kind, and calls dispatch through existing `py_obj_call` /
  `py_func_call`.
- The C runtime and pcc-Python runtime mirror both export the helper. This is a
  bound-method boundary, not a fake truthy regex object.

Evidence:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  uv run pytest tests/python/test_native_re_match.py -q -n0 --maxfail=1
```

Observed result: `3 passed in 6.36s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0 --maxfail=1
```

Observed result: `1 passed in 46.67s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  env PCC_CURRENT_PCC1=/Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc1 \
      PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 \
      PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4 \
      uv run pytest tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in \
      -q -n0 --maxfail=1
```

Observed result: `1 passed in 23.78s`.

A diagnostic `--python-libpython=auto` IR dump at
`/tmp/pcc_numpy_fallback_re_compile.ORDxFJ` showed `numpy.f2py.crackfortran`
fallback helpers dropping from 1302 to 1228, with 8 native
`py_re_compile_method` calls present.

This still does not implement full `re.Pattern` or `re.Match` semantics. The
remaining `re` fallback sources include `.findall`, `.sub`, `.split`, `.group`,
and `.groupdict`, which need real runtime behavior rather than success
sentinels.

## 2026-05-27 — guarded native re.findall subset

After the `re.compile(...).match/search` bound-method slice, the remaining
`numpy.f2py.crackfortran` regex fallback included `findall` in two concrete
literal-pattern forms used by package code:

- `\b[a-z][\w$]*\b` for identifier scanning through `word_pattern.findall(...)`
- `\(.*?\)` for non-greedy parenthesized spans through direct `re.findall(...)`

The implementation lowers only those literal patterns. Unsupported `findall`
patterns keep the existing fallback path; they are not replaced with a fake
empty list.

Evidence:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  uv run pytest tests/python/test_native_re_match.py -q -n0 --maxfail=1
```

Observed result: `3 passed in 6.63s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0 --maxfail=1
```

Observed result: `1 passed in 56.44s`.

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 600 \
  env PCC_CURRENT_PCC1=/Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc1 \
      PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY=1 \
      PCC_NUMPY_ARTIFACT=/Users/jiamo/my/pcc/projects/numpy-2.4.4 \
      uv run pytest tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in \
      -q -n0 --maxfail=1
```

Observed result: `1 passed in 26.40s`.

A diagnostic `--python-libpython=auto` IR dump at
`/tmp/pcc_numpy_fallback_re_findall.iPSnMO` showed `numpy.f2py.crackfortran`
fallback helpers dropping from 1228 to 1220. `@.cpy.attr.findall` is now zero,
and 2 native `py_re_findall_flags` calls are present.

This is still fallback-surface shrinkage only. Remaining regex fallback sources
include `.sub`, `.split`, `.group`, `.groupdict`, `.start`, and `.end`, which
need real compiled-pattern / match-object semantics. Non-regex stdlib surfaces
such as `fileinput` and `codecs` are also still present.

## 2026-05-27T15:14:06+08:00 — codecs BOM / binary read boundary

Added native `codecs.BOM_UTF8`, `BOM_UTF32_LE`, `BOM_UTF32_BE`, `BOM_LE`, and `BOM_BE` constants plus binary-mode `open(...).read(...)` bytes results and bytes/tuple-aware `startswith` / `endswith`. This removes the `codecs.BOM_*` CPython attribute fallback from the f2py `openhook()` BOM detection shape without adding NumPy-specific branches.

Evidence:

- Focused gate: `tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_codecs_bom_binary_startswith_lowers_without_libpython` -> 1 passed in 0.79s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 37.40s.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 15.94s.
- Auto-mode diagnostic IR: `/tmp/pcc_numpy_bom_diag.lU5NZu/out.ll` has zero `@.cpy.attr.BOM_*` constants and native `py_bytes_new` / `py_str_startswith` calls.

Boundary note: the public no-libpython NumPy boundary now reaches the stricter `PCC-PKG-004` CPython-extension ABI rejection for the current `--abi=cpython-compat` install before pure-Python fallback scanning. Keep using `--python-libpython=auto` diagnostics, or a package-native/pure-Python package fixture, when the goal is to measure remaining pure-Python fallback shrinkage.

## 2026-05-27T15:36:58+08:00 — native fileinput scanner subset

Added a narrow native `fileinput.FileInput(files, openhook=...)` subset for
package scanner code. The supported methods are `readline`, `filename`,
`lineno`, `filelineno`, `isfirstline`, and `close`. The runtime reads text
files through the existing native file path and intentionally ignores the
`openhook` callback in this subset; that matches the current package-scanner
need but is not full `fileinput` compatibility.

During validation, the pcc-Python runtime mirror first failed in pcc-compiled
execution because module-level `FILEINPUT_*` integer indexes behaved like the
known early-bootstrap constant hazard: `readline()` treated `state[0]` as the
lines list, returned the filename, corrupted `state[0]`, then crashed on the
second read. The mirror now uses integer literals at the fileinput state use
sites.

Evidence:

- Focused gate: `tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_fileinput_fileinput_lowers_without_libpython` -> 1 passed in 1.90s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 46.59s.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 13.74s.

Boundary note: this proves the source state can still build pcc1 -> pcc2 ->
pcc3 after the runtime/frontend slice. The public no-libpython NumPy boundary
still reaches `PCC-PKG-004` CPython-extension ABI rejection; this is not a
successful `import numpy` claim.

## 2026-05-27T15:47:02+08:00 — literal-separator re.split lowering

Added native lowering for `re.split(pattern, text, maxsplit=...)` only when
`pattern` is a non-empty string literal containing no regex metacharacters and
`flags` is absent or zero. The lowering reuses the existing string split
runtime helpers. Unsupported regex patterns, capture-producing splits,
dynamic `maxsplit`, and nonzero flags keep the fallback path.

Evidence:

- Focused gate: `tests/python/test_native_re_match.py` -> 4 passed in 6.54s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 47.27s.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 15.31s.
- Strict-closure diagnostic for the pure-Python NumPy site after removing extension files only in `/tmp`: `/tmp/pcc_numpy_re_split_diag.NgZ5j9/ir` dumped 149 IR modules before the known self-backend generator emission failure; `@.cpy.attr.split` is now 47, down from 49 in `/tmp/pcc_numpy_pure_closure_diag.pHnKs3/ir`.

Boundary note: this is generic fallback-surface shrinkage, not full
`re.split` support and not a successful `import numpy` claim. The public
strict no-libpython NumPy boundary remains `PCC-PKG-004` CPython-extension ABI
rejection before pure-Python fallback scanning.

## 2026-05-27T15:56:47+08:00 — native builtin import alias consistency

The strict-closure diagnostic still showed `codecs.BOM_*` and
`fileinput.FileInput` CPython fallback inside `numpy.f2py.crackfortran`, even
though focused fixtures already had native lowering for both. Root cause: the
top-level import declaration pass and function-body pre-scan had native module
allowlists that lagged behind `import_lowering.py`, and native builtin aliases
could be hidden by module-global predeclarations. The fix keeps these
allowlists aligned for the package path and lets an explicitly registered
native builtin alias win over module-global predeclaration; assignment paths
still clear aliases for true shadowing.

Evidence:

- Focused codecs/fileinput gate: `tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_codecs_bom_binary_startswith_lowers_without_libpython tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_fileinput_fileinput_lowers_without_libpython` -> 2 passed in 1.38s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 35.25s.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 14.37s.
- Strict-closure diagnostic after deleting extension files only in `/tmp`: `/tmp/pcc_numpy_native_alias_diag3.jIWYWa/ir` dumped 149 IR modules before the known self-backend generator emission failure; `load ptr, ptr @.cpy.modref.codecs` = 0, `load ptr, ptr @.cpy.modref.fileinput` = 0, `@.cpy.attr.BOM` = 0, `@.cpy.attr.FileInput` = 0, and `@.cpy.attr.split` = 45.

Boundary note: this removes the `crackfortran` native-module alias regression
from the pure-Python diagnostic. It is not a successful `import numpy` claim;
the public strict no-libpython NumPy boundary remains `PCC-PKG-004`
CPython-extension ABI rejection before pure-Python fallback scanning.

## 2026-05-27T16:28:20+08:00 — safe re.compile literal-alias lowering

Added native lowering for module-level aliases of the form:

```python
name = re.compile(<str literal>[, static_flags])
```

The alias is only installed when a current-AST scan proves `name` is used only
as `.match`, `.search`, or `.findall`. If the compiled pattern is used as a
normal value, for example `return name`, pcc keeps the CPython fallback
boundary rather than pretending to implement a full `re.Pattern` object. The
alias table is lazily created; an initial constructor-field version broke
stage2 bootstrap in parallel codegen workers with `_native_re_compile_aliases`
missing and was replaced before accepting the slice.

Evidence:

- Focused regex gate: `tests/python/test_native_re_match.py` -> 6 passed in 1.73s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 39.66s.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 26.68s.
- Strict-closure diagnostic after deleting extension files only in `/tmp`: `/tmp/pcc_numpy_re_alias_diag.cYDlso/ir` dumped 149 IR modules before the known self-backend generator emission failure; `@.cpy.attr.compile` = 118, down from 151 in `/tmp/pcc_numpy_textwrap_diag.dXXS4T/ir`; `@.cpy.attr.dedent` = 7, `@.cpy.mod.textwrap` = 9, `@.cpy.attr.split` = 45, and `codecs`/`fileinput` module loads remain zero.

Boundary note: this is generic fallback-surface shrinkage for literal regex
patterns with safe method-only alias use. It is not full `re.Pattern`
compatibility and not a successful `import numpy` claim. The public strict
no-libpython NumPy boundary remains `PCC-PKG-004` CPython-extension ABI
rejection before pure-Python fallback scanning.

## 2026-05-27T16:37:50+08:00 — POSIX os.pathsep constant and alias lowering

Added native lowering for `os.pathsep` and `from os import pathsep` as the
POSIX literal `":"`. This targets generic package path-list construction such
as `os.pathsep.join(...)` without broadening `os.path` behavior or adding
NumPy-specific branches.

Evidence:

- Focused OS native gate: `tests/python/test_native_os_misc.py` -> 18 passed in 3.02s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 39.22s.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 14.46s.
- Strict-closure diagnostic after deleting extension files only in `/tmp`: `/tmp/pcc_numpy_pathsep_diag.nns719/ir` dumped 149 IR modules before the known self-backend generator emission failure; `@.cpy.attr.pathsep` = 0, down from 18 in the previous fallback distribution; `@.cpy.attr.join` = 78, down from 86; `@.cpy.attr.compile` remains 118; `@.cpy.attr.dedent` remains 7; `codecs`/`fileinput` module loads remain zero.

Boundary note: this is generic POSIX package-path fallback shrinkage. It is
not full `os.path` compatibility and not a successful `import numpy` claim.
The public strict no-libpython NumPy boundary remains `PCC-PKG-004`
CPython-extension ABI rejection before pure-Python fallback scanning.

## 2026-05-27T17:01:10+08:00 — function-local re.compile alias lowering

Added native lowering for function-local aliases of the form:

```python
pat = re.compile(<str literal>[, static_flags])
```

when the alias is used only as `.match`, `.search`, or supported `.findall`
inside the same function. Local aliases are scoped by the current function id;
a normal local assignment to the same name shadows any module-level regex
alias. This targets real NumPy/distutils shapes such as local
`prune_file_pat = re.compile(...); prune_file_pat.search(name)` without
pretending to implement a full `re.Pattern` value.

Evidence:

- Focused regex gate: `tests/python/test_native_re_match.py` -> 7 passed in 23.42s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 50.68s.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 17.15s.
- Strict-closure diagnostic after deleting extension files only in `/tmp`: `/tmp/pcc_numpy_re_local_alias_diag.peDcqX/ir` dumped 149 IR modules before the known self-backend generator emission failure; `@.cpy.attr.compile` = 96, down from 118; `@.cpy.attr.path` = 105; `@.cpy.attr.join` = 44; `@.cpy.attr.normcase` = 0; `@.cpy.attr.splitdrive` = 0; `codecs`/`fileinput` module loads remain zero.

Boundary note: this is generic regex fallback shrinkage for method-only local
aliases. It is not full `re.Pattern` compatibility and not a successful
`import numpy` claim. The public strict no-libpython NumPy boundary remains
`PCC-PKG-004` CPython-extension ABI rejection before pure-Python fallback
scanning.

## 2026-05-27T16:52:37+08:00 — POSIX os.path.normcase and splitdrive helpers

Added native `os.path.normcase(path)` and `os.path.splitdrive(path)` helpers
in the C runtime and pcc-Python runtime mirror. The implementation is
intentionally POSIX-scoped: `normcase` returns a path string copy and
`splitdrive` returns `("", path)`. This targets real distutils chains such as
`os.path.splitext(os.path.normcase(src_name))` and
`os.path.splitdrive(base)[1]` without claiming Windows drive support.

Evidence:

- Focused OS native gate: `tests/python/test_native_os_misc.py` -> 26 passed in 4.62s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 54.92s.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 15.36s.
- Strict-closure diagnostic after deleting extension files only in `/tmp`: `/tmp/pcc_numpy_ospath_posix_diag.5KWH2N/ir` dumped 149 IR modules before the known self-backend generator emission failure; `@.cpy.attr.path` = 105, down from 114; `@.cpy.attr.normcase` = 0; `@.cpy.attr.splitdrive` = 0; `@.cpy.attr.splitext` = 4; `@.cpy.attr.join` = 44; `@.cpy.attr.compile` remains 118.

Boundary note: this is generic POSIX path helper fallback shrinkage. It is
not full `os.path` compatibility, not Windows drive semantics, and not a
successful `import numpy` claim. The public strict no-libpython NumPy boundary
remains `PCC-PKG-004` CPython-extension ABI rejection before pure-Python
fallback scanning.

## 2026-05-27T16:45:25+08:00 — os.path dispatch accepts object-attribute path args

Widened the existing native `os.path.*` dispatch so ordinary object-attribute
path arguments, for example `box.root` or `self.build_temp`, can flow into the
native helpers instead of forcing the whole `os.path.join/dirname/...` call
through CPython. The guard still rejects `os.path` itself and native module
attributes as path values; this is a parameter-shape expansion of the existing
POSIX helper surface, not a broad `os.path` module emulation.

Evidence:

- Focused OS native gate: `tests/python/test_native_os_misc.py` -> 22 passed in 3.69s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 38.90s.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 15.97s.
- Strict-closure diagnostic after deleting extension files only in `/tmp`: `/tmp/pcc_numpy_ospath_attr_diag.lBsYK7/ir` dumped 149 IR modules before the known self-backend generator emission failure; `@.cpy.attr.path` = 114, down from 162; `@.cpy.attr.join` = 44, down from 78; `@.cpy.attr.dirname` = 8, down from 23; `@.cpy.attr.isdir` = 2, down from 4; `@.cpy.attr.exists` = 2, down from 4; `@.cpy.attr.isfile` = 2; `@.cpy.attr.pathsep` remains 0; `@.cpy.attr.compile` remains 118.

Boundary note: this is generic path helper fallback shrinkage. It is not full
`os.path` compatibility and not a successful `import numpy` claim. The public
strict no-libpython NumPy boundary remains `PCC-PKG-004` CPython-extension ABI
rejection before pure-Python fallback scanning.

## 2026-05-27T17:17:29+08:00 — POSIX os.path.normpath and sep lowering

Added native POSIX `os.path.normpath(path)` support in both the C runtime and
pcc-Python runtime mirror, plus native `os.path.sep` constant lowering. The
`normpath` helper is intentionally POSIX-scoped: it collapses empty, `.`,
duplicate slash, and resolvable `..` components, preserves POSIX
 double-leading-slash behavior, and does not claim Windows drive semantics.

Evidence:

- Focused OS native gate: `tests/python/test_native_os_misc.py` -> 30 passed in 4.77s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 66.44s, proving pcc1 -> pcc2 -> pcc3 for this source state.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 18.69s.
- The public no-libpython NumPy boundary now stops at `PCC-PKG-004` before generating self-backend IR for the cpython-compat install, so the fallback-count evidence uses a count-only `--python-libpython=auto` diagnostic: `/tmp/pcc_numpy_ospath_normpath_diag.WTg7F3/ir` dumped 148 IR modules before self-backend native emission failure; `@.cpy.attr.path` = 95, down from 105; `@.cpy.attr.normpath` = 2, down from 8; `@.cpy.attr.sep` = 0, down from 6; `@.cpy.attr.join` = 44; `@.cpy.attr.compile` = 96; `codecs`/`fileinput` module loads remain zero.

Boundary note: this is generic POSIX path fallback-surface shrinkage. It is
not full `os.path` compatibility, not Windows path semantics, and not a
successful `import numpy` claim. The public strict no-libpython NumPy boundary
remains `PCC-PKG-004` CPython-extension ABI rejection before pure-Python
fallback scanning.

## 2026-05-27T17:30:44+08:00 — POSIX os.path.isabs and function-call path args

Added native POSIX `os.path.isabs(path)` support in both runtime variants and
widened native `os.path.*` path-argument acceptance to ordinary function-call
results. This matches the existing allowance for local names and object
attributes: the runtime helper still coerces the value to a path string, but
`os.path` dispatch no longer forces CPython fallback for shapes such as
`os.path.abspath(njoin(...))` or `os.path.basename(make_path(...))`.

Evidence:

- Focused OS native gate: `tests/python/test_native_os_misc.py` -> 34 passed in 5.39s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 40.03s, proving pcc1 -> pcc2 -> pcc3 for this source state.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 15.99s.
- Count-only `--python-libpython=auto` diagnostic: `/tmp/pcc_numpy_ospath_callarg_diag.eqAWcy/ir` dumped 148 IR modules before self-backend native emission failure; `@.cpy.attr.path` = 69, down from 95; `@.cpy.attr.isabs` = 0, down from 13; `@.cpy.attr.join` = 38, down from 44; `@.cpy.attr.dirname` = 3, down from 8; `@.cpy.attr.abspath` = 6, down from 8; `@.cpy.attr.basename` = 15, down from 17; `@.cpy.attr.compile` remains 96; `codecs`/`fileinput` module loads remain zero.

Boundary note: this is generic POSIX path fallback-surface shrinkage. It is
not full `os.path` compatibility and not a successful `import numpy` claim.
The public strict no-libpython NumPy boundary remains `PCC-PKG-004`
CPython-extension ABI rejection before pure-Python fallback scanning.

## 2026-05-27T17:37:59+08:00 — conservative class-level re.compile pattern strings

Added conservative class-level `re.compile(<str literal>)` handling for compiled
patterns with flags=0 that are used only as the pattern argument to
`re.split(...)` or `re.findall(...)`. These class attributes are stored as the
literal pattern string, so CPython fallback `re.split/re.findall` still owns the
full regex semantics while pcc avoids emitting a separate `re.compile` fallback.

This deliberately does not fake a full compiled-pattern object. `.match`,
`.search`, or `.findall` method use keeps the existing compile fallback, and
nonzero compile flags are not rewritten because passing a string pattern to
CPython `re.*` would lose the compiled flags.

Evidence:

- Focused regex gate: `tests/python/test_native_re_match.py` -> 9 passed in 7.00s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 42.75s, proving pcc1 -> pcc2 -> pcc3 for this source state.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 13.77s.
- Count-only `--python-libpython=auto` diagnostic: `/tmp/pcc_numpy_re_class_attr_diag.67tANj/ir` dumped 148 IR modules before self-backend native emission failure; `@.cpy.attr.compile` = 90, down from 96; `@.cpy.attr.path` = 69; `@.cpy.attr.split` = 29; `@.cpy.attr.findall` = 9; `codecs`/`fileinput` module loads remain zero.

Boundary note: this is fallback-surface shrinkage only. It is not full
`re.Pattern` compatibility and not a successful `import numpy` claim. The
public strict no-libpython NumPy boundary remains `PCC-PKG-004`
CPython-extension ABI rejection before pure-Python fallback scanning.

## 2026-05-27T17:51:32+08:00 — POSIX os.path.split helper

Added native POSIX `os.path.split(path)` dispatch and runtime support in both
the C runtime and pcc-Python runtime mirror. The helper follows the POSIX
`posixpath.split` shape for string paths: it returns `(head, tail)`, strips
trailing slashes from `head` only when the head is not all slashes, and leaves
Windows drive semantics out of scope.

This targets real NumPy distutils fallback shapes such as
`os.path.split(pythonexe)` and `os.path.split(f)`. It does not change the
remaining `re.split`, `shlex.split`, or unbound `str.split` fallback surfaces.

Evidence:

- Focused OS native gate: `tests/python/test_native_os_misc.py` -> 36 passed in 5.52s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 54.57s, proving pcc1 -> pcc2 -> pcc3 for this source state.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 15.16s.
- Count-only `--python-libpython=auto` diagnostic: `/tmp/pcc_numpy_ospath_split_diag.z0Qu0M/ir` dumped 149 IR modules before the known self-backend native emission failure. Using the same exact-pattern count as the previous dump: `@.cpy.attr.split,` = 10, down from 13; `@.cpy.attr.path,` = 52, down from 55; `@.cpy.attr.compile,` = 71; `@.cpy.attr.findall,` = 5.

Boundary note: this is POSIX path fallback-surface shrinkage only. It is not
full `os.path` compatibility, not Windows path support, and not a successful
`import numpy` claim. The public strict no-libpython NumPy boundary remains
`PCC-PKG-004` CPython-extension ABI rejection before pure-Python fallback
scanning.

## 2026-05-27T18:04:48+08:00 — sys.prefix/base_prefix and subscript path args

Added native `sys.prefix` and `sys.base_prefix` string attrs plus import-from
aliases for `from sys import prefix, base_prefix`. The runtime helper queries
the host `python3` through a subprocess boundary, matching the existing
`sysconfig.get_config_var(...)` substrate style while avoiding in-process
libpython calls.

Also widened native `os.path.*` path-argument acceptance to non-slice
subscript values. This targets real NumPy fallback shapes such as
`os.path.basename(sys.argv[0])`, `os.path.basename(compiler[0])`, and
`os.path.join(options["buildpath"], vrd["src"])`. Slice values still stay out
of this path-argument shortcut.

`sys.real_prefix` remains intentionally outside the native attr set: in CPython
it is a virtualenv-specific optional attribute, so lowering it unconditionally
would make `hasattr(sys, "real_prefix")` and direct missing-attr behavior
wrong.

Evidence:

- Focused OS native gate after `sys.prefix` / `sys.base_prefix`: `tests/python/test_native_os_misc.py` -> 40 passed in 6.37s.
- Full self bootstrap after `sys.prefix` / `sys.base_prefix`: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 56.70s.
- Opt-in real NumPy boundary after `sys.prefix` / `sys.base_prefix`: 1 passed in 15.41s.
- Count-only diagnostic after `sys.prefix` / `sys.base_prefix`: `/tmp/pcc_numpy_sys_prefix_diag.4VTR5x/ir` dumped 149 modules; `@.cpy.attr.path,` = 41, down from 52; `@.cpy.attr.prefix,` = 0; `@.cpy.attr.base_prefix,` = 0; `@.cpy.attr.real_prefix,` = 3.
- Focused OS native gate after subscript path args: `tests/python/test_native_os_misc.py` -> 44 passed in 6.72s.
- Full self bootstrap after subscript path args: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 36.41s, proving pcc1 -> pcc2 -> pcc3 for this source state.
- Opt-in real NumPy boundary after subscript path args with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 13.99s.
- Final count-only diagnostic: `/tmp/pcc_numpy_path_subscript_diag.xsk12G/ir` dumped 149 IR modules before the known self-backend native emission failure. Compared to `/tmp/pcc_numpy_ospath_split_diag.z0Qu0M/ir`: `@.cpy.attr.path,` = 20, down from 52; `@.cpy.attr.split,` = 9, down from 10; `@.cpy.attr.prefix,` = 0; `@.cpy.attr.base_prefix,` = 0; `@.cpy.attr.real_prefix,` remains 3 by design; `@.cpy.attr.compile,` = 71; `@.cpy.attr.findall,` = 5.

Boundary note: this is fallback-surface shrinkage only. It does not prove
successful `import numpy`, NumPy native extension execution, or ecosystem
readiness. The public strict no-libpython NumPy boundary remains
`PCC-PKG-004` CPython-extension ABI rejection before pure-Python fallback
scanning.

## 2026-05-27T18:51:48+08:00 — typed str slice path args and pcc1 package-install repair

Added a narrow native `os.path.*` path-argument acceptance rule for slice
expressions whose inferred type is `StrType`. This covers typed paths such as
`os.path.join(prefix[:len(d)], prefix[len(d):])` without broadening list/tuple
slice behavior.

The fresh real NumPy boundary then exposed package-shim issues in compiled
`pcc1`: optional path locals in the `pip`/package install parsers mixed
`None` with strings, and the `pip` shim's `bool` `dry_run` local compiled back
to the dry-run branch. The package shim now uses empty-string sentinels for
optional path strings, an integer `dry_run` state, and integer return checks
for package-name containment.

Evidence:

- Focused OS native gate: `tests/python/test_native_os_misc.py` -> 54 passed
  in 8.08s.
- Full self bootstrap with `PCC_PYTHON_IR_PASSES=on`:
  `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  -> 1 passed in 40.81s, proving pcc1 -> pcc2 -> pcc3 for this source state.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` ->
  1 passed in 13.98s.
- Count-only `--python-libpython=auto` diagnostic:
  `/tmp/pcc_numpy_str_slice_diag.6xRDnk/ir` dumped 149 IR modules before the
  known self-backend native emission failure. Counts were unchanged from
  `/tmp/pcc_numpy_star_join_diag.gG21AY/ir`: `@.cpy.attr.path,` = 12;
  `@.cpy.attr.join,` = 8; `@.cpy.attr.realpath,` = 5;
  `@.cpy.attr.expanduser,` = 1; `@.cpy.attr.split,` = 7;
  `@.cpy.attr.compile,` = 71; `@.cpy.attr.findall,` = 5.

Boundary note: the typed slice fast path does not cover the remaining untyped
NumPy slice shapes, so this is package-boundary repair plus a narrow typed-path
capability, not fallback-surface shrinkage. It does not prove successful
`import numpy`, NumPy native extension execution, or ecosystem readiness.

## 2026-05-27T18:12:42+08:00 — commonprefix and shlex split keyword lowering

Added native `os.path.commonprefix(paths)` and widened `shlex.split(text, posix=True)` to the existing native `py_shlex_split` helper.

`commonprefix` is a string-prefix helper, matching the `os.path.commonprefix` behavior rather than the filesystem-aware `commonpath`. `shlex.split` only accepts the explicit `posix=True` keyword; `posix=False` and other keyword shapes remain on the existing fallback path.

Evidence:

- Focused OS/native-system gate: `tests/python/test_native_os_misc.py` -> 48 passed in 7.26s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 50.64s, proving pcc1 -> pcc2 -> pcc3 for this source state.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 15.07s.
- Count-only `--python-libpython=auto` diagnostic: `/tmp/pcc_numpy_commonprefix_shlex_diag.qGoDy9/ir` dumped 149 IR modules before the known self-backend native emission failure. Compared to `/tmp/pcc_numpy_path_subscript_diag.xsk12G/ir`: `@.cpy.attr.path,` = 18, down from 20; `@.cpy.attr.commonprefix,` = 0; `@.cpy.attr.split,` = 7, down from 9; `@.cpy.attr.compile,` = 71; `@.cpy.attr.findall,` = 5.

Boundary note: this is fallback-surface shrinkage only. It does not prove successful `import numpy`, NumPy native extension execution, or ecosystem readiness. The public strict no-libpython NumPy boundary remains `PCC-PKG-004` CPython-extension ABI rejection before pure-Python fallback scanning.

## 2026-05-27T18:20:23+08:00 — starred os.path.join and generic iterable list.extend

Added generic-iterable `py_list_extend` support in both runtime variants and
relaxed native `os.path.join(*expr)` lowering to rely on that runtime iterable
semantics instead of requiring static `ListType` / `TupleType` evidence.

This targets real NumPy fallback shapes such as `os.path.join(*name.split("."))`
and `os.path.join(*([root] + name.split(".")[:-1]))`. Non-starred path
argument filtering is unchanged.

Evidence:

- Focused OS native gate: `tests/python/test_native_os_misc.py` -> 52 passed in 8.00s.
- Full self bootstrap: `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self` -> 1 passed in 43.97s, proving pcc1 -> pcc2 -> pcc3 for this source state.
- Opt-in real NumPy boundary with fresh `build/bootstrap-pytest-self/pcc1` -> 1 passed in 17.57s.
- Count-only `--python-libpython=auto` diagnostic: `/tmp/pcc_numpy_star_join_diag.gG21AY/ir` dumped 149 IR modules before the known self-backend native emission failure. Compared to `/tmp/pcc_numpy_commonprefix_shlex_diag.qGoDy9/ir`: `@.cpy.attr.path,` = 12, down from 18; `@.cpy.attr.join,` = 8; `@.cpy.attr.realpath,` = 5; `@.cpy.attr.expanduser,` = 1; `@.cpy.attr.split,` = 7; `@.cpy.attr.compile,` = 71; `@.cpy.attr.findall,` = 5.

Boundary note: this is fallback-surface shrinkage only. It does not prove
successful `import numpy`, NumPy native extension execution, or ecosystem
readiness. The public strict no-libpython NumPy boundary remains
`PCC-PKG-004` CPython-extension ABI rejection before pure-Python fallback
scanning.
