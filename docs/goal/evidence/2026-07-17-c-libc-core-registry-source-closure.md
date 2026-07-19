# AUD-P1-C-CODEGEN-SOURCE-OF-TRUTH libc-core closure

The broad audit card was split before refactoring. This completed slice moves
the declarative registry's core libc/POSIX names out of the monolithic legacy
`c_codegen.py` signature table: `printf`, `fprintf`, `malloc`, `free`,
`strlen`, `memset`, `memcpy`, `read`, `write`, `open`, `close`, and
`__errno_location`.

`LIBC_FUNCTIONS` is now built from the remaining explicitly named legacy map,
then populated from the declarative registry. `refresh_libc_registry_from_declarative`
fails if any declarative name still shadows a legacy entry; a source guard
asserts zero overlap and full publication into the effective map.

Focused gates:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/c/test_c_libc_registry.py tests/c/test_c_codegen_libc_registry_wire.py tests/c/test_libc.py tests/c/test_libc_extra.py tests/c/test_libc_math.py
52 passed in 3.90s

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/c/test_float_semantics.py tests/c/test_clang_compat.py
95 passed in 10.27s
```

Signedness metadata/conversion and ABI size/alignment are separate task rows;
this evidence does not claim those migrations. No full GCC suite was run.
