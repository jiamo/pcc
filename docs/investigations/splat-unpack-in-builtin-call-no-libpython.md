# Investigation: *iterable splat in a builtin call / *args forwarding (no-libpython)

## Status
resolved 2026-05-30 for the common cases: `print(*items)` (#62, No.1),
`g(*xs)` into `def g(*args)` (#63, No.3), `zip(*matrix)` (#64, No.2). Only
`max(*items)` / `min(*items)` remain — RARE (people write `max(items)`;
`max(*items)` is redundant and has 0/1-element edge-case semantics), documented
as low-priority below.

## Problem Description
In strict no-libpython mode, a positional `*iterable` splat in a CALL works for
a user function with FIXED params and for `**kwargs`, but FAILS for builtin
calls and for forwarding into a `*args` parameter:

```
zip(*matrix)        # runtime "NameError: name '*' is not defined"
print(*items)       # runtime "NameError: name '*' is not defined"
max(*items)         # runtime "NameError: name '*' is not defined"
g(*xs)  where def g(*args): ...   # runs but g receives EMPTY args (sum -> 0)

f(*[1,2,3])  where def f(a,b,c): ...   # WORKS (splat expands into fixed slots)
f(**{"a":1,"b":2,"c":3})               # WORKS (**kwargs handled)
```

Found 2026-05-30 by realistic probes real28 (`zip(*matrix)` transpose) and the
real30 batch.

## Repro
```bash
printf 'def main():\n    m=[[1,2,3],[4,5,6]]\n    print([list(c) for c in zip(*m)])\nmain()\n' > /tmp/z.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/z.py -o /tmp/z_bin
/tmp/z_bin            # NameError: name '*' is not defined
python3 /tmp/z.py     # [[1, 4], [2, 5], [3, 6]]
```
NB: the repro MUST call `main()` — see the methodology note below.

## Root cause
`*m` in a call is lifted as a synthetic `Call(func=Name("*"), args=(m,))` marker
(there is no `Starred` AST node). `call_arg_lowering.py` has the machinery to
expand it — `_is_starred_unpack` / `_has_starred_unpack` /
`_emit_pcc_args_list` (builds a runtime list, `py_list_extend` for the starred
part) — and the user/object call paths (`call_object_lowering.py:66-69`,
`call_resolution_lowering.py`) DO use it. But the **builtin-specific** lowerings
process `expr.args` directly:
- `print_lowering.py::_emit_print_many` builds a FIXED-size tuple and
  `self._emit_expr(arg)` each — on the `Call(Name("*"), ...)` marker it emits a
  `Name("*")` lookup → "NameError: name '*'".
- `zip` / `max` / etc. likewise.
And the `*args`-binding path drops the forwarded list (g receives empty args).

## Test [N/A yet]
Probe files under /tmp/gap_probe/x_*.py (zip/printstar/maxstar DIFF=Traceback;
userfn/kwargs IDENTICAL; argsfwd DIFF=0). A regression test goes in with the fix.

## Proposals
- No.1 print(*items): build args via _emit_pcc_args_list, convert list→tuple, py_print_many   [CONFIRMED #62]
- No.2 zip(*matrix): runtime transpose via a py_zip_star helper  [CONFIRMED #64]  (max/min(*) still pending — rare)
- No.3 g(*xs) into def g(*args): fix the *args-binding to receive the forwarded list  [CONFIRMED #63]

## No.2 zip(*matrix) [CONFIRMED #64]
### Code Change
New C-only helper `py_zip_star(rows)` in py_runtime/src/py_call_splat.c (reuses
the file's static `pcc_sequence_len/get_for_splat` + `py_obj_len`/`py_obj_getitem`):
transposes the splat sequence's elements into a list of tuples, truncated to the
shortest row; refcount mirrors py_call_merge_posargs (set_item/append take their
own ref, decref locals). py_call_splat.c is already OBJ_PY_CC_HELPER (C in both
modes, no port). Declared in py_runtime.h, registered in runtime_abi.py.
tuple_zip_lowering.py `_maybe_emit_zip_builtin` routes a single `*splat` arg
(`_is_starred_unpack`) to it; normal `zip(a, b, ...)` keeps the static path.
### CONFIRMED
`/tmp/gap_probe/zipstar.py` IDENTICAL (transpose comprehension + list(zip(*m)),
ragged truncation, 3 rows, normal-zip regression, `nums, lets = zip(*pairs)`
unzip). `tests/python/test_native_zip_star.py` 2 passed. FULL bootstrap 18/4.

## No.3 g(*xs) into def g(*args) [CONFIRMED #63]
### Root
`_expand_direct_call_unpacks` STATICALLY expands a splat into a known number of
fixed positional slots; a *args param needs the splat's runtime-many elements,
so `needed` computed to 0 and the splat was silently dropped → empty *args.
### Code Change (call_resolution_lowering.py)
When a runtime-sized splat (`star_count is None`) feeds the *args param ENTIRELY
(`len(plain_pos) == len(positional_formals)`, no `**kwargs`), emit a synthetic
`Call(Name("__star_to_varargs__"), (star_src,))` marker rather than dropping it;
the resolver, seeing that single marker as the *args extra, sets
`resolved[var_pos_idx] = Call(Name("tuple"), (star_src,))` — the *args tuple IS
`tuple(star_src)` (reuses the working `tuple()` builtin). Splats that also fill
fixed slots / span fixed+*args / co-occur with `**kwargs` keep static expansion.
### CONFIRMED
`/tmp/gap_probe/argsfwd.py` IDENTICAL (g(*xs), list-literal splat, h(1,*xs),
wrapper(*args)->g(*args), g(*range(4)), + direct/fixed regressions).
`tests/python/test_native_args_forwarding.py` 2 passed. FULL bootstrap 18/4 —
call_resolution_lowering.py is bootstrap-critical (every user call) and stays
green, confirming the tight gating leaves normal calls untouched.

## No.1 print(*items) [CONFIRMED #62]
### Code Change (print_lowering.py)
`_emit_print_call` routes a starred-arg print (kwargs limited to sep/end) to new
`_emit_print_many_splat`: `lst = _emit_pcc_args_list(call.args, ...)` (expands
every splat + appends plain args), `tup = py_call_merge_posargs(NULL, lst)`
(the EXISTING call-splat helper merges an empty base + the list's elements into
a new tuple — no new runtime helper needed), then `py_print_many(tup, sep, end)`
GC-rooted like the fixed-arity path.
### CONFIRMED
`/tmp/gap_probe/printsplat.py` IDENTICAL (print(*items), print(*[...]), mixed
print('x',*items,'y'), print(*range(3)), sep=/end=, no-splat regressions).
`tests/python/test_native_print_splat.py` 3 passed. FULL bootstrap 18/4
(print_lowering.py is bootstrap-critical, green).

## Notes for the fix iteration
- `py_print_many` is TUPLE-specific (`load_i64(tup,16)` length, elems at
  `tup+24+i*8`) — a list cannot be passed directly; convert list→tuple first
  (need/confirm a `py_list_to_tuple` or build a tuple from the list).
- Prefer a GENERAL mechanism if clean: a builtin call with `_has_starred_unpack`
  could materialise its args to a tuple once, then each builtin consumes the
  tuple. But print/zip/max have different runtime entries, so this may stay
  per-builtin.
- The user-fixed-param path already works; mirror its expansion for the
  `*args`-binding path.

## METHODOLOGY NOTE (important — caused false passes here)
A printf-based idiom probe MUST include the `main()` call. The first scoping
batch wrote `printf '%b\n' 'def main():\n    ...print(...)'` WITHOUT a trailing
`main()`, so `main` was defined but never executed → the program printed nothing
→ pcc and python3 both produced empty output → `diff` reported IDENTICAL (a
FALSE PASS). Five "splat works" results were spurious; re-running with
`printf '%b\nmain()\n' '...'` showed the real failures. Always: (1) `%b` not
`%s` (newlines), and (2) append `main()` (execution). See
[[feedback_idiom_diff_methodology]].
