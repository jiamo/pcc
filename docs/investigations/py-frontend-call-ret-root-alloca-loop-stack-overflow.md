# Investigation: call.ret.root alloca inside loop bodies leaks stack; hot loops SIGSEGV after ~500K call iterations

## Status

active

## Problem Description

Any pcc-compiled hot loop whose body contains a rooted user-function call
(`x = f(...)` where the result is a GC-managed pointer) crashes with SIGSEGV
after roughly 500K iterations — single-threaded, no recursion, correct
results until the crash. Found while building the shared-refcount contention
benchmark (shared-refcount-contention-thread-scaling.md): both the parallel
and the serial variant died instantly at 5M iterations.

## Repro

```python
# mini_d.py — crashes; passes with rounds=10
class Shared:
    def __init__(self, v: int) -> None:
        self.v = v

SHARED = Shared(7)

def touch(o: Shared) -> Shared:
    return o

def worker(idx: int, rounds: int) -> None:
    acc = 0
    i = 0
    while i < rounds:
        s = touch(SHARED)
        acc = acc + s.v
        i = i + 1
    print("t" + str(idx) + " acc=" + str(acc))

def main() -> None:
    worker(0, 5000000)

if __name__ == "__main__":
    main()
```

Compiled via `compile_python(ir_scaffold_mode="on", libpython_mode="off")`
(threaded C runtime archive, but the crash is independent of threading):
exit -11 (SIGSEGV), no output. `rounds=10` prints `t0 acc=70` and exits 0.

## Test [CONFIRMED]

LLDB, 2026-08-07:

```text
EXC_BAD_ACCESS (code=2, address=0x16f603ff0)      <- stack guard page
frame #0: py_incref            (crash in prologue `stp x20, x19, [sp,#-0x20]!`)
frame #1: pcc_gc_store_root + 348
frame #2: user_mini_d_touch + 128
frame #3: user_mini_d_worker + 200
frame #4: user_mini_d_main + 100
frame #5: main + 3840
```

Only 6 frames — NOT recursion. Disassembly of `user_mini_d_worker` shows the
cause directly after the call in the loop body:

```text
+196: bl   user_mini_d_touch
+200: mov  x1, x0
+204: mov  w8, #0x1
+208: lsl  x8, x8, #3        ; 8 bytes
+212: add  x8, x8, #0xf
+216: and  x9, x8, #~0xf     ; aligned to 16
+220: mov  x8, sp
+228: mov  sp, x0            ; alloca executed EVERY iteration
...
+552: sub  sp, x29, #0x10    ; stack only reclaimed at function exit
```

Root cause: `_call_user` (pcc/py_frontend/codegen/unary_call_lowering.py,
`call.ret.root` slot) emits `self.builder.alloca(...)` at the current insert
point. When the call site is inside a loop body, that alloca instruction
re-executes every iteration; LLVM only restores the stack pointer at function
exit, so each iteration permanently burns 16 bytes of stack. 8MB main-thread
stack / 16B = ~500K iterations to the guard page. The paired
`enter_lifo`/`leave_lifo` frame registration is balanced per iteration — only
the stack memory leaks.

Introduced by f4922050 (2026-06-13, "frontend: GC-root & lowering rework for
the closed-world self-host path"). The bootstrap never crossed the threshold
because compiler loops are far smaller than 500K rooted calls per function
invocation, so this stayed invisible until a long-running-hot-loop benchmark
existed (which is exactly the T-track long-running claim surface).

## Proposals

- No.1 Hoist the call.ret.root slot to the entry block via `_alloca_in_entry`   [CONFIRMED, not landed]

## No.1 Hoist the call.ret.root slot to the entry block via `_alloca_in_entry`

### Code Change

In `_call_user`, replace the call-site `self.builder.alloca(result.type,
name=...)` with the existing `self._alloca_in_entry(result.type, ...)` helper
(core_helpers.py:147 — already handles the self-host builder-alias and
insertion-cache subtleties). The per-use `store null` stays at the call site:
the slot is re-nulled after every use, so the store is a no-op on re-entry
but keeps the first-use `pcc_gc_store_root` from decref'ing garbage. One
entry-block slot is then reused across all iterations; enter/leave pairing is
unchanged.

### CONFIRMED

Applied experimentally in-session: mini_d went from SIGSEGV to
`t0 acc=35000000`, rc=0, at 5M iterations; the full contention benchmark
matrix in shared-refcount-contention-thread-scaling.md was measured with this
change in place (20M-iteration serial runs, 4x5M parallel runs, all correct
on PCC_GC_BACKEND=0..4). The change is currently NOT in the tree —
unary_call_lowering.py retains the call-site alloca — so the crash repro
above still reproduces on current source; landing the one-line hoist (plus a
regression test that runs a >1M-iteration rooted-call loop, and the bootstrap
gates required for a unary_call_lowering.py touch) is the open work.

## Open boundaries

- Fix not landed; repro still fires on current source.
- Sibling call-site allocas were not audited (`return_lowering.py:134
  ret.tmp.root` executes once per call — benign; `name_lowering.py:277
  range.idx.addr` unchecked for in-loop positioning).
- Bootstrap gates for any landing of this change are mandatory
  (unary_call_lowering.py is on the bootstrap-critical list).

## Update 2026-08-07 (later the same day)

The `_alloca_in_entry` hoist is now IN the tree
(unary_call_lowering.py `_call_user`). Re-verified on current source: the
5M-iteration repro compiles and prints `t0 acc=35000000`, rc=0. Regression
landed at tests/python/test_call_ret_root_loop_stack.py (5M-iteration
rooted-call loop, asserts rc=0 + exact result; `1 passed in 1.29s`, -n0).
Remaining before `resolved`: the stage1..3 bootstrap gates for the
unary_call_lowering.py touch, and the sibling-alloca audit above.
