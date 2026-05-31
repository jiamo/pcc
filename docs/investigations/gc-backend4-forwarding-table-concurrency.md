# Investigation: Backend #4 forwarding table must be read under graph lock

## Status
resolved

## Problem Description
The pcc-Python runtime mirror protects public forwarding operations with the
object graph lock, but the C runtime's `pcc_gc_install_forwarding()` and
`pcc_gc_note_relocation_read()` currently access the forwarding side table
without taking that lock. A Backend #4 reader thread can traverse
`pcc_gc_forwardings` while another thread installs a new forwarding node.

## Repro
Run the focused ThreadSanitizer gate:

```bash
CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_colored_relocating_forwarding_table_threadsanitizer_or_skip' -q -n0
```

Expected result after the fix: readers repeatedly calling
`pcc_gc_note_relocation_read()` can run concurrently with a writer installing
Backend #4 forwarding entries without a TSan data-race report, and all final
reads resolve to the installed forwarding targets.

## Test [CONFIRMED]
The focused TSan gate fails before the fix:

```bash
CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_colored_relocating_forwarding_table_threadsanitizer_or_skip' -q -n0
```

Observed result:

```text
1 failed in 34.41s
stderr: WARNING: ThreadSanitizer: data race
```

The report is emitted while reader threads call `pcc_gc_note_relocation_read()`
and the writer thread installs forwarding entries.

## Proposals
- No.1 Lock C runtime forwarding install/read public paths     [CONFIRMED]

## No.1 Lock C runtime forwarding install/read public paths
### Code Change
Split the C runtime forwarding install into an unlocked helper plus a public
locked wrapper, and make `pcc_gc_note_relocation_read()` take the graph lock
around forwarding-table lookup and source flag clearing. Keep pcc-Python mirror
behavior unchanged because it already locks these public operations.
### CONFIRMED
The focused C runtime TSan gate now passes:

```bash
CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_colored_relocating_forwarding_table_threadsanitizer_or_skip' -q -n0
```

Observed result:

```text
1 passed in 7.03s
```

The broader concurrent and relocation gates pass:

```text
CC=clang tests/test_gc_concurrent_collection.py:
8 passed in 53.00s

tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py:
36 passed in 229.51s
```

## Report (only when the investigation is closing)
No.1 landed. C runtime public forwarding install/read paths now match the
pcc-Python mirror's graph-lock protocol: `pcc_gc_install_forwarding()` wraps an
unlocked helper with the graph lock, and `pcc_gc_note_relocation_read()` locks
around forwarding-table lookup plus relocation-candidate flag clearing. This
closes the TSan race between read-barrier traversal and forwarding insertion.
