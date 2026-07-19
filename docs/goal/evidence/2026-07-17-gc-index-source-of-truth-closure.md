# GC object-index source-of-truth closure (2026-07-17)

Task: `AUD-P1-GC-INDEX-TABLE-SOURCE-OF-TRUTH`

## Proven boundary

- `pcc/py_runtime/src/py_obj_gc.c` no longer owns its former chained
  `PyGcNodeSlot` hash table. Its lookup/insert/remove path now calls the same
  `py_gc_index_*` open-addressed C-kernel implementation already consumed by
  `pcc/py_runtime/py/py_obj_gc.py`.
- A source guard rejects restoration of the duplicate C hash/rehash/table
  implementation and requires both semantic runtimes to consume the shared ABI.
- The C runtime probe uses the kernel's actual pointer hash and proves colliding
  insert/find, duplicate insert, tombstone deletion without probe-chain loss,
  and a 300-entry resize followed by deletion and lookup.
- This slice does not change backend-4 relocation slot rules, forwarding
  retirement, zpage lifetime policy, or collector graph semantics.

## Runtime archive gate repair

The first matrix attempt exposed a build-system defect: the pcc-Python archive
staleness scan treated C semantic modules replaced by `PY_MODULES` as archive
inputs. Changing `py_obj_gc.c` therefore made `libpy_runtime_pcc_py.a` appear
permanently stale even though Make correctly excludes that object. The scan now
reads `PY_MODULES` / `PY_REPLACED_C_MODULES` from the Makefile and skips only
replaced C sources; active C helpers remain freshness inputs. A compiled-stage
follow-up replaced host-only `file.readlines()` with the already supported
`file.read().splitlines()` path.

## Gates

- Source contract:
  - `2 passed in 0.52s` for the open-addressing contract and new shared-owner
    source guard.
- Collision/delete/resize C probe:
  - `1 passed in 12.56s`.
- Shared slot/update contract:
  - `27 passed in 49.68s` for `tests/python/test_gc_update_referents.py`.
- Default backend cycle behavior:
  - `1 passed in 1.11s` for
    `test_gc_effectiveness.py::test_cycle_collect_finds_simple_cycles`.
- Variant-aware archive staleness:
  - `5 passed, 34 deselected in 0.42s`.
- Strict self-host source compile:
  - `pipeline.py` emitted LLVM successfully under self/no-libpython mode.
- Compiled-stage staleness path:
  - rebuilt `build/bootstrap-gc-index-pcc1/pcc1` in about 54 seconds;
  - with `PCC_HOST_PYTHON=/usr/bin/false`, that pcc1 compiled and ran the tiny
    archive probe, printing `1`.
- Five-GC fixed-point matrix:
  - `gtimeout 900s env -u LC_ALL uv run pytest -q tests/python/gc/test_pcc_bootstrap_full_gc0.py tests/python/gc/test_pcc_bootstrap_full_gc1.py tests/python/gc/test_pcc_bootstrap_full_gc2.py tests/python/gc/test_pcc_bootstrap_full_gc3.py tests/python/gc/test_pcc_bootstrap_full_gc4.py`
  - final result: `5 passed in 890.93s`.

The final run was a cold, all-input-invalidated matrix. Stage2+stage3 profile
wall totals were approximately gc0 238s, gc1 316s, gc2 271s, gc3 312s, and gc4
368s. The matrix deliberately limits active backend chains on this 12-core host;
the 890s matrix wall is not the duration of one GC bootstrap.
