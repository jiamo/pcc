# Frame pool object-closure evidence — 2026-08-14

Mode: source-owner plus independent LLVM/self freestanding object emission.

The source-owner node and the two parametrized object-closure nodes in
`tests/python/test_freestanding_gc_frame_registry.py` completed with 3 passed.
They prove exact exported/TLS/raw-symbol ownership and thread-unregister drain
wiring without publishing or linking the production runtime archive.

This remains weak evidence. The C-versus-pcc-Python GC0..4 behavior, threaded
isolation, cache/cap/drain runtime assertions, allocation reduction, stage2
wall/RSS/pause thresholds and bootstrap gate remain open.
