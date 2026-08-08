# Bounded default pass tier focused evidence — 2026-08-14

Mode: host-side textual/self pass contracts; no runtime, pcc1 or bootstrap.

- `tests/python/test_compiled_default_pass_tier.py` — 10 passed.
- Ten exact nodes from `test_py_frontend_ir_pass_pipeline.py` covering the
  versioned manifest, default dispatch, content cache, telemetry and both large
  and huge-module breakers — 10 passed.

The focused evidence proves the default tier is exactly `mem2reg+sroa`, the
self-safe bounded implementation does not start a host subprocess, telemetry
reports runs/skips, and the `skip_huge` decision depends on input size rather
than wall-clock timing.

This does not prove the task's performance exit: the pinned same-source
no-pass/default stage2 wall time, peak RSS, artifact equivalence and bootstrap
gate remain open after final source freeze.
