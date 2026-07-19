# Self-backend platform capability verdict

Date: 2026-07-17

Task: `AUD-P1-PLATFORM-GUARD-CLAIM-AUDIT`

## Inventory and selected family

Platform guards outside already mode-labeled GPU paths include self-backend
target support, platform-specific binary inspection, macOS-arm64 bootstrap
baselines, and POSIX-only runtime/process behavior. The finite selected family
is LLVM-vs-self vector parity on the host self-backend target.

## Proven change

- `classify_self_backend_target_triple` consumes the existing canonical target
  registry; it does not duplicate architecture/OS conditionals.
- A verdict reports `SUPPORTED` or `UNSUPPORTED`, target identity, reason, and
  mandatory false `backend_executed` / `runtime_executed` fields. Capability
  presence alone is not execution proof.
- Unsupported triples produce an explicit
  `UNSUPPORTED[self-backend:<triple>]` reason rather than a generic skip.
- The vector-parity family uses that verdict. On the current Darwin arm64 host,
  the registered AArch64 emitter was selected and both integer-vector and
  pointer-vector LLVM-vs-self programs executed with hard parity assertions.
- An AST source guard rejects return to the raw boolean target guard for this
  selected family.

## Gate

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_self_backend_platform_verdict.py \
  tests/c/test_llvm_self_vector_parity.py -rs
```

Result: `5 passed in 1.31s` (current supported platform executed; no skips).

## Remaining schedulable families

Separate task-board rows retain macOS-arm64 bootstrap-baseline guards,
platform-specific binary inspection, and POSIX-only runtime/process guards.

## Claim boundary

This proves explicit platform capability classification and executed parity
only for the selected self-backend vector family on Darwin arm64. It does not
prove every self-backend instruction, Linux execution in this run, unsupported
targets, bootstrap, binary inspection, POSIX runtime behavior, or GPU support.
