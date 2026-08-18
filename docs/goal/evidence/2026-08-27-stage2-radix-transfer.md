# Stage2 transfer of the granule span radix

Date: 2026-08-27  
Task: `PERF-P0-STAGE2-COLD-CACHE-REGRESSION`

## Retraction — the full-stage transfer below used a stale optimized runtime archive

The 793.029 s Stage2 and the 1.44x item result below are valid executions but
are **not valid radix attribution**.  The 64 KiB radix Stage1 profile spent only
1.288 s in `ensure_runtime`, so it reused an existing runtime archive.  After
changing radix-node size to the exact 32 KiB payload, Stage1 spent 23.798 s in
`ensure_runtime` and rebuilt the archive under the bootstrap's declared
`PCC_PYTHON_IR_PASSES=off` mode.

Disassembly makes the mismatch unambiguous: the reused archive's
`pcc_gc_granule_is_object_start` is 276 bytes with register-resident values;
the current rebuilt archive's same query is 436 bytes with a 112-byte stack
frame.  The query source differs only outside that function in the radix-node
allocation literal.

Under the current matched bootstrap configuration, module98 measures only
about 1.05x with instructions about 0.955x, and therefore misses the parent
granule-map row's pre-registered 1.10x bar.  The current-source full cold
Stage2 baseline remains the prior valid 1076.793 s; no replacement full-stage
number is claimed here.

The same-binary heavy-object A/B in
`2026-08-26-granule-span-radix.md` remains valid: both query modes used one
runtime archive and measured radix at 1.038x.  This retraction affects only the
cross-pcc1/full-stage transfer claims below, which are retained as historical
evidence of the cache-control failure.

## Exact critical-item transfer

Ordinary-bootstrap pcc1 before and after the accepted radix compiled the same
frozen `string_method_lowering` item 423.  Three alternating pairs produced
byte-identical assembly:

```text
median wall speedup             1.44068x
median CPU speedup              1.45108x
candidate/base instructions     0.75175
candidate/base cycles           0.68500
candidate/base footprint        1.00239
```

Manifest: `build/stage2-radix-item423-ab-v1/manifest.json`.

## Full cold Stage2

Current ordinary pcc1
`0b427d498c737c5284014960157590bd17f8ab56e1e297d5239e0254b4968787`
used a verified-empty isolated cache.  Stage2 completed in 793.029 s and
produced pcc2
`7a1ae242ac4e8447ef24ee0798a90aa1f7ab799935d5d30472192ea5896206ca`.

Compared with the immediately preceding No.62 source, work counts match
exactly: 212 frontend modules, 464 native objects, seven oversized and 457
safe.

| phase | No.62 | radix | delta |
|---|---:|---:|---:|
| Stage2 wall | 1076.793 s | 793.029 s | -283.764 s |
| compiler profile | 1072.209 s | 788.585 s | -283.624 s |
| native emit | 711.927 s | 439.258 s | -272.669 s |
| safe workers | 600.185 s | 350.677 s | -249.508 s |
| oversized workers | 91.261 s | 73.270 s | -17.991 s |
| frontend codegen | 168.235 s | 150.760 s | -17.475 s |
| link driver | 132.223 s | 143.179 s | +10.956 s |

Against the original isolated cold baseline 1356.194 s, cumulative wall
improvement is 563.165 s / 41.53%.  Stage2 remains 193.029 s above the 600 s
task threshold.

Stage3 completed in 310.587 s and pcc2/pcc3 were byte-identical.

## Post-radix critical path and denied local proposal

A complete 152-item medium replay moved the critical completion to
`assignment_statement_lowering` item 302.  Its pcc1 profile is function emit
51.94%, stack-map plans 16.59%, stack-map render 13.06%, adjacent-memory pass
9.89%, and regalloc 6.79%.  The fused granule leaf is down to 8.49%.

Proposal No.65 tried a disabled-mode barrier-only fast path for the
adjacent-memory pass.  Host evidence suggested saved parsing, but pcc1 A/B
showed the `for` iterator was much more expensive: wall 0.75015x, CPU 0.73306x,
instructions 1.22572x and cycles 1.37373x.  Assembly remained byte-identical;
the candidate was removed before any Stage2 run.

## Open boundary

The current 793.029 s build is correct and much faster but not complete.  The
remaining local emit/stack-map families have measured denials.  Continue the
parent `ARCH-P0-PROVENANCE-GRANULE-MAP` acceptance/removal work before another
cold Stage2: it owns retiring the now-cold hash authority, module98 evidence,
same-source Stage1/Stage2 comparison and final fixed-point/five-GC gates.
