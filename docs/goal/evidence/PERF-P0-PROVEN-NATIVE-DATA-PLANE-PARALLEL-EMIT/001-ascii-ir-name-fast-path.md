# ASCII IR name fast path

## Supported claim

The confident-only ASCII fast path in
`pcc/backend/self_backend_parse.py` is accepted for the current GC0 Darwin
AArch64 pcc1 emit path. Ordinary numeric, dot-numeric, plain SSA, and plain
global names bypass the pcc-Python regex engine. Quoted, Unicode, escaped,
empty, and invalid spellings still fall through to the unchanged anchored
regex and diagnostic path.

This slice does **not** prove whole-Stage2 improvement, Stage2 <= Stage1,
parallel emit, provenance elision, or GC1--4 transfer.

## Correctness and closure

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/c/test_self_backend.py -k 'name_decoder'
2 passed, 299 deselected in 0.13s

gtimeout 120s env -u LC_ALL uv run pcc --backend self \
  --python-libpython=off --ir-scaffold=on --python-library \
  --emit-llvm=/tmp/no72-self-backend-parse.ll \
  pcc/backend/self_backend_parse.py
rc=0
```

The focused regression proves that confident names do not invoke either regex
object and that quoted, Unicode, and invalid cases preserve the old fallback.

## Native runtime prefilter

The first attempted driver is rejected as evidence: it compiled with rc=0 but
its produced binary failed at runtime with
`ImportError: pcc.backend.self_backend_parse`. It is excluded below.

Replacement drivers embedded the exact baseline/candidate helper bodies and
were compiled by one accepted pcc1 and frozen runtime. Both produced
`11900000` and linked only libSystem:

```text
metric                  baseline          candidate
wall                      15.62 s             2.73 s
instructions       230,662,624,099     38,055,741,838
peak footprint          325,681,776        254,181,928
output                    11900000           11900000
```

The 5.72x wall result cleared the pre-registered 1.20x rebuild prefilter.
Artifacts are under `build/name-decode-bench-{baseline,candidate}-v2/` and
`build/name-decode-bench-runtime-v2/`.

## Receipt-bound Stage1 compilers

Both arms were copied from one current-source snapshot, made read-only, and
checked over all 1,137 bootstrap-source files. Their manifests differ only at
`pcc/backend/self_backend_parse.py`; the exact source diff is
`build/no72-name-decode-ab-v1/source.diff`.

```text
                         baseline                           candidate
source     b36b1953c613e1bfc2a28de6e0f8ded9...  00f912fc97ad19257a96cf73c5f1ea5bb...
parser     2637ec490ff863449da1d5b8c68a0954b...  809341afa02de5d5c42c6c64d90e6acb...
pcc1       f7aee392f2517ceb4fcfc1d5348088d8...  ebde05bbdf2bf0caf47e1f15421de7d5...
runtime    624e1de9d6686744906ed3cd0e22cb8de...  identical
host       CPython 3.15.0rc1 / cpython-315       identical
mode       GC0, self, no-libpython, libSystem    identical
```

Stage1 build walls were 276.95s and 274.56s. Those single sequential builds
are construction receipts, not a claimed Stage1 speedup.

## Item311 alternating A/B

After one unmeasured warmup per arm, frozen item311
(`pcc.py_frontend.codegen.call_expression_lowering`, 5,108,635 bytes, input
SHA-256 `76af6689...`) ran in B/C, C/B, B/C order:

```text
pair   baseline wall/cpu   candidate wall/cpu   wall B/C   cpu B/C   instr C/B   footprint C/B
1      15.87 / 15.83       14.50 / 14.47          1.0945    1.0940      0.90069        0.98720
2      16.56 / 16.49       14.51 / 14.48          1.1413    1.1388      0.90085        0.98721
3      16.28 / 16.25       17.24 / 15.63          0.9443    1.0397      0.90103        0.98715
median                                              1.0945    1.0940      0.90085        0.98720
```

Pair 3 contains about 1.6s of candidate off-CPU delay; it remains in the
result. The pre-registered criterion was median wall and CPU >=1.05x,
instructions improving, footprint <=1.02x, and byte-identical assembly. All
six measured outputs and both warmups have assembly SHA-256
`ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`.
The candidate therefore passes the registered acceptance boundary.

Raw manifests are under
`build/no72-item311-pair{1,2,3}-{baseline,candidate}-v1/`.
