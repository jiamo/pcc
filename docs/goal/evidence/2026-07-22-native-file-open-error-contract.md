# Native file-open error-contract evidence

## Claim

Native `open()` failure now returns NULL with an active OSError in both the C
and pcc-Python runtime mirrors. Generated `open` lowering checks that channel,
and a `with open(...) as fh` root is NULL-initialized on the error edge. This
proves focused exception behavior, the original pcc1 package fallback, and
complete-suite closure.

## Evidence

- LLDB showed the pre-fix failure continued down the success path with a NULL
  `py_file_open` result because `py_err_occurred()` was false.
- `3 passed in 35.28s` for the minimized nested-except/finally regressions and
  native-file round trip.
- A rebuilt current pcc1 passed
  `test_pcc1_package_install_writes_manifest_without_host_python` in 6.65s.
- The combined native-file, exception, package install/build, and acquisition
  group passed `77 passed, 3 skipped in 38.57s`.
- Complete integration passed `4551 passed, 12 skipped in 669.70s`.
- Complete non-integration passed
  `9503 passed, 28 skipped, 1 warning in 970.63s`.

## Open boundary

Empty for this focused error-contract task.
