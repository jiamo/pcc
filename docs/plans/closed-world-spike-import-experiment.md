# Closed-World Spike — Import-Path Diagnostic Experiment

**Date:** 2026-04-27
**Outcome:** Negative — hypothesis was wrong but the failure mode is itself the answer.
**Decision:** Stop closure convergence as an active workstream. Accept hybrid bootstrap as a design choice.

## Hypothesis

After categorization (#217) showed ~76% of stage1 closure fallbacks
clustering around `self.builder.X` (LLVM IR builder ops) and `ir.X`
constructions, I conjectured the root cause was the dynamic
module-resolution chain:

```python
# pcc/llvm_capi/compat.py
ir = ir_py = _pick(USE_LLVMLITE_PY)[0]   # dynamic-resolved module ref
```

Since `_pick(...)` returns a tuple and `[0]` indexes it at module
import time, type inference cannot statically tell which module `ir`
points to. Therefore `ir.IRBuilder` is a dynamic getattr, the
`self.builder` assignment lands as `DynType`, and every one of the
1340 `self.builder.method(...)` call sites in `layer1.py` falls back
to `py_cpy_*`.

**Predicted fix:** change `from pcc.llvm_capi.compat import ir` to
`from pcc.llvm_capi import ir` in the four codegen files
(`layer1.py`, `runtime_abi.py`, `class_gen.py`, `marshal.py`). This
should let the type inferrer see `ir` as a known module and lower
`self.builder.method(...)` natively, eliminating ~7500 fallbacks
(~3000 actions + ~4500 plumbing tail).

## Action

Backed up the four files, applied the one-line import change in each,
re-ran `scripts/probe_stage1_closure.py` to regenerate the closure IR.

## Result

```
Per-module independent codegen pass: 12 / 16  (was 16 / 16)
Full multi-file compile:             FAIL    (was OK)

L1CodegenError: reference to unbound name 'ir' at
  pcc/py_frontend/codegen/layer1.py:227:1
```

Reverted all four files. Confirmed baseline (16/16, multi OK) restored.

## Root cause (the real one)

The codegen path treats `pcc.llvm_capi` as a *scaffold module* (it is
in `_SCAFFOLD_MODULES = {"pcc.extern", "pcc.llvm_capi", "pcc.unsafe"}`)
— meaning import statements from it are folded away at compile time
rather than lowered to `py_cpy_import`. **But the codegen has never
been taught how to lower references like `ir.IRBuilder`,
`ir.IntType`, `ir.Constant` natively** — only `pcc.extern` and
`pcc.unsafe` have explicit scaffold-lowering rules.

So today's working state is:

- `from pcc.llvm_capi.compat import ir` — `compat` is **not** in the
  scaffold set, so codegen treats this as a runtime import → emits
  `py_cpy_import` + `py_cpy_getattr` chains → 1340 `self.builder.X`
  calls all become `py_cpy_*`. Slow / fallback-heavy, but functionally
  correct: at runtime `ir` really is a module, CPython resolves the
  attributes.

- `from pcc.llvm_capi import ir` — `pcc.llvm_capi` **is** in the
  scaffold set, so codegen tries to fold the import away. With no
  scaffold-lowering rules for `ir.IRBuilder` etc., subsequent uses
  see `ir` as an unbound name → hard error.

In other words, swapping import paths is not a trivial fix. The real
work to make `self.builder.X` lower natively is:

1. Teach codegen that `ir.IRBuilder`, `ir.IntType`, `ir.PointerType`,
   `ir.VoidType`, `ir.DoubleType`, `ir.Constant`, `ir.Function`,
   `ir.Module`, `ir.GlobalVariable` and ~10 other `ir.X` classes are
   compile-time scaffold types.
2. Teach codegen what each of `ir.IRBuilder.{position_at_end,
   append_basic_block, store, load, branch, cbranch, icmp_signed,
   call, ret, ret_void, sext, zext, trunc, ...}` is — every method
   used by layer1 needs a native lowering rule.
3. Teach codegen that calling these methods at compile time produces
   IR text snippets, not runtime values.

That is a meta-circular `ir`-as-scaffold subsystem, similar to what
`pcc.unsafe` does for raw memory ops but a much larger surface. **The
work scale matches codex's earlier "several months" estimate for
extending the codegen native subset**.

## Decision

- **Closure convergence as an active workstream: stopped.** The
  cheapest possible diagnostic experiment showed the work boundary
  is `ir`-as-scaffold codegen subsystem, not type annotations or
  closed-world desugaring of user idioms. Closed-world specialization
  cannot bypass this.

- **Hybrid bootstrap is now an explicit design choice, not a debt
  to repay.** Stage 1/2/3 byte-identical bootstrap already works on
  macOS arm64 with the self backend (5× faster than LLVM in stage 1
  per gate Step A). The frontend stays interpreted by CPython —
  acceptable.

- **What to remove from "real remaining work":**
  - "frontend self-compile through `pcc.cli`" — this was the
    libpython-free goal that turns out to need an `ir`-as-scaffold
    codegen subsystem. Reframe as design choice.

- **What to keep:**
  - `scripts/probe_stage1_closure.py` and the two reports — they are
    the baseline if anyone ever wants to take this on.
  - Self-backend bootstrap promotion (Phase 4/5: stage-3 byte-identical
    + perf gates + CI) — orthogonal, still has clear payoff.

## What this experiment cost

- Backup + edit + probe + revert: ~15 minutes total.
- One IR generation cycle.
- Zero permanent code changes.

The negative result is the answer: it identified the actual cost
boundary (`ir`-as-scaffold codegen) without two weeks of speculative
desugaring work.
