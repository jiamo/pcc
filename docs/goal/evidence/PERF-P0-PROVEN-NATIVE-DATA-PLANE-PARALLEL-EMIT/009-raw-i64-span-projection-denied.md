# Raw-i64 packed-span projection denied

## Boundary

No.90 tested whether making No.89's parser span indices and counts physically
raw `pcc.i64`, together with the seven generic explicit-raw projection owners,
produced a large enough pcc1 emit win to retain the added compiler complexity.
It did not change ordinary Python `int`, retained parser objects, diagnostics,
or the cold structural fallback.

## Frozen candidate

- source files: 1,137 in both arms;
- source delta: exactly the parser plus type inference, assignment, ABI,
  coercion, binary-op, function-declaration and exact-int planning owners;
- candidate pcc1: `06df6da60fb59f3184cf047c8a317067f137d8d821a7232d896ae4608ae36d60`;
- host: CPython 3.15.0rc1;
- mode: self backend, no-libpython, GC0, libSystem-only;
- runtime archive: `624e1de9d6686744906ed3cd0e22cb8de1aa76d37aa10c9c6e4b986f94dcf29d`;
- Stage1: 275.13s wall / 1,135.88 CPU-s / 177.596B instructions /
  1,317,438,976-byte peak footprint.

The first v1 build is retained as a failed receipt: its runtime annotation
import enlarged the closure from 218 to 219. The v2 candidate used a
`TYPE_CHECKING`-only import and restored the exact 218-module closure.

## Correctness and shape evidence

Before the build, the eight span helpers had raw-i64 ABI/locals with none of
the forbidden tagged-int/object operations. Focused gates passed 152/152;
the frozen 2,678,736-call differential remained 2,624,882 packed hits, 53,854
explicit fallbacks and zero field mismatches; host item311 stayed
`ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`.

After rollback, every one of the eight production files is byte-identical to
the accepted No.89 source snapshot and the retained indexed-call-plane suite
passes 7/7.

## Receipt-bound paired result

```text
pair   wall B/C   CPU B/C   instructions C/B   footprint C/B   assembly
1       1.04056    1.03906       0.967777          0.999974      exact
2       1.03583    1.03591       0.968484          1.000000      exact
3       1.04059    1.04147       0.966892          0.999947      exact
median  1.04056    1.03906       0.967777          0.999974      exact
```

All six outputs have the exact registered assembly hash above. The candidate
reduces instructions without increasing footprint, but median wall and CPU
miss the pre-registered `>=1.05x` line.

## Verdict

`[DENIED]`. The threshold was not moved after observation. The candidate's
eight production changes and its two candidate-only tests were forward-removed;
accepted No.89 remains. This proves neither whole Stage2 performance nor a
fixed point and authorizes no GC1--4 run.

Key commands:

```bash
gtimeout 540s env -u LC_ALL uv run python scripts/run_pcc_stage1_build.py \
  --arm candidate --source-root /tmp/pcc-no90-raw-span.15AALy/candidate \
  --runtime-archive build/no89-call-span-stage1-candidate-315-v1/runtime-bundle/libpy_runtime_pcc_py.a \
  --output-dir build/no90-raw-span-stage1-candidate-315-v2 \
  --timeout 480 --smoke-timeout 30 --jobs 10 --self-backend-jobs 8 --gc-backend 0

gtimeout 90s env -u LC_ALL uv run python scripts/pcc_emit_rank.py \
  --compiler <accepted-No89-or-No90-pcc1> \
  --input-manifest build/stage2-current-object-inputs-no62-v1/manifest.json \
  --output-dir <unique-pair-dir> --lane all --jobs 1 --timeout 60 \
  --item-index 311

gtimeout 120s env -u LC_ALL uv run pytest \
  tests/python/test_self_backend_indexed_call_plane.py -q -x -n0
```
