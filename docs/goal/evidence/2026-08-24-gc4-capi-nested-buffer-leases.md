# GC4 nested Py_buffer leases — 2026-08-24

## Claim

C production/oracle and strict C-API owners now implement counted buffer leases
with one metadata node per active `Py_buffer`.  Each node stores shape, strides,
the final raw-storage exporter, the original `Py_buffer.obj` owner, and the next
active lease.

`PyObject_GetBuffer` resolves memoryview chains to the final bytes/bytearray
exporter, links the node under graph/no-park, and pins each distinct owner only
on its first active occurrence.  `PyBuffer_Release` unlinks first, scans the
remaining active nodes, and unpins exporter/wrapper only when their final lease
is gone; it then decrefs `view->obj`, frees metadata and zeroes the public view.

Pinning both identities is required: the raw `buf` belongs to the final exporter,
while the later Release dereferences/decrefs the original memoryview wrapper in
`Py_buffer.obj`.

## Dynamic proof

Threaded C and strict probes acquire two views from one memoryview over bytes.
Both views expose the same exact bytes base.  Base and wrapper are pinned and
reject relocation.  Releasing the first leaves both pinned/rejected.  Releasing
the second clears both pins and makes each directly admissible again.

This closes the required C-API raw-view/buffer lease family.  Constructor fresh
admission blockers, callbacks beyond list, resurrection metadata, stale-candidate
fairness, stage2 performance, fixed point and broad five-GC parity remain open.

## Gates

- C/oracle/strict source-order contract and strict closures: pass.
- no-C buffer ownership plus C/strict nested lease matrix:
  `5 passed in 138.93s`.
- full source/ABI/GC abstraction neighbors: `32 passed in 11.02s`.
- task relocation payload/forwarding retirement gate: `24 passed in 13.98s`.
- existing native-extension buffer gate is blocked before runtime by the
  recorded self-link native-extension export-anchor capability boundary; no
  baseline or implementation was weakened.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-capi-nested-buffer-leases-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
e8eb4891286e37d871108d487b00f803f8c840b73947a1cde84cd12387fee0bd  pcc/py_runtime/src/py_capi_shim.c
9f45f4114e42a469d1015ac2b98dcb8893998c744e1fd2520f720336b7e8a19a  pcc/py_runtime/src/py_capi_shim_oracle.c
3a2260a767d93bcb6775e10985785208b69434ce8733992dd5b886fc9e13b96d  pcc/py_runtime/py/py_capi_buffer_runtime.py
9e355ce975afac5c9c220449d9a2a8a75a7c0afccfc253489705311dfb431019  pcc/py_runtime/py/py_capi_misc_runtime.py
35756f12eec7b961b9bdd752bfc0217cf4f658de80658801d1b63001f5a5bd25  tests/python/test_gc_threading_substrate.py
da4126cc26e0ce8170e94f41a985a89dc12023d02160ceeb655ddd53ba77c8e0  build/gc4-capi-nested-buffer-leases-final.log
5b0c6cacb7135c6970a9318ffee185ed78e60e91c9f36503ac584403a645c7ba  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.13d nested Py_buffer/memoryview leases.  The GC4
parent remains `IN_PROGRESS` for callback and constructor/resurrection gaps.
