# Packed call span lane confirmed

## Boundary and correctness

No.89 scans the original already-stripped IR instruction with integer spans,
materializes only final canonical fields, and writes directly into the existing
`IndexedCallPlane`.  Complex, Unicode, aggregate, nested and malformed shapes
still use the unchanged regex/structural parser.

```text
frozen call-bearing items             416
regex-accepted calls            2,678,736
packed-span hits                2,624,882
explicit fallback                  53,854
full-field mismatches                    0
focused gates                         150 passed
host item311 assembly          ff943e10... exact
```

The accepted-pcc1 driver produces identical `8950000` output and improves
wall 4.65 -> 3.23s, CPU 4.63 -> 3.17s and instructions 69.643B -> 43.036B.
Its high synthetic footprint was carried forward as a risk and tested on the
real workload.

## Frozen compiler

The 1,137-file manifests differ only in
`pcc/backend/self_backend_parse.py`.  Candidate pcc1 is `b0c6844f...`, built by
CPython 3.15.0rc1 with GC0 and runtime archive `624e1de9...`; it is
self/no-libpython and links only libSystem.

```text
pair   wall B/C   CPU B/C   instructions C/B   footprint C/B   assembly
1       1.07629    1.07802       0.936046          0.728948      exact
2       1.07721    1.07814       0.936471          0.728936      exact
3       1.07485    1.07502       0.936380          0.728988      exact
median  1.07629    1.07802       0.936380          0.728948      exact
```

All six measured assemblies equal
`ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`.
The real item311 footprint falls 27.1%, resolving the synthetic memory warning.
No complete Stage2 or GC transfer is claimed by this slice.
