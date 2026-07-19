# Generic package campaign profile evidence

Date: 2026-07-11

Task: `AUD-P1-PACKAGE-NO-SPECIAL-CASE-CAMPAIGNS`

## Change

- `pcc/package_schema.py` now owns data-driven campaign capability profiles.
- Host `pcc/package/campaign.py` and pcc1 `pcc/cli_bootstrap.py` consume the
  same registry for root, area, description, selection, task, and feature data.
- `tests/python/test_no_numpy_special_cases.py` rejects future
  `numpy-core-l6` profile equality/switch branches.
- The former report shape is parity-checked between host and native JSON.

## Gates

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_no_numpy_special_cases.py \
  tests/python/test_package_campaign.py
12 passed, 1 skipped in 0.61s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_no_numpy_special_cases.py \
  tests/python/test_package_abi_mode_labels.py \
  tests/python/test_package_install_import_claims.py \
  tests/python/test_package_build_exec.py
28 passed, 7 skipped in 10.43s

gtimeout 420s env -u LC_ALL uv run pcc --backend self \
  --python-libpython=off --ir-scaffold=on pcc/__main__.py \
  -o build/bootstrap-compat-runner-pcc1/pcc1
PASS

gtimeout 180s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-compat-runner-pcc1/pcc1 \
  uv run pytest -q -n0 \
  tests/python/test_package_campaign.py::test_pcc1_campaign_cli_does_not_need_host_python
1 passed in 0.24s
```

Black and Python compile checks pass. This proves generic campaign selection
and host/pcc1 report parity, not NumPy import or pcc-native extension support.

