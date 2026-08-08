# stdlib compression/archive gaps (evidence for the new tasks)

Status: TODO_READY (gaps recorded, not yet fixed)

Observed in `pcc/py_stdlib/` ports (new, committed 2026-08-10):

- `zlib.py`: decompress only; module docstring states "Compression remains
  fail-closed until pcc owns the exact `deflateInit2` ABI and level/window
  semantics."  Decompress caps output at 64 MiB.
- `bz2.py`, `gzip.py`, `lzma.py`: compression paths not proven.
- `tarfile.py`, `zipfile.py`: new ports, no corpus evidence against real
  archives (long names, symlinks, sparse, multi-volume, encrypted/ZIP64).

Gates for the follow-up tasks (each module): differential test vs CPython
AND a compiled-mode check under `--backend self --python-libpython=off`.
Batch-1 lesson: a CPython-only suite cannot see a skip-list or a
regex-subset gap — compiled-mode evidence is mandatory.
