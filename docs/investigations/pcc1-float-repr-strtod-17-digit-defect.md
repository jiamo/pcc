# `2**52` does not round-trip through pcc1's float formatting or parsing

## Why this matters

This is the root cause of the **five-GC bootstrap matrix failing on all five
backends**: `stage2 -> stage3` passes its build (`rc=0`) and then fails
`verify: cmp pcc2 pcc3`, i.e. the self-hosted fixed point.

## The chain, measured stage by stage

```
host pcc  ->  double 1.000000e+03          correct
pcc1      ->  double 0x408F400000000000    correct   (built by host pcc)
pcc2      ->  double 0x408F400000000004    WRONG     (built by pcc1)
pcc3      ->  double 0x408F400000000004    WRONG     (built by pcc2)
```

`pcc2` and `pcc3` agree about the wrong constant; the +100 byte difference comes
from their *surrounding binaries* being built from different constants —
`0x...004` needs three `movz/movk` instructions where `0x...000` needs two, and
that costs 4 bytes at each of 13 sites.

## The defect, minimised

```python
v = 4503599627370496.0        # exactly 2**52

                   CPython                    pcc1
str(v)             4503599627370496.0         4503599627370499.9   <- format wrong
float(str(v))      4503599627370496.0         4503599627370503.0   <- parse wrong too
round-trips        True                       False
int(v)             4503599627370496           4503599627370496     <- value is FINE
v == 2**52         True                       True                 <- value is FINE
```

The stored double is correct — `int()` and `==` both agree. **Only the decimal
formatting and the decimal parsing are wrong**, and they are wrong in different
directions, so the error compounds.

Boundary: `1000.0`, `0.5`, `2.0`, `1e16` and `123456789012345.0` all print
correctly. The failure appears at 17 significant digits.

## Why the shortest-repr loop cannot save itself

`py_float_repr_shortest` implements the correct algorithm — try 1..17
significant digits, stop when `strtod` of the formatted text equals the input:

```python
while significant <= 17 and found == 0:
    pcc_stdio_format_float_raw(probe, value, 101, significant - 1, 0, 0, 0)
    parsed = strtod_c(probe, null())
    if parsed == value:
        found = 1
    ...
```

That loop is only as good as `strtod_c`. With a parser that is itself inexact at
17 digits, the equality never holds, the loop runs out at `significant = 17`,
and the function emits whatever the formatter produced — the wrong digits. So
**both primitives have to be fixed; repairing only one leaves the loop unable to
verify its own answer.**

## Why it lands on this exact constant

`pcc/stdlib/_float_bits.py::_float64_to_bits` scales by
`4503599627370496.0` (`2**52`) to extract the mantissa. That literal is the one
value in the compiler's own source that trips this defect, so every float literal
compiled by a pcc-built compiler inherits the error — which is why all 13
functions that changed size are numeric/time formatters (`seconds`, `ms`,
`decimal`, `timeout`, `now_ms`, `phase_totals_ms`).

## Where to fix

```
pcc_stdio_format_float_raw   the %.17e formatter in the freestanding stdio layer
strtod_c                     the decimal->double parser it is checked against
```

Both are in the pcc-Python runtime / freestanding substrate. A correct
implementation needs exact big-integer intermediate arithmetic (Grisu/Ryū style
or plain bignum scaling); floating-point-only digit generation cannot be exact at
17 digits, which is the likely current shape.

## Correction: `strtod` is the system one; the defect is the formatter

`py_format_runtime.py` declares `strtod_c = extern("strtod", ...)` — the parse
side is libc's, which is exact. The digit generation is not:

```python
# freestanding_stdio.py::_format_float_raw
while normalized >= 10.0: normalized = normalized * 0.1
while normalized < 1.0:   normalized = normalized * 10.0
...
digit = float_to_i64(remainder)
remainder = (remainder - i64_to_float(digit)) * 10.0
```

Repeated multiplication by 10 in binary floating point rounds at every step, so
the low digits of a 17-digit result are wrong by construction. `float(str(v))`
disagreeing with `str(v)` in the probe is that error compounding, not a second
bug in the parser.

## A layering constraint that rules out the obvious fix

