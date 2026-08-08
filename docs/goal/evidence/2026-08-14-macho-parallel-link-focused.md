# LINK-P3-PARALLEL — deterministic focused evidence

Mode: host-Python owned Mach-O linker. This proves the deterministic parallel
primitives and linker integration, not native pcc-runtime thread attribution,
pcc2/pcc3 identity or throughput scaling.

The first run correctly failed the fixture because its synthetic `__text`
sections lacked `TEXT_SECTION_FLAGS`; the current executable boundary rejects
a non-executable entry section. The fixture now models real executable text;
no production check was weakened.

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/python/test_macho_parallel_link.py
10 passed in 0.13s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/python/test_self_link_argument_contract.py::test_pcc_link_driver_publishes_a_fresh_non_mmap_inode
1 passed in 0.10s

for gc in 0 1 2 3 4; do
  env -u LC_ALL PCC_GC_BACKEND=$gc PCC_WITH_THREADS=1 uv run pytest -q -x -n0 \
    tests/python/test_macho_parallel_link.py
done
GC0..4: 10 passed each

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/python/test_macho_archive.py tests/python/test_macho_link_relocatable.py
17 passed in 1.40s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/python/test_macho_exec_link.py
27 passed in 1.01s
```

Static review confirmed stable contiguous work ownership, lowest-index failure
selection, a freeze barrier before symbol lookup, pre-mutation region
validation, disjoint mmap chunks and a bounded explicit/automatic worker
contract. The five environment runs execute the host linker and therefore do
not establish five-GC native-thread semantics.
