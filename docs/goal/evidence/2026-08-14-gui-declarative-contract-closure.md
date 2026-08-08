# GUI-P2-DECLARATIVE-CONTRACT closure evidence — 2026-08-14

Mode: versioned design contract and executable model/source guards only.  This
does not claim that the downstream GUI runtime is implemented or rendered.

Command:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/test_gui_declarative_design.py
```

Result: `15 passed in 0.05s`.  The tests cover versioned layout/ownership and
capacity tables, keyed identity replacement, replay/retry ownership, commit
phases, listener removal, style generations/candidates, command completion,
and application cancellation transitions.  The pinned reference inventory and
upstream-absorption boundary are part of the checked source contract.

The task is design-only; implementation remains in its downstream task rows.
