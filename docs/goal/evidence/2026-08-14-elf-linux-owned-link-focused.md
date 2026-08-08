# LINK-P3-ELF-LINUX — focused owned-link evidence

Mode: host pcc plus Linux x86_64 Docker oracle. This proves the finite
encoder/object/static-link slice; it is not production-runtime zero-libc or
self-host fixed-point evidence.

The first focused run exposed a real Intel-syntax parser bug: whitespace in
`[rbp - 8]` survived as the invalid integer token `- 8`. The encoder now
compacts address expressions before splitting signed terms. A route fixture was
also updated to model the current non-empty `NativeObject` boundary rather than
the superseded empty-section Mach-O mock.

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_elf_x86_64.py tests/python/test_self_obj_pcc_route.py \
  -k 'x86 or linux or elf'
16 passed, 2 deselected in 1.23s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_self_link_argument_contract.py -k 'linux_pcc_link'
1 passed, 38 deselected in 0.08s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_self_obj_pcc_route.py
7 passed, 2 deselected in 1.04s

gtimeout 300s env -u LC_ALL \
  scripts/run_self_backend_linux_x86_64_docker.sh bash -lc \
  'env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_x86_64_encode.py'
91 passed in 18.63s

gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 -m integration \
  tests/integration/test_self_backend_x86_64_linux.py::test_linux_x86_64_pcc_owned_static_elf_runs_without_undefined_symbols
1 passed in 6.64s
```

The Docker executable is written from internal assembly by the pcc ELF writer
and linker, is `ET_EXEC`, has no `PT_INTERP` or dynamic segment, has an empty
`nm -u`, and runs with exit status 42. Linux's default link selector remains
unchanged. The production pcc-Python runtime zero-libc executable and the final
current-source pcc1/pcc2/pcc3 proof remain source-freeze gates.
