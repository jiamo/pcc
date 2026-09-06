# Investigation: initial installer timeouts lose output and can orphan helpers

## Status
resolved

## Problem Description
The installer's canary/dependency checks captured output in memory and wrote
logs only after subprocess.run returned. TimeoutExpired skipped those writes
and killed only the immediate process. A separate outer timeout also wrapped
the process-tree sampler and could kill that owner before it cleaned a compiler
running in a separate process group.

## Repro
Review scripts/install_pcc1_toolchain.py's former subprocess.run boundaries.
The focused timeout reproducer launches a Python child that starts a detached
sleeping descendant, prints stdout/stderr, then exceeds the watchdog.

## Test [N/A]
The original leaking installer path was identified statically. The repaired
real subprocess boundaries are exercised by
`test_logged_timeout_keeps_output_and_terminates_detached_descendant` and
`test_terminating_installer_watcher_cleans_its_detached_child`; both pass.

## Proposals
- No.1 Use one in-process repository sampler for each command [CONFIRMED]

## No.1 Use one in-process repository sampler for each command
### Code Change
run_logged uses run_process_tree_sample directly for source stages and installed
checks. Output is durable while the command runs. Removing the outer watcher
process removes its premature-kill boundary; SIGTERM requests the sampler's
existing cooperative interrupt cleanup, as SIGINT already does.
### CONFIRMED
Both timeout and externally terminated watcher tests retain output and terminal
receipts and verify that the detached child is gone or awaiting reaping. A
success test also verifies that the caller environment is restored.

## Report
This is process-lifecycle evidence from small host-Python subprocesses. It does
not claim a completed source installation or bootstrap. Work is tracked in pcc
issue #186; no existing installation was modified.
