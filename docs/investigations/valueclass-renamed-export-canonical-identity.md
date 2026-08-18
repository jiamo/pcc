# Investigation: renamed valueclass exports retain the alias as class identity

## Status

active

## Problem Description

The required renamed re-export control for free-function valueclass arguments
exposes a distinct descriptor defect. A local Handle binding refers to
records.Pair, but expansion combines local name Handle with owning module
records. That names nonexistent records.Handle and disagrees with the actual
Pair semantic type even though its physical aggregate layout is identical.

## Repro

The new three-module metadata/IR controls in
`tests/python/test_cross_module_valueclass_free_function_abi.py` rename the
imported valueclass. Before correction the argument case fails ClassType
coercion and the return control reports expected Pair/got Handle. This is
separate from the stale function call_sig failure recorded in
`cross-module-valueclass-free-function-call-signature.md`.

## Test [CONFIRMED]

The alias cases were observed red after the call_sig gap was isolated. The
export info already carries class_name=Pair and owning_module=records; only
`_expand_local_valueclass_type_descriptor` incorrectly substitutes the local
descriptor binding name. The direct non-renamed reduction is a control.

## Proposals

- No.1 Preserve the exported canonical class_name with owning_module [selected].
- No.2 Permit coercion between unrelated same-layout classes [DENIED].

## No.1 Canonical identity

### Code Change

The expanded valueclass descriptor now reads info.class_name, falling back to
the binding name only when the export has no canonical name. Argument/return
descriptors, positional/keyword calls, repeated expansion and wire roundtrip
preserve records.Pair identity. This does not equate unrelated classes by
layout or alter method receiver specialization.

### Focused CONFIRMED

The final metadata/IR/export packet is 11 passed. The real slots and stackmap
consumers also compile with direct aggregate calls after the separate call_sig
fix. Fresh full context and native execution remain required before closure.

## No.2 Same-layout semantic coercion

### DENIED

The source names are aliases of one class; the descriptor must preserve that
identity. Relaxing semantic coercion would accept genuinely different classes
and would conceal the incorrect owner/name pair.
