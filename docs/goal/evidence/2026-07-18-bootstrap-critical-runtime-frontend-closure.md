# Bootstrap-critical runtime/frontend closure evidence

Source identity: dirty worktree based on
`20afd0795e24c9abc178f87b3304cc1f4760a312` on macOS arm64.  This is
worktree-local evidence, not a clean-commit or release claim.

## Exact fixed-point evidence

```text
gtimeout -k 10s 700s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py
result: 1 passed in 310.04s (0:05:10)
```

This was a cold current-source GC0 run.  The gate reused the current shared
stage1 artifact, then completed real `pcc1 -> pcc2` and `pcc2 -> pcc3`
self-backend/no-libpython compilation and the fixed-point checks.  It was not a
five-GC matrix or a cache-only smoke test.

## Rows closed by this shared gate

- `BUG-P1-PCC1-LINKAGE-SCANNER-FALSE-LIBPYTHON-EDGE`: the bootstrap-critical
  native package/linkage shim survives the fixed point after its focused
  package and host/native scanner parity gates.
- `AUD-P2-PY-LIST-SUBSCRIPT-STORE-INDEXERROR`: the bootstrap-critical frontend
  lowering and mirrored runtime public/internal setter split survive the fixed
  point after their focused CPython-parity gates.
- The package-loader, module-exec split, parent-package initialization, and
  `ImportError.msg` changes cited by `PKG-P1-NATIVE-EXTENSION-LADDER` have their
  pending bootstrap proof.  The package row remains open only for its separate
  from-source small C-extension rung.

## Performance observation

The cold gate completed under the repository's 600-second target.  Stage2 and
stage3 spent most of their visible time in four-way Python module codegen and
eight-way self-backend object-emission subprocess batches.  This establishes a
5:10 cold baseline; it does not claim the path is optimally efficient.

## Not proven

This is not the five-GC bootstrap matrix, full pytest suite, integration suite,
clean-commit result, or closure of the self-host workaround-removal task.

