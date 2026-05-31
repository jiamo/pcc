# Investigation: bootstrap.sh should use bounded Python IR pass default

## Status

active

## Problem Description

While validating a Python codegen edit, the full pytest bootstrap gate was
stopped because it was taking longer than the expected interactive budget. The
interrupted process had reached:

```text
build/bootstrap-pytest-self/pcc2 --backend self --python-libpython off \
  pcc/__main__.py -o build/bootstrap-pytest-self/pcc3
```

Current history in `docs/current-goal-state.md` shows recent full bootstrap
times are commonly around 150s-370s, so the run was not proven hung. However,
`scripts/bootstrap.sh` did not apply the bounded bootstrap policy already used
by `scripts/run_self_backend_bootstrap_gate.py`: child compilers should default
`PCC_PYTHON_IR_PASSES=off` unless the caller explicitly requests another IR
pass mode.

## Root Cause

`scripts/run_self_backend_bootstrap_gate.py` sets child
`PCC_PYTHON_IR_PASSES=off` by default and preserves explicit caller overrides.
`scripts/bootstrap.sh`, which is the path used by
`tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`,
did not set this environment variable for stage compilers.

That meant full bootstrap validation could accidentally use the default Python
IR pass preset instead of the bounded self-backend performance gate policy.

## Code Change

`scripts/bootstrap.sh` now computes:

```bash
BOOTSTRAP_PYTHON_IR_PASSES="${PCC_BOOTSTRAP_PYTHON_IR_PASSES:-${PCC_PYTHON_IR_PASSES:-off}}"
```

and passes it into every stage compiler invocation as:

```bash
PCC_PYTHON_IR_PASSES=${BOOTSTRAP_PYTHON_IR_PASSES}
```

This preserves explicit `PCC_PYTHON_IR_PASSES=...` experiments and adds a
bootstrap-specific `PCC_BOOTSTRAP_PYTHON_IR_PASSES=...` override.

## Validation

Shell syntax and help smoke:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 10 \
  bash -n scripts/bootstrap.sh

env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 10 \
  bash scripts/bootstrap.sh --help
```

Both exited 0.

Stage1 bounded smoke:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 90 \
  zsh -lc 'out=/tmp/pcc_bootstrap_stage1_profile_$$; prof=/tmp/pcc_bootstrap_stage1_profile_json_$$; PCC_BOOTSTRAP_PROFILE_DIR="$prof" bash scripts/bootstrap.sh --backend self --stage 1 --out-dir "$out"'
```

Observed output included:

```text
runtime: PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py PCC_PYTHON_IR_PASSES=off --python-libpython off
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=26519
```

Profile result:

```json
{
  "compile_wall_ms": 25609,
  "publish_barrier_ms": 829,
  "wall_ms": 26519
}
```

## Open Boundary

This does not prove the full three-stage bootstrap gate is green after the
current source edits. It only restores the bounded IR-pass policy to the
`scripts/bootstrap.sh` path and verifies stage1 uses that policy.
