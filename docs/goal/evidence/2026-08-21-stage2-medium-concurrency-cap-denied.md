# Stage2 medium native-emit concurrency cap is denied

Date: 2026-08-21

Claim level: focused GC0, Darwin arm64, `backend=self`,
`python-libpython=off` native-emit experiment.  This is a frozen 32-item
medium-lane replay, not a complete stage2 timing result.

## Frozen experiment

- pcc1:
  `b2ba3969609dd0ba2b25b5c9d99cc480b606f451af57d01517001d0afda29d47`;
- runtime archive:
  `b42890eeca1e1387c7282be0297a9f7daadb1042c0efb65d533cf8b94375b3d0`;
- 32 unique medium-lane IR inputs, 40,553,830 input bytes, digest
  `ec352792ba7eb3b4ccfef983e515cd75fb5976433bdcae63ebc1e524ad1f2b29`;
- 112,170,303 expected assembly bytes;
- every item remained one fresh pcc worker process; the experiment changed a
  four-worker candidate cap versus the production-equivalent cap of eight;
- frontend/object caches and Python IR passes were disabled; both arms record
  the same pcc1, IR, runtime and selected environment fields.

The durable local manifest is
`build/stage2-medium-concurrency-ab-v2/manifest.json`, SHA256
`65cb49d6a1778e5bbae61f23cfa4a8867ba85a39b63410bd9cdf0b300874a14b`.
Four balanced four-item warmups were ordered `C/B/B/C`.  The first full pair
was ordered candidate then baseline.  The harness-recorded early rejection
line was candidate wall above 1.10x baseline or RSS above 0.80x baseline; the
task-board acceptance boundary required wall at most 1.03x, RSS at most 0.60x
and at most 8 GB, with compute ratios at most 1.05x.

The raw manifest does not preserve the complete per-arm argv, the harness
source identity or every ambient tool/environment receipt.  It therefore does
not independently prove a claim-grade single-variable acceptance experiment.
That limitation cannot turn a 52.8% slowdown into a candidate, so this artifact
is retained only as a negative focused slice.

## First full pair

| metric | candidate, cap 4 | baseline, cap 8 | candidate / baseline |
|---|---:|---:|---:|
| wall | 86.854 s | 56.836 s | **1.52814** |
| user + system | 323.61 s | 364.27 s | 0.88838 |
| instructions | 3.55584e12 | 3.56921e12 | 0.99625 |
| cycles | 1.05499e12 | 1.13961e12 | 0.92575 |
| synchronized process-tree RSS | 10,023,927,808 B | 13,601,423,360 B | **0.73698** |

A direct post-run rehash found all 32 candidate and baseline assembly files
byte-identical to each other and to the retained per-item oracle.  The manifest
stopped after this pair under its recorded early-stop rule rather than
continuing to collect a four-pair acceptance set after the wall failure.  A
post-run process check found no surviving pcc/bootstrap worker.

## Verdict

`[DENIED]`.  Four workers reduce the observed aggregate RSS by about 26.3%,
but still exceed 8 GB and miss the required 40% reduction.  More importantly,
they make this lane about 52.8% slower.  Lower aggregate CPU and cycles do not
justify that wall regression.  Production stays at eight workers; no source
change was made for this candidate and the remaining three pairs are
intentionally not run.

The next finite boundary is one complete batch1 medium-worker lifecycle under
the existing `scripts/pcc_flamegraph.py` CPU profiler plus synchronized worker
RSS.  It must attribute parse, precise-stackmap planning and render ownership
before an algorithmic or object-lifetime source change is proposed.  Do not
retry batch sizing or concurrency tuning from this result.

This evidence does not close complete unsampled stage2 performance,
stage2<=stage1, pcc2/pcc3 fixed point, or GC1--GC4 equality.
