# B3 classvar pack README

This pack advances B3 by adding runtime class-owned attributes.

Run:

```bash
bash scripts/run_classvar_goal_gate.sh
python -m pytest tests/data_model/test_classvar_source_shape.py
```

The parser/source-shape test is intentionally separate from the runtime gate:
source-level lowering still needs a dedicated codegen slice so class body
assignments and `C.count = ...` route to `py_obj_setattr` in no-libpython
binaries.
