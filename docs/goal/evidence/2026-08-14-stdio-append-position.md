# Freestanding stdio append positioning

Mode: host-compiled C oracle versus the freestanding pcc-Python stdio object on
Darwin arm64.  This is focused semantic evidence, not Linux zero-libc or
self-host fixed-point evidence.

The first fail-fast differential exposed that the source implementation and
its hard-coded expectation both treated every pending append buffer as
`EOF + buffered bytes`.  The actual C stdio oracle distinguishes the stream's
logical cursor from the physical O_APPEND write destination: an explicit seek
changes `ftell` immediately, while a later flush commits at EOF.  `SEEK_CUR`
must be computed from the pre-flush logical cursor rather than the fd offset
after O_APPEND moved it to EOF.

The freestanding owner now records EOF when opening `a`/`a+`, updates its
logical base across seek/read/flush transitions, and preserves the pre-flush
logical cursor for append-mode `SEEK_CUR`.

Focused fail-first result:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_freestanding_stdio.py::test_append_positioning_matches_guarded_c_stdio_oracle
1 passed in 1.56s
```

Adjacent lifecycle plus ABI-filtered check:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_freestanding_stdio.py::test_basic_file_lifecycle_matches_c_stdio_contract \
  tests/python/test_freestanding_stdio.py::test_append_positioning_matches_guarded_c_stdio_oracle \
  tests/python/test_port_abi_constants.py \
  -k 'stdio or basic_file_lifecycle or append_positioning'
2 passed, 52 deselected in 2.95s
```

Open: full freestanding stdio/file-object, Linux zero-libc, current-pcc1 and
sequential fixed-point gates on frozen source.
