# The compiled mem2reg pass scanned every alloca candidate for every line

Date: 2026-08-24

Rows: `PERF-P1-MEM2REG-CANDIDATE-SCAN-QUADRATIC`,
`PERF-P0-PCC1-BOOTSTRAP-BEATS-HOST` (route)

Status: accepted compiler-source slice with a single-variable **pcc1** A/B and
byte-identical output. Frontend-only measurement; no complete stage1/stage2
timing and no fixed point.

## How this was found

Not from the leaf. A current-source pcc1 was built and profiled with
`scripts/pcc_profile.py` while it compiled a real 5,924-line module
(`pcc/py_frontend/type_infer.py`, frontend only). The flat profile named
`pcc_gc_granule_is_object_start` at **18.2%** -- four times the next symbol.
Optimizing that leaf is the trap `scripts/pcc_flamegraph.py` documents, so the
same run was re-profiled for caller attribution:

```text
callers of pcc_gc_granule_is_object_start   (2458 attributed samples)
   790  <- compiled_default_passes._rewrite_functions
   503  <- compiled_default_passes._mem2reg_function
    94  <- llvm_capi.ir._irbuilder_call_from_args_list
    43  <- pcc_gc_free_object_memory
```

Two functions in one file were **53%** of the provenance-question cost. The
intermediate `pcc_gc_pointer_is_managed` / `_ptr_can_have_header` frames are
absent because the tail-call pass elided them; the attributed caller is the
compiled pcc source function.

## The defect

`_mem2reg_function`'s classification loop visited **every alloca candidate for
every line**:

```python
    for index, line in enumerate(lines):
        store = _parse_store(line)
        load = _parse_load(line)
        for name, candidate in candidates.items():
            if not candidate["safe"]:
                continue
            ...
            if not _contains_ssa_name(line, name):
                continue
```

Measured on the same real emitted module:

```text
262 functions, 252,453 lines
sum(lines x candidates) = 38,528,560   vs sum(lines) = 252,453   -> 152.6x
worst function: 41,519 lines x 407 candidates = 16,898,233 inner iterations
```

38.5 million inner iterations for 252k lines. Each one is at minimum a
`candidate["safe"]` dict lookup and a `_contains_ssa_name` substring scan --
and under a self-hosted pcc1 every one of those object touches is a
provenance-checked barrier. That is why the *leaf* looked like the problem.

This is the same failure family already recorded in this repository: a
text-key scan that is quadratic in a dimension nobody measured.

## The fix

Invert the loop. Only the candidates a line actually mentions can change
state, so tokenize the line's maximal `%name` tokens and look up those:

```python
        names = _ssa_names_in(line)
        if not names:
            continue
        store = _parse_store(line)
        load = _parse_load(line)
        for name in names:
            if name not in candidates:
                continue
            candidate = candidates[name]
```

Exactness rests on two facts:

* `_ssa_names_in` returns **exactly** the names for which
  `_contains_ssa_name(line, name)` is True. That helper accepts `%name` only
  when the next character is outside `_SSA_NAME_CHARS`, which is the same
  thing as the token being maximal -- so `%s1` does not answer for `s`, and the
  prefix families real IR is full of (`%s1` / `%s10` / `%s100`) stay distinct.
  The tokenizer is the one `_replace_ssa_names` already used.
* Candidates never interact: the loop body mutates only the candidate it is
  examining. Visiting them in the line's token order instead of insertion
  order therefore cannot change the outcome.

Lines with no `%` token now skip `_parse_store`/`_parse_load` entirely, which
the old shape called 252k times to no effect.

`name not in candidates` + subscript is deliberate rather than `dict.get`:
`dict.get` mis-lowers in self-compiled frontend code.

## Measured -- single-variable pcc1 A/B

Both arms are pcc1 binaries built from the same tree differing only in this
change (base `pcc1_cand`, candidate `pcc1_cand2`). Same input, same knobs
(`--backend self --python-libpython=off --ir-scaffold=on --python-library`),
one discarded warmup per arm, alternating pairs, emitted IR hashed every run:

```text
  1  base   25.86  cand   14.70  C/B 0.5683
  2  base   25.65  cand   14.67  C/B 0.5717
  3  base   25.38  cand   15.36  C/B 0.6054
  4  base   25.76  cand   15.84  C/B 0.6149
  5  base   25.30  cand   14.78  C/B 0.5844

base median 25.65s   cand median 14.78s
paired-median C/B 0.5844  =>  1.7113x    5/5 pairs favour the candidate
IR sha256 across all 10 compiles: 1b970f540922afa6  (one value, byte-identical)
```

For scale: the routed baseline records the pcc1 frontend at 24.42s against
2.47s for host CPython on the controlled frontend-only comparison, so this
closes roughly a third of that 9.89x gap on this input.

The host-side speedup of the pass alone is only 1.82x (1.30s -> 0.72s over the
same 262 functions), which is exactly why this had to be measured under pcc1:
on the host, `str.find` and dict lookup are C-fast, and the cost this removes
is the compiled barrier per object touch.

## Equivalence

Byte-equal against the historical every-candidate scan on three differently
shaped real modules, function by function:

```text
pyobj.ll      61 functions   mismatches 0
lift.ll      141 functions   mismatches 0
ti.ll        262 functions   mismatches 0
```

`tests/python/test_compiled_default_pass_tier.py` gains two regressions:

* `test_mem2reg_token_scan_matches_the_all_candidates_scan` keeps the
  historical loop as an executable oracle (`_mem2reg_all_candidates_reference`)
  and compares outputs on the existing fixtures plus generated wide functions
  up to 120 slots, including the prefix-family case, an alloca-address escape
  and a cross-block use.
* `test_ssa_name_tokens_agree_with_contains_ssa_name` pins the tokenizer
  against `_contains_ssa_name` directly, including `%%`, a bare `%`, and dotted
  names.

Both were written before the change and passed against the unchanged source
(the oracle was the identity then), so they are known to exercise the path.

## Gates

```text
tests/python/test_bootstrap_gate_baseline.py
  + test_fallback_baseline.py
  + test_ir_py_fallback_baseline.py
  + test_compiled_default_pass_tier.py         57 passed, 2 deselected 546.87s
pcc1 construction from current source          rc 0, 5:50 wall, runs a real
                                               class+method module correctly
```

## Next owner, stated so it is not re-derived

Re-profiling the candidate pcc1 on the same input moves the ranking:

```text
  13.7%  pcc_gc_granule_is_object_start   (was 18.2%)
   5.2%  py_class__strs_eq                (was 3.2%)
   3.8%  pcc_gc_store_root
   3.2%  py_class__class_lookup_in_mro    (was 1.9%)
   3.2%  pcc_gc_load_ptr
   2.9%  strlen                           (was 1.6%)
   2.1%  py_capi_type_runtime__is_type_object
```

`strs_eq` + `class_lookup_in_mro` + `strlen` + `is_type_object` is now
**13.4%**: MRO method lookup comparing method-name strings byte by byte, with
`strlen` per comparison. That is the existing `S-P0-MRO-METHOD-CACHE` row and
is the next thing to attack, not the granule leaf.

## Nonclaims

Frontend-only, one module, one machine, GC0, `--emit-llvm` (the native emit and
link phases are untouched and unmeasured here). The routed stage2 split is
frontend 171.186s of 875.10s, so no complete-stage ratio follows from this
number. No stage1/stage2 pair, no module98 A/B, no pcc2/pcc3 fixed point and no
five-GC matrix was run. The read-barrier gate accepted earlier today is present
in *both* arms, so nothing here measures it.
