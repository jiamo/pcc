# Investigation: GC3 cpy-handle oldification loses foreign ownership

## Status

active

## Problem Description

The C Backend-3 oldifier treats `PY_TYPE_CPY_HANDLE` as shallow-copy safe even
though the object owns a foreign reference. Its only ownership hook is a
release callback. Copying the raw pointer and later decrefing the forwarding
source can release the foreign object while the target still publishes the
same pointer, followed by a possible second release when the target dies.

Strict pcc-Python excludes cpy handles from GC3 copy-oldification. This is both
an ownership defect in the C capability and a C/strict capability mismatch.

## Source audit [CONFIRMED]

- `pcc_gc_relocate_copy_supported_tag` includes `PY_TYPE_CPY_HANDLE` and calls
  it shallow-copy safe.
- `pcc_gc_generational_oldify_copy` copies the complete object bytes, installs
  forwarding and ultimately transfers the replaced TLS source to cleanup.
- `py_cpy_handle.c` defines one `py_cpy_handle_release_fn`; it has no matching
  retain hook.
- `py_dealloc_cpy_handle` calls the release hook for the non-NULL copied
  `cpy_ref`.
- strict `pcc_gc_generational_oldify_supported_tag` omits cpy handles.

Therefore no source operation creates a second valid owned foreign reference
or transfers the original exclusively to the target before source cleanup.

## Test [CONFIRMED]

During the GC3 TLS-cleanup A3b slice, a C true-pthread probe placed a cpy handle
in TLS, observed a distinct oldified target, and observed the foreign release
hook when the source cleanup decref ran. The strict form returned rc=6 because
the TLS value remained the original handle, matching its fail-closed supported
tag set. The C probe intentionally did not destroy the target, so it establishes
premature source release but does not claim a dynamic second-release result.

Evidence and source identities are in
`docs/goal/evidence/2026-08-23-gc4-a3b-gc3-tls-cleanup-after-unlock.md` and
`docs/goal/evidence/2026-08-23-gc3-cpy-handle-oldify-ownership-intake.md`.

## Proposals

- No.1 Remove cpy handles from GC3 copy eligibility and oldify them in place
  [pending].
- No.2 Add an explicit foreign retain or exclusive ownership-transfer hook
  before source retirement [pending].

## Open Questions

- Does any libpython bridge require physical GC3 movement of cpy handles, or
  is in-place oldification the correct fail-closed production contract?
- Can a retain hook be generic across every foreign-handle provider without
  reintroducing libpython ownership into no-libpython mode?
- Which existing fallback/bridge gates exercise a live cpy handle under GC3?

## Final Status

Active. No production change has been made. The task board row
`GC-P0-GC3-CPY-HANDLE-OLDIFY-OWNERSHIP` owns the next implementation decision
and focused regression.

## Update 2026-08-23 — exclusive transfer [CONFIRMED]

Proposal No.2 is accepted as an exclusive-transfer contract, not as a foreign
retain hook. C and strict pcc-Python now provide
`pcc_cpy_handle_move_owned_ref(from, to)`. GC3 calls it after preparing and
registering the bytewise target but before publishing forwarding; the install
failure branch reverses the move before freeing the unpublished target. The
source is therefore empty before its cleanup decref and the target is the sole
owner.

The initial spelling `py_cpy_handle_move_owned_ref` is [DENIED]. Strict
no-libpython intentionally classifies `py_cpy_*` calls as CPython bridge
operations and replaced the oldifier with a fail-closed stub containing
`py_exc_new`. The final `pcc_cpy_*` name describes the actual pcc-runtime
boundary and both direct strict closures pass without libpython.

Proposal No.1 (non-copyable/in-place oldification) was not implemented. A GC3
young handle may reside in a reusable minor-arena block; merely changing its
generation flags would not establish stable non-arena storage. Copy plus
exclusive transfer preserves the existing physical promotion contract without
inventing a retain operation.

C and strict true-pthread TLS probes now observe a distinct OLD target that
still returns the foreign pointer, zero releases during source cleanup, and
exactly one release after the final target drop. The callback also joins a
graph-lock contender successfully. Source/ABI/archive ownership, direct strict
closure, and baseline deallocation neighbors pass. Full commands, mode limits,
timings and hashes are recorded in
`docs/goal/evidence/2026-08-23-gc3-cpy-handle-oldify-ownership.md`.

## Final Status 2026-08-23

Resolved for Backend 3 in C and strict pcc-Python. No real CPython-extension,
libpython bridge, or Backend-4 movement claim is made.
