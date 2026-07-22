# Investigation: package acquisition ignores the pcc target Python language version

## Status
active

## Problem Description

The default host acquisition backend resolves a bare package against the host
interpreter version. On a Python 3.14 host this selected NumPy 2.5.1, whose
metadata requires Python 3.12 and whose runtime typing modules use PEP 695
`type` statements. PCC's current target-language floor is Python 3.11 and its
native parser does not implement PEP 695 semantics, so acquisition succeeded
but the subsequent strict self/no-libpython compile failed. This is distinct
from the resolved NumPy 2.5 C-API surface investigation.

## Repro

Acquire and install bare `numpy` with the current pcc1 on a Python 3.14 host,
then compile a basic array-add program with `--backend self
--python-libpython=off --ir-scaffold=on`. Acquisition selects 2.5.1; compilation
exits 1 at `numpy._typing._char_codes.py:3`, with `expected NEWLINE, got NAME
_BoolCodes`.

## Test [CONFIRMED]

The failure was reproduced with the installed pcc-native NumPy 2.5.1 site.
Focused acquisition tests will add two repository artifacts whose
`data-requires-python` bounds straddle the target, and assert the host, owned,
and pcc1 selectors choose only the compatible artifact. The command-shaped
integration gate must then acquire a compatible NumPy source and run basic
array addition in strict pcc1/self/no-libpython mode.

## Proposals

- No.1 Resolve against an explicit target Python version [pending]

## No.1 Resolve against an explicit target Python version

### Code Change

Define the current pcc package target as Python 3.11, allow an explicit
`--python-version` override, include it in provenance reports, pass it to host
pip, and evaluate Simple Repository `data-requires-python` metadata in the
owned selectors. For pcc-native acquisition, ask host pip for source directly
instead of first downloading an unusable CPython wheel. The selection remains
package-neutral and fail-closed.
