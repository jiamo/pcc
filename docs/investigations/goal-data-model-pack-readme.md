# Goal data-model pack README

This pack is a goal-ordered continuation.

It targets:

- No.20 B1 bytes literal / native bytes
- No.21 B2 type(x) / type(x).__name__

It intentionally does not include B3 because `Cls.count` needs a class-owned
attribute storage model.  Reusing the current method table would either store
borrowed values unsafely or change method ownership semantics, so B3 should be
implemented as its own runtime/class-layout slice.

Run:

```bash
bash scripts/run_data_model_goal_gate.sh
```
