# GC3 cpy-handle oldification ownership intake

Date: 2026-08-23

Task: `GC-P0-GC3-CPY-HANDLE-OLDIFY-OWNERSHIP`

Status: source-confirmed task intake; no fix claimed.

## Confirmed boundary

C GC3 lists `PY_TYPE_CPY_HANDLE` as copy-oldification supported and performs a
shallow `memcpy`. A cpy handle owns its `cpy_ref` and has only a release hook;
there is no foreign retain hook or explicit ownership-transfer operation in
the oldification path. When old TLS source cleanup reaches its terminal
decref, the source deallocator calls the foreign release hook even though the
copied target still contains the same `cpy_ref` and remains published in TLS.

A focused C pthread probe observed the release hook after TLS copy publication.
The strict oldifier excludes cpy handles and returned the original object, so
it does not share the unsafe C capability. This is a C/strict capability and
foreign-ownership parity gap, separate from the now-fixed graph-lock cleanup
ordering.

## Open boundary

Choose and prove one generic ownership contract: either remove cpy handles
from GC3 copy eligibility and oldify them in place, or add an explicit foreign
retain/transfer mechanism that guarantees exactly one live owner and exactly
one final release. A shallow-copy-only patch is forbidden. Add a focused C and
strict production-runtime regression before changing the implementation.
