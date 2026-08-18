# Repository migration to the allstoalls organization

Date: 2026-09-06. Human request (mid-session, verbatim intent): move pcc into
https://github.com/allstoalls; the core compiler stays `pcc`; the GUI and
gateway subsystems named in the architecture review may become separate
repositories.

## What was done

1. `jiamo/pcc` (public, 12 stars, default branch `master`) was transferred to
   the organization with `POST /repos/jiamo/pcc/transfer {new_owner: allstoalls}`.
   Verified afterwards: `allstoalls/pcc`, PUBLIC, 12 stars, default `master`,
   https://github.com/allstoalls/pcc. GitHub redirects the old URL.
2. The local `origin` remote was repointed to `git@github.com:allstoalls/pcc.git`
   (`git remote set-url origin`); remotes `jiamo` (mypcc2) and `shenming` are
   untouched. `origin/master` is `977ad074`; the local `master` (`2574f585`) is
   one commit ahead and was NOT pushed (pushing is the human's call).
3. Two private seed repositories were created from the committed HEAD
   (`2574f585`) content only, same relative layout as the core so a later
   extraction can `git rm` the paths without renames:
   - `allstoalls/pcc-gui` (54 tracked paths): `pcc/py_runtime/py/pcc_gui_*.py`,
     `pcc/py_runtime/gui_declarative_contract_v1.json`, `projects/mac_diff_app/`,
     `projects/harness/gui*`, `tests/python/test_pcc_gui_*`,
     `tests/python/test_mac_diff_app*`, `tests/fixtures/contextual_pcc_gui_*`,
     `tests/test_gui_declarative_design.py`, `docs/design/gui-declarative-absorption.md`.
   - `allstoalls/pcc-gateway` (47 tracked paths): `pcc/gateway/`, `pcc/web/`,
     `pcc/py_runtime/py/freestanding_gateway_control.py`,
     `tests/python/test_gateway_*`, `tests/fixtures/gateway/`,
     `docs/design/pcc-vthread-gateway.md`, `docs/refs_docs/gateway-research/`.
   Each seed carries `README.md` (provenance and status) and `SEED_FILES.txt`.
4. `README.md` and `pyproject.toml` in the core now point at
   `https://github.com/allstoalls/pcc`; historical investigation links to the
   old issue URLs are left as written (GitHub redirects them).

## Not done (deliberately)

- The core repository still contains the GUI and gateway sources. Removing
  them changes the runtime archive (`pcc/py_runtime/Makefile` compiles the
  `pcc_gui_*` ports), the wheel file list (`pyproject.toml` ships the TLS
  provider), and several test/closure gates, so the removal is tracked as two
  extraction rows (`REPO-P1-EXTRACT-PCC-GUI-REPO`,
  `REPO-P1-EXTRACT-PCC-GATEWAY-REPO`) that must pass the bootstrap and
  runtime-archive gates before the seeds become authoritative.
- Seed repositories are private; the human decides visibility.
- No history rewrite was performed; the seeds start from a single commit.
