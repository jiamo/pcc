# Investigation: label records can be reinterpreted as instruction text

## Status

active

## Problem Description

The new fragment label producer interns arbitrary text. Publication passes
that name plus a colon to append_chunk, whose multiline parser can emit more
than a label. A payload containing two labels and nop therefore bypasses the
typed instruction record and its counters. Generated stackmap labels are safe,
but the new record boundary itself must reject malformed label payloads.

## Repro

`test_label_payload_cannot_be_reinterpreted_as_assembly` supplies
`Lfirst:\n  nop\nLsecond` to a populated fragment. Before the fix it does not
raise. Log: `build/native-fragment-label-validation-red.log`.

## Test [CONFIRMED]

The first malformed payload test fails with DID NOT RAISE EncodeError in 0.10s.
Source tracing confirms publication delegates to the multiline parser while
counting the record as one label, not an instruction.

## Proposals

- No.1 Validate emitted label names and publish through a label-only builder API [pending].

## No.1 Typed label publication

### Code Change

Validate the emitted symbol spelling before interning it. Add a label-only
module-builder entry that shares the existing text/data label definition and
duplicate-label handling, so mutated/malformed names cannot reach instruction
parsing. The text adapter retains its existing normalization and diagnostics.
Regress validation-before-mutation, publication of a corrupted name, exact
valid-label offsets and the existing ASM/PCO/reload differentials.

### Pending

Require focused, contextual and native qualification before the fragment
candidate can be frozen for a source-frozen compiler build.
