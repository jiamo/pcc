# Owned package acquisition focused evidence — 2026-08-14

Mode: host-side local Simple-API/acquisition/receipt contracts; no external
network and no pcc1.

The compiler-free selection of `test_package_network_acquisition.py` completed
with 14 passed and 3 compiled/pcc1 nodes deselected. It covers hash-required
owned downloads, target metadata, fail-closed unsupported resolver/build
isolation shapes, source-build delegation, immutable host provenance and pip
install planning.

A broader fail-fast run reached the self-backend transport/SHA executable, but
its compile entered a current-runtime cold rebuild and the test's own 120-second
subprocess timeout fired. No child survived; the incomplete run is not green
evidence and the timeout was not widened. That executable, current-pcc1 network
install, GC0..4 NumPy app and sequential fixed point remain open.
