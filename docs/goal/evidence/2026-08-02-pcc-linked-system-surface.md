# System-dependency surface of a fully pcc-linked binary

Mode: host pcc, Darwin arm64. Measured on `v1_rebase` — the vocab1 program
compiled through the full runtime and linked entirely by pcc's own Mach-O
toolchain (no ld/as/codesign), running standalone under ASLR.

Now that pcc links its own executables, this is the **authoritative** answer
to "what system surface does a pcc-owned binary still use?" — it connects the
LINK track to the LIBC ownership track with a real artifact rather than a
per-object ratchet.

## Dylib dependencies: one

```text
/usr/lib/libSystem.B.dylib
```

## dyld-bound imports: 49

```text
___error ___exp10 ___stack_chk_fail ___stack_chk_guard ___stderrp
__tlv_bootstrap _abort _access _backtrace _backtrace_symbols_fd
_calloc _clock_gettime _close _dlclose _dlerror _dlopen _dlsym _exit
_fclose _ferror _fflush _fgetc _fopen _fprintf _fread _free _frexp
_fseek _ftell _fwrite _getenv _gettimeofday _kevent _kqueue _malloc
_memcmp _poll _realloc _setenv _snprintf _strcpy _strerror _strtol
_strtoll _strtoul _time _unsetenv _vsnprintf _write
```

## What this hands the LIBC track

These 49 are the exact remaining system calls in a pcc-linked binary, grouped
by the rows that target them:

- **LIBC-P2-ALLOCATOR** (malloc family): `_malloc _calloc _realloc _free`
- **LIBC-P2-STDIO-SUBSET** (buffered IO): `_fopen _fclose _fread _fwrite
  _fflush _ferror _fgetc _fprintf _snprintf _vsnprintf _fseek _ftell
  ___stderrp`
- **LIBC-P2-THIN-WRAPPERS** (fs/env/time/process): `_access _getenv _setenv
  _unsetenv _time _gettimeofday _clock_gettime _close _write _exit _abort`
- **String/mem** (some already vendored as musl; these are the ones the
  runtime still imports): `_strcpy _memcmp _strerror _strtol _strtoll
  _strtoul`
- **Platform-inherent** (kept as the machine boundary, not ownership targets):
  `__tlv_bootstrap` (dyld TLV), `___stack_chk_fail/guard` (stack protector),
  `___error` (errno), `_dlopen/_dlsym/_dlclose/_dlerror` (C-extension loading),
  `_kqueue/_kevent/_poll` (event loop), `_backtrace*` (diagnostics),
  `_frexp/___exp10` (libm)

The distinction matters for claim hygiene: the allocator/stdio/wrapper
families are ownership targets the LIBC track is chipping at; the
platform-inherent set is the C-kernel machine boundary that no-libpython
never claimed to remove.

`v1_rebase` was produced with no ld, no as, and no codesign — so this surface
is what the *pcc-owned* link path actually leaves, not what the system
toolchain would.
