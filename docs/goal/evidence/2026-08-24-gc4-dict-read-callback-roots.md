# GC4 dict read callback roots — 2026-08-24

## Claim

C and strict pcc-Python `py_dict_get` now root owner/query before user hash and
use a restartable read probe.  A callback-capable entry key is retained and
rooted across equality.  After callback return, owner/query/candidate are
reloaded; the probe restarts from its hash origin if owner identity, capacity,
indices, entries or the current slot/key changed.  A found value is returned
owned before roots are detached.

`py_dict_contains` reuses get and releases that owned value.  This slice does
not change set/update/delete mutation paths.

## Dynamic proof

C and strict probes cover both supported callback surfaces:

- an ordinary instance `__hash__` directly relocates the first dict; lookup
  reloads it and returns the existing value `11`;
- a C-extension equality callback directly relocates a second dict; lookup
  restarts and returns `22`.

Subsequent contains calls succeed and only the two external probe roots remain
before cleanup.

## Gates

- static C/strict seam plus C callback probe: `2 passed in 0.26s`.
- strict hash/equality callback probe: `1 passed in 141.43s`.
- dict get/default/in/int-key semantics: `4 passed in 4.48s`.
- strict no-libpython source closure and C syntax: pass.
- strict archive owner: `1 passed in 142.29s`.
- runtime ABI chunk plus GC abstraction: `17 passed in 32.59s`.
- task relocation payload/forwarding retirement gate: `24 passed in 13.89s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-dict-read-source-c-oracle.log`
- `build/gc4-dict-read-callback-roots.log`
- `build/gc4-dict-read-semantics.log`
- `build/gc4-dict-read-archive-owner.log`
- `build/gc4-dict-read-abi-gc.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
f06bca3857f2627d0a66eecbf1c79cc1f3d442720e6e68c6c227f8ca80055925  pcc/py_runtime/src/py_dict.c
c7c74b046eee905a47e2fad5500de510afc8a3e405fa8e67e353c7de0ed70bff  pcc/py_runtime/py/py_dict.py
9660c89c72fabd93151ba3149fb441d257ac7f6b31d857a1f2c18277451e4628  tests/python/test_gc_threading_substrate.py
46e378e8dd4ced0bf798db473aa5c62dc5728d2d14cca95a0510dc85eea20a29  build/gc4-dict-read-source-c-oracle.log
deea327ebf4c917fc210e4b5405c3cb3387b9c824be58522e215d723ee748c2f  build/gc4-dict-read-callback-roots.log
134c93d8af9de9a98fd0267b99a25ac192b055c593095ce6b4e7937dc0e83892  build/gc4-dict-read-semantics.log
4587e95f810f5297026b96ad72480ce564a31d4c5964567a473f9ed40474db38  build/gc4-dict-read-archive-owner.log
a80f373279280b6affdbb47b177a3b3d10a328db0943570196c3060e7ae7f24c  build/gc4-dict-read-abi-gc.log
0e88f16dd00263e4ae39fcb4a43b83563cd3ba140b16a06eaae2681239cc8e4a  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.19 dict get/contains callback roots.  Dict
mutation/delete, set operations and the GC4 parent remain open.
