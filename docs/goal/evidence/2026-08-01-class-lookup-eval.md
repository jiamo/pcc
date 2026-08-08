# py_class_lookup: measured, hashmap deferred

Date: 2026-08-01

Task: `PERF-P2-LOOKUP`

## The premise

`py_class_lookup()` walks the MRO and scans each class's method table
linearly. Its own comment promises "a future phase can swap to a hashmap",
and the row notes that stage2 compilation is class-dense, so the win should
be directly measurable. That is the claim this slice tested before writing a
hashmap into a bootstrap-critical runtime with a C/pcc-Python mirror and a
layout contract.

## Measurement 1 — is a hashmap faster per lookup? Yes.

Microbenchmark of the exact inner shape (MRO walk x linear scan with the
pointer-equality shortcut the real code has), worst-case target (last method
of the first class), 20M lookups, `cc -O2`:

```text
mro=1 methods= 4   linear 0.173s   hash 0.047s   hash/linear = 0.27x
mro=1 methods= 8   linear 0.284s   hash 0.055s   0.20x
mro=1 methods=12   linear 0.436s   hash 0.049s   0.11x
mro=2 methods= 4   linear 0.159s   hash 0.047s   0.30x
mro=3 methods=12   linear 0.424s   hash 0.047s   0.11x
```

Per lookup, the hash probe is 3-9x faster and, unlike the scan, flat in
method count.

## Measurement 2 — how often is it actually called? Almost never.

Static call sites in the compiler's own emitted closure
(`pcc/__main__.py` multi-file IR, 286 MB):

```text
call @py_class_lookup                        0
direct calls to user mixin methods        8897
py_obj_getattr / py_instance_getattr     10886
```

Dynamic count, from a temporary counter compiled into the runtime and run on
a class-heavy program (3-deep MRO, 200 instances x 200 iterations, ~40,000
Python-level method calls):

```text
PROBE_LOOKUP calls=403 cmps=1408 ptrhits=0
```

403 lookups for ~40,000 method calls, averaging **3.5 comparisons each**.
Method dispatch on statically known classes does not reach this function at
all — the frontend emits direct calls — so the linear scan runs on class
construction and the residual dynamic paths, not in the hot loop the row
assumed.

## Verdict: hashmap deferred, not rejected

A 3-9x win on a function that performs 1408 comparisons across an entire
class-heavy run is worth microseconds. Against that: the change touches
`PyClassObject` (its layout contract is pinned by
`tests/python/test_runtime_layout_contract.py`), must land identically in the
pcc-Python port mirror, and adds an allocation and a hash table per class to
a structure the runtime currently keeps trivially copyable.

What would justify it, recorded so the next attempt starts from evidence: a
workload where the probe shows lookups in the millions (heavy `getattr`,
`__getattr__` chains, or duck-typed dispatch); or a class with a large method
table (the measured MRO depth was 3 and method counts single-digit); or the
value-model work introducing dynamic attribute lookup on hot paths.

The function's own comment is now the only remaining promise of a hashmap;
this evidence file is the reason it has not been kept.

## Not proven

- The measured workload is one class-heavy program, not stage2 itself.
  Stage2's own class density is served by direct calls (0 static call sites
  in the closure), which is the stronger signal here.
- The temporary counter was removed after measurement; the numbers above are
  from that instrumented build, not from the shipped runtime.
