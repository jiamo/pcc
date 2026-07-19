# Investigation: select a real pcc-native extension canary by generic gap cost

## Status

resolved

## Problem Description

M1 requires one pinned, real third-party source C-extension that covers PEP 489
or `PyType_FromSpec`. The repository had no checked candidate comparison,
source identity, or first-boundary report, so later package work had no stable
target and could drift toward a fixture or package special case.

## Repro

Run `scripts/goal_state.py next`; `M1-PKG-CANARY-SELECTION` reports that no real
package is pinned. Inspecting the previous task artifacts finds no M1 source
URL/SHA pair or source guard for a selected canary.

## Test [N/A]

This is a selection/design gap rather than a regressed behavior. The proposed
gate is `tests/python/test_m1_package_canary_selection.py`; it will validate the
machine pin, checked report, required init mechanism, all four first phase
boundaries, and absence of the selected package name from compiler/runtime
dispatch.

## Proposals

- No.1 Pin the lowest-cost real candidate that exercises PEP 489 [CONFIRMED]

## No.1 Pin the lowest-cost real candidate that exercises PEP 489

### Code Change

Compare three current real sdists by planner surface, PCC-header first error,
and init mechanism. Add a machine-readable selected pin, a checked report, and
a source guard derived from the pin rather than hard-coding a package name into
compiler/runtime code.

### CONFIRMED

`simplejson` 4.1.1 is the only compared candidate that combines a single-TU
extension with PEP 489. Its pinned archive hash matched, its upstream extension
built and passed the nested container oracle under CPython, and its first PCC
header error is the generic public `PyUnicodeWriter` surface. The focused gate
passed 8/8 together with the existing no-special-case suite. The first run
exposed only a test-harness path mistake caused by the repository's patched
`Path.resolve()`; anchoring with `Path.absolute()` made the intended checks
pass without changing the selection or product code.

## Report

Proposal No.1 landed as `docs/goal/m1-package-canary.json`, the checked report
at `docs/reports/m1-package-canary-selection.md`, and
`tests/python/test_m1_package_canary_selection.py`. `immutables` was rejected
because its single TU uses legacy init and a larger private C-API surface;
`pyahocorasick` was rejected because its build-plan gap is broader and it also
uses legacy init. Selection is complete; build, link, import, and pcc behavior
remain explicitly owned by the next M1 cards.
