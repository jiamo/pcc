# README freestanding-libc and GUI surfaces

Date: 2026-08-12

Task: `DOC-P2-README-LIBC-GUI-SURFACES`

Source identity: repository HEAD
`ed6f1b30ebcc7f60f1eb84fd41fcd2d075d00743` plus the recorded dirty-worktree
documentation changes. This evidence does not make a clean-commit or release
claim.

## Claim boundary

The main README now makes the freestanding runtime/libc-ownership and native
GUI tracks discoverable without promoting either track beyond its current
evidence.

For libc ownership, it explicitly separates no-libpython from zero-libc,
labels the existing x86_64 Linux process-entry tracer as a host-pcc0,
self-backend, no-libpython bounded proof, leaves the full runtime/five-GC
closure open, and states that Darwin retains named libSystem ABI calls and is
not a zero-libc target.

For GUI, it describes the existing pcc-Python runtime modules and macOS
`mac_diff_app` canary, links the design and project guide, and retains the
current limitations: continuous interaction, pixel correctness, full text
metrics, large-file virtualization, and non-macOS portability are not claimed.
The README also states that the AppKit/Metal/libSystem/Objective-C bridge makes
the GUI route no-libpython but not zero-libc.

## Changed documentation

- `README.md`: highlights, status rows, freestanding runtime capability,
  native GUI capability and build example, repository-map entries, and
  documentation routes.
- `docs/goal/task-board.yaml`: finite documentation task and claim gates.

## Verification

All checks passed on the recorded dirty worktree:

- `gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate`
  -> `OK: 259 tasks validated`.
- `gtimeout 30s git diff --check -- README.md docs/goal/task-board.yaml docs/goal/evidence/2026-08-12-readme-libc-gui-surfaces.md`
  -> exit 0 with no output.
- A bounded existence check resolved all four newly linked documentation
  targets: the Linux tracer evidence, final no-C runtime investigation, GUI
  design contract, and `mac_diff_app` guide.
