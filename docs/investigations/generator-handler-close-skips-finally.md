# Investigation: closing a generator suspended in except skips finally

## Status
active

## Problem Description
The application-performance investigation (pcc #188) needs a regression for
first-entry and resumed generator locals. The unmodified compiler fails that
prerequisite: close() after a yield in an except handler omits the enclosing
finally. The generator closed while paused in the try body cleans up normally.
This is a correctness prerequisite, not an optimization result.

## Repro
Run tests/python/test_generator_first_entry.py with -x -n0 and the current
pcc-Python runtime. Two generators retain distinct lists, receive send values,
and one handles throw(ValueError) by yielding again. Closing both must append
both identities in finally. CPython prints [1, 8]; the current self/no-libpython
artifact prints [8].

## Test [CONFIRMED]
Control failed twice under GC0 (6.85 s / 5.60 s). No compiler optimization had
been applied. The gate also checks the runtime-selected collector ID so its
planned GC0–4 runs cannot silently exercise only the default backend.

## Proposals
- No.1 lower combined try/except/finally as nested try constructs [pending]

## No.1 lower combined try/except/finally as nested try constructs
### Code Change
Normalize the equivalent source structure to an outer try/finally surrounding
the original try/except/else. Handler-body errors and throw/close resume edges
then target the same finally owner as try-body errors. Preserve source spans
so generator handler/finally frame-slot identities remain stable. Reuse the
existing rooted exceptional-finally implementation rather than adding a
second cleanup protocol.

### pending
The current emitter restores the outer error target before emitting handlers;
their normal exits and explicit returns have finally coverage, but exceptional
resume edges bypass it. After the targeted regression, run existing generator,
vthread failure/cancellation and exception-cleanup checks before performance
work resumes.
