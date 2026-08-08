# Evidence: first batch of build-tool stdlib modules (STDLIB-P1-BUILD-TOOL-CLOSURE)

Date: 2026-08-08
Task: STDLIB-P1-BUILD-TOOL-CLOSURE
Direction: PKG-P0-BUILD-WITHOUT-HOST-PYTHON (no host Python, including build tools)

## Delivered

Six modules added to `pcc/py_stdlib/`, taking it from 59 to 65:
`errno`, `stat`, `fnmatch`, `glob`, `textwrap`, `posixpath`.

Claim: these lower **natively** under `--backend self --python-libpython=off`
and produce byte-identical output to CPython for the covered surface. Not
claimed: full CPython API coverage of any of them (scope is the build-tool
closure), and the remaining ~28 modules of the measured gap are untouched.

## Two gaps that only compiled mode revealed

Both would have shipped green on a CPython-only test suite:

1. **`textwrap` was on the pipeline's module skip-list**
   (`pcc/py_frontend/pipeline.py`). It was skipped because the only
   implementation the walker could see was the *host* `textwrap.py`, so
   `dedent` worked through its dedicated native lowering and **every other
   entry point silently fell back to libpython**. With a compilable port in
   `pcc/py_stdlib/`, the entry is removed and `wrap`/`fill`/`indent`/`shorten`
   lower natively.
2. **fnmatch matching through `re` is not available under no-libpython.**
   `translate()` emits `(?s:...)\Z`, which is outside pcc's native regex
   subset, so every compiled call raised
   `NotImplementedError: pcc re: pattern outside the native regex subset`.
   Matching now uses a direct glob matcher; `translate()` is kept as public
   API but is off the matching path.

Also fixed on the way: `pcc/py_stdlib/os.py`'s `os.path.dirname` returned
`"/a/"` for `"/a//b"` (CPython strips trailing slashes unless the head is all
slashes), and the first `errno` table assumed Linux/Darwin agree — **EAGAIN
and EDEADLK are swapped** (Linux 11/35, Darwin 35/11), so it is now selected
by platform.

## Verification

- `tests/python/test_py_stdlib_build_tool_closure.py` — **151 differential
  assertions** against CPython's own modules, plus an integration test that
  compiles a probe with pcc and asserts the compiled stdout equals CPython's
  stdout byte for byte (`1 passed` under `-m integration`).
- fnmatch fuzz: **4000** random pattern/name pairs vs CPython, 0 mismatches.
- Compiled probe under `PCC_HOST_PYTHON=/usr/bin/false`:

```text
errno 2 35
stat True True 0o644
fnmatch True False
filter ['a.py']
dedent 'a\nb\n'
wrap ['the quick', 'brown fox']
posixpath /a b.txt
split ('/a', 'b')
```

## Gates

- `tests/python/test_py_multi_file_compile.py` + `test_py_multi_file_bootstrap_shim.py` — **133 passed**
- `scripts/bootstrap.sh --backend self --stage 1` — green after the pipeline change
- `test_fallback_baseline.py` + `test_ir_py_fallback_baseline.py` — **24 passed, 3 failed**,
  byte-identical to the pre-change result (same single pre-existing
  `pcc.py_frontend.pipeline: 14`, tracked as
  FALLBACK-P1-PIPELINE-SUBPROCESS-KWARGS-RESOLUTION). Removing `textwrap` from
  the skip-list added no fallback.
- `scripts/goal_state.py validate` — OK

## Remaining

The measured meson gap was 34 directly-referenced missing modules; 6 are done.
Still open, roughly by cost: `codecs`, `pprint`, `difflib`, `filecmp`,
`netrc`, `locale`, `gettext`; then `configparser`, `sysconfig`, `signal`,
`runpy`, `pwd`, `uuid`; then the archive family (`tarfile`, `zipfile`,
`gzip`, `bz2`, `lzma`); `importlib` module machinery is the deep one.
Windows-only (`msvcrt`, `ntpath`) and dev-only (`cProfile`, `unittest`,
`compileall`, `zipapp`) still need an explicit exclusion with evidence that
the build path never reaches them.
