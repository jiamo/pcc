# Native provider closure scan reparses the export AST

## State

Resolved on 2026-07-29. This was a stacked regression from
[`native-subprocess-provider-omitted-from-shallow-multi-file-closure.md`](native-subprocess-provider-omitted-from-shallow-multi-file-closure.md):
the mandatory-provider edge is correct, but its discovery path violates the
multi-file frontend's AST-reuse contract.

## Reproduction

```bash
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_frontend_ir_pass_pipeline.py::test_compile_python_multi_reuses_export_pass_ast
```

Observed on 2026-07-29:

```text
assert ['<scan>', '<scan>'] == []
1 failed in 0.62s
```

Each of the two explicit sources is parsed again by provider discovery.

## First failing boundary

`pcc0` host execution of `compile_python_multi` reaches
`_expand_required_native_builtin_providers`, which calls the AST-based
`_stdlib_absolute_imports_in(..., include_function_bodies=True)`. That helper
invokes `parse_and_lift(..., "<scan>")` before the frontend reuses the export
pass AST.

## Causality

The regression was introduced by the mandatory native-provider closure added
for `subprocess.CalledProcessError`. The repository already has
`_source_absolute_imports_for_discovery`, a string/comment-masked lexical
scanner that includes function bodies on request and explicitly promises not
to construct an AST. The new closure helper bypassed it.

## Proposal

Read each queued provider-closure source once and feed it to
`_source_absolute_imports_for_discovery(include_function_bodies=True)`.
Retain the existing provider allowlist, source-location checks, queue, and
deduplication. This changes only the discovery mechanism; it does not make
recursive stdlib admission broader.

## Denied alternatives

- Accept the second parse: violates a tested frontend performance contract.
- Remove the mandatory provider edge: restores the unresolved native
  `CalledProcessError` link failure.
- Enable recursive stdlib globally: broadens a shallow compile into optional
  host-stdlib closure and changes the requested mode boundary.

## Experiments

### E1 — focused regression

Confirmed RED with two `"<scan>"` calls, one per explicit source.

### E2 — route provider closure through lexical discovery

Replaced the provider helper's `_stdlib_absolute_imports_in` call with one
source read plus `_source_absolute_imports_for_discovery`. The focused
AST-reuse regression became GREEN:

```text
1 passed in 0.65s
```

### E3 — closure and semantic gates

```text
test_py_frontend_ir_pass_pipeline.py
81 passed in 3.69s

test_recursive_stdlib_compile.py + test_recursive_stdlib_import_codegen.py
37 passed in 5.33s

compiled pcc_multi shallow bootstrap pair
1 passed in 66.44s

native subprocess no-libpython + check_output
10 passed in 42.67s

current-source pcc1 subprocess returncode smoke
1 passed, 57 deselected in 178.54s

fallback + IR fallback ratchets
27 passed in 267.58s
```

## Report

The link-correct mandatory provider edge remains in place, but discovering it
no longer consumes a second typed frontend parse. The fix reuses the
bootstrap-safe lexical scanner already exercised for function-body imports,
masked strings/comments, class initialization, and compound statements. The
focused and current-source bootstrap gates show both sides of the stacked
failure are now closed.
