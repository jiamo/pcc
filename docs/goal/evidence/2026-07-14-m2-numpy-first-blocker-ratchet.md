# M2 NumPy first-blocker ratchet

Date: 2026-07-14

Task: `M2-NUMPY-FIRST-BLOCKER-RATCHET`

## Claim boundary

This slice proves that real NumPy integration gates record exactly one first
blocker in one of three categories (`first_missing_module`,
`first_missing_symbol`, or `first_semantic_mismatch`) and compare it against a
pinned source-and-mode frontier. A stable blocker keeps the gate green but does
not count as progress. A changed blocker makes the gate fail as an unreviewed
candidate until an explicit promotion preserves the old blocker in ordered
history. Reappearance of resolved blockers, earlier-phase movement, source
drift, and mode drift are rejected.

It does not claim the current `math` blocker moved. Both real lanes remain at
frontier zero with `progressed=false`. It also does not claim `import numpy` L4
or array behavior L5.

## Implementation

- `tests/numpy_first_blocker_baseline.json` pins NumPy 2.4.4, source/scaffold
  SHA-256
  `92dea23be728ec1d084397b4b1e4bbd96845932c252d48eb771c204c3393c941`,
  complete execution-mode labels, the current blocker, and ordered resolved
  history for two real lanes: `numpy-core-head` and
  `numpy-package-artifact`.
- `scripts/numpy_first_blocker.py` validates, checks, and explicitly promotes
  the baseline. Promotion requires a changed blocker from the same source and
  mode at the same or a later ordered phase. The previous blocker is retained
  permanently in the resolved history; a return to it is a regression.
- Both real NumPy gates write `first_blocker_ratchet` into their result JSON and
  fail unless it is accepted. `scripts/head_truth_manifest.py` recomputes the
  observation from the checked baseline and requires one classified blocker
  plus an accepted ratchet before `numpy-core-head` can be PASS.
- Loader classification now distinguishes missing `PyInit_*`/dlsym symbols from
  opaque semantic mismatches. Loader compilation failures are also expressed as
  `first_semantic_mismatch`, so no private fourth blocker category can bypass
  the manifest.
- A fake provider-slot result has the wrong real-gate schema and is rejected.
  Provider counts alone neither match a source/mode lane nor advance a
  frontier.

Explicit future promotion uses the real gate result, for example:

```text
env -u LC_ALL uv run python scripts/numpy_first_blocker.py \
  --baseline tests/numpy_first_blocker_baseline.json \
  promote \
  --lane numpy-core-head \
  --result build/head-truth/numpy-core/result.json \
  --write tests/numpy_first_blocker_baseline.json
```

The same result cannot be promoted while it still equals the current blocker.

## Real integration results

Current-source core gate:

```text
gtimeout 180s env -u LC_ALL uv run python scripts/numpy_head_gate.py run \
  --source projects/numpy-2.4.4 \
  --build-root build/head-truth/numpy-core \
  --result build/head-truth/numpy-core/result.json \
  --jobs 8 --compile-timeout 90 --link-timeout 90 --loader-timeout 120
```

Result: PASS in 12.717 seconds, 137/137 compiled, 136 linked, strict
self/no-libpython loader entered PyInit and Py_mod_exec, exactly one blocker
`first_missing_module / Py_mod_exec / math`, ratchet `STABLE`, accepted true,
progressed false.

Package-artifact lane:

```text
gtimeout 180s env -u LC_ALL uv run python \
  scripts/numpy_package_artifact_gate.py \
  --jobs 8 --output build/head-truth/numpy-package/result.json
```

Result: PASS in 15.915 seconds, 136/136 package-executor compile/link, identical
pinned source SHA, exactly one blocker `first_missing_module / Py_mod_exec /
math`, ratchet `STABLE`, accepted true, progressed false.

Direct checks of both result files returned exit zero:

```text
env -u LC_ALL uv run python scripts/numpy_first_blocker.py validate
env -u LC_ALL uv run python scripts/numpy_first_blocker.py check \
  --lane numpy-core-head --result build/head-truth/numpy-core/result.json
env -u LC_ALL uv run python scripts/numpy_first_blocker.py check \
  --lane numpy-package-artifact \
  --result build/head-truth/numpy-package/result.json
```

## Focused controls

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_numpy_first_blocker.py \
  tests/test_numpy_head_gate.py \
  tests/test_head_truth_manifest.py \
  tests/test_head_truth_workflows.py
```

Result: `32 passed in 3.19s`.

No full GCC suite, full pytest suite, LLVM bootstrap, stage chain, or five-GC
bootstrap matrix was run.