`freestanding_stdio.py` is annotated **entirely in `i64`** — 163 `: i64`
annotations, zero uses of arbitrary-precision integers. It is the freestanding
layer, which by the project's own layering rule must not depend on heap,
boxing or GC while those facilities are themselves being bootstrapped. Exact
decimal conversion needs big-integer intermediates, so **it cannot go here.**

`py_format_runtime.py`, one layer up, is annotated `: int` (205 of them) — the
semantic layer, where arbitrary precision is available. `pcc.unsafe` already
exposes `f64_bits(value) -> int`, so the raw bit pattern is reachable there.

## Exact algorithm, verified on the host

Extract `(mantissa, exp2)` from the bits, form the exact rational
`num/den = m * 2**exp2`, normalise to `[1, 10)` tracking the decimal exponent,
then generate digits by integer division and round half-up on the remainder.
Only integer arithmetic is involved, so it is exact at any digit count.

Shortest-round-trip digit counts it produces, each verified to satisfy
`float("%.*e" % (n-1, v)) == v`:

```
1000.0                    1 digit     0.1                       1
0.5                       1           0.3                       1
1e16 / 1e22 / 1e23        1           5e-324                    1
2.675                     4           3.14159265358979         15
123456789012345.0        15           1/3                      16
4503599627370496.0       16           1.7976931348623157e308   17
```

16 cases, 0 failures, and the counts match CPython's shortest form (`1000.0`
takes one digit, the largest double takes 17).

## Smallest viable change

Replace only the **digit-count search** in `py_float_repr_shortest` — compute
the shortest `n` exactly, then let the existing formatter emit `%.{n-1}e`. The
string-assembly path (~100 lines of buffer work) stays untouched. That confines
the change to one loop while removing its dependence on a formatter that cannot
verify itself.

Risk: this path decides how **every** float prints. It needs its own slice with
a float-repr regression corpus (integral values, subnormals, the 2**52 family,
1e22/1e23, and negative zero) run on the host and under pcc1 before it is
believed.

## Status

Root cause identified, minimised to a two-line probe, bisected per stage, and
the layering constraint that blocks the naive fix established. Exact replacement
algorithm written and verified on the host across 16 edge cases. Not yet applied
to the runtime.

## [DENIED] Fixing only the digit-count search

Applied exactly the "smallest viable change" proposed above — compute the
shortest digit count with exact integer arithmetic, then let the existing
formatter emit that many digits. **It made the output worse.**

```
value                      before                     after this change
0.3                        0.3                        0.30000000000000000
2.675                      2.675                      2.6749999999999998
3.14159265358979           3.14159265358979           3.1415926535897900
4503599627370496.0         4503599627370499.9         4503599627370500.0
1e-300                     1e-300                     9.9999999999999929e-301
1.7976931348623157e308     1.7976931348623468e+308    2e+308
```

Reverted.

**The reasoning error is worth stating plainly.** The host verification proved
that a standalone exact algorithm picks the right digit count — 16 edge cases,
0 failures. It did **not** verify what happens when that count is handed to a
formatter whose digit generation is itself inexact. Feeding a correct count to a
broken generator does not produce a correct string; it produces a differently
wrong one, and here a more visibly wrong one, because the count no longer
happens to mask the generator's error.

The old strtod loop was, accidentally, doing damage control: by increasing the
digit count until the round trip succeeded, it papered over the generator's
inaccuracy for the values where a longer form happened to read back correctly.
Removing that loop removed the accidental protection.

**So the digit count was never the defect.** The defect is
`_format_float_raw`'s digit generation:

```python
while normalized >= 10.0: normalized = normalized * 0.1
digit = float_to_i64(remainder)
remainder = (remainder - i64_to_float(digit)) * 10.0
```

and it has to be replaced with exact integer digit generation. That runs into
the layering constraint already established: `freestanding_stdio.py` is
annotated entirely in `i64` and must not depend on boxing or the heap while it
is being bootstrapped, yet exact decimal conversion needs big-integer
intermediates.

The two ways out, neither small:

