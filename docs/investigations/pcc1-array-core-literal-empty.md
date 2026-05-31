# Investigation: pcc1 array-core literal values collapse to empty

## Status
resolved

## Problem Description
While validating the generic array-core `--reduce mean` slice through a freshly
built strict no-libpython pcc1 probe, `pcc1 -m pcc.package array-core --literal
...` reported empty literal values. The failure is broader than `mean`: existing
literal reductions such as `sum` also fail in the probe.

## Evidence
Built a strict no-libpython probe:

```bash
PCC_BUILD_SKIP=1 env -u LC_ALL perl -e 'alarm shift; exec @ARGV' 420 \
  uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o /tmp/pcc1_array_mean_probe
```

`otool -L /tmp/pcc1_array_mean_probe | rg 'libpython|Python'` produced no
output.

No-host literal report collapses to an empty array:

```bash
PCC_HOST_PYTHON=/bin/false /tmp/pcc1_array_mean_probe \
  -m pcc.package array-core --literal '[1,2,3]' --json
```

Observed shape/data:

```json
{"shape": [0], "data": [], "flat_data": [], "source": "literal", "ok": true}
```

No-host existing reduction also fails, so this is not specific to the new mean
operation:

```bash
PCC_HOST_PYTHON=/bin/false /tmp/pcc1_array_mean_probe \
  -m pcc.package array-core --literal '[[1,2,3],[4,5,6]]' --reduce sum --json
```

Observed diagnostic:

```json
{"ok": false, "diagnostics": [{"code": "PCC-ARRAY-REDUCE-EMPTY"}]}
```

The host path is healthy: `tests/python/test_package_array_core.py` passes the
host array-core tests, including the new `mean` assertions.

## Current Hypotheses
1. pcc1 self-backend string indexing/comparison in `_native_array_literal_*`
   does not behave like host Python for bracket/comma tokenization.
2. pcc1 argument capture for the value after `--literal` is corrupting or
   truncating bracketed values before the literal parser sees them.
3. A stale or partial bootstrap artifact is still being used for some native
   string helpers despite the freshly built probe recognizing newer flags.

## Impact
Do not claim pcc1 parity for new array-core literal-value operations until this
is fixed. The new `mean` implementation is host-validated and the pcc1 source
mirror has been updated, but no-host pcc1 runtime evidence is currently blocked
by this literal parser failure.

## Next Steps
- Add a minimized pcc1 probe or regression for `_native_array_literal_values`
  / `_native_array_literal_shape_and_diagnostics`.
- Determine whether the failure is argv capture, string indexing, or string
  comparison/tokenization.
- Fix the underlying pcc1/runtime behavior, then rerun no-host array-core
  literal reports and reductions.

## Update 2026-05-19: resolved

Root cause: `_run_native_package_array_core_from_pcc1()` used `None` sentinels
for optional string/int CLI parameters inside a large self-hosted function.
Under the pcc1 self-hosted lowering, the default `repeat is not None` branch
could behave as if repeat were present. Because repeat defaults effectively to
zero in that path, the no-op literal report was transformed into an empty
repeat result (`shape=[0]`, `data=[]`) before JSON reporting.

Fix: replace the array-core native path's optional operation sentinels with
string sentinels (`""`) for `literal`, `rhs`, `matmul`, `concat`, `stack`,
`tile`, `full_like`, `otherwise`, and `repeat`, and gate those operations with
`!= ""` instead of `is not None`. The pcc1 path now avoids accidental optional
operation execution when no such flag was provided.

Verification with strict no-libpython `/tmp/pcc1_array_mean_probe`:

- `otool -L /tmp/pcc1_array_mean_probe | rg 'libpython|Python'`: no output.
- `PCC_HOST_PYTHON=/bin/false /tmp/pcc1_array_mean_probe -m pcc.package array-core --literal '[1,2,3]' --json` returned `shape=[3]`, `data=[1,2,3]`.
- `PCC_HOST_PYTHON=/bin/false /tmp/pcc1_array_mean_probe -m pcc.package array-core --literal '[[1,2,3],[4,5,6]]' --reduce sum --json` returned `data=21`.
- `PCC_HOST_PYTHON=/bin/false /tmp/pcc1_array_mean_probe -m pcc.package array-core --literal '[[1,2,3],[4,5,6]]' --reduce mean --json` returned `data=3.5`, `dtype=float64`.
- `PCC_HOST_PYTHON=/bin/false /tmp/pcc1_array_mean_probe -m pcc.package array-core --literal '[[1,2,3],[4,5,6]]' --reduce mean --axis 1 --keepdims --json` returned `shape=[2,1]`, `data=[[2.0],[5.0]]`.
