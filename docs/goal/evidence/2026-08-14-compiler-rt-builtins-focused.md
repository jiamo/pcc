# Compiler-rt builtin inventory focused evidence — 2026-08-14

Mode: current host inventory and supported-self-target differential.

Command:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/c/test_compiler_rt_builtin_inventory.py
```

Result: 4 passed. The checked inventory maps the selected helper families to
pcc owners and pinned compiler-rt oracle sources; signed/unsigned i64
division/modulo rows include boundary cases such as `INT64_MIN / -1` and keep
the x86 trap guard explicit.

The other supported self target, i128 legalization and broader compiler-rt
adoption are not proven by this focused run.
