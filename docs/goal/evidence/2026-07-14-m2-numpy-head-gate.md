# M2 NumPy current-source build/load/PEP 489 gate

Date: 2026-07-14

Task: `M2-NUMPY-HEAD-GATE`

## Claim boundary and modes

This slice proves the pinned NumPy 2.4.4 `_core` compile surface and the actual
`_multiarray_umath` link closure against the current pcc C-API/runtime headers,
then proves current-source host pcc can load the pcc-native artifact through the
self backend with `python-libpython=off` and enter PEP 489 `Py_mod_exec`.

It does not prove a pcc1 package-pipeline build (`M2-NUMPY-PCC-NATIVE-ARTIFACT`),
full NumPy Python-module graph execution, `import numpy` L4, array behavior L5,
five-GC NumPy behavior, or a pcc1/pcc2/pcc3 fixed point.

## Source identity

- package: NumPy 2.4.4 at `projects/numpy-2.4.4`
- pinned source + generated-scaffold SHA-256:
  `92dea23be728ec1d084397b4b1e4bbd96845932c252d48eb771c204c3393c941`
- compile surface: 137 actions = 113 C + 23 `.cpp` + one `.cc`
- link closure: 136 objects selected recursively from the Meson Ninja graph
- manifest source identity: recorded in
  `build/head-truth/m2-numpy-manifest.json`; the worktree is dirty, so this is
  fingerprint-bound evidence and not a clean-release claim

The 137/136 distinction is intentional. One `_simd` baseline dispatch object
belongs to the historical non-test `_core` compile surface but not to the real
`_multiarray_umath` link closure.

## Implementation

- `scripts/numpy_head_gate.py` owns source/scaffold hashing, fresh curated C-API
  header materialization, 137-action parallel compilation, recursive 136-object
  closure selection, pcc-native linking, linkage/PyInit inspection, strict
  loader execution, and deterministic first-blocker classification.
- `scripts/head_truth_manifest.py` registers `numpy-core-head`, validates its
  result, and attaches compile/link/loader observations and artifact paths.
- `scripts/head_truth_gate.py --gate numpy-core-head` runs only this heavy card;
  it does not force LLVM/bootstrap/GC gates and captures source identity once.
- `tests/test_numpy_head_gate.py` locks graph counts, pcc C/C++ include redirect,
  stale dependency-output removal, current libc++ flag migration, and honest
  PEP 489 blocker classification.
- the heavy workflow uploads the NumPy result/artifacts and budgets the new gate.

No package-name branch was added to compiler/package behavior. The NumPy-specific
selection exists only in this explicit NumPy milestone gate.

## Red gate and root cause

The first automated current-Xcode run completed in 10.430 seconds:

```text
compile 137/137, failures=24
passed=113, failed=24
```

All 24 C++ actions failed at the same current-libc++ diagnostic:
`_LIBCPP_ENABLE_ASSERTIONS has been removed`. The 113 C actions passed. This was
toolchain replay drift in the pinned Meson graph, not a pcc header or NumPy
semantic failure. NumPy's vendored Meson maps this legacy flag to
`_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_FAST` for AppleClang >=16. The
gate applies that exact mapping. A one-TU substitution compiled `abort.cc`
successfully before rerunning the full surface.

## Required gate

```text
gtimeout 1300s env -u LC_ALL uv run python scripts/head_truth_gate.py run \
  --gate numpy-core-head \
  --output build/head-truth/m2-numpy-manifest.json \
  --artifacts-root build/head-truth/logs
```

Observed strict result:

```text
numpy-core-head: PASS
fresh compile: 137/137, failures=0
link closure: 136/136 objects, exit 0
artifact: _multiarray_umath.pcc3-pcc_native-macosx_14_0_arm64.so
exports PyInit__multiarray_umath: true
links libpython: false
artifact dependencies: Accelerate, libSystem, libc++
loader compile: exit 0, backend=self, libpython=off, ir-scaffold=on
loader dependencies: libSystem only; no libpython and no LLVM
entered PyInit: true
entered Py_mod_exec: true
first blocker: first_missing_module / Py_mod_exec / math
runtime diagnostic: PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython] module not found: math
```

The direct successful run completed in 13.056 seconds; the manifest-owned rerun
also returned PASS and `scripts/head_truth_gate.py validate` accepted the
result. The artifact SHA is recorded inside the manifest observation because
Mach-O link metadata may vary across fresh builds.

## Focused controls

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_numpy_head_gate.py \
  tests/test_head_truth_manifest.py \
  tests/test_head_truth_workflows.py \
  tests/test_goal_startup_docs.py
```

Result before the final manifest capture: `26 passed in 0.51s`.

No full GCC suite, full pytest suite, LLVM bootstrap, or five-GC bootstrap
matrix was run for this gate-only control-plane slice.
