# Mach-O default-link route focused evidence (2026-08-14)

Mode: host-pcc route/ownership contracts only. No real runtime executable or
bootstrap chain was linked.

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_self_link_pcc_route.py::test_the_stage1_closure_stays_free_of_the_macho_toolchain \
  tests/python/test_self_link_argument_contract.py::test_self_link_mode_uses_host_default_and_accepts_explicit_modes \
  tests/python/test_self_link_argument_contract.py::test_default_self_link_mode_is_pcc_only_on_darwin_arm64 \
  tests/python/test_self_link_argument_contract.py::test_darwin_default_architecture_probe_failure_is_fail_closed \
  tests/python/test_self_link_argument_contract.py::test_darwin_arm64_default_routes_through_the_pcc_driver
9 passed in 0.37s
```

Unset selection routes Darwin arm64/aarch64 through the owned pcc driver;
other hosts retain `cc`, explicit selectors are authoritative, and an unknown
Darwin architecture fails closed. The Mach-O toolchain remains outside the
stage1 closure. Real pcc-vs-cc programs, repeated ASLR launches and the
sequential pcc-linked fixed point remain open.
