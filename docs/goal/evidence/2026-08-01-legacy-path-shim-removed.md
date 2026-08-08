# tests/conftest.py global path shim removed

Date: 2026-08-01

Task: `TEST-P2-REMOVE-LEGACY-PATH-SHIM`

## What the shim did

`tests/conftest.py` monkeypatched `pathlib.Path.resolve` and
`os.path.dirname` **process-wide** so that, for any `*.py` under
`tests/{c,python}` (or `tests/normal`), they reported the file as living
directly in `tests/`. That kept pre-migration arithmetic
(`os.path.dirname(__file__)`, `Path(__file__).resolve().parents[1]`) working
after the test files moved down a level.

The cost was a process-wide lie about the filesystem: any code that resolved
such a path — including code under test — got a wrong answer. It bit this
session twice (two new tests computed a wrong repo root and looked for
`scripts/pcc_multi.py` one directory too high), which is exactly the failure
mode the row was filed for.

## Audit and migration

Classification of the 91 shim-shaped uses under `tests/{c,python}`:

```text
80  this_dir = os.path.dirname(__file__)      + parent_dir = os.path.dirname(this_dir)
21  repo_root = os.path.dirname(os.path.dirname(__file__))
 5  PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
 2  testdir = os.path.dirname(__file__)
 1  c_tests_dir = os.path.join(this_dir, "c_cases")   (tests/c_cases, one level up)
 2  parents[1] on pcc/**.py, NOT on a test file — the shim never applied,
    so these were already correct and are untouched
```

`tests/security/*` and other subdirectories were never covered by the shim
(it only matched `tests/{c,python,normal}`), so their `dirname(dirname(...))`
arithmetic was already right and is unchanged.

All 89 affected files were migrated mechanically: `this_dir` now resolves to
the file's real directory (`os.path.dirname(os.path.abspath(__file__))`) and
every consumer's depth is corrected by one level, so the same values come out
with no monkeypatch. `tests/c/test_c_files.py`'s `c_cases` lookup now goes up
one level explicitly. The shim and its helpers are deleted from
`tests/conftest.py`, whose docstring records the change.

## Commands and results

```text
representative migrated tests (c_files, c_parser, pcc1_gate,
  libc_import_baseline, compile_cache)        82 passed, 1 deselected
rg 'Path.resolve = |os.path.dirname = ' tests/conftest.py   no matches
remaining dirname(__file__)/parents[1] under tests/{c,python}: only the two
  pcc/**.py uses, proven unaffected

first full run of the migrated directories: 6 failed, 8120 passed
after fixing both causes:                    8126 passed, 60 subtests
                                             passed in 1389.11s (0:23:09)
```

The first run's six failures were both consequences of the shim, and are the
best evidence that removing it was right:

- Five in `tests/c/test_run.py`, which did a bare `import conftest`. That
  landed on the repository ROOT conftest.py only because the shim made
  `tests/c/*.py` look like `tests/*.py`; with real paths, `tests/` is on
  sys.path and the bare import picked `tests/conftest.py` instead. The file
  now loads the repo-root `conftest`/`run` modules explicitly by path.
- One in this session's own `test_libc_fortify_wrappers.py`: the abort path
  writes a platform crash report whose stderr is not ASCII, and the test used
  `text=True`. It now captures bytes.

## Supported claim

The test suite no longer patches path resolution process-wide; every test
under `tests/{c,python}` computes its own directory and the repo root
explicitly. Path arithmetic in tests now means what it says, and code under
test sees the real filesystem.

## Not proven

- Test files outside `tests/{c,python}` were not audited for other path
  idioms; they never depended on the shim.
- The migration hardcodes the two-level depth for direct children of
  `tests/{c,python}` (the same assumption the shim faked). Files moved to a
  deeper directory later will need the AGENTS.md walk-up helper
  (`tests/python/pcc1_gate.repo_root()`), which is what new tests already use.
