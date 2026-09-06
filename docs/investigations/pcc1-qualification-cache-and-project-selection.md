# Investigation: pcc1 qualification misses effective cache and discovery selection

## Status
resolved

## Problem Description
The earlier [effective-selection repair](pcc1-qualification-effective-pytest-selection.md)
covered keyword/path filters, but a full-suite collection could still inherit
`--lf` through PYTEST_ADDOPTS or an addopts override without a literal flag in
its invocation argv. The cache then narrowed the recorded collection. This is
a separate follow-up to that immutable resolved investigation, under issue #186.

## Repro
Create two ordinary tests and a pytest lastfailed cache naming only one. Run
real pytest collection with PYTEST_ADDOPTS=--lf through the existing process
sampler/live reporter, then feed the hashed result to qualification.

## Test [CONFIRMED]
Before the fix, the real environment-addopts regression observed only the cached
node in the collected event and failed with `DID NOT RAISE ValueError`: the
narrowed collection was accepted as the full default suite.

## Proposals
- No.1 Bind effective cache modes and discovery configuration [CONFIRMED]

## No.1 Bind effective cache modes and discovery configuration
### Code Change
The reporter records lf, stepwise and stepwise_skip, plus inifile, override_ini,
pyargs, noconftest and confcutdir. Qualification requires valid present fields.
Full default/integration collectors reject active cache/stepwise modes, alternate
configuration, ini overrides, pyargs and conftest suppression; they use the
frozen checkout's pyproject.toml. Execution shards retain their supported filters.

### Builtin selection audit
The installed pytest main, mark, cacheprovider, stepwise, python and doctest
option definitions were inspected together. Keyword/path/deselect filters and
the canonical mark expression are already checked. Cache narrowing is owned by
lf and stepwise/stepwise_skip; lfnf applies only with lf. Failed-first/new-first
reorder rather than omit tests; stepwise-reset restarts the workflow. Python
file/class/function patterns, norecursedirs and testpaths come from project
configuration, now bound to the hashed pyproject without overrides. Positional
full-suite input remains the tests root; pyargs and conftest suppression cannot
redirect it. Collection errors and incomplete node reports remain rejection
conditions. Optional additional collectors do not substitute for this protocol's
complete Python test-node collection.

### CONFIRMED
`gtimeout 60s env -u LC_ALL uv run pytest tests/python/test_install_pcc1_toolchain.py -q -x -n0 --tb=short`
reports **80 passed in 7.60s**. Coverage includes real PYTEST_ADDOPTS=--lf,
`-o addopts=--lf`, --sw and --sw-skip aliases, missing/nonboolean fields,
alternate discovery configuration and the real collect/execute/qualify protocol.

## Report
Only installer/reporter policy and focused tests changed. No compiler/runtime
source, installed payload or stable command was mutated. These are tool-protocol
results, not actual release-suite or promotion evidence.
