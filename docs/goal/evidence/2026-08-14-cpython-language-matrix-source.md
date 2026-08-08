# CPython language matrix — source-complete preflight

Mode: separate host CPython oracle only.  Current-pcc1 execution is
deliberately deferred until the shared source tree is frozen and one pcc1 is
built for all release gates.

`tests/python/test_pcc1_cpython_language_matrix.py` now defines a finite ten
case matrix covering arbitrary-precision container boundaries, for-target
representation, properties/descriptors, explicit exception causes, finally
unwinding, generator resume/return, coroutine completion, weakref/finalizer
lifecycle, threaded exception state, imports and reflection.  Each case is
compiled once by a receipt-current pcc1 with `--backend self`,
`--python-libpython=off`, and `--ir-scaffold=on`, inspected for libpython
dependencies, then run under GC0 through GC4.

Lightweight checks that do not provision pcc1 passed:

```text
python -m py_compile tests/python/test_pcc1_cpython_language_matrix.py
10/10 sources matched their separate CPython oracle; the five checked-in
corpus cases also matched their frozen expected stdout/status.
```

This is source evidence, not current-pcc1 or five-GC evidence.  The task stays
open until the required fail-fast matrix, the exhaustive 648-program parity
ratchet, fallback ratchets and sequential fixed point all pass on one frozen
source identity.
