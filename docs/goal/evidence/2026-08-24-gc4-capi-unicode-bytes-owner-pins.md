# GC4 C-API unicode/bytes raw-pointer owner pins — 2026-08-24

## Claim

Unpaired C-API unicode and bytes raw-pointer exports now lifetime-pin their
owners without adding pin cost to pcc's internal string hot path.

Unicode uses a dedicated `pcc_capi_str_utf8_pinned` ABI in C and strict:

- fake-header `PyUnicode_{1,2,4}BYTE_DATA` and `PyUnicode_DATA` macros call it;
- `PyUnicode_AsUTF8` and `PyUnicode_AsUTF8AndSize` call it; and
- internal `py_str_utf8` remains a plain short-lived accessor with no pin.

Bytes pins in the unique `PyBytes_AsString` and
`PyBytes_AsStringAndSize` C/oracle/strict entrypoints before returning/storing
the inline data pointer.

The new runtime signature was placed in a non-full ABI chunk; every literal
chunk remains at most 50 entries.

## Dynamic proof

Threaded C and strict probes show internal `py_str_utf8` returns correct data,
does not set PINNED and leaves its string directly relocatable.  C-API UTF-8 and
bytes pointer/size calls return exact stable bases/data, set owner PINNED and
make direct relocation admission reject both owners.

This closes unicode/bytes unpaired pointers.  Counted Py_buffer/memoryview
leases remain open, along with constructor admission blockers, callbacks,
resurrection, stage2 performance and fixed point.

## Gates

- C/oracle/strict/fake-header routing contract and strict closures: pass.
- runtime ABI chunking: pass (`max <= 50`).
- final unicode/bytes plus sequence raw-view matrix: `6 passed in 1.42s`.
- full source/ABI/GC abstraction neighbors: `32 passed in 30.67s`.
- task relocation payload/forwarding retirement gate:
  `24 passed in 142.13s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-capi-unpaired-raw-pins-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
51361d95f92769c30b3ec8df68a8d8d137796569bd86dfcc52786962db79ad9c  pcc/py_runtime/include/py_runtime.h
000d51584d8643e1d563d75a79e4bca14fb3fc4ba1ac7e9701c06d3a716432f4  pcc/py_runtime/src/py_str_accessors.c
151ba5936ab0a93e3d0a7ebd403d812ce49959007262d17a98276acbc77ff564  pcc/py_runtime/py/py_str_accessors.py
57f3717af947f99cceb572428fdf6ad546b17e99bb397f4ace96a312f2bd5bcf  pcc/py_frontend/codegen/runtime_abi.py
90dd800de0e89a31939ca614f193925024a43b1f7237833617a795787bc491bc  utils/fake_libc_include/Python.h
707b7ed0fab4e9ef1e00bd9ceabf8e1f6532090909bbcc38405e5e5c25c26776  pcc/py_runtime/src/py_capi_shim.c
25c178f0acad92bb52c36997d2c25b08f0e2bf6039f1175b1f8328b421062260  pcc/py_runtime/src/py_capi_shim_oracle.c
893bfe92311f874f5f5c1ca1a933c453bf04415b438fa662011fbb3bed8e1a07  pcc/py_runtime/py/py_capi_unicode_runtime.py
da1c1ad0af9aeec3a7c928ccbd0e3de4085028c0515c9acc444d2338a7c5be91  pcc/py_runtime/py/py_capi_collections_runtime.py
09a990f323738b5f1f507967d28af93e9e4f552a6f7ac68c654830a37d07605f  tests/python/test_gc_threading_substrate.py
2a1cb3b751a9e9281be64bd62ab86cf352c61e9aca9f35476e9183fc7f9e406b  build/gc4-capi-unpaired-raw-pins-final.log
96fc0d5abb17051bf7b00ea71c311a1938dedfce3fe9968092bfa4276b521042  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.13c unicode/bytes unpaired raw-pointer owner pins.
The GC4 parent remains `IN_PROGRESS` for counted buffer leases.
