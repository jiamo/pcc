# Investigation: pcc1 qualification accepts a filtered full-suite collection

## Status
resolved

## Problem Description
Promotion qualification rejected a literal `-k` argument but admitted joined
forms such as `-krelease`. Selection injected through pytest addopts could also
be absent from raw invocation argv. A narrowed collection could therefore be
presented as the complete default or integration suite in issue #186.

## Repro
`test_full_collection_rejects_effective_selection_filters[joined-k]` builds
source-bound gate artifacts with an effective keyword and joined argv.

## Test [CONFIRMED]
Before the fix, that regression failed with `DID NOT RAISE ValueError`.

## Proposals
- No.1 Validate effective pytest selection instead of argv spelling [CONFIRMED]

## No.1 Validate effective pytest selection instead of argv spelling
### Code Change
The live reporter records parsed keyword, ignore, ignore_glob, deselect and
collect_only fields. Qualification requires their presence and valid types.
Default/integration collections reject nonempty effective filters; execution
shards may filter while their aggregate must cover the full collection.
### CONFIRMED
Joined flags, ini/override addopts, ignore/deselect filters and missing fields
are covered by the focused installer tests. The real reporter/qualifier protocol
also runs successfully on a tiny temporary pytest checkout.

## Report
This closes the qualification-filter bypass. It is not evidence that the actual
default/integration release suites ran or that a toolchain was promoted.
