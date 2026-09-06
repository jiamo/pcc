# Investigation: metadata helper import reintroduces standalone CLI bridges

## Status
active

## Problem Description
The commit qualification fallback gate reports ten py_cpy calls in the
standalone cli_bootstrap module after package_metadata_paths was extracted
into a shared helper module.

## Repro
Run `tests/python/test_fallback_baseline.py::test_cli_bootstrap_package_schema_static_imports_stay_native`.
The observed assertion was 10 != 0. Dumping the complete IR identifies
`user_pcc_cli_bootstrap__native_requires_from_tree`: the call target comes
from `@.cpy.modref.package_metadata_paths`, with an import of
`pcc.package_metadata_paths` during module initialization.

## Test [CONFIRMED]
The baseline stopped after 17 passing tests in 63.93 seconds. This existing
regression is the retained gate; no numerical threshold is changed.

## Proposals
- No.1 register the shared helper's typed standalone export [pending]

## No.1 register the shared helper's typed standalone export
### Code Change
Add package_metadata_paths and package_metadata_member_paths to the static
exports consumed by independent module compilation, matching their source
signatures, and include the module in the static-import consumer set.
Closed-world builds already collect and compile the source module.

### Validation
Pending the same zero-bridge assertion and complete fallback gates.
