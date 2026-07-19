# 2026-07-07 GPU Level-5 C FFI Shim Evidence

## Summary

The pcc1-facing Metal runtime path now has a no-libpython C ABI shim for calling
a prebuilt runtime-source bridge dylib and for creating/reading/writing/releasing
native buffers through a prebuilt runtime dylib. `pcc_metal_source_runtime_call_prebuilt`
and `pcc_metal_buffer_runtime_{create,length,write,read,release}_prebuilt`
live in the C-level runtime kernel and use `dlopen`/`dlsym` directly; they do
not import Python, libpython, or `ctypes`.

The shim consumes the pure call-plan data shape already introduced for Level 5:

- bridge dylib path and symbol;
- Metal source bytes and length;
- native `id<MTLBuffer>` pointer slots represented as raw 64-bit addresses;
- one aligned scalar payload plus per-scalar offsets;
- synchronous `waitUntilCompleted` only, with NULL fence callback/context.

The buffer wrappers expose the matching native-buffer runtime surface:

- create a native runtime buffer and return it as a raw 64-bit address;
- query its byte length;
- write host bytes into the native buffer;
- read bytes back from the native buffer;
- release the native buffer.

It is wired into the default runtime archive plus the pcc and pcc-Python runtime
archive helper paths as a CC-only C kernel helper. This is deliberate: dynamic
loader / Objective-C bridge calls are a platform ABI boundary, not Python
semantics to port into pcc-Python.

The same shim is now also exercised from a strict no-libpython pcc-compiled
program. The probe uses `pcc.extern` for the C symbol and `pcc.unsafe` for the
raw ABI arrays, then creates a fake native buffer, writes/reads bytes through
the buffer shim, and calls the fake source bridge through the runtime shim.
This proves compiled pcc-native code can reach the no-libpython shim without
CPython `ctypes`.

This still does not prove `GPU_LEVEL_5_PCC1_NATIVE`; it removes the dynamic FFI
blocker from the runtime/codegen side and leaves the pcc1 binary plus real
Metal package execution as the next boundary.

## Files

- `pcc/py_runtime/src/pcc_metal_runtime.c`
- `pcc/py_runtime/include/py_runtime.h`
- `pcc/py_runtime/Makefile`
- `tests/kernel/test_metal_runtime_ffi.py`
- `docs/goal/task-board.yaml`

## Gates

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  tests/kernel/test_metal_runtime_ffi.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_runtime_ffi.py -rs
```

Result: `4 passed in 0.98s`. This includes the strict no-libpython
pcc-compiled probe that calls the C shim, fake native-buffer runtime, and fake
source bridge.

```bash
gtimeout 180s env -u LC_ALL make -C pcc/py_runtime \
  build/pcc_metal_runtime.o build_pcc/pcc_metal_runtime.o \
  build_py/pcc_metal_runtime.o
```

Result: passed; all three targets compile the shim with system `cc`.

```bash
gtimeout 180s env -u LC_ALL make -C pcc/py_runtime libpy_runtime.a
```

Result: passed. Existing runtime warnings from other files remain; the new
`pcc_metal_runtime.c` compiled without warning.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py -rs
```

Result: `10 passed in 0.10s`.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `7 passed in 0.50s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: `6 passed in 4.46s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: `6 passed in 4.27s`.

## Claim Boundary

This proves a fixed no-libpython C ABI shim can load a prebuilt bridge dylib and
call it with source, buffer slots, scalar payload offsets, NULL fence callback,
and synchronous wait semantics. It also proves the native-buffer create,
length, write, read, and release ABI wrappers can call a prebuilt runtime dylib,
and that a strict no-libpython pcc-compiled program can call these shims via
direct extern lowering. The regression uses a fake dylib so the ABI is tested
without relying on Metal hardware or host Python `ctypes`.

Still not proven: a pcc1-built no-libpython binary generating this call, a pcc1
process executing the prebuilt runtime-source package against real Metal,
`.air/.metallib` production, five-GC GPU lifetime parity, performance, or
whole-program GPU execution.
