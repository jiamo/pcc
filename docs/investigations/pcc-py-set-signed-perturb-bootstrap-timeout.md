# Investigation: pcc-py set lookup signed perturb bootstrap timeout

## Status

fixed in the current working tree.

The immediate symptom was a stage2 bootstrap compile that no longer looked like
GC pressure or heap corruption. Sampling showed the compiled `pcc1` process
spending effectively all CPU time in:

```text
user_py_set__lookup_slot
```

The root cause was a runtime parity bug between the C set implementation and
the pcc-Python port.

## Context

This happened while compiling the Python frontend under the pcc-py runtime:

```bash
env PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  <pcc1> --verbose --backend self --python-libpython off \
  pcc/__main__.py -o <pcc2>
```

After earlier fixes moved the run past GC tracking and Layer1 walker
corruption, the run reached `codegen[pcc.py_frontend.codegen.layer1]` and then
appeared to stall. The process stayed CPU-bound instead of crashing.

## Symptom

Sampling changed the diagnosis:

- not dominated by `py_gc_track`
- not dominated by `pcc_gc_note_object_freeing`
- not dominated by traceback frame allocation
- dominated by set probing in `user_py_set__lookup_slot`

This matters because the stage2 compiler is exercising the pcc-Python runtime,
not CPython's container implementation. A small semantic gap in a runtime
container becomes a full compiler hang when the compiler itself uses that
container shape in hot paths.

## Root Cause

The C runtime uses unsigned perturb probing for sets:

```c
uint64_t perturb = (uint64_t)hash;
...
perturb >>= 5;
```

The pcc-Python port used signed integer perturb:

```python
perturb: int = hash_val
...
perturb = perturb >> 5
```

Today pcc-Python integers lower to signed `i64` arithmetic. For negative string
hashes, `>>` is an arithmetic right shift, so the high sign bit is preserved.
For example, a negative `perturb` can remain negative forever instead of
converging to zero.

That breaks the open-addressing probe invariant. The probe sequence:

```python
j = (j * 5 + perturb + 1) & mask
```

can cycle over a small subset of slots and never reach the empty slot that
would terminate the lookup. In stage2 this presented as a 100% CPU loop inside
set lookup.

## Why Testing Missed It

Yes, the tests were insufficient.

The gap was specifically "C runtime and pcc-Python runtime are intended to be
equivalent, but only one side had this signedness behavior covered".

Missing coverage:

- `dict` had an IR-level regression for masking signed hash perturb; `set` did
  not.
- existing set stress tests mostly used integer keys, where hashes are usually
  non-negative and do not exercise the arithmetic-shift bug.
- C runtime tests validate the native C implementation, but they do not prove
  that the pcc-Python replacement has copied unsigned arithmetic semantics.
- no probe-termination defense existed in the pcc-Python set lookup, so one
  parity mistake became an unbounded loop.
- bootstrap timeouts only tell us "stage2 is stuck"; without sampling they do
  not identify which runtime primitive is spinning.

The failure was therefore not that "runtime set lookup must be slow". It was
that the Python port encoded an unsigned C algorithm with signed Python
operators and the test suite did not require the two implementations to stay
equivalent.

## Fix

`pcc/py_runtime/py/py_set.py` now masks perturb before the shift loop:

```python
perturb: int = hash_val & 9223372036854775807
```

It also adds a probe-count cap:

```python
probes = probes + 1
if probes > capacity:
    ...
```

The mask is the currently available pcc-Python-friendly representation of the
C-side unsigned perturb rule. It is not a general replacement for native
`uint64_t` arithmetic, but it restores the required termination property for
negative hashes in the current runtime model.

The probe cap is a defensive guard. It should not normally fire, but it turns a
future table-invariant bug into a bounded failure instead of a compiler hang.

## Regression Tests

New coverage was added at both layers:

- `tests/test_runtime_substrate_spike.py::test_pcc_python_set_lookup_masks_signed_hash_perturb`
  checks that compiled `py_set._lookup_slot` contains the perturb mask.
- `tests/test_self_compile_container_stress.py::test_self_compile_set_string_negative_hash_stress`
  self-compiles and runs a set workload with string keys, which are the key
  shape that exposed the negative-hash bug.

Focused verification:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_runtime_substrate_spike.py::test_pcc_python_set_lookup_masks_signed_hash_perturb \
  tests/test_self_compile_container_stress.py::test_self_compile_set_string_negative_hash_stress \
  -q -n0
# 2 passed
```

The broader default failure group was later re-run with this regression included
and passed:

```bash
/opt/homebrew/bin/timeout 900s env -u LC_ALL uv run pytest \
  tests/test_fallback_baseline.py \
  tests/test_gc_abstraction_surface.py \
  tests/test_gc_codegen_write_barrier.py \
  tests/test_compile_cache.py \
  tests/test_gc_effectiveness.py \
  tests/test_cli_core_observability.py \
  tests/test_py_class_symbol_collisions.py \
  tests/test_runtime_substrate_spike.py::test_pcc_python_set_lookup_masks_signed_hash_perturb \
  tests/test_self_compile_container_stress.py::test_self_compile_set_string_negative_hash_stress \
  -q -n0
# 63 passed, 3 xfailed, 3 xpassed
```

## Follow-up Test Policy

For pcc-Python ports of C runtime algorithms, add parity tests for:

1. signed/unsigned arithmetic boundaries (`hash < 0`, high-bit values,
   overflow-shaped values)
2. loop termination invariants for open-addressing containers
3. tombstone reuse after deletion
4. growth and rehash behavior at high load factors
5. string-key and object-key equality paths, not only integer keys

The practical rule is: when a C runtime helper uses `uint*_t`, bit masks,
logical shifts, pointer identity, or sentinel values, the pcc-Python port needs
a targeted regression proving that the generated IR still has the same
semantic shape.

