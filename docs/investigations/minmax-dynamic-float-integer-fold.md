# Investigation: iterable min/max silently converts dynamic floats to integers

## Status
active

## Problem Description
The gateway performance summary reports latency_min_ms=latency_max_ms=0 for
100 ms requests even though the raw latency array contains 101.68–102.46 ms.
The normal raw-sample comparison is valid; the optional summary fails its
latency lower-bound validation. This predates the first-entry-frame proposal:
the control arm with that optimization disabled reproduces it.

## Repro
Compile benchmark_native.py with the current host compiler and run
`100 100 1` versus `100 100 1 --summary`. The raw array has positive ~102 ms
samples; native min(samples)/max(samples) return zero after dictionary pop.
The minimized regression is tests/python/test_native_min_max_dynamic_float.py.

## Test [CONFIRMED]
The application control printed 1000 requests, elapsed_ms=1048.63 and min/max
both zero. A raw 100-request replay printed elapsed_ms=104.10 and min/max
101.68/102.46 when calculated by CPython from the actual samples.

## Proposals
- No.1 route float/unknown element iterables to object comparisons [pending]

## No.1 route float/unknown element iterables to object comparisons
### Code Change
The iterable int accumulator is selected for DynType and float collections;
it converts each element through the integer ABI. Restrict that selection to
known integer element types, preserving existing static custom-__lt__/key
routes. Unknown iterable min/max results must also remain dynamically typed
instead of being inferred as int. Reuse the existing owned-object min/max
runtime helper for the no-key/no-default surface.

### pending
Do not repeat the denied attempts to change runtime custom-__lt__ dispatch in
sorted-min-max-custom-lt-not-used-no-libpython.md. This correction concerns
numeric float elements and an unjustified integer assumption in the frontend.

## Update: host application correction verified
The minimized test initially failed self-backend type verification: the
integer-fold result was passed to a double operand. After routing noninteger
list/tuple and dynamic inputs through the object helper, inference also had
to preserve the selected object instead of claiming an unboxed float result.
The correction now preserves dynamic float values, arithmetic on their
extrema, mixed numeric selections and selected-element identity.

The targeted test passed (2.69 s); 12 existing/new min/max cases covering
strings, defaults, custom __lt__, iterators, keys and variadic calls passed
(35.65 s). The original native benchmark now completes 1000 100-ms requests
with latency_min_ms=100.931 and latency_max_ms=106.637 instead of zero.

This is a frontend correction for the no-key/no-default object-iterable route.
It does not change the runtime comparison primitive or claim to close every
min/max protocol surface. Fresh pcc1/self-host qualification remains pending.
