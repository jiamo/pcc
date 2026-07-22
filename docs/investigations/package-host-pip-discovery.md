# Investigation: host acquisition stops at a PATH-shadowing Python without pip

## Status
active

## Problem Description

The host acquisition backend defaults to the first `python3` on PATH. Under
`uv run pytest`, that is the repository venv interpreter, which intentionally
does not contain pip, even though a later PATH interpreter provides pip. The
real command-shaped network gate therefore fails before downloading anything.

## Repro

Run `PCC_RUN_PCC1_PIP_NUMPY_NETWORK=1 PCC1_BINARY=<current-pcc1> uv run pytest
-q -n0 -m integration tests/integration/test_pcc1_pip_numpy_network.py`.
The install subprocess exits 2 with `/.../.venv/bin/python3: No module named
pip` and `PCC-PKG-ACQUIRE-HOST-FAILED`.

## Test [CONFIRMED]

The command above reproduced deterministically in 0.13 seconds. A focused
synthetic test will put a no-pip interpreter before a working fake host Python
on PATH and require acquisition to continue to the working provider.

## Proposals

- No.1 Probe PATH Python candidates until one provides pip [pending]

## No.1 Probe PATH Python candidates until one provides pip

### Code Change

When `PCC_HOST_PYTHON` or an API argument is explicit, preserve fail-closed
single-provider behavior. Otherwise enumerate unique `python3` executables in
PATH and try the bounded pip-download command with each. Record the selected
host interpreter in acquisition provenance. Apply the same rule to host pcc
and compiled pcc1.
