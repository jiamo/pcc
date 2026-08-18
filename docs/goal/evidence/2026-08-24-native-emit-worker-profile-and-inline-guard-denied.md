# The native-emit worker profiled, and inlining a hot predicate is DENIED twice

Date: 2026-08-24

Rows: `PERF-P0-PCC1-BOOTSTRAP-BEATS-HOST` (route),
`PERF-P1-CLASS-NAME-COMPARE-SINGLE-PASS` (neighbour)

Status: one new profile of the phase nothing in this session had measured, plus
one `[DENIED]` candidate reverted by forward patch. No accepted change.

## Everything before this measured the wrong 20%

The mem2reg and `_strs_eq` slices were both measured with `--emit-llvm`, which
returns before the self backend. Measured on the same module and pcc1:

```text
full compile to a binary   86.4s wall   212.9s user   259% CPU (parallel)
frontend only (--emit-llvm) 15.0s wall    14.2s user    97% CPU (serial)
```

So the native emit + link phase is **~93% of a full compile's CPU**, matching the
routed stage2 split (frontend 171.186s, native emit 516.717s, link 99.719s of
875.10s). Two notes for whoever profiles it next:

* `--python-library` with `-o <file>` fails immediately with an empty
  `PCC-PY-COMPILE-001` message on both host pcc and pcc1. Drop
  `--python-library` to compile through the backend.
* Sampling the coordinator pid attributes **83% of samples outside the image**.
  The emit workers are separate `pcc1` child processes, and they are
  short-lived (a worker sampled during a real stage2 had `etime 00:02`), so
  `scripts/pcc_profile.py <coordinator>` measures almost nothing. Find a busy
  child by `ps` and profile that pid.

## The emit worker's profile (current source, GC0)

650 self samples over 25s of one worker — thin, and treated as a pointer, not a
number to act on directly:

```text
   9.7%  pcc_gc_granule_is_object_start
   6.9%  py_capi_type_runtime__is_type_object
   5.8%  pcc_gc_store_root
   3.5%  pcc_gc_load_ptr
   2.6%  pcc_gc_unpin        2.3%  pcc_gc_pin
   2.3%  pcc_gc_frame_roots_disabled_fast
   1.8%  note_frame_leave_lifo   1.2% note_frame_leave
   1.2%  frame_enter_lifo        1.1% frame_enter
   1.7%  managed_pointer_find_slot   1.7% pointer_is_managed_no_lock
   1.1%  index_py_find_slot
```

Frame enter/leave bookkeeping is ~7.6% together. Provenance is ~15%. This is
the same "66.2% GC/refcount leaf tax" the routed investigation named, now
localized to the emit worker on current source.

`is_type_object` at 6.9% deserves a note because it looks like a regression and
is not one: it is a 24-way linear chain of `ptr_eq` against builtin type-object
globals. Update No.11's fix put the O(1) index probes *ahead* of it in
`_pointer_is_managed_no_lock`, and that ordering is still in place. The 6.9%
means the emit worker asks provenance about genuinely **unmanaged** pointers
often, and every such negative answer still walks the 24-way chain after all
the O(1) probes miss. Making the negative case cheap (e.g. a conservative
address-range prefilter over the 24 statics) is untried.

## `[DENIED]`: inlining `pcc_gc_frame_roots_disabled_fast`

All four frame entry points (`note_frame_enter`, `note_frame_enter_lifo`,
`note_frame_leave`, `note_frame_leave_lifo`) begin with

```python
    if pcc_gc_frame_roots_disabled_fast() != 0:
        return
```

Compiled code hits one of these on every frame enter and every frame leave, and
under the default backend the answer is "disabled", so the whole body was a call
out to a three-load predicate and back. The C mirror never paid it — its version
is `static` and the C compiler inlines it — so this looked like the same
port-vs-C cost divergence that `_strs_eq` turned out to be.

Inlined the three loads at all four sites. Measured:

```text
frontend A/B, 6 pairs    1.0113x   4/6 favouring   (one 17.63s contended base)
frontend A/B, 10 pairs   1.0059x   7/10 favouring  ratios 0.9584 .. 1.0087
full compile, 2 pairs    wall 0.9855 / 1.0334
                         CPU  1.0259 / 1.0237   <-- candidate uses MORE CPU
```

The frontend result is noise. The full-compile **CPU** numbers are the decisive
ones — CPU is the low-noise metric for a 259%-CPU parallel workload — and both
pairs agree that the candidate burns about **2.5% more** of it. Inlining three
global loads into four hot entry points costs more in code size and register
pressure at those sites than the saved call is worth.

`[DENIED]`, reverted by forward patch (`git diff` clean). Emitted output was
identical throughout.

## The pattern, so it stops being re-derived

This is the **second** denial of the same shape today. Both were reasoned from
the same correct premise — a call under this compiler's cost model carries frame
and root bookkeeping — and both measured negative:

```text
first-byte prefilter in _class_lookup_in_mro / _lookup_field_index   -0.6%
inlining pcc_gc_frame_roots_disabled_fast at 4 frame entry points    -2.5% CPU
```

What *did* work in the same session was removing work outright, not relocating
it: the mem2reg loop stopped visiting 152 of every 153 candidate pairs (1.71x),
`_strs_eq` stopped walking each name three times (1.0200x), and the relocation
read barrier stopped asking a provenance question it could not need (1.345x on
its workload).

**Under pcc's self backend, hoisting a small predicate into a hot call site is
not a reliable win and has now measured negative twice. Removing redundant work
is. Do not retry the inline-the-guard shape without new evidence.**

## Nonclaims

One machine, GC0, one module. The emit-worker profile is 650 samples from a
single worker and is a direction, not a measurement. No stage1/stage2 pair, no
module98 A/B, no fixed point, no five-GC matrix was run for this entry, and no
change was accepted from it.
