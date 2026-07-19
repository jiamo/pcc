# AUD-P1-CLI-ENTRYPOINT-SOURCE-OF-TRUTH closure evidence

`pcc/cli_contract.py` is now the self-host-safe owner for values shared by the
full host CLI, pcc1 bootstrap CLI, and legacy Click adapter:

- backend choices;
- python-libpython choices;
- IR-scaffold and diagnostic choices for their consuming surfaces;
- the optional `--emit-llvm` default sentinel;
- an inventory of the six flags shared by all three surfaces.

All three entry modules consume the shared constants. The same contract records
the intentional surface deltas for IR scaffold, observability, module running,
GPU backend, C-project build options, and pcc1 pytest, including which surfaces
are present/absent and why. A validation guard rejects duplicate shared flags,
partial consumers, unknown surfaces, duplicate divergences, and unclassified
surfaces. This centralizes metadata/delta accounting without moving Click into
pcc1 or changing execution behavior.

Gates:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 tests/python/test_cli_contract.py tests/python/test_cli.py tests/python/test_cli_launcher.py tests/python/test_cli_bootstrap_observability.py -k 'not pcc1'
53 passed, 1 deselected in 41.92s

gtimeout 180s env -u LC_ALL PCC_DEBUG_STRICT_NOLIB_STUB=pcc.cli_contract uv run pcc --backend self --python-libpython=off --ir-scaffold=on pcc/__main__.py -o build/bootstrap-cli-contract-pcc1/pcc1
exit 0 in about 61s; no cli_contract strict-stub diagnostic

gtimeout 60s env -u LC_ALL PCC_CURRENT_PCC1=build/bootstrap-cli-contract-pcc1/pcc1 uv run pytest -q -n0 tests/python/test_pcc1_compat_runner.py
12 passed in 0.35s
```

No pcc2/pcc3 bootstrap, GC matrix, full GCC suite, Click removal, or CLI rewrite
was performed.
