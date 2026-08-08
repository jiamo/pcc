# PY-P0-FOR-TARGET-REPRESENTATION-JOIN

Mode: host pcc Python frontend, `--backend=self`, `--python-libpython=off`,
Darwin arm64, production pcc-Python runtime archive with verified provenance.

The compiler now plans one representation-compatible visible target across
enumerate/list/tuple/reversed/range writes and keeps a private `range`
induction counter.  Body rebinding and nested same-name loops therefore cannot
change loop progress, while zero-iteration, abrupt-exit, exact-int re-entry and
owned-object cleanup retain the existing binding/root contract.

Evidence:

- `tests/python/test_py_for_target_representation_join.py`: 13 passed in 6.50s.
- The executable representation node passed in 4.39s using the verified
  production archive.
- `tests/python/test_py_multi_file_bootstrap_shim.py -k 'compiled_repo_main or compiled_pcc_multi_can_compile_toy_module'`:
  7 passed, 100 deselected in 396.07s.
- Python syntax and `git diff --check` passed for the touched lowering and
  regression files.

The compiled repo-main gate exposed an adjacent host-Python ownership defect;
that independent slice is recorded in
`2026-08-14-compiled-self-link-host-python-owner.md`.

