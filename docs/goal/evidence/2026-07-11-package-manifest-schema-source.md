# Package manifest schema source-of-truth evidence

Date: 2026-07-11

Task: `AUD-P1-PACKAGE-MANIFEST-SCHEMA-SOURCE`

## Source identity

Base commit `58c595ac0bea18c2f74af52581d259f29aac5d6d`; evidence applies to the
current dirty working tree and makes no published-release claim.

## Change

- Added self-host-safe `pcc/package_schema.py` as the single owner of
  `pcc.package-manifest.v1`, schema version 1, wheel tag parsing, execution-mode
  mapping, and package capability profiles.
- Host metadata/install/linkage and pcc1 native linkage/install JSON consume the
  same functions.
- Manifests now expose `manifest_schema` and `capability_profile`; existing
  claim fields remain intact.

## Gates

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_package_abi_mode_labels.py \
  tests/python/test_package_install_import_claims.py
8 passed in 0.30s

gtimeout 420s env -u LC_ALL uv run pcc --backend self \
  --python-libpython=off --ir-scaffold=on pcc/__main__.py \
  -o build/bootstrap-compat-runner-pcc1/pcc1
PASS

gtimeout 300s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-compat-runner-pcc1/pcc1 \
  uv run pytest -q -n0 \
  tests/python/test_package_abi_mode_labels_pcc1.py \
  tests/python/test_package_install_import_claims_pcc1.py
5 passed in 1.68s

python compile check, Black on all modified Python files, git diff --check
PASS
```

The pre-rebuild pcc1 failed four assertions because it was an old artifact
without the new fields; the current-source strict rebuild passed unchanged
tests. This proves host/pcc1 parity and no-host fallback for the selected
contract, not package import success or a pcc-native third-party canary.

