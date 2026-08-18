# Class-name comparison walked the name three times; a first-byte prefilter is DENIED

Date: 2026-08-24

Rows: `PERF-P1-CLASS-NAME-COMPARE-SINGLE-PASS`, `S-P0-MRO-METHOD-CACHE`
(neighbour, **not** closed by this)

Status: one accepted 1.0200x runtime slice and one measured `[DENIED]`
candidate. Frontend-only pcc1 measurement. This does **not** clear the
`S-P0-MRO-METHOD-CACHE` row's pre-registered 3% floor and makes no claim
against it.

## Where this came from

After the mem2reg inversion, a re-profile of the candidate pcc1 compiling
`pcc/py_frontend/type_infer.py` (frontend, GC0) ranked MRO method lookup as the
next owner:

```text
   5.2%  py_class__strs_eq
   3.2%  py_class__class_lookup_in_mro
   2.9%  strlen
   2.1%  py_capi_type_runtime__is_type_object      => 13.4% together
```

`S-P0-MRO-METHOD-CACHE` already records a 2026-08-20 `[DENIED]` for a
1024-entry direct-mapped location cache (0.22% wall). That shape was not
retried. What follows is a different mechanism: make each comparison cheaper
rather than skip comparisons.

## Accepted: `_strs_eq` in one pass

`_strs_eq` compares raw C strings -- method and field names -- so there is no
cached length to consult. It was doing three walks:

```python
    n: int = strlen(a)
    if strlen(b) != n:
        return 0
    i: int = 0
    while i < n:
        if (load_i8(a, i) & 0xFF) != (load_i8(b, i) & 0xFF):
            return 0
        i = i + 1
    return 1
```

`strlen(a)`, `strlen(b)`, then the bounded byte loop: a matching name was read
about three times. The C mirror in `py_class.c` never did this -- it calls
`strcmp`, which is single-pass -- so this was a port-vs-C **cost** divergence,
not a semantic one.

Replaced with the ordinary single-pass compare, resuming at index 2 because
bytes 0 and 1 are already known equal and nonzero:

```python
    result: int = -1
    i: int = 2
    while result < 0:
        ca: int = load_i8(a, i) & 0xFF
        cb: int = load_i8(b, i) & 0xFF
        if ca != cb:
            result = 0
        elif ca == 0:
            result = 1
        else:
            i = i + 1
    return result
```

Exactly equivalent: unequal lengths are caught when one side reaches its NUL
while the other has not, and equal-length strings are decided at the first
differing byte or at the shared terminator. A done-flag loop rather than
`break`, matching the port subset.

### Measured (single-variable pcc1 A/B)

Base `pcc1_cand2`, candidate `pcc1_cand4`, same tree apart from this function,
same input and knobs, one discarded warmup per arm, alternating:

```text
  1  base 15.01  cand 14.48  C/B 0.9646
  2  base 14.93  cand 14.69  C/B 0.9839
  3  base 14.93  cand 14.61  C/B 0.9784
  4  base 14.95  cand 14.69  C/B 0.9824
  5  base 15.02  cand 14.79  C/B 0.9846
  6  base 15.13  cand 14.68  C/B 0.9705

base median 14.98s   cand median 14.68s
paired-median C/B 0.9804  =>  1.0200x    6/6 pairs favour the candidate
emitted IR sha256: one value across all 12 compiles, byte-identical
```

Profile after: `strs_eq` 5.2% -> 2.5%, and `strlen` left the top 18 entirely.

## `[DENIED]`: first-byte prefilter at the walk's call sites

The obvious companion was to reject candidates before paying for a call, since
under this compiler's cost model a call carries frame and root bookkeeping:

```python
                prefiltered: int = 0
                if name0 >= 0 and ptr_is_null(m_name) == 0:
                    if (load_i8(m_name, 0) & 0xFF) != name0:
                        prefiltered = 1
                if prefiltered == 0 and _strs_eq(m_name, name) != 0:
```

applied to both `_class_lookup_in_mro` and `_lookup_field_index`, with the
wanted name's first byte hoisted out of the loops.

Measured as a batch with the single-pass change (`pcc1_cand3`): **1.0136x**,
5/5 pairs, byte-identical IR. Then the single-pass change measured **alone**
(`pcc1_cand4`): **1.0200x**, 6/6. The prefilter therefore *cost* about 0.6%:
its inline load and compares per candidate are worth more than the call it
avoids, and `class_lookup_in_mro`'s own share barely moved (3.2% -> 3.0%)
because the work simply relocated into it.

`[DENIED]`, removed by forward patch. Do not retry a first-byte prefilter at
these call sites.

Part of the explanation for the field half: `_lookup_field_index` already sits
behind a one-entry `py_inst_field_cache_name0` cache, so most field lookups
never reach the walk and there was little left for a prefilter to reject.

## Relationship to `S-P0-MRO-METHOD-CACHE`

That row requires ">=3% lower median wall and summed CPU" for a
method-dispatch proposal. **1.0200x does not clear it**, and this slice is
therefore not a closure or a partial claim against that row. It is recorded
separately as a local redundancy removal: no new state, no side table, no
`PyClassObject` layout change, strictly less work than before.

The row's own denied shape (direct-mapped raw-address location cache) remains
denied and untouched.

## Gates

```text
tests/python/test_class_lookup_cache_runtime.py
  + test_class_name_compare_prefix_families.py
  + test_native_class_attr_subclass_override.py
  + test_native_unbound_class_method_call.py
  + test_py_class_constructor_attr_args.py
  + test_dataclasses_full.py                       21 passed 329.55s
five-backend finalizer/resurrection/weakref/trashcan, PCC_GC_BACKEND=0..4
  backend 0  44 passed 101.71s    backend 3  44 passed  94.35s
  backend 1  44 passed  97.62s    backend 4  44 passed  97.10s
  backend 2  44 passed  94.77s                    (exit 0)
```

## Regression

`tests/python/test_class_name_compare_prefix_families.py` compiles a
DEFAULT-mode program -- deliberately *not* `PCC_RUNTIME_CC=cc`, which would
link the C `strcmp` sources and exercise none of this -- whose classes carry
prefix-family method names (`a` / `ab` / `abc` / `abcd`, `p` / `pq` / `pr`,
`foo1` / `foo2`) and prefix-family instance fields (`x` / `xy` / `xyz` /
`xyzw`, `q` / `qr`), across a three-deep MRO with shadowing at two levels. A
length-blind or early-terminating compare resolves `ab` to `abc`'s method and
fails it. Written and passing before the change.

## Nonclaims

Frontend-only, one module, one machine, GC0, `--emit-llvm`. No stage1/stage2
pair, no module98 A/B, no fixed point, no five-GC matrix beyond the focused
gate above. The 13.4% profile share did not translate into 13.4% of wall time
and was never expected to; the 2.0% is the number with a control, and where
the profile and the paired measurement disagree the paired measurement is what
counts.
