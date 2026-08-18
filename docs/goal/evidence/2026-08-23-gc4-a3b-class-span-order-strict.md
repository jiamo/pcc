# GC4 A3b class raw-span descriptor order strict recovery

Date: 2026-08-23

## Claim

The previously interrupted strict freestanding pcc-Python node for the
Backend 4 class raw-payload span-order case is now green on current source.
The case gives one class multiple relocation payload spans and checks that the
preallocated descriptors are linked in descriptor order.  Together with the
pre-existing task-board checkpoint for the C transition-oracle node, this
closes only the finite class-span-order recovery boundary in both mirrors.

No runtime or test source changed in this recovery slice.

## Exact command and result

The first invocation used the right node name under the wrong test module and
therefore collected zero tests.  It exited 4 and is not evidence:

```text
ERROR: not found: .../tests/python/test_freestanding_gc_relocation_payload.py::test_backend4_relocation_copies_type_specific_raw_payloads
no tests ran in 0.08s
```

Read-only source lookup located the parameterized node in
`tests/python/test_gc_backend4_production.py`.  The corrected exact command was:

```bash
gtimeout 240s zsh -o pipefail -c 'gtimeout 210s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short "tests/python/test_gc_backend4_production.py::test_backend4_relocation_copies_type_specific_raw_payloads[class-span-order-7-pcc_python]" 2>&1 | tee build/gc4-a3b-class-span-order-strict-v2.log'
```

Observed final result:

```text
collected 1 item
tests/python/test_gc_backend4_production.py::test_backend4_relocation_copies_type_specific_raw_payloads[class-span-order-7-pcc_python] PASSED [100%]
1 passed in 1.21s
```

The content-addressed strict runtime archive was already warm, so this is a
current-source execution result, not a new cold-build timing measurement.

## Source and log identity

```text
c71cf1c5cc1850168f1e8a22127d46828ed2dabec6cf8b6e38e3c4034b4ab08c  pcc/py_runtime/src/py_gc_backend.c
38f115872f5fbabac907d554588d824790b3a9e2686f7ca9165daeed409d522d  pcc/py_runtime/py/py_gc_backend.py
b06824cf548bbc8993fdce51c9cc76c6e4bf81969710751aea647e42a5cd3233  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
a696390d161d93c7c5efd1b82efc36db4bde492c260b124bb6be873ff8ecd317  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
9bd9a193a126d4b4ce7bc4d562cb17e3c88a0c70e183bcb4dc9d777bc6800854  tests/python/test_gc_backend4_production.py
78be82f4ffdfbd2526fc8cbe1cbdbd5348abbf902bd6286030a78a745631c2be  build/gc4-a3b-class-span-order-strict-v2.log
```

## Supported boundary and nonclaims

This proves descriptor-order publication for the strict class multi-span case
that lacked a final pytest summary in the recovery checkpoint.  It does not
move raw byte copying outside the graph lock, establish raw-mutator phase or
no-park admission, protect selected-source/page lifetime across unlocked
planning windows, or prove the remaining forwarding/identity-index/ZPage
commit, remap/retirement, remembered-root admission, GC3 holder, callback,
C-API raw-view, target-death, resurrection, stage, performance, fixed-point or
broad five-GC boundaries.  The parent task remains `IN_PROGRESS`.
