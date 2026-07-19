# Investigation: strict-self synthetic PEP 489 extension loses C-API exports to dead stripping

## Status

resolved

## Problem Description

The second synthetic M1 extension can be built and linked through the generic
pcc-native sdist installer, and its importing application compiles with
`backend=self` and `python-libpython=off`, but `dlopen` fails because the final
executable does not export `PyModuleDef_Init`. The extension also references
`PyModule_AddIntConstant`. Both functions exist in the pcc C-API shim archive,
so this is a final executable symbol-retention failure rather than a missing
C-API implementation or package-specific import failure.

## Repro

```text
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-pytest-shared-stage1/pcc1 \
  PCC_REQUIRE_CURRENT_PCC1=1 \
  uv run pytest -q -n0 \
  tests/python/test_m1_pcc_native_source_build.py::test_current_pcc1_imports_generic_built_extension_without_host_helpers
```

Expected: the strict-self executable prints `ready 1` and exits zero.

Observed deterministically: the extension and executable build successfully,
then execution exits 1 with `symbol not found in flat namespace
'_PyModuleDef_Init'`.

`nm -u` on the extension lists `_PyModuleDef_Init` and
`_PyModule_AddIntConstant`. `nm -gU` on the pcc-Python runtime archive lists
both definitions, while `nm -gU` on the failed executable lists neither.

## Test [CONFIRMED]

The focused pytest node above was observed failing in `5.43s`. It builds a
second anonymous PEP 489 source extension with both host escape hatches set to
`/usr/bin/false`, compiles its importing application through strict pcc1/self,
checks no libpython/LLVM dynamic dependency, executes `Py_mod_exec`, and checks
the exported integer constant.

## Proposals

- No.1 Preserve the whole generic C-API shim when native extensions are loaded [DENIED]
- No.2 Detect absolute `from package import extension` native imports [CONFIRMED]

## No.1 Preserve the whole generic C-API shim when native extensions are loaded

### Code Change

The self-backend link already anchors `PyArg_ParseTuple` to pull
`py_capi_shim.o` from the static runtime archive and requests dynamic exports.
On Darwin it subsequently adds `-dead_strip`, which discards other unreferenced
C-API functions before a later `dlopen` can reference them. Suppress
`-dead_strip` only when `needs_native_extension_exports` is true, in both the
single-assembly and split-object self link paths. Keep the existing anchor and
export flag; do not enumerate package-specific undefined symbols and do not
force-load unrelated runtime objects.

### DENIED

The source substitution was compiled with `--no-cache --verbose`. Its final
link command contained `-dead_strip`, but more importantly contained neither
`-export_dynamic` nor the `PyArg_ParseTuple` archive anchor. Therefore
`needs_native_extension_exports` was false and this run did not prove that
dead stripping defeats the existing retention design. The speculative source
change and its matching unit test were removed before any further proposal.

## No.2 Detect absolute `from package import extension` native imports

### Code Change

`_module_imports_pcc_native_extension` checks the resolved module of every
`ImportFrom`, but checks `resolved + alias` only for a bare relative
`from . import name`. Apply that structural alias check to absolute and
module-qualified `ImportFrom` statements as well. Exclude names already in the
closed-world Python module set and require the candidate path to have the
pcc-native suffix, preserving the existing generic resolver and avoiding any
package-name rule.

### CONFIRMED

The structural alias check now applies to every `ImportFrom`, not only bare
relative imports. Candidates already present in the closed-world Python
module set remain excluded, and the resolver still requires the pcc-native
extension suffix. No package-name rule was added.

The second anonymous PEP 489 extension now builds and imports through the
current strict pcc1 with `PCC_HOST_PYTHON=/usr/bin/false` and
`PCC_HOST_PCC=/usr/bin/false`. The final executable retains the generic C-API
exports, reaches `Py_mod_exec`, prints `ready 1`, and exits zero with empty
stderr.

The fresh pinned simplejson 4.1.1 vertical chain also passes: source install
and native link return zero, the strict application gate is `2 passed in
8.09s`, and one unchanged executable passes GC0..4 with native bindings and
CPython-oracle behavior. The second-extension/import-detection/no-package-name
gate is `5 passed in 1.77s`. See
`docs/goal/evidence/2026-07-14-b-p0-package-vertical-canary.md`.
