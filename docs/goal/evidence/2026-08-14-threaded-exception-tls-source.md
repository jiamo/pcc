# Threaded exception TLS source evidence — 2026-08-14

Mode: exact pcc-Python runtime source contract; no runtime archive build.

The focused source node completed with 1 passed. It checks that current
exception storage and its root handle are compiler-owned TLS, that nonempty
slots register/update roots through the scheduler registry and write barrier,
and that thread teardown clears the exception before unregistering thread GC
buffers.

This does not prove the native two-pthread C-oracle/pcc-Python differential,
GC0..4 scheduler-root baseline, or sequential pcc1 fixed point; those require
the single final frozen-source runtime build.
