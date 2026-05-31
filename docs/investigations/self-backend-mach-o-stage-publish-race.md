# Investigation: self-backend Mach-O stage publish race

## Status
active follow-up: see `self-bootstrap-reliability-performance-2026-05-15.md`

## Problem Description

During backend #4 GenZGC work, the mandatory self-bootstrap gate regressed
intermittently:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
AssertionError: Bootstrap script failed with exit code 139
/Users/jiamo/my/pcc/scripts/bootstrap.sh: line 94: ... Segmentation fault: 11  "${full_cmd[@]}"
```

The failure appeared after stage 2 had produced `pcc2` and stage 3 immediately
executed that newly linked binary.

## Reproduction

The full pytest gate reproduced the failure:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0
```

A minimized shell chain also reproduced the same shape: host `pcc` produced
`pcc1`, `pcc1` produced `pcc2`, and immediate execution of the fresh `pcc2`
segfaulted in roughly 0.2s.

Running the same `pcc2` binary again after the failed chain succeeded, and
running exact stage commands under LLDB also exited 0. This pointed away from a
stable Python frontend semantic failure and toward a freshly linked executable
publish/loader boundary.

## Root Cause

The self-backend link path let clang write directly to the final stage output:

```text
cc ... -o build/bootstrap-pytest-self/pcc2 ...
```

On macOS arm64, the next bootstrap stage can exec that freshly linked Mach-O
immediately. The observed behavior was consistent with a stage binary whose
file contents were present but whose executable/signature state was not yet
stable for immediate loader use. A later exec of the same file succeeded.

Atomic rename alone was not enough: linking to `pcc2.tmp` and renaming with
`/bin/mv -f` still allowed the immediate stage-3 segfault. Explicit ad-hoc
codesigning after publishing the file reduced the failure, but a later
self-bootstrap run still reproduced the immediate stage-3 crash. The stable
boundary is ad-hoc signing followed by `codesign --verify`, which forces the
system verifier to observe the final Mach-O before the next stage execs it.

An attempted `os.replace()` implementation was rejected because strict
self-bootstrap reported a no-libpython fallback in `pcc.py_frontend.pipeline`.
The final implementation uses the already-supported subprocess boundary:

```text
cc ... -o <out>.tmp ...
/bin/mv -f <out>.tmp <out>
/usr/bin/codesign --force -s - <out>
/usr/bin/codesign --verify <out>
```

## Fix

`pcc/py_frontend/pipeline.py` now publishes self-backend linked outputs through
a same-directory temporary file and, on Darwin, explicitly ad-hoc signs and
verifies the final output before returning from the link step.

This keeps bootstrap stages from immediately executing a freshly linked Mach-O
before the platform loader/signature state is stable.

## Validation

Backend #4 selector work still passed:

```text
tests/python/test_gc_backend4_production.py
82 passed in 272.62s
```

The pcc-Python runtime archive rebuild succeeded:

```bash
env -u LC_ALL make -B -C pcc/py_runtime libpy_runtime_pcc_py.a \
  PCC=/Users/jiamo/my/pcc/.venv/bin/pcc \
  PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3
```

The self-bootstrap gate passed twice consecutively after the initial publish
fix:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 79.19s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 78.96s
```

After adding `codesign --verify`, the self-bootstrap gate again passed twice
consecutively:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 79.64s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 79.36s
```

## Remaining Risk

This fixes the macOS arm64 stage-publish boundary for the current self-backend
path. It does not address the separate self-bootstrap performance regression:
the same gate is still roughly 79s and remains an active optimization task.

## Update 2026-05-15: verify did not fully close the stage3 crash class

A later mandatory self-bootstrap run reproduced a stage3 crash after
`codesign --verify` had already been added:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
AssertionError: Bootstrap script failed with exit code 139
stage1 elapsed_ms=14980
stage2 elapsed_ms=32191
stage3: /Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc2
Segmentation fault: 11
```

The macOS crash report for `pcc2-2026-05-15-061541.ips` points at a different
surface than a pure loader failure:

```text
EXC_BAD_ACCESS at 0x151854df0
py_decref
user_pcc_py_frontend_pipeline_compile_python
user_pcc_cli_bootstrap__observed_compile_python
user_pcc_cli_bootstrap_bootstrap_cli_main
```

The same `pcc2` binary subsequently completed the identical stage3 command
under LLDB and as a standalone plain command, and the full bootstrap gate then
passed again in `82.30s`. Therefore the old publish-boundary fix is still
useful, but the stage3 crash class is not proven resolved. Continue the active
follow-up in `self-bootstrap-reliability-performance-2026-05-15.md`.
