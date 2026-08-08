# Native subprocess provider closure repair

Date: 2026-07-28

Task: `BUG-P0-NATIVE-SUBPROCESS-CALLED-PROCESS-ERROR`

Source identity: current worktree based on `d82e5816`, Darwin arm64, Python
3.13.2. Modes exercised include explicit multi-file compilation,
`--python-libpython off`, native subprocess lowering, and a current-source
self-backend pcc1.

Failure:

- Native `check=True` lowering emitted hard references to the pcc-Python
  `subprocess.CalledProcessError`.
- Explicit `compile_python_multi(..., recursive_stdlib=False)` omitted that
  required provider and failed to link the bootstrap `pcc_multi.py +
  pipeline.py` pair.
- The terminal failure was two undefined provider symbols; repeated target
  triple messages were warnings.

Change:

- Required native semantic providers are now admitted independently of
  optional recursive stdlib expansion.
- The shallow multi-file contract remains shallow; callers and tests do not
  manually name `pcc/py_stdlib/subprocess.py`.

Evidence:

- Original bootstrap-pair E2E: RED with undefined provider symbols, then
  `1 passed in 97.53s`.
- Complete bootstrap-shim file: `93 passed in 362.05s`.
- Multi-file compile file: `40 passed in 20.56s`.
- Shallow/recursive stdlib contracts: `37 passed in 4.50s`.
- Native subprocess field/status gates: `10 passed in 4.11s`.
- Current-source pcc1 exit forwarding: `1 passed, 57 deselected in 174.04s`.
- Fallback ratchets: `27 passed in 262.80s`.

Supported claim: explicit shallow multi-file builds now receive mandatory
native provider link edges while preserving exact no-libpython
`CalledProcessError` fields and child status forwarding.

Not proven: complete default/integration suites, five-GC fixed point, or a
clean published release claim.
