# AArch64 SIMD Q PAGEOFF12 scale — DONE_STRONG evidence

Mode: host pcc self-backend Mach-O executable linker on Darwin arm64.

LLDB reduced a deterministic C-runtime crash in `py_dict_lookup` to a corrupt
literal load.  The emitted instruction was `ldr q0, [x8, symbol@PAGEOFF]`.
AArch64 encodes the Q-register width as `size=0`, `V=1`, and the high `opc`
bit; the linker previously interpreted only `size` and wrote an unscaled byte
offset into the relocation field.  Hardware then multiplied that immediate by
16 and loaded unrelated globals.

The implementation now recognizes the SIMD/FP Q encoding and uses scale 16.
The real executable regression passed:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_macho_exec_link.py::test_pageoff12_ldr_q_uses_sixteen_byte_scale
1 passed
```

The original minimized C-runtime symptom also passed after the change, and its
adjacent diagnostic group completed with `17 passed in 142.62s`.

The full current-source executable-link gate also completed with its final
summary:

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_macho_exec_link.py
27 passed in 0.75s
```

No HARNESS source or build process was changed or interrupted.  This finite
correctness card is `DONE_STRONG`; broader default-link/bootstrap acceptance
remains attributed to `LINK-P1-MACHO-LINK-DYLD` and its acceptance rows.