```
1. Generate the digits in the semantic layer (py_format_runtime, `: int`,
   arbitrary precision available) and stop calling the freestanding formatter
   from the repr path entirely.  Needs the ~100 lines of buffer assembly to be
   rewritten around integer digits instead of a C-style %e string.
2. Do exact generation inside the freestanding layer using explicit
   multi-word i64 arithmetic (a 128/192-bit helper), keeping the no-boxing
   contract.  More code, but keeps the layering.
```

Option 1 is smaller but moves the repr path across a layer boundary; option 2
respects the boundary at the cost of hand-rolled wide arithmetic. Either is a
design decision, not a patch, and the regression corpus in
`tests/python/test_float_repr_shortest_exact.py` (which reproduces all four
failures **on the host build in 3 seconds**, no pcc1 rebuild needed) is the gate
for whichever is chosen.

## Second attempt, and what the probes actually established

Implemented exact digit generation plus string assembly in the semantic layer
(option 1). Verified on the host as pure Python: **25/25 cases matched
CPython's `repr`**, including `-0.0`, the `1e-4`/`1e-5` exponent-form boundary,
`1e22`/`1e23`, the subnormal `5e-324` and the largest double.

Applied to `py_format_runtime.py`, the output was garbage:
`1e22` printed as `/./0/////0/00//0//e+19`. `/` is ASCII 47 = `48 + (-1)`, i.e.
`load_i8` returned -1 — the digit buffer was read past what had been written.
Reverted.

Isolating the digit generator as a standalone pcc-compiled probe narrowed it
usefully:

```
value                    expected digits/exp10        pcc
1000.0                   1,0,0,0  exp10=3            same        OK
0.5                      5,0,0    exp10=-1           same        OK
4503599627370496.0       17 digits, exp10=15         same        OK  <-- 2**52 fine
1e-300                   1,0,0    exp10=-300         1,7,9  exp10=8      WRONG
1.7976931348623157e308   ...      exp10=308          all zeros, exp10=-25 WRONG
```

So the exact algorithm handles the value that originally failed (2**52); it
breaks on the extremes, where `den` becomes `1 << 1049`.

Everything the failure could plausibly be reduces to *correct* in isolation:

```
big-integer shift/mul/div    1<<1074, (1<<60)**2, (1<<200)//(1<<100)   all exact
loop-accumulated bignums     320 iterations of *10 -> 336 digits        exact
f64_bits                     bit pattern, biased exponent, fraction     exact
variable shift amounts       1 << n and 1 << (0 - e) for n = 1049       exact
negative comparison          (26 - 1075) >= 0 takes the else branch     correct
conditional assignment       mantissa/exp2 assigned inside `if`         correct
```

Yet inside the full function `den` came out with **8 digits** instead of 316,
which can only mean the `exp2 >= 0` branch was taken with `exp2 == -1049`.
Every shape that could cause that tests correct on its own.

## Honest status

This is a context-dependent miscompile: each constituent is right in isolation
and wrong in composition. Narrowing it further needs a systematic bisection of
the function (progressively delete statements until the branch flips), not more
guesses about which construct is at fault — the guesses have now been wrong six
times in a row, and each was individually plausible.

What this session leaves behind:

```
root cause chain      5GC matrix red -> cmp pcc2 pcc3 -> +100 bytes in __TEXT
                      -> 13 numeric formatters -> 3-instruction window
                      -> double 1000.0 encoded as 0x...004
                      -> 2**52 fails str() round-trip
                      -> _format_float_raw generates digits in binary float
layering constraint   freestanding_stdio is wholly i64 and must not box;
                      exact conversion cannot live there
three call sites      the repr path calls the broken formatter 3x (101/102/101),
                      so replacing one is provably insufficient
[DENIED] fix 1        exact digit count only -> output got worse, and revealed
                      that the old strtod loop was accidentally damage-limiting
[DENIED] fix 2        full exact assembly -> buffer read underflow; the
                      generator is correct for 2**52 and wrong at the extremes
regression gate       tests/python/test_float_repr_shortest_exact.py, 22 cases,
                      reproduces all 4 failures on the HOST build in 3 seconds
```

The 3-second host gate is the practical result: whoever picks this up no longer
needs a 42-minute matrix run to know whether they have fixed it.
