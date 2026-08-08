# LINK-P3-SEMANTIC-LAYOUT — focused opt-in evidence

Mode: host-Python owned Mach-O linker, explicit unsplit semantic-layout policy.
This proves the finite policy/transform/cache boundary, not performance,
five-GC runtime behavior or self-host fixed point.

The first run exposed a stale direct-`NativeObject` fixture with local symbols
after the external partition. The fixture was canonicalized and its relocation
index updated; production validation remained fail-closed. Static review also
removed two unused atom maps without changing the transform.

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/python/test_macho_semantic_layout.py \
  tests/python/test_macho_incremental_link.py -k semantic
12 passed, 2 deselected in 0.41s

gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/python/test_self_link_argument_contract.py \
  tests/python/test_pipeline_self_backend_link_owner.py \
  -k 'semantic_layout or semantic_policy'
7 passed, 39 deselected in 0.39s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/python/test_macho_semantic_layout.py
9 passed in 0.11s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/python/test_macho_incremental_link.py
5 passed in 0.18s
```

The green boundary includes exact manifest/object identity, internal-only DCE,
hot/normal/cold order, relocation/data-in-code rewrites, precise-stackmap
filtering, incremental-cache separation, split-module rejection and opt-in
Darwin routing.
