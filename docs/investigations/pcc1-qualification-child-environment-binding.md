# Investigation: pcc1 qualification trusts the sampler parent environment

## Status
resolved

## Problem Description
An `env PCC1_BINARY=/other/compiler ... pytest` child could override the selected
compiler while the sampler parent still recorded candidate-bound settings.
Qualification originally checked only that parent's environment.

## Repro
The focused installer regression records a correct parent invocation and an
overridden actual pytest-side compiler selection.

## Test [CONFIRMED]
Before the fix, the receipt regression failed with `DID NOT RAISE ValueError`.
Its final form runs the real sampler -> env override -> pytest -> live reporter
path with two trivial assertions and a successful pytest exit.

## Proposals
- No.1 Bind qualification to the actual pytest environment [CONFIRMED]

## No.1 Bind qualification to the actual pytest environment
### Code Change
The reporter records the six validation environment values in its start event.
Qualification requires their presence, validates the actual compiler selection
against the candidate, and compares the remaining source/runtime bindings with
the parent receipt. PCC1_BINARY is normally absent from the sampler's PCC_*
filter, so its mandatory actual pytest-side value is authoritative; if the
parent also records it, the two must agree.
### CONFIRMED
The real child override exits pytest successfully but fails qualification.
The unmodified real reporter/qualifier protocol succeeds in the temporary
checkout integration test.

## Report
This is invocation-provenance evidence for issue #186, not compiler execution,
package compatibility, release qualification or an actual stable promotion.
