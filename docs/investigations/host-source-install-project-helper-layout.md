# Investigation: host source installation wraps a project with top-level helpers

## Status
active

## Problem Description
A real host install of the gateway reports success but does not place
pcc_gateway/server.py under the selected site. _iter_importable_roots treats
any directory with a direct Python file as an import package. A project root
with demo_app.py, its real package and another visible directory is therefore
copied whole; its real package is no longer directly importable.
The native installer's source-project marker guard already distinguishes this
shape, and the host path needs the same boundary.

## Repro
The real source install returned success but readback raised FileNotFoundError
for selected-site/pcc_gateway/server.py. A reduced ordinary Hatchling project
has example_tools/, demo_app.py and examples/demo.py.

## Test [CONFIRMED]
The expanded host source-overlay test fails with the same missing
site/example_tools/__init__.py (1 failed in0.12s). A weaker fixture with only
one visible directory passed because the single-child fallback happened to
find the package; it did not represent the real failure.

## Proposals
- No.1 Apply the native source-project marker boundary to host root discovery [pending]

## No.1 Apply the native source-project marker boundary to host root discovery
### Code Change
Pending: pyproject.toml/setup.py mark a project root, so a top-level helper
module does not make that root itself an import package. Explicit __init__.py
packages retain their existing behavior. Keep preferred named-payload selection.

### Pending
Run the reduced host/native source-overlay packet and read back all actual
Python source files from fresh gateway/GUI installations.

## Update — current shared package packet

The current host/native-helper packet passed21 tests in1.68s, preserving the
real setuptools/PyInit C-extension gate. Actual host source installation of
pcc-gateway and pcc-gui then passed readback of all19 and18 Python source files
respectively, under the same selected pcc environment. Build actions were empty
with reason declarative_python_source. Receipts are
`build/correctness-20260906-a/pcc-gateway-host-install-v2.json` and
`pcc-gui-host-install-v2.json`. Native CLI installation is still pending a fresh
compiler; helper canaries do not substitute for that boundary. Work is tracked
in https://github.com/allstoalls/pcc/issues/186.
