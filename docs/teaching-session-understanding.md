# Teaching Session Understanding Checklist

This document tracks what the human should be able to explain before this
session is considered understood. It is intentionally a learning artifact, not
completion evidence for the engineering goal.

## Session Scope

- Active long goal: fully implement `docs/goal/goal-prompt.md` without shrinking the
  goal to one passing slice.
- Recently taught/requested slice: `PYTEST-P0-PCC1`, the pcc1-native pytest
  workflow requirement.
- Current repository audit has moved on to `V-P1-VAL`; the pcc1 pytest slice is
  `DONE_WEAK`, not total-goal completion.

## Mastery Checklist

### 1. Problem Understanding

- [ ] Explain why `pcc1 --pytest` as a host `uv run pytest` launcher was not
      acceptable evidence that pcc1 can replace Python.
- [ ] Explain why the user required this as P0.
- [ ] Explain the difference between:
  - host CPython running pytest,
  - pcc1 launching host pytest,
  - pcc1 compiling and running a native pytest-compatible subset.
- [ ] Explain why unsupported Python/test constructs must be added to the pcc1
      subset rather than bypassed by editing C-related tests or runtime code.
- [ ] Explain why the current result is `DONE_WEAK`, not `DONE_STRONG`.

### 2. Solution Understanding

- [ ] Explain how the pcc1-native pytest subset discovers `test_*` files.
- [ ] Explain how marker selection works for default `not integration` and
      explicit `-m integration`.
- [ ] Explain why generated runner files inject `pcc.test_runner.run_tests(...)`.
- [ ] Explain why module-level `pytestmark = pytest.mark.integration` is used
      for selection and then rewritten to inert runner source.
- [ ] Explain how literal `@pytest.mark.skip`, `@pytest.mark.skipif(True, ...)`,
      and `@pytest.mark.skipif(False, ...)` are handled.
- [ ] Explain why dynamic skipif, xfail, full pluggy, fixture discovery, and
      assertion rewriting remain out of claim.

### 3. Edge Cases And Failure Modes

- [ ] Explain the failure mode where a selected native runner tries to evaluate
      `pytest` at runtime and gets `NameError`.
- [ ] Explain why literal `skipif(True)` must prevent a failing test body from
      being compiled into the selected run list.
- [ ] Explain why accepting metadata decorators in lowering is separate from
      actually implementing all pytest semantics.
- [ ] Explain what a future red pcc1-backed pytest gate should trigger:
      implement the pcc1 subset or record a precise frontier, not weaken the
      tests.

### 4. Broader Context

- [ ] Explain how this supports the north-star claim that pcc1 can replace
      Python for key workflows.
- [ ] Explain why pcc1/no-host evidence matters more than host-pcc evidence for
      replacement claims.
- [ ] Explain why the current active audit can move to V-track while the pytest
      slice remains only weakly complete.
- [ ] Explain how overclaiming would damage the project: a passing subset is not
      the same as full pytest, full pcc1 replacement, full package ecosystem, or
      full value-model completion.

## First Diagnostic Prompt

Before any explanation from the agent, the human should restate her current
understanding in her own words:

1. What was wrong with the old `pcc1 --pytest` behavior?
2. What did the pcc1-native pytest subset change?
3. Why is the result still `DONE_WEAK`?
4. What would count as an invalid shortcut for the next red pytest case?

Current baseline:

- 2026-06-05: Human replied that she completely does not know. Teaching starts
  from the problem model rather than assuming prior context.

## Quiz Bank

Use these only after the human has attempted the first diagnostic prompt.
Do not reveal answers before the human responds.

1. Multiple choice: Which statement is strongest evidence for pcc1 pytest
   replacement progress?
   - A. Host CPython runs `uv run pytest`.
   - B. pcc1 invokes `uv run pytest` as a subprocess.
   - C. pcc1 compiles selected pytest-shaped tests with `--python-libpython=off`
        and runs the native binary.
   - D. A test file imports `pytest` successfully under CPython.

2. Open ended: Why does `pytestmark = pytest.mark.integration` need special
   handling in generated runner source?

3. Multiple choice: A future pcc1-backed pytest gate fails on
   `@pytest.mark.xfail`. What is the correct next move?
   - A. Delete the xfail from the repository test.
   - B. Route that test through host pytest and still call the pcc1 gate green.
   - C. Implement or explicitly frontier xfail support in the pcc1 subset.
   - D. Change C runtime behavior to avoid the failure.

4. Open ended: Why is `DONE_WEAK` the honest status even after several pcc1
   pytest smoke tests pass?
