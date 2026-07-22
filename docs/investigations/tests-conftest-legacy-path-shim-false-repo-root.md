# Investigation: tests/conftest.py legacy path shim silently corrupts `Path(__file__).resolve()` repo-root arithmetic in 18 test files

## Status
resolved (this slice: victims migrated to `repo_root()` walk-up; global shim
removal remains open as task `TEST-P2-REMOVE-LEGACY-PATH-SHIM`)

## Problem Description
While auditing the default suite's 114 skips ("探求每个 skip 的原因" — find
the reason for every skip), one skip was provably false:
`tests/python/test_package_install.py::test_numpy_local_source_metadata_filters_non_build_surfaces`
skipped with "local NumPy source tree is not present" although
`projects/numpy-2.4.4/` exists.

Root cause: `tests/conftest.py` (the tests/ → tests/{c,python}/ migration
shim) globally monkeypatches **`pathlib.Path.resolve`** and
**`os.path.dirname`** at import time. For any `*.py` path whose parent is
exactly `tests/c`, `tests/python`, or `tests/normal`, the patched
`resolve()` returns the path re-parented one level up (under `tests/`), so
legacy `parents[1]` arithmetic keeps working. The cost: every test file that
uses the *correct* post-migration arithmetic
`REPO = Path(__file__).resolve().parents[2]` computes the repo root one
level too high (`/Users/jiamo/my` instead of `/Users/jiamo/my/pcc`).

18 direct-children files of `tests/python/` were affected (all `parents[2]`
users), including the whole `test_package_*` family. Consequences observed:

1. False skip in `test_package_install.py` (numpy source tree "not
   present").
2. `tests/python/pcc1_gate.py::_normalize_repo_root` contains a nested
   `repo/pcc/pcc/__main__.py` band-aid that silently un-breaks pcc1 lookups
   fed with the corrupted root — a prior collision with this shim that was
   patched around instead of root-caused.
3. `tests/python/test_no_numpy_special_cases.py` scanned a nonexistent
   root, iterated zero files, and passed vacuously — its no-package-
   special-case guard had been blind since the shim landed. Re-enabling it
   surfaced one unreviewed metadata mention (see Proposal No.2).

## Repro
```bash
ls -d projects/numpy-2.4.4   # exists
env -u LC_ALL uv run pytest -q -rs -n0 \
  'tests/python/test_package_install.py::test_numpy_local_source_metadata_filters_non_build_surfaces'
# before fix: SKIPPED "local NumPy source tree is not present"
# with --noconftest (shim not loaded): PASSED
```
Diagnostic chain: `os.path.realpath('/Users/jiamo/my/pcc')` is correct
inside the pytest process while `Path(...).resolve()` drops the `pcc`
component; disassembling the cached pyc shows `parents[2]` intact; a probe
plugin recomputing `Path(item.module.__file__).resolve().parents[2]`
in-process yields `/Users/jiamo/my`. `tests/conftest.py:63`
(`Path.resolve = _patched_resolve`) is the only mutation.

## Test [CONFIRMED]
- The repro above was observed SKIPPED before the fix and PASSED with
  `--noconftest`.
- After the fix:
  `env -u LC_ALL uv run pytest -q -rs tests/python/test_package_*.py
  tests/python/test_no_numpy_special_cases.py ...` (all 18 victims) →
  248 passed, 1 deliberate env-gated skip, and initially 1 real failure in
  the previously-blind torch guard (Proposal No.2), green after review.

## Proposals
- No.1 Migrate the 18 `parents[2]` victims to an AGENTS.md walk-up helper   [CONFIRMED]
- No.2 Allowlist `pcc/cli_bootstrap.py` for torch *metadata mentions*        [CONFIRMED]
- No.3 Remove the global shim and migrate all 91 legacy files               [pending — task board row]

## No.1 Migrate the 18 `parents[2]` victims to an AGENTS.md walk-up helper
### Code Change
`tests/python/pcc1_gate.py`: new `repo_root()` — walk up from
`Path(__file__).resolve().parent` until a directory containing `AGENTS.md`
is found (the established convention already used by e.g.
`test_gc_root_rebound_local.py`; immune to the shim because walking up from
the re-parented path still reaches the real root). All 18 victim files
replace `REPO(_ROOT) = Path(__file__).resolve().parents[2]` with
`repo_root()` (one local-variable case in
`test_cli_self_backend_vectorize_policy.py` imports it as `_repo_root`).
### CONFIRMED
All 18 files: 248 passed, 1 deliberate skip
(`PCC_REQUIRE_CURRENT_PCC1` CI gate). The false numpy skip is gone.

## No.2 Allowlist `pcc/cli_bootstrap.py` for torch metadata mentions
### Code Change
`tests/python/test_no_numpy_special_cases.py`: add
`Path("pcc/cli_bootstrap.py")` to `TORCH_METADATA_MENTION_ALLOWLIST` with a
comment. The only match is the `_PACKAGE_COMPAT_TARGETS` vllm row
description ("vLLM PyTorch/CUDA extension stack target"), i.e. the same
metadata-mention category as the already-allowlisted
`pcc/package_compat.py`; `FORBIDDEN_TORCH_BRANCH_PATTERNS` still scans the
file, so real `if package == "torch"` branches remain forbidden.
### CONFIRMED
`env -u LC_ALL uv run pytest -q -rs -n0
tests/python/test_no_numpy_special_cases.py
tests/python/test_package_install.py` → 28 passed.

## No.3 Remove the global shim and migrate all 91 legacy files
### Code Change
Not in this slice. `tests/conftest.py` still monkeypatches
`Path.resolve`/`os.path.dirname` process-wide; 91 direct-children files of
`tests/{c,python}` still rely on legacy `parents[1]` / `dirname(__file__)`
arithmetic, and files added while the shim was active may use either
convention (each needs classification, not a blind rewrite). Tracked as
task board row `TEST-P2-REMOVE-LEGACY-PATH-SHIM`.

## Report
No.1 + No.2 landed (19 test files + 1 helper; zero `pcc/` production-code
changes). The remaining default-suite skips are all legitimate:
pcc1-freshness fail-closed gates, missing bootstrap artifacts, deliberate
env-var opt-in gates (two-Mac, CI ratchet, stress, pinned sites, network),
a broken macOS ThreadSanitizer runtime (auto-detected), optional MLX, and
csmith native-oracle timeouts under load. None are unimplemented-feature
placeholders; none should be deleted. Follow-up: No.3 as
`TEST-P2-REMOVE-LEGACY-PATH-SHIM`.
