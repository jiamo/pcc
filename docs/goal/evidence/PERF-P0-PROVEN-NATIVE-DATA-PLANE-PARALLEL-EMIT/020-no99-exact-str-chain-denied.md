# No.99 exact-string concat-chain `[DENIED]`

## Candidate and correctness

No.99 fused maximal 3--8-leaf `StrType` addition trees through one bounded
`py_str_concat_n8` ABI.  It evaluated leaves left-to-right, pinned every live
native operand while later leaves executed, mirrored the runtime helper in C
and pcc-Python, copied moving-GC inputs to raw temporary storage before the
output allocation, and left Dyn/class/two-leaf/unsupported trees on the old
path.

The source-frozen candidate pcc1 is
`build/no99-exact-str-stage1-candidate-315-v1/pcc1`, SHA-256
`94eebf9b921cb5b103938e8bd23abc71a43cf98901c995bdf543edd0ab66654b`.
Its runtime archive is
`e8ba20ee18209c2979a199b8111c0ebc23a24e90b9bb8a3f5bb12889bc24e495`.
Stage1 completed in 264.15 seconds, retired 177.804B instructions, reached
4.565GB max RSS / 1.316GB peak footprint, linked only libSystem, and passed
its publication smoke.

Focused evidence before the build:

- exact 3/8-leaf IR shape, two-leaf/Dyn fallback, left-to-right runtime,
  Unicode/empty strings, runtime mirror and the two existing nested-string
  ownership nodes passed;
- all four changed Python modules passed strict no-libpython/self single-file
  closure from out-of-package copies;
- the broader focused packet reached nine relevant passes and then stopped at
  the pre-existing stale text assertion in
  `test_split_string_accessors_allocate_strings_through_gc`, which expects
  literal `40`/`4` although the baseline source already uses ABI constants;
- item311 emitted exact assembly SHA-256
  `ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`.

## Prefilter and frontend measurement

The standalone five-part runtime micro initially justified the build.  Three
alternating pairs produced output `25` in every run:

```text
old nested chain     1.43 / 1.44 / 1.43 s   ~23.150B instructions
bounded fused call   1.20 / 1.22 / 1.20 s   ~19.353B instructions
                     about 1.19x wall, -16.4% instructions, RSS flat
```

That upper bound did not transfer to the real No.89 `pcc.cli_bootstrap`
frontend worker.  One balanced B/C pair, after a candidate warmup:

```text
metric                     baseline             candidate       B/C or C/B
wall                       15.94 s              15.77 s          1.01078
user + sys                 15.79 s              15.71 s          1.00509
instructions               209.932254B          205.129611B      0.97712 C/B
time max RSS               2.427994GB           2.427322GB       0.99972 C/B
sampled tree RSS           2.318991GB           2.428535GB       1.04724 C/B
IR bytes                   19,279,474           19,276,697
```

The pre-registration allowed stopping after one stable pair below 1.08; the
acceptance bar was 1.15 wall/CPU, candidate instructions at most 0.90x, and
memory at most 1.02x.  The candidate misses every throughput bar and the
sampled tree-RSS bar.  A second candidate execution was already available from
the warmup; both candidate IRs are byte-identical at
`156a7e2fc9aa3480dd1d9a930815de5c443733f4127d6d123c385ff46a77452a`.

Static call counts explain why the allocation-type upper bound did not belong
to concat chains alone:

```text
                                      baseline   candidate
py_str_concat references                1,257       1,006
py_str_concat_n8 references                 0          85
pcc_gc_pin references                  10,904      10,571
pcc_gc_unpin references                29,425      29,452
pcc_gc_release references              19,463      19,595
```

The optimization removed 251 binary concat calls, but the real worker's
short-string population is mainly two-part construction, conversion, slicing,
names and other producers.  Fusing the finite chain family therefore retires
only 2.3% of instructions.  Additional fixed arities, ownership hand-tuning or
another concat cache cannot produce the missing 15% worker threshold.

## Verdict and rollback

`[DENIED]`.  No second/third frontend pair, Stage2, Stage3 or GC1--4 gate ran.
All six production files and both modified test files were restored by forward
patch; the production files compare byte-for-byte with the accepted No.89
snapshot.  The restored focused packet passed 5/5 in 29.56 seconds and rebuilt
the runtime archive from the restored source hash.  No candidate ABI or test
remains in production source.

The retained result is the owner boundary: short strings are a real 68.55% of
allocation requests / 77.21% of requested bytes in the diagnostic window, but
concat-chain prefixes are not that family.  A successor must keep compiler
text in a non-object value/arena projection across multiple producers and
consumers; a local concat/render/cache proposal is forbidden by this result and
the earlier IRBuilder denials.

