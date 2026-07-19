# pcc1-run linkage scanner fabricates a false "libpython]" edge on a clean artifact

## Symptom

`tests/python/test_package_build_exec.py::test_pcc1_build_exec_builds_reusable_numpy_capi_provider_without_host_python`
fails: `pcc1 -m pcc.package build-exec pccnpapi ... --execute --json` exits 2.
The c_compile and native_link actions both pass (`returncode 0`), but the
linkage scan reports:

```
"links_libpython": true,
"link_libpython_edges": ["libpython]"],
"diagnostics": [{"code": "PCC-PKG-003", "message": "native artifact mentions libpython under pcc-native mode"}]
```

so the pcc-native no-libpython claim is (falsely) rejected.

## The artifact is provably clean

The built `pccnpapi.so` does NOT link or mention libpython:

- `otool -L pccnpapi.so` -> no python dylib dependency.
- `nm pccnpapi.so | grep -i libpython` -> nothing (no `libpython*` symbol,
  defined or undefined).

And the HOST scanner agrees it is clean:

```
from pcc.package.linkage import _libpython_edges
_libpython_edges(open(nm_of_artifact).read())  ->  ()   # empty, correct
```

The same test's HOST sibling
(`test_execute_build_actions_builds_reusable_numpy_capi_provider_with_include_dirs`)
passes. So the archive, the pccnpapi.c source, and the link command are all
fine; only the scanner *running under pcc1* produces the phantom edge.

## Root cause direction: pcc1 self-host miscompile of the scanner's regex/string path

`pcc/package/linkage.py` detects edges with
`re.compile(r"(?:^|[\s/:=,-])libpython\d+(?:\.\d+)*", re.IGNORECASE)` — it
requires `libpython` followed by a digit. The fabricated edge `"libpython]"`
has NO digit after `libpython` and carries a stray `]`, so the host regex could
never produce it. That string is the signature of a pcc1-compiled `re` /
string / bytes mis-lowering in the scan path (`_libpython_edges` /
`_decode_probe` / the artifact probe in `linkage.py`), not a real link edge.

This is the same family as other pcc1-only string/regex self-host miscompiles
already in this repo's history; it is NOT caused by the ImportError.msg runtime
change landed the same day (that touches only `py_obj_getattr` for exception
objects; the artifact is clean and the host scanner agrees, so an
exception-getattr change cannot have hallucinated a libpython edge from clean
input).

## Why it surfaced now

The test is gated on a *current* pcc1 (`_find_current_pcc1()`); with no pcc1 on
disk it skips. It had been skipping, so the PKG-P0 provider-split gate was not
actually being enforced through pcc1. Building a fresh stage-1 pcc1 made the
test run — and fail — for the first time in this working tree. This is a
regression in some recent (uncommitted or concurrent) change to the scanner or
a runtime primitive it uses, to be bisected; it is not this session's
`.msg`/package-E2E work.

## Test [CONFIRMED]

- `PCC_HOST_PYTHON=/usr/bin/false build/bootstrap/pcc1 -m pcc.package build-exec
  pccnpapi --path <copy of utils/pcc_numpy_capi_provider> --search-path <cc dir>
  --include-dir utils/fake_libc_include --include-dir pcc/py_runtime/include
  --library-dir pcc/py_runtime --library py_runtime --execute --link-output
  pccnpapi.so --json` -> exit 2, `linkage.ok=false`, edge `["libpython]"]`.
- `otool -L` / `nm` on that same `pccnpapi.so` -> no libpython.
- `pcc.package.linkage._libpython_edges` on the artifact's nm text (host) ->
  `()`.

## Update 2026-07-18 (same day): matched text found; miscompile framing needs revision

- The artifact's ONLY libpython-adjacent text is pcc's own runtime diagnostic
  string `PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython] module not found: %s`
  (via `strings pccnpapi.so`). The fabricated edge `libpython]` is exactly that
  token sliced from `no-libpython]` — consistent with a substring/token scan,
  not with the host regex (which requires a digit after `libpython`).
- pcc0-compiled probes of the regex semantics are CORRECT (default runtime
  mode): `re.findall(r"\d+", "ab]12cd]")` -> `['12']`,
  `(?:^|[,-])foo` -> `['-foo']`, and the full `_LIBPYTHON_PATTERNS[0]` over the
  diagnostic string -> `[]`, all byte-identical to CPython. So a generic
  `\d`/alternation miscompile is NOT reproduced at pcc0 level.
