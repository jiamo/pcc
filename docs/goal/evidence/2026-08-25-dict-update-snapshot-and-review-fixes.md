# dict.update snapshot and three review fixes — 2026-08-25

## Claim

Three defects found by review in my own earlier work are fixed, and a fourth in
a tool I added is fixed.  `GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT` was
**reopened** from `DONE_STRONG` because my "every dict/set mutation path is
covered" claim was false.

Not Stage1, Stage2, fixed-point, five-GC, or performance evidence.

## 1. A raising `__eq__` no longer mutates the dict  (was P0)

`py_obj_eq` returns 0 and leaves the exception pending when a user comparison
raises.  Neither mirror checked `py_err_occurred()` afterwards, so the probe
read that 0 as "not equal", kept probing, and in set mode **inserted** — the
statement raised *and* the dict grew.

Reproduced before fixing:

```text
dict mutated by a failed set: len=2
```

Both mirrors now abort without committing when the comparison raised.
Regression: `test_dict_raising_equality_leaves_the_dict_unmodified`, which
asserts the exception propagates, the length is unchanged, the original mapping
is intact, and scheduler roots are balanced.  `2 passed` on both tiers.

## 2. `py_dict_update` no longer holds the source across callbacks  (was P0)

It cached `PyDictObject *s` and `entries_used`, then re-read `s->entries` on
every iteration while each `py_dict_set` ran destination hash/equality.  Both
mirrors now use the snapshot-plus-roots design `py_set_update` already had:
root destination and source, snapshot key/value pairs into a rooted list before
any destination callback, then root each pair while inserting and reload every
moving root after the call.

### Demonstrated, after two failed attempts

The first two probes passed both before and after the fix.  Relocating the
source from a destination callback was not enough, and neither was deleting a
source entry — `py_dict_del` only tombstones, so the cached `entries_used`
stays valid, and a relocated source's old copy **stays readable until it is
retired**.

Forcing retirement inside the callback is what exposes it:

```c
pcc_gc_relocate_copy(src, 56);
pcc_gc_reset_relocation_set();
pcc_gc_backend4_remap_and_retire_stopped_world();   /* twice */
```

Differential against the current C archive, one variable — the old
`py_dict_update` object linked ahead of the archive so it wins:

```text
with fix    relocations=1 eq_calls=16 dst_len=5 src_len=4
            k[0..3] -> 100,101,102,103 (all correct)      rc=0
pre-fix     rc=139   (segmentation fault)
```

So the old implementation genuinely crashes once the source it cached is
relocated and retired mid-update.  This is a real P0, not a latent contract
issue, and the review's severity was right.

### A trap this hit on the way

The first differential run reported `rc=139` for **both** arms, which would have
looked like "the fix changes nothing".  The cause was the recorded stale-archive
trap: I linked against an archive built at `01:31` while `py_dict.c` was from
`10:14`, nearly nine hours older than the source under test.  Re-running against
the current archive produced the clean split above.  Check the archive's mtime
against the edited source before believing any standalone-link result.

## 3. The C hash-error path leaked `value_handle`  (was P1)

My earlier regex patch added the `value_handle` release to seven return sites
but skipped those before the `restart` label, on the assumption that
`value_handle` was not yet registered there.  That assumption was wrong: the
`py_obj_hash` error check sits *after* all three prepares.  A raising
`__hash__` in `d[key] = heap_value` therefore leaked a scheduler root and kept
the value alive under GC3/GC4.  The strict mirror was already correct.

Fixed, and the whole rooted op was rescanned for any other site releasing two
of the three handles — none.

## 4. `raise_owner_audit.py` could have produced a double free  (was P1)

The tool decided `released` from a two-line lookahead, so a cleanup sequence
that releases further down would be classified fresh-and-unreleased, converted
to `py_raise_owned`, and then double free on the later `py_decref`.

**Checked for real damage first.**  Every converted file was scanned for
`py_raise_owned(var)` followed within twelve lines by `py_decref(var)`:

```text
total: 0
```

So the completed sweep did not create a double free.  The tool now searches the
whole enclosing function (C: a `}` at column 0; Python: a `def`/`class`/
decorator at indent 0) and two cases were added to its contract test — the
reviewer's exact cleanup shape, and a release appearing in a *later* function,
which must not count.  Tool tests: `10 passed`.

## Gates

```text
dict raising equality (both tiers)                     2 passed
dict update snapshot (both tiers)                      2 passed
dict + set parity and dict/set substrate slice        51 passed
raise_owner_audit contract                            10 passed
task board                                       405 validated
```

## What the earlier green gates did not cover

The review is right that the previously reported greens said nothing about
raising equality, `dict.update` relocation, or hash-error root balance.  None of
those had a probe until now.  Two of the three now do; the `dict.update` one
is demonstrated by the pre-fix/post-fix differential.

## Nonclaims

- Finding 2's in-suite probe passes with the fix; the pre-fix failure is shown
  by the standalone differential above rather than by a test that can run both
  arms.
- The five-GC gates have not been run for any of these changes.
- No bootstrap, stage or fixed-point gate was run.
