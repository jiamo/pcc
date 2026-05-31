# CPython Symbol Collision and Teardown Order Investigation

## Overview
During the execution of Phase 4 CPython fallback tests (such as `test_callable_type_alias_literal_with_cpython_values`), compiled binaries crashed with exit status `-11` (SIGSEGV) in `_Py_Dealloc` within `py_cpy_main_exitcode()`. This document records the root cause, design decisions, and symbol resolution model implemented to solve it.

## 1. Symbol Collision Root Cause

### Context
In the fallback path (`--python-libpython=auto`), the final binary is linked against `libpy_runtime_pcc_py_libpython.a`. This static archive contains:
- `py_capi_shim.o`: implements pcc-native C-API stubs (so compiled C extensions can interact with pcc objects).
- `py_libpython.o`: wraps real CPython C-API calls to bridge compiled code to CPython's dynamic library (`Python.framework`).

### Link-Time Collision
Because static library symbols take precedence over dynamically linked symbols on macOS/unix, standard `extern` calls within `py_libpython.c` (e.g., `PyErr_Occurred()`, `PyErr_Fetch()`, `PyImport_ImportModule()`) were statically bound to the overrides in `py_capi_shim.c` rather than the real symbols in CPython.

Consequently:
1. `PyImport_ImportModule("sys")` called from `py_cpy_sync_sys_argv()` inside `py_libpython.c` resolved to `py_capi_shim.c`'s implementation, raising a pcc-native `"module not found: sys"` exception in the pcc-native thread-local storage (TLS) slot.
2. At program exit, `py_cpy_main_exitcode()` queried `PyErr_Occurred()` (which resolved to the pcc stub) and fetched the pcc-native exception.
3. This pcc-native exception object (which has a `PyClassObject` layout) was passed to CPython's real `PyErr_GivenExceptionMatches` in `Python.framework`, causing memory layout corruption, garbage reads, and eventually jumping through `0x0` during `_Py_Dealloc`.

---

## 2. Symbol Resolution Model

To resolve the dynamic symbol collision, we bypass link-time resolution for CPython functions in `py_libpython.c` using runtime dynamic loading (`dladdr` and `dlsym`).

### Resolution Strategy
1. **Locate Real CPython Library**: Since `Py_Initialize` is not defined in `py_capi_shim.c`, calling `dlsym(RTLD_DEFAULT, "Py_Initialize")` successfully resolves to CPython's real library. We query the path to CPython using `dladdr`.
2. **Clean Handles**: We `dlopen` CPython specifically using the path obtained from `dladdr`, yielding a `g_libpython_handle` that only queries CPython.
3. **Symbol Loading**: We load all colliding functions via `dlsym(g_libpython_handle, ...)` into static function pointers.
4. **Macro Mapping**: We use `#define` macro overrides (e.g., `#define PyImport_ImportModule p_PyImport_ImportModule`) to redirect standard call sites to these function pointers, maintaining code readability.

### Type Structs (`PyLong_Type`)
Most type structs (e.g. `PyBool_Type`) and exception globals (e.g. `PyExc_SystemExit`) are declared `extern` but not implemented in `py_capi_shim.c`, so they resolve normally via the dynamic linker. However, `PyLong_Type` is used in `py_libpython.c` (specifically `py_cpy_is_instance` to check integer conversions). To ensure type layout consistency, we also resolve `PyLong_Type` dynamically via `dlsym` and map it using:
```c
#define PyLong_Type (*p_PyLong_Type)
```
This is the only type struct resolved dynamically because it is the only one actively referenced in the file.

### Single-Initialization Thread Safety
The resolution function `py_cpy_resolve_symbols()` is invoked exclusively in `py_cpy_ensure_init()` inside a thread-safe atomic compare-and-swap block:
```c
void py_cpy_ensure_init(void) {
    int expected = 0;
    if (atomic_compare_exchange_strong(&g_initialized, &expected, 1)) {
        py_cpy_resolve_symbols();
        Py_Initialize();
        ...
    }
}
```
This guarantees that resolution happens exactly once, before any CPython calls are made.

### Lifetime & Failure Recovery
- `g_libpython_handle` is kept open for the lifetime of the process to prevent the dynamic library from being unloaded.
- If any symbol fails to load, `py_cpy_resolve_symbols()` prints a fatal error to `stderr` and calls `abort()` to fail loudly, preventing downstream segfaults.

---

## 3. Teardown Order and Exception Leaking

### Teardown Order
In `pcc/py_frontend/codegen/module_lifecycle_lowering.py`, `py_cpy_main_exitcode()` is called *before* module teardown:
```python
if self.emit_cpy_main_exitcode:
    exit_code = self.builder.call(self.runtime["py_cpy_main_exitcode"], ...)
self._emit_module_teardown_call(self.module.name or "mod")
```
If module teardown occurred first, CPython finalization could null out type descriptors or deallocate global exceptions. Subsequent exception deallocations in `py_cpy_main_exitcode` would then attempt to access finalized type objects, leading to crashes in `_Py_Dealloc`.

### Exception Leaking
In `py_cpy_main_exitcode()`, all fetched exception references are intentionally leaked at process exit.
At program shutdown, the exception value may reference partially-freed pcc-native objects whose types have `tp_dealloc == NULL`. Attempting to `decref` or `clear` these references would cause CPython to jump through a null pointer. Since the process is exiting, leaking these references is a safe compromise.
