# Investigation: qualification source validation rejects a real pytest checkout

## Status
resolved

## Problem Description
Qualification reused the bootstrap snapshot's exact-directory validator. That
layout intentionally contains only the compiler build closure; a real pytest
checkout also needs tests and produces report/cache artifacts. Synthetic JSON
tests had missed the resulting rejection of usable qualification inputs.

## Repro
The actual child-environment regression adds a tiny tests directory to an
otherwise matching source fixture and runs real pytest before qualification.

## Test [CONFIRMED]
It failed with `qualification source is not the complete build closure` before
the intended environment check. This is an installer boundary error, not a bug
in the bootstrap snapshot validator.

## Proposals
- No.1 Compare the compiler build closure while admitting the test checkout [CONFIRMED]

## No.1 Compare the compiler build closure while admitting the test checkout
### Code Change
Qualification uses the existing build_source_files and SHA256 primitives to
require exactly the installed compiler/helper source closure. Unrelated test
and report directories no longer violate a bootstrap-only storage layout.
### CONFIRMED
`test_real_pytest_receipts_flow_through_record_gate_and_qualify_cli` runs actual
default/integration collection and execution, hashes their real sampler/reporter
artifacts through --record-gate, and successfully invokes --qualify. No compiler
is executed by these tiny tests. The same integration work corrected the false
assumption that sampler PCC_* filtering also includes PCC1_BINARY; see the
[child environment investigation](pcc1-qualification-child-environment-binding.md).

## Report
This closes the producer/consumer protocol mismatch for issue #186. It does not
prove the real release suites, bootstrap or installed application packages.
