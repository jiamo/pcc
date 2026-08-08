# Hatch editable isolation focused evidence — 2026-08-14

Mode: local hook/consumer and uv-environment contracts.

Runtime archive consumer cases passed in the 131-case artifact group, and
`test_package_uv_environment.py` completed with 6 passed. The hook loads its
in-tree verifier without ambient `pcc`/`PYTHONPATH`, rejects a missing helper,
and keeps provenance validation on publication.

Fresh isolated wheel/editable builds and Linux Docker consumers remain open.
