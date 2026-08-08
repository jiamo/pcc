# BUILD-P0-COMPILED-SELF-LINK-HOST-PYTHON-OWNER

Mode: native compiled repo-main invoking the pcc-owned Darwin self linker.

`pipeline_self_backend_link.run_link_command` used to fall back directly to
`sys.executable`.  In a compiled stage that value is the native pcc executable,
so the linker command recursively invoked pcc as though it were Python and pcc
rejected `scripts/pcc_link_macho.py --out ...` as an unknown CLI option.

The link owner now receives the existing pipeline host-Python resolver.  That
resolver preserves an explicit `PCC_HOST_PYTHON` and otherwise selects the
source/install `.venv` interpreter; it never treats the native compiler as a
Python interpreter merely because it is `sys.executable`.

Evidence:

- Self-link owner/command tests: 14 passed in 0.15s.
- Host-Python source-root resolver tests: 2 passed in 0.19s.
- The previously failing compiled repo-main toy-compile node passed in 138.10s.
- The complete compiled repo-main/pcc_multi group passed 7/7 in 396.07s.
- Python syntax and `git diff --check` passed for the production and test files.

