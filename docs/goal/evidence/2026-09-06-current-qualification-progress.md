# Current qualification progress — 2026-09-06

Unfinished tasks: CHECK-P0-CURRENT-PCC-PCC1-CORRECTNESS,
INSTALL-P0-VERIFIED-PCC1-TOOLCHAIN, B-P0-UNIFIED-APPLICATION-PACKAGE-INSTALL,
DIST-P0-PYTHON-CC-0-1-8. Publication is not authorized before qualification.

Stable initial v84 installation is documented separately. It is not the
current-source release candidate.

Isolated full checkout: `/private/tmp/pcc-correctness-20260906-a`.
Logs and initial source manifest: `build/correctness-20260906-a/`.
Only the isolated release candidate's pyproject/uv.lock version is 0.1.8;
shared main metadata remains 0.1.7 until changes are ready to reconcile.

## Completed focused evidence

- Incremental pytest reporter: 2 passed; first failures are retained as JSONL.
- Existing gateway/ABI changes: focused-01, 4 passed in 45.69s.
- Direct-link test fixture fix: 4 passed in 10.01s (C runtime now prepared by
  the existing immutable fixture, no production compiler change).
- Installer safeguards and copied-runtime ABI regression: 7 passed in 0.12s.

## Incomplete / red evidence

- focused-02: 1 failed, 47 passed, 4 deselected in 128.03s. Missing generated
  C-runtime fixture was fixed and its four affected execution cases passed.
- focused-03: 180s timeout with no failed reports, no final pytest summary.
  It is not green. Split fallback closure phases from the remaining focused
  files before retry; do not repeat this oversized combination.
- first-install-01: runtime copy named runtime/py lost static port ABI exports.
  Fixed to py_runtime/py; second cold runtime build is COMPLETE / rc0.
- first-install-02: Stage1 timed out at360s, Stage2 not run. Host budget8GiB
  resolves to3 workers; historical v84 used7. All228 native object inputs
  (427,917,535 bytes) were ready by324.12s. Source-bound linker replay passed
  in52.14s and produced `replayed-pcc1`; this is a diagnostic artifact, not a
  successful Stage1 receipt or release compiler. No owned children survived.
- Host pip dry-run for external checkouts reached a30s watchdog with no result;
  it is not installation evidence. Real installed-pcc1 local install checks
  remain pending.

## Next gates

First-install rerun uses the existing source-frozen runners, host admission
budget capped at16GiB (7 workers on this machine), a480s compile watchdog and
an8GiB process-tree RSS circuit breaker. Runtime and Stage2 retain their own
watchdogs; no stable command replacement occurs. Correctness still requires
full default/integration summaries and sequential bootstrap gates. No PyPI
publication, Git commit, or source rewind has occurred.

## First-install third run

`first-install-03` work root:
`~/.cache/pcc/installations/source-2gdr4ie9`.
Fresh runtime passed. Fresh Stage1 SUCCEEDED in183.07 seconds at7 workers;
compiler SHA256 `6770c323b9b2787f838148a77d810fe844e5d3375c6e6b67fbce2f46fdc6254a`.
Function-bearing pcc1 canary compiled and executed with output42; compiler
linkage is libSystem only. Stage2 began and was still running at this update.
No candidate promotion/release claim is made. The previous 3-worker timeout
was an installer admission/time-budget mismatch, not evidence of a required
compiler correctness tax.

## Subsequent package work

First-install-03 Stage2 reached the unchanged600s watchdog: native indexed emit
was still running, no completed pcc2/Stage2 receipt. Stage2 profile has the same
2-worker export/codegen concurrency as v84, but checkpoint time196.906s versus
126.859s; this is not the Stage1 admission mismatch. No owned children remain.
Do not widen/repeat the full chain as a diagnostic; localize with retained
workers/profiles after functional package fixes.

Fresh Stage1 pcc1 executed the two new release feature gates (cross-module int
ABI and return across parking finally): 2 passed in19.30s. Stable initial v84
installation remains unchanged.

Actual gateway install (`gateway-install-01`) failed immediately: it called
the project pcc rather than pcc-gateway and tried building optional
openssl_provider.c, failing on missing openssl/crypto.h. Two investigations
now own those distinct issues. New shared package-schema helpers preserve
hyphenated source names/versions and recognize conservative declarative
Hatchling Python source builds (hooks/in-tree/alternate config excluded).
Host/native naming paths and host/native source-build classification are
being unified. Tests:18 focused name/build-policy/real C-extension cases passed
in1.55s, and exact helper source compiled/executed through pcc1 in12.46s.
A current-pcc1 wheel-repo test remains deferred until the CLI is rebuilt; its
freshness guard correctly rejected the older binary. No full-suite claim.

Shared core source drift since the frozen candidate includes exception and
generator lowering plus both virtual-thread runtime mirrors. Preserve and
include those changes in the final candidate after reading their focused
regressions. New package helper edits are not yet in the old isolated tree.
Only the isolated pyproject/lock have version0.1.8; main stays0.1.7 for now.

New files include scripts/install_pcc1_toolchain.py, scripts/pytest_live_report.py,
tests/python/test_install_pcc1_toolchain.py, test_pytest_live_report_tool.py,
test_package_source_identity.py, test_package_declarative_source_install.py,
and tests/integration/test_pcc1_release_features.py. READMEs in core, pcc-gui
and pcc-gateway now describe initial installation, the stable entry and common
package environments, with incomplete qualification explicitly labeled.
