# Investigation: pcc1 self-host UAF caught in `_parse_float_literal_lift`

## Status

**Open.** pcc1 self-host crashes deterministically with macOS nano-allocator
heap corruption while compiling `pcc/__main__.py`.  Probe instrumentation
narrowed the symptom to a stale `py_decref` inside
`_parse_float_literal_lift`'s scope-exit cleanup, but minimal repros built
from the same source patterns do **not** crash — the bug requires the full
pcc1 heap allocation sequence to manifest.

## Reproducer

```bash
uv run pcc --ir-scaffold=on --python-libpython=off --backend self \
    pcc/__main__.py -o pcc1
./pcc1 --ir-scaffold=on --python-libpython=off --backend self \
    pcc/__main__.py -o pcc2
# pcc2: heap corruption, SIGABRT
```

Crash line (under `script` capture, since macOS sends to /dev/tty):
```
pcc1(NNN,...) malloc: Heap corruption detected, free list is damaged at 0x...
*** Incorrect guard value: NNN
```

## Backtrace from `lldb` (default malloc, no MallocScribble / libgmalloc)

```
nanov2_guard_corruption_detected
nanov2_allocate_outlined
py_instance_new + 60
user_pcc_parse_py_lift__Lifter__s_Return + 612
user_pcc_parse_py_lift__Lifter_lift_stmt + 632
...
```

The malloc reports the corrupted free list during a later `py_instance_new`
call (when `Lifter._s_Return` is allocating a `pa.Return` instance).  The
allocator detects the bad guard byte; the free list was corrupted earlier.

## Probe finding

A diagnostic probe added to `py_obj.py::py_decref` and
`pcc_threads.c::pcc_debug_bad_incref` (now reverted; see git log of this doc
to recover) catches the **first** `py_decref` on a stale pointer:

```
[BAD_INCREF] o=0x600058cec6f0 tag=2043
```

`tag=2043` (0x7FB) is not a valid `PY_TYPE_*` value — the object's memory
has already been freed and reused.  Memory dump at the stale address showed
a NEW `PyStrObject` (rc=1, tag=4) starting 48 bytes later, confirming the
old chunk was reallocated as a string.

Backtrace at `pcc_debug_bad_incref` trap:

```
pcc_debug_bad_incref
py_decref + 212
pcc_gc_release + 16
user_pcc_parse_py_lift__parse_float_literal_lift + 1280
user_pcc_parse_py_lift__Lifter__e_Num + 1844
user_pcc_parse_py_lift__Lifter_lift_expr + 148
user_pcc_parse_py_lift__Lifter__s_Return + 748
```

The released local is at `[x29, #-0x50]`, which assembly inspection
identified as the function's `mantissa` slot.

## Source under investigation

```python
def _parse_float_literal_lift(text: str) -> float:
    exp = 0
    mantissa = text                        # alias 1
    lower = text.lower()
    e_idx = lower.find("e")
    if e_idx >= 0:
        mantissa = text[:e_idx]            # reassign new
        exp = int(text[e_idx + 1:], 10)
    dot_idx = mantissa.find(".")
    frac_len = 0
    digits = mantissa                      # alias 2 (often alias-of-alias)
    if dot_idx >= 0:
        frac_len = len(mantissa) - dot_idx - 1
        digits = mantissa[:dot_idx] + mantissa[dot_idx + 1:]
    if not digits:
        digits = "0"
    value = float(int(digits, 10))
    ...
    return value
```

Disassembly confirms branch 2 of `if e_idx >= 0` stores `text` into
`mantissa` slot **without** an `incref`:

```
ldur   x9, [x29, #-0x8]    ; load text
str    x9, [x13]            ; sp[0] = text (no incref)
...
stur   x9, [x29, #-0x50]   ; mantissa = text
```

At function exit, `pcc_gc_release(mantissa)` decrefs the alias.

## What didn't reproduce the bug

Eight C-level container-helper balance tests
(`tests/test_gc_store_ptr_balance.py`) and eleven pcc-self-compile container
stress tests (`tests/test_self_compile_container_stress.py`) all pass
without crash.  In particular, the following minimal patterns ran 100k+
iterations cleanly:

- `def f(text: str): s = text; return s` (alias + return)
- `def f(text: str): mantissa = text; if cond: mantissa = text[:1]; return len(mantissa)`
  (alias + conditional reassign + length)
- `def f(text: str): a = text; b = a; return len(a) + len(b)` (double alias)
- The exact source of `_parse_float_literal_lift` invoked 100k× on a
  heap-allocated `Holder.text = "3" + ".14"` string

So the alias-pattern hypothesis alone is **insufficient**.  The bug
requires either (a) interaction with other heap allocations in the same
process, or (b) a different code path I haven't yet isolated.

## Hypotheses still on the table

1. **Multi-level alias decref**: `digits = mantissa` after `mantissa = text`
   means digits is alias-of-alias.  At function exit, decrefing both
   without proper ownership tracking leaks 2 decrefs on `text`.  Minimal
   repro of this pattern alone passes, but the full
   `_parse_float_literal_lift` plus surrounding allocations may differ.

2. **Recursive `_pow10f_lift` interaction**: each call to
   `_pow10f_lift(frac_len)` allocates intermediate floats / boxed ints
   that may interact with mantissa/digits lifetime.

3. **Bug elsewhere, surfaces here**: heap corruption may be caused by an
   unrelated function earlier in pcc1's execution, with the
   `_parse_float_literal_lift` exit decref being the first observer.

## Next steps

- Add `MallocStackLogging=stack` + `malloc_history(addr)` post-mortem to
  identify what allocated/freed the chunk at the stale address before the
  bad `py_decref`.
- Diff the codegen IR of `_parse_float_literal_lift` between
  `8ecac2a9` (pre-`3952f6e5 fix`) and HEAD to check if the
  `pcc_gc_store_ptr` migration changed local-variable lifetime emission.
- Bisect by reverting individual files from `3952f6e5` to find the
  smallest change that re-enables clean self-host.

## What's already verified safe

- C runtime container helpers (`libpy_runtime.a`):
  `tests/test_gc_store_ptr_balance.py` — 8/8 pass.
- pcc-py runtime container helpers in self-compile context:
  `tests/test_self_compile_container_stress.py` — 11/11 pass.

Both suites cover dict update, set rehash, list set replace, tuple-of-instances
dealloc, instance field replace, nested instances, frozen dataclass with
inheritance, `getattr(obj, name, default)`, `dict.get + sorted + add try/except`,
and the lifter-like recursive pattern.

The bug is **not** in any of those isolated paths.
