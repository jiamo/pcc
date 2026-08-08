# C frontend shared freestanding pcc-Python libc closure

Date: 2026-08-04

Task: `LIBC-P2-C-FRONTEND-FREESTANDING-LIBC`

Source identity: Git `6219a61f8f1ea84b13d9448ad66898d5ebf24a7c`, dirty
shared worktree. Relevant fingerprints:

```text
freestanding_c_linux_start.py             2f683fc4103b1e198fccfb1afc7831c2968605a5f406fabe10e328b3eea38b5a
c_evaluator.py                            eb1d1fd629c1d78d3ba66679789d713e29ee99cbabdc5242b00867b9bc0f7da7
cli_core.py                               d7c3cd75f2ea2faa695a98083974513619fe315c770ea233916be913ab713082
propagation.py                            2fe9d94657aed20452d1e14783dd831be8a6fe5416434e086e736dc89f58a055
test_c_freestanding_libc_link.py          1f1a443fb796031216b03a96d0490029f787b03de8036ca211dd80ee9563b275
test_self_backend_x86_64_linux.py         6e9aac4b3e2ebd009e55ff6e8cd174f1f6f273f72f13840b81c9398b7b85b314
```

## Claim

The public C frontend has an explicit `--freestanding-libc` final-link mode.
Its supported libc ABI resolves to the same strict pcc-Python semantic modules
used by the Python runtime: memory/string, allocator, platform IO/filesystem/
environment/system/time/process/socket, and stdio. No parallel handwritten C
or vendored musl libc implementation is selected.

On Linux x86_64 the same modules are compiled for the Linux self-backend target
into a dedicated link archive, and a strict pcc-Python `_start` reconstructs
`argc`/`argv`/`envp`, initializes the environment, calls the exact C
`main(i32, ptr, ptr) -> i32` ABI, and exits through the raw process substrate.
The final supported artifact is static, has no interpreter or dynamic
dependency, has zero undefined symbols, and selects no C/libc runtime object.

On Darwin arm64 the route selects `libpy_runtime_pcc_py.a`; the tested allocator
consumer's only SDK-owned symbol entries are `_mmap.got` and `_munmap.got` from
`libsystem_kernel.tbd` under the `libSystem.tbd` umbrella. This is deliberately
labeled a libSystem machine boundary and is not a Darwin zero-libc claim.

## Focused semantic and ownership gates

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_c_freestanding_libc_link.py \
  tests/c/test_lvn_translation.py \
  tests/python/test_freestanding_c_linux_start.py \
  tests/python/test_freestanding_module.py \
  tests/python/test_freestanding_mem_str.py \
  tests/python/test_py_frontend_ir_pass_pipeline.py::test_runtime_make_resolves_path_command_without_using_repo_relative_path \
  tests/c/test_self_backend.py::test_self_backend_x86_64_linux_sanitizes_anonymous_select_labels \
  tests/c/test_self_backend.py::test_self_backend_x86_64_linux_emits_scalar_fp_int_bitcasts

56 passed in 7.03s
```

This includes the public CLI route, exact source-module ownership ratchet,
Darwin link-map boundary, representative Lua/SQLite/zlib-shaped consumers,
exact startup extern signatures, and the existing portable memory/string C
differential through both host libc and the new route. The differential found
and now regresses a pre-existing LVN array-initializer miscompile; see
`docs/investigations/c-lvn-array-string-initializer-reuse.md`.

## Real-project gates

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_zlib.py::test_zlib_runtime_with_self_backend_freestanding_libc

1 passed in 4.67s

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 -m integration \
  tests/integration/test_sqlite.py::test_sqlite_runtime_with_freestanding_libc

1 passed in 36.24s
```

The zlib gate exercises the real project through the self backend and shared
memory/string implementation. The SQLite gate exercises the real amalgamation,
file IO, allocator, memory/string and variadic stdio path, validates the
created database through host `sqlite3`, and checks final link ownership.

## Linux static acceptance

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 -m integration \
  tests/integration/test_self_backend_x86_64_linux.py::test_linux_x86_64_c_frontend_freestanding_libc_is_static_and_python_owned

1 passed in 9.62s
```

Inside the Linux x86_64 Docker harness this gate checks `-nostdlib -static`,
the pcc-Python `_start`, `file`, absence of `PT_INTERP`, absence of
`DT_NEEDED`, empty `nm -u`, and a link map selecting pcc-Python allocator,
memory/string and environment objects. It rejects `vendor_`, `libc.a`, and
objects compiled from the runtime's C source tree.

## Bootstrap-facing current-source gate

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_compiled_pcc_multi_can_compile_toy_module \
  tests/python/test_py_frontend_ir_pass_pipeline.py::test_compile_python_multi_reuses_export_pass_ast

2 passed in 61.52s
```

This compiles the current `pcc_multi.py + pipeline.py` closure into a native
no-libpython helper and uses it to compile and run a toy module. It is a
focused current-source bootstrap-facing check, not a claim that a fresh full
five-GC pcc1/pcc2/pcc3 matrix was run for this link-only slice.

## Boundary disposition

The finite C-frontend link task is closed. It does not claim that every object
in the complete production runtime archive is already pcc-Python; that larger
whole-runtime audit remains `LIBC-P3-FREESTANDING-RUNTIME-CLOSURE`. It also does
not widen the supported libc API beyond the completed dependency tasks.
