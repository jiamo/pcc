# Nested-function default-cache ownership evidence

Date: 2026-07-17

Task: `V-P1-NESTED-FUNC-DEFAULT-CACHE-OWNERSHIP-REGRESSION`

## Outcome

Hoisted nested definitions no longer reuse the module-level native function
object cache.  Each execution creates a fresh function object and fresh
defaults, allowing pointer-bearing `ValueBox` payloads owned by those defaults
to be finalized under GC0..4.  Genuine module-level function caching remains
unchanged.

## Gates

```text
focused nested-default parity
2 passed in 1.02s

ValueBox and direct-payload roots across GC0..4
10 passed in 35.61s

entire five-GC production contract
168 passed in 55.65s

strict fallback baselines
25 passed in 263.48s

shared final five-GC self-backend bootstrap matrix
5 passed in 1500.11s (0:25:00)
```

The matrix was run once and shared with the bootstrap scheduling and long-run
GC closures; it was not repeated for this task.

## Claim boundary

This proves fresh nested-function defaults, payload release/finalization under
all five collectors, strict no-libpython compatibility, and preservation of
the self-backend fixed point.  It does not change general call-return ownership
or remove the intentional cache for module-level native functions.
