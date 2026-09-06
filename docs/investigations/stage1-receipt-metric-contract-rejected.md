# Investigation: Stage1 receipt validator rejects current producer metric labels

## Status
resolved

## Problem Description
run_pcc_stage1_build emits metric_scopes and comparison_contract in its v1
stage result. run_pcc_compile_ab's exact field validator admitted neither field,
so using it for installation rejected successful historical v84 evidence and
would reject the same current producer shape.

## Repro
Call the existing build-receipt validator on retained
build/heapsort-stage1-v84/build-receipt.json. It reports
`stage result fields mismatch: missing=[] extra=['comparison_contract', 'metric_scopes']`.

## Test [CONFIRMED]
The retained-receipt check failed exactly as above. The new
`test_build_receipt_validates_current_producer_metric_contract[producer]`
regression imports the current producer's real constants and failed with the
same diagnostic before the fix.

## Proposals
- No.1 Admit exact current measurement contracts alongside original v1 [CONFIRMED]

## No.1 Admit exact current measurement contracts alongside original v1
### Code Change
The validator admits either the original exact v1 field set or that set plus
both labels. Present labels must match the current contract, including JSON
types. Partial labels, changed scopes and integer zero in place of false are
rejected. No provenance, source, runtime, linkage or artifact checks are relaxed.
### CONFIRMED
Producer-shaped and original-v1 cases pass; partial/wrong/type-changed labels
reject. The real v84 read-only receipt validation now succeeds. Constants remain
independent of the producer import path to avoid an import cycle; the regression
compares against producer-supplied values so subsequent drift is visible.

## Report
This fixes receipt-consumer compatibility; it is not fresh bootstrap evidence.
The work belongs to pcc issue #186 and the installation receipt-binding repair.
