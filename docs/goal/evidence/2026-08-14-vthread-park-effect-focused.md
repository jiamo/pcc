# GATEWAY-P2-VTHREAD-PARK-EFFECT focused evidence — 2026-08-14

Mode: current host-source frontend/runtime focused tests, serial fail-fast;
integration/current-pcc1 cases were deselected by the repository marker policy.

Command:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_virtual_thread_park_effect.py
```

Result: `25 passed, 6 deselected in 16.34s`.  The focused suite covers
transitive may-park propagation, ordinary/local/sibling bound calls, explicit
dynamic callback delegation, hidden traced slots, strict unresolved-receiver
rejection, and middleware before/park/after control flow.

Open: the explicit current-pcc1 self/no-libpython integration node and the
cross-GC execution evidence required by downstream runtime rows.
