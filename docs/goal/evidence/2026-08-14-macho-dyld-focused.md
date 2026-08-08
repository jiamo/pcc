# LINK-P1-MACHO-LINK-DYLD — current-source focused evidence

Mode: host pcc executable linker on Darwin arm64.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_macho_exec_link.py
27 passed in 0.75s
```

The finite executable-container suite exercises MH_EXECUTE layout,
libSystem imports/stubs/GOT/fixups, entry point, writable data, multiple
objects, zerofill/linkedit separation, unwind handling and code-signature
validation with real linked execution.  It now also includes the corrected
sixteen-byte PAGEOFF12 scale for SIMD Q-register literal loads.

The row remains `DONE_WEAK` until the frozen current source passes the
sequential bootstrap/default-link acceptance.  Archives, dylib output, TLS,
lazy binding, dead stripping and classic dyld-info remain outside this row.
