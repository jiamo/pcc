# The denied managed-refcount ABI was the sole cause of the batch79 Stage2 crash

Date: 2026-08-27
Tasks: `PY-P0-SET-FROM-MAPPING-EMPTY`, `RT-P0-SET-DROPS-ELEMENT-ON-CYCLING-PROBE`,
`SELF-P0-STALE-MANAGED-SELF-OUTLIVES-ROOT`, `PERF-P0-STAGE2-COLD-CACHE-REGRESSION`
Claim level: root cause of a self-host Stage2 segfault, confirmed by byte-identity
to an already-green GC0 fixed point and by a substitution test. Darwin arm64.

## What was crashing

`build/bootstrap-batch79-v1` (and batch78) carried the wave-batch
`py_incref_managed`/`py_decref_managed` ABI (Update No.77) that skips the
raw-C-pointer provenance walk (`py_pointer_can_have_header`) and derefs the
object header directly. Its pcc1 segfaults ~3 s into Stage2:

```text
PCC_BOOTSTRAP_STAGE_FAILED stage=2 elapsed_ms=3368 rc=139
frame #0 pcc_allocator_take_small_object + 320   (x0 = 0x040000000111a5e9, a
         corrupted free-list pointer -- 0x04<<48 smeared into a real address)
frame #1 user_..._import_scan__source_import_discovery_text
```

The reproduction is deterministic: batch79 pcc1 segfaults every run compiling
`pcc/__main__.py`; two pcc1 generations (batch78, batch79) crash identically.

## Cause

A container slot in pcc-compiled code is NOT guaranteed to hold a managed
object -- dyn values can carry raw C pointers, exactly the case the provenance
walk exists to tolerate. Routing `py_list_get`/`py_dict`/`py_tuple`/`py_set`
retain/release through the provenance-skipping variants let a raw pointer reach
a header deref and a refcount write, corrupting adjacent allocator state. The
crash surfaces later in the allocator, not at the bad write. The ABI shipped
with NO tests (its gate test was written but never confirmed green -- the batch
crashed first).

## Fix: remove the ABI; restore the checked refcount path

All 10 runtime files reverted to the checked `py_incref`/`py_decref` at every
container call site, the split `*_prepare_ex`/`*_managed`/`*_checked` helpers
collapsed back, and the header declarations removed. Each replacement was
count-asserted per site (dict 4, list 6, set 3, tuple 3 py; dict 4, list 6,
set 3, tuple 2 C). Runtime source now equals HEAD; `rg cref_managed pcc/` is
empty. Archive rebuilt: `libpy_runtime_pcc_py.a.provenance.json` members=186
stale=0, `nm` shows 0 managed symbols.

## Proof the removal is correct

A Stage1 pcc1 rebuilt from the reverted source is **byte-identical** (raw
`cmp`) to `build/bootstrap-batch77-v1/pcc1`, which already produced a GREEN GC0
self-backend fixed point:

```text
batch77 chain: Stage2 873.541 s rc=0, Stage3 272.865 s rc=0,
               pcc2 == pcc3 byte-identical, 0 stale-managed rejections.
```

So the current working tree (HEAD runtime + the No.76 stackmap optimization,
which batch77 also carried) is in the exact state that produced that fixed
point. A substitution test: the reverted pcc1 compiling `pcc/__main__.py`
survives well past the 3 s crash point (killed by a 30 s watchdog while still
compiling), where batch79 died at 3 s.

## What this also resolves

`SELF-P0-STALE-MANAGED-SELF-OUTLIVES-ROOT` (19 self-outlives-root rejections)
was already closed by the committed probe-budget fix (`capacity + 16`, commit
47c9b7d7): batch77 shows 0 stale-managed rejections. The rejections were the
set dropping frame offsets; the ABI crash was a separate, later regression.

## Focused gates (all green, this turn)

```text
native set family (from_dict_keys, methods, update, inplace, symmetric_diff)  35 passed
test_precise_stackmap_abi + test_native_refcount_managed_variants (GC0/3/4)    35 passed
test_bootstrap_gate_baseline                                                    2 passed (self; llvm deselected)
test_fallback_baseline + test_ir_py_fallback_baseline                          40 passed
scripts/goal_state.py validate                                                  428 tasks OK
```

The container-retain gate was repurposed from the denied ABI's test into a
GC0/3/4 retain/release parity gate; its finalizer round-trip uses only
proven-balanced paths (append + subscript get + clear) after this turn found a
separate owned-method-call-result leak (list.pop, dict.get) filed as
`PY-P1-OWNED-METHOD-CALL-RESULT-LEAK`.

## Not proven / open

The five-GC bootstrap matrix (backends 1..4) has NOT been run on this source;
batch77 proves GC0 only. That matrix is the remaining required gate before
DONE_STRONG on the four rows above. `tests/bootstrap_gate_baseline.json` must
be re-established from a fresh green chain (it currently reflects Aug-8
binaries), never edited to match an output.
