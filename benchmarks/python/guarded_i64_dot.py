"""Pinned benchmark for PERF-P1-GUARDED-SPECIALIZATION-LOOP-PLAN.

The ``scalar`` mode calls the exact runtime slow-path owner directly.  The
``hit`` and ``miss`` modes call the production guarded intrinsic with distinct
and aliased buffers respectively.  Timing stays inside the executable so
process startup and compiler time cannot masquerade as a loop speedup.
"""

import sys
from time import perf_counter

import pcc
from pcc.extern import c_int64, c_ptr, c_void, extern


scalar_dot = extern(
    "py_i64_buffer_dot_scalar",
    (c_ptr, c_ptr, c_int64),
    c_ptr,
)
release = extern("py_decref", (c_ptr,), c_void)

left = pcc.i64_buffer[32](
    1, 2, 3, 4, 5, 6, 7, 8,
    9, 10, 11, 12, 13, 14, 15, 16,
    17, 18, 19, 20, 21, 22, 23, 24,
    25, 26, 27, 28, 29, 30, 31, 32,
)
right = pcc.i64_buffer[32](
    1, 2, 3, 4, 5, 6, 7, 8,
    9, 10, 11, 12, 13, 14, 15, 16,
    17, 18, 19, 20, 21, 22, 23, 24,
    25, 26, 27, 28, 29, 30, 31, 32,
)


def run(mode: str, rounds: int) -> float:
    index = 0
    last: int = 1
    started = perf_counter()
    if mode == "hit":
        while index < rounds:
            last = pcc.guarded_i64_dot(left, right)
            index = index + 1
    elif mode == "miss":
        while index < rounds:
            last = pcc.guarded_i64_dot(left, left)
            index = index + 1
    elif mode == "scalar":
        while index < rounds:
            raw = scalar_dot(left, right, 32)
            release(raw)
            index = index + 1
    else:
        raise ValueError("mode must be hit, miss, or scalar")
    elapsed = perf_counter() - started
    # Keep the final guarded result live without adding work inside the timed
    # loop.  Both valid buffers produce a positive dot product.
    if mode != "scalar" and last <= 0:
        raise RuntimeError("guarded dot produced an invalid result")
    return elapsed


print(run(sys.argv[1], int(sys.argv[2])))
