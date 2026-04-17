"""pcc-Python ports of stdlib modules.

When ``compile_python(..., recursive_stdlib=True)`` resolves an
``import X`` statement and ``pcc/stdlib/X.py`` exists, it's used in
preference to CPython's ``X.py``. This lets us provide pcc-Python
implementations of stdlib modules whose CPython source has features
pcc can't compile (C accelerators, decorators we don't yet support,
etc.) — so user code can still ``import struct`` etc. without
falling back to libpython.

Adding a port:
1. Create ``pcc/stdlib/<name>.py`` containing a pcc-compilable
   implementation that exposes the same public API as the CPython
   ``<name>`` module.
2. Cover the actual usage with tests so future drift can't regress
   silently.

Current ports:
- (none yet — this is the registry directory; see Issue 11.C.2 for
  the first port, ``struct``, which unblocks Phase 9.2)
"""
