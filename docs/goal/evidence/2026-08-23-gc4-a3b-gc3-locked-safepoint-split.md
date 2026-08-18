# GC4 A3b GC3 locked safepoint split

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b holder sub-boundary confirmed; parent task remains
`IN_PROGRESS`.

## Claim boundary

Backend-3 remembered overflow/drain and young-promotion code in the C and
strict pcc-Python roots no longer calls `pcc_thread_safepoint` while holding
the object graph lock. Each generational step caps successful remembered/young
promotion work at 16 and performs its single processed-work safepoint only
after graph unlock and detached-node finish.

This is not a bounded-holder claim. Overflow fallback may still examine an
unbounded number of nonmatching object nodes to find up to 16 remembered
owners. Registered-root promotion and extension visitors may also traverse or
callback without a batch boundary. TLS exception oldification still performs
cleanup decref under the lock. These remain blockers before A3c.

## Genuine RED

`test_generational_locked_step_caps_work_without_safepointing` failed first on
the strict overflow scanner's in-lock safepoint:

```text
AssertionError: assert 'pcc_thread_safepoint' not in strict_scan
1 failed in 0.10s
```

## Implementation

- C/strict remembered-owner scan and drain no longer safepoint internally.
- C/strict generational step derives `batch_budget = min(requested, 16)` and
  uses it for remembered drain and young promotion success accounting.
- Inner young-promotion safepoints were removed; the existing outer safepoint
  stays after unlock and detached-node finish.
- Strict raw extern ownership dropped the now-unused remembered-owner module
  dependency on `pcc_thread_safepoint`.

## Focused evidence

- Direct strict self/no-libpython closure passed for remembered-owner and
  generational-scheduler modules.
- Python syntax, C syntax with `PCC_WITH_THREADS=0/1`, and `git diff --check`
  passed.
- C/strict remembered-child promotion remained green after the smaller step
  budget.
- Final focused packet:

  ```text
  gtimeout 120s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short <7 focused node ids> 2>&1 | tee build/gc3-generational-locked-safepoint-final.log'
  7 passed in 3.35s
  ```

## Frozen identities

```text
64bc594c874a55ca4311ccd9593ffe9d0122bd6c1a4094281ecfdb12c02dd10f  pcc/py_runtime/src/py_gc_backend.c
90c1c0edc12c8db1b6dda77679fbd718b01f61a037c1e4c3935e994547151851  pcc/py_runtime/py/freestanding_gc_generational_remembered_owners.py
87c1bfca3be9f9999f48b9a45c9f927a21fe0b4580866639b555d2c1ef04c5dd  pcc/py_runtime/py/freestanding_gc_generational_scheduler.py
5e21f374903fcbd34e454e523f9f784fb3ef9376ddc65bc314e0751821ab5324  tests/python/test_freestanding_gc_generational_remembered_owners.py
da8367bd49a5808f086a7c2e79a107f69f66cc424333404fd4e4d34da5e9491a  tests/python/test_freestanding_gc_generational_scheduler.py
8cf36884b5ddcf3837b4ca255ca90d75a1c140c2c9b90ebbd4ad205693231bf4  tests/python/test_gc_backend_generational.py
73c315d9ee14f8584cc9fea9b49d0e841851957b1b7ff6d608644168c9ee3013  build/gc3-generational-locked-safepoint-final.log
b036fa5d7a78ff0c3ebf75489846eee08e88edaa7581c41ba680a08c8eaf498a  build/gc3-generational-locked-safepoint-closures-final.log
aa4ed550aafca977a728f31b38cf1e8414f755b2ce607d3516436db81889e768  build/gc3-generational-locked-safepoint-source-identity.txt
```

## Next boundary

Do not connect A3c. Bound overflow fallback by examined object nodes using an
unlink-safe cursor plus object-list revision/restart, then split TLS cleanup
decref. Registered-root and extension/caller callbacks remain separate.
