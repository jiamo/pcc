# Existing Meson-output install evidence

## Claim

A pcc-native source install now recognizes a distribution-matching importable
payload already present under the managed Meson build root and installs it
without host Python. Unbuilt or mismatched source trees still use the explicit
bounded host-assisted backend. The selection is generic and contains no
package-name special case.

## Evidence

- The direct selector regression passes with `PCC_HOST_PYTHON=/usr/bin/false`
  and reports `build_backend: existing`, `host_assisted: false`.
- A rebuilt current pcc1 passed the original scenario in 6.65s, including
  direct install, cache reinstall, wheelhouse, repository, direct index, and
  owned pip-index installation.
- The combined native-file, exception, package install/build, and acquisition
  group passed `77 passed, 3 skipped in 38.57s`.
- Complete integration passed `4551 passed, 12 skipped in 669.70s`.
- Complete non-integration passed
  `9503 passed, 28 skipped, 1 warning in 970.63s`.

## Open boundary

Empty for this existing-output selection task.
