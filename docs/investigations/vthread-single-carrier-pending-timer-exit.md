# Investigation: a single carrier exits with sleeping children pending

## Status
active

## Problem Description
The dashboard compiled successfully but printed no success marker. A small
probe disproves the earlier gateway attribution to an unexecuted plain def:
main and the child both start, but run returns while its timer is pending.

## Repro
`gtimeout 90s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 --tb=short --show-capture=no tests/python/test_vthread_gateway_regressions.py::test_single_carrier_run_waits_for_sleeping_children`

## Test [CONFIRMED]
The child sleeps for 20 ms and returns 42. Before the fix, a self/no-libpython
artifact prints outcome 0 and result None (2.97s test). A diagnostic probe
with a plain main prints MAIN, WORKER_START, STEPS 1, OUTCOME 0, RESULT None.

## Proposals
- No.1 Include pending timers in single-carrier idle handling [DENIED]
- No.2 Drive a selected root task from the application [pending]

## No.1 Include pending timers in single-carrier idle handling
### Code Change
Read the next timer deadline under the scheduler lock (sorted-list head in
pcc-Python, heap peek in the C oracle). Limit an IO wait to that deadline, or
wait the idle carrier when only timers remain. Preserve the successful-step
budget and stop when both timer and IO queues are empty. This change covers
the single-carrier run path; persistent/multicarrier scheduling is unchanged.
Gateway probes now reject nonterminal tasks and nonzero results explicitly.
### DENIED
The existing cooperative-cancellation test calls run() to let an infinite
sleeper park, then cancels it. Waiting for timers inside run() prevents the
caller from reaching cancel and times out after 30 seconds. The runtime change
was removed. run() is a stepping API, not a run-to-completion contract.
The regression now preserves pending-timer return-to-caller behavior.

## No.2 Drive a selected root task from the application
### Code Change
The gateway's run_until_complete helper repeatedly steps the scheduler until
its selected root is terminal. When no work ran, it waits 1 ms on the outer
driver thread. User virtual threads continue using sleep_current. It propagates
the selected child's error and rejects cancellation instead of returning None.
### pending
Full native dashboard and benchmark checks pending.
