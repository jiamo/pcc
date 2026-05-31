# goal native stdlib system surface

This pack expands system-adjacent native stdlib modules.

## sys / platform

Adds tuple-ish version_info, streams, implementation metadata, encoding helpers,
and platform uname/python_version helpers.

## tempfile / shutil

Adds practical host-friendly fallbacks for temporary files/directories and
copy/move/rmtree/copytree.

## subprocess

Routes run/check_output/check_call to existing runtime helpers:

- `py_subprocess_run`
- `py_subprocess_check_output`

Gate:

```bash
bash scripts/run_stdlib_system_goal_gate.sh
```

Still open: full no-libpython subprocess text/capture semantics and native
tempfile unlink cleanup.