- `pattern.finditer` does not exist in the pcc port runtime: a pcc0-compiled
  `re.compile(...).finditer(...)` raises `AttributeError: finditer`, and
  `rg finditer pcc/py_runtime/py/py_re.py` finds nothing. Yet the pcc1 scan
  produced an edge — so the pcc1 `-m pcc.package build-exec` scan path cannot be
  running `linkage.py`'s finditer loop as written. Leading revised hypothesis:
  pcc1's native build-exec path (cli_bootstrap native shim or a
  compiled-closure fallback after the finditer AttributeError) performs a
  SIMPLIFIED substring/token libpython scan whose token slicing produces
  `libpython]`, falsely matching digit-less text the host regex rejects.
  Unverified — next step is reading the pcc1 build-exec scan code path in
  `pcc/cli_bootstrap.py` / the compiled closure rather than assuming either
  framing.

## Update 2026-07-18 (later): true root cause found — NOT a miscompile; FIXED

`nm build/bootstrap/pcc1` settled it: pcc1 ships
`_user_pcc_cli_bootstrap__native_linkage_edges_for_root` /
`_native_linkage_json` / `_run_native_package_linkage_from_pcc1` — pcc1
intercepts `-m pcc.package build-exec` with a NATIVE shim in
`pcc/cli_bootstrap.py` (like the pip shim). The compiled `linkage.py` finditer
loop never runs (consistent with `pattern.finditer` raising AttributeError in
every host-compiled repro, both cc and pcc runtime tiers, single-file and
linkage-shaped).

The shim probed artifacts via `strings -a <so> | grep -i -m 1 -E <pattern>`
and then:

- `_native_text_has_libpython` matched the BARE substring `libpython`
  (dropping the host regex's mandatory version digit), and
- `_native_libpython_edge` sliced from the marker to the next whitespace.

pcc's own runtime diagnostic literal
`PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython] module not found: %s` is
embedded in every artifact that links `libpy_runtime.a`, so EVERY pcc-native
artifact scanned by a pcc1-run build-exec was flagged `links_libpython=true`
with the sliced token `libpython]` -> false `PCC-PKG-003` -> exit 2.

Fix (host-parity, in `pcc/cli_bootstrap.py`):

- New `_native_libpython_match_span()` mirrors `linkage._LIBPYTHON_PATTERNS`
  exactly on detection: `libpython` requires start-or-separator before AND a
  version digit after; `-lpython` requires start-or-whitespace (digits
  optional); `Python.framework` is case-sensitive with boundaries on both
  sides; `python<digits>.dll`. `_native_text_has_libpython` /
  `_native_libpython_edge` are thin wrappers over the shared span, so has/edge
  can never drift; the digit-aware `_native_libpython_grep_pattern` keeps
  `grep -m 1` from surfacing the diagnostic line and hiding a real edge later
  in the output.
- Separate pre-existing report divergence fixed while the gate finally ran:
  the shim reported `effective_include_dirs` (internally materialized
  pcc-capi-include appended) where the host contract (`build_exec.py`) echoes
  the caller's include dirs; the shim now echoes the caller's list.

## Verification [CONFIRMED]

- Required gate:
  `tests/python/test_package_build_exec.py::test_pcc1_build_exec_builds_reusable_numpy_capi_provider_without_host_python`
  -> 1 passed against a freshly rebuilt stage-1 pcc1 (the manual pcc1 run
  shows `ok=true`, `links_libpython=false`, `link_libpython_edges=[]`,
  `no_libpython_runtime=true`, empty diagnostics).
- New host/native parity regression:
  `tests/python/test_package_linkage.py::test_pcc1_native_libpython_scan_parity_with_host_patterns`
  (corpus includes the diagnostic literal, `no-libpython]`, `libpythonic`,
  `xlibpython3.9`, plus real dylib/-lpython/framework/dll edges) -> passed.
- Suites: `test_package_build_exec.py` 24 passed; `test_package_linkage.py`
  17 passed (one stale expectation updated: the edge spelling is now the host
  regex match `libpython3.14`, not the old token-to-whitespace
  `libpython3.14.dylib`); pcc1 pip surface `-k pcc1_pip_install` 14 passed.

## Status

RESOLVED at the focused-gate level. The "pcc1 self-host miscompile" framing in
the opening sections was WRONG — the artifact/host evidence was real, but the
divergence lived in a deliberately simplified native scanner, not in codegen.
`pcc/cli_bootstrap.py` is bootstrap-critical, so the full pcc1->pcc2->pcc3
bootstrap gates must run before commit-level completion (stage-1 rebuilds were
green twice; the full matrix is scheduled with the end-of-goal full-project
validation).
