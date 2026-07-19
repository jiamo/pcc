# no-libpython idiom sweep #2 — gap backlog (2026-06-25)

Produced by a 6-agent parallel idiom-diff workflow (`gap-sweep-and-patch`):
each agent compiled ~16-38 real-world idioms in its domain under
`pcc --python-libpython=off` and diffed vs CPython, then root-caused each
confirmed gap and emitted **apply-ready edits** (`old_string`/`new_string`).
The per-domain JSONs in this directory hold the full details incl.
`proposed_edits`, `root_cause`, `owning_files`, `confidence`.

**38 confirmed gaps.** These are workflow-confirmed (compiled + diffed by the
agents). RE-VERIFY each with your own probe before landing (an agent's
proposed edit is a strong starting point, not gospel — see the abs case below
where the proposed fix shape was right but the test had to be scoped).

Landing discipline is unchanged: one gap → both-tier mirror if runtime →
probe vs CPython → gc0 bootstrap → state-sync. Don't batch-land.

## Status

- **DONE 2026-06-25**: `abs(bignum)` → 0 (high/MISMATCH). Fixed
  (`numeric_builtin_lowering.py` IntType branch → `py_obj_abs`), gc0 bootstrap
  green. See `docs/current-goal-state.md`.
- **DONE 2026-06-25**: `float(bignum)` → 0.0 (high/MISMATCH). Fixed
  (`coercion_lowering.py` `_to_double` boxed-IntType branch → `py_float_to_f64`
  via `marshal_from_object(FloatType)` instead of lossy i64), gc0 bootstrap
  green. `tests/python/test_native_float_of_bignum.py`.
- **DONE 2026-06-25**: `divmod(bignum)` → (0,0) (high/MISMATCH). Fixed
  (`call_expression_lowering.py` int divmod path → `py_obj_floordiv`/`py_obj_mod`
  instead of lossy `_emit_expr_as_i64`; NULL→ZeroDivisionError preserved), gc0
  bootstrap green. `tests/python/test_native_divmod_bignum.py`.

- **DONE 2026-06-26**: bytes/bytearray `.partition(sep)` -> `(before, sep, after)`
  3-tuple (else `(copy-of-whole, b'', b'')`). Added `py_bytes_partition` BOTH
  tiers (mirror `py_str_partition`, same-family parts; tuple externs added to the
  port) + frontend dispatch. gc0 bootstrap + fallback baselines green.
  `tests/python/test_native_bytes_partition.py`.
- **DONE 2026-06-26**: bytes/bytearray `.split(sep)` -> list of same-family
  pieces. Added `py_bytes_split` BOTH tiers (mirror `py_str_split`, bytes parts
  via `bytes_new_same_family`; list-append externs added to the port) + frontend
  dispatch (err-check for the empty-separator ValueError). gc0 bootstrap +
  fallback baselines green. `tests/python/test_native_bytes_split.py`. Non-empty
  sep only; no-arg whitespace split still falls back.
- **DONE 2026-06-25**: bytes/bytearray `.rfind()` (highest match index, or a
  single byte value; `-1` if absent; `b"x".rfind(b"")`==len). Added
  `py_bytes_rfind` BOTH tiers (mirror `py_bytes_find` backward) + frontend
  dispatch. gc0 bootstrap + fallback baselines green.
  `tests/python/test_native_bytes_rfind.py`.
- **DONE 2026-06-25**: bytes/bytearray `.lower()` + no-arg `.strip()`. Added
  `py_bytes_lower`/`py_bytes_strip` BOTH tiers + frontend dispatch. gc0 bootstrap
  + fallback baselines green. `tests/python/test_native_bytes_lower_strip.py`.
  **KEY LESSON (bytes both-tier layout)**: the default no-libpython archive gets
  its bytes methods from `py/py_obj_stubs.py` (a PY_MODULE port), NOT from
  `src/py_bytes.c` — `py_bytes` is correctly EXCLUDED from `LIB_PCC_PY` (it's in
  the dead `PY_REPLACED_C_MODULES` var). So a bytes-method fix must mirror
  `src/py_bytes.c` (cc tier) AND the bytes section of `py/py_obj_stubs.py` (port
  tier). Do NOT add py_bytes.o to OBJ_PY_CC_HELPERS — `py_obj_stubs.o` already
  defines the bytes symbols, so it double-defines and the link fails. `strip(chars)`
  still falls back.
- **DONE 2026-06-25**: `str.count(sub, start[, end])` (range form) forced a
  libpython fallback (only 1-arg `count(sub)` was native). Fixed
  (`string_method_lowering.py`, both dyn + static paths): `s.count(sub,start,end)
  == s[start:end].count(sub)`, so slice via `py_str_slice` then `py_str_count`.
  Pure frontend, no runtime change. gc0 bootstrap green.
  `tests/python/test_native_str_count_range.py`. (ASCII-correct, like the
  existing byte-based py_str_count.)
- **DONE 2026-06-25**: `round(int, ndigits)` returned a float (`round(12345,-2)`
  -> `12300.0`) instead of an int. Fixed (`call_expression_lowering.py`): the
  2-arg round result is converted back to int when the first arg is int-typed
  (the value via `py_float_round_ndigits` was already correct, incl. banker's).
  gc0 bootstrap green. `tests/python/test_native_round_int_ndigits.py`. Exact for
  |value| < 2**53 (huge ints lose precision via the float round-trip — rare).
- **DONE 2026-06-25**: float literals with exponents lost precision
  (`1e100`->`1.0000000000000006e+100`, `6.022e23`->`6.0219999999999996e+23`) —
  both parsers (`py_parse.py`/`py_lift.py`) AND runtime `float(str)` shared a
  repeated-`10.0`-mult decimal->double. Fixed the parsers: scale the integer
  mantissa by an EXACT `10**net` and round once via the (just-fixed)
  `float(bignum)` for the common magnitude range (|exp| <= 308, correctly
  rounded); extreme tails (overflow->inf, subnormal `5e-324`) keep the graceful
  imprecise float-mult fallback (no crash). gc0 bootstrap green (bootstrap-
  critical parser path; pcc's own float literals re-parse byte-identical).
  `tests/python/test_native_float_literal_precision.py`. NOTE: runtime
  `float("1e100")` (str->double) still imprecise — same root, separate code
  path; documented follow-up. LIMITATION: `1e308`/`5e-324`/`1e-323` (edge of
  double range) imprecise as before.
- **DEFERRED `__context__` implicit chaining**: `raise Y` inside `except X`
  should set `Y.__context__ = X`. The `context` field + getattr exist, but the
  handler exc is only retained (`_active_handler_excs`) for NAMED handlers or
  bare-raise bodies — so unnamed `except X: raise Y` has no active exc to read.
  Needs retaining the handler exc whenever the body raises (GC-sensitive) + a
  `py_exc_set_context` call in raise lowering. Low value; deferred.
- **DONE 2026-06-25**: `finally` was SKIPPED on `continue`/`break` out of a try
  (only `return` ran pending finallys). Fixed (`stmt_dispatch_lowering.py`):
  each `loop_stack` frame now records the finally-stack depth at loop entry (3rd
  element, set at all 9 while/for push sites), and `break`/`continue` run the
  finallys entered inside the loop before jumping (`_run_loop_exit_finallys`).
  Boundary verified: a `finally` enclosing the loop does NOT run on inner
  break/continue. gc0 bootstrap green (loop lowering = high blast radius; pcc's
  own loops compile byte-identical). `tests/python/test_native_finally_continue_break.py`.
- **DONE 2026-06-25**: `e.args` on a builtin exception raised `AttributeError`.
  Fixed (both tiers, `py_obj_ops_dispatch` PY_TYPE_EXC getattr): expose `args`
  built from the stored `message` — `()` for an empty/None message (no-arg
  exception), else `(message,)`. gc0 bootstrap + fallback baselines green.
  `tests/python/test_native_exception_args.py` (port + cc). LIMITATION (message-
  only storage, shared root with multi-arg str(exc)): `Error(a, b).args` reports
  `(a,)` and `Error("").args` reports `()`; capturing args[1:] / an explicit
  empty-string arg needs a dedicated args-tuple field (frontend construction +
  layout + str/repr) — documented follow-up.
- **DONE 2026-06-25**: `finally` was SKIPPED when an `except` handler existed
  but none matched (the unmatched-exception "propagate" path branched to the
  outer handler without running `finally`). Python guarantees `finally` always
  runs. Fixed (`exception_lowering.py` propagate path emits `finally_body`
  before branching to outer, mirroring the no-handlers path). gc0 bootstrap
  green. `tests/python/test_native_finally_unmatched_handler.py`. (Bare
  `try/finally` with no handlers was already correct.) Remaining exception
  gaps: `e.args` on builtin exc (both-tier), `e.__cause__`/`__context__`
  (chaining), multi-arg `str(ValueError("a","b","c"))`, `finally`+continue/break.
- **DONE 2026-06-25**: `list(filter(None, iterable))` fell back to libpython
  (the map/filter materialize path required `args[0]` be a Name/function).
  Fixed (`list_builtin_lowering.py` `_maybe_emit_list_from_map_filter` special-
  cases `filter(None, ...)` → keep truthy elements). gc0 bootstrap green.
  `tests/python/test_native_filter_none.py`. SEPARATE gap left: bare
  `for x in filter(...)` (any predicate) is unsupported (`NameError: filter`) —
  the for-loop iterator path doesn't handle filter() at all; needs its own slice.
- **DONE 2026-06-26** (fresh sweep #3 find): `list(map/filter(lambda, it))` with
  an inline lambda raised `NameError: filter`/`map` (only named-fn + None
  predicate were handled). Fixed (`list_builtin_lowering.py`
  `_maybe_emit_list_from_map_filter`): bind the lambda's single param to each
  element and emit the lambda body INLINE (closes over the current scope),
  restoring the outer binding after. Frontend-only. gc0 bootstrap + fallback
  baselines green. `tests/python/test_native_map_filter_lambda.py`.
  NOTE: a fresh ~64-idiom diff sweep (batches A–E) otherwise found pcc's common
  no-libpython idiom coverage IDENTICAL to CPython — the cheap-slice vein is
  largely exhausted; remaining gaps are bigger (bytearray model, collections
  classes, bytes %-format, itertools module) or niche. Bare `for x in filter(...)`
  remains a separate unsupported path.
- **DONE 2026-06-26**: `math.factorial(n)` forced a libpython fallback. Added a
  frontend-inline lowering (`native_math.py`, registered as the `math.factorial`
  alias): a product loop via bignum-aware `py_int_mul` (n! overflows i64 at
  n>=21) with a negative-arg ValueError guard. No runtime change. gc0 bootstrap
  + fallback baselines green. `tests/python/test_native_math_factorial.py`.
  DEFERRED (bigger): bytearray mutators (append/insert/pop) need an in-place
  resizable buffer — pcc bytearray has no growable buffer (extend builds a NEW
  one), so true mutators need a bytearray object-model change.
- **DONE 2026-06-26**: `float(<str>)` at RUNTIME returned `0.0` (even for "3.14")
  — `float(DynType)` went through `py_float_to_f64`, which has no PY_TYPE_STR
  case. Fix: str-aware C-only helper `py_float_value_of` (strtod-based, raises
  ValueError on bad/partial string, else `py_float_to_f64`) + route
  `float(StrType)` / `float(DynType)` through it (`call_expression_lowering.py`).
  C-only -> both tiers, no port. gc0 bootstrap + fallback baselines green.
  `tests/python/test_native_float_of_str.py`. MINOR follow-up: StrLit
  `float("1e100")` still uses the compile-time fold (`_parse_simple_decimal_float`,
  2 copies) which is last-ULP imprecise for scientific notation.
- **NOT A BUG / deferred (`-0.0`)**: `print(-0.0)` shows `0.0`. The formatter
  `py_float_repr_shortest` (py_format.c:356-357) ALREADY handles negative zero
  correctly — the sign is lost UPSTREAM (the `-0.0` literal fold, unary `-` on a
  0.0, and/or the inline float-print path all reach the formatter with +0.0).
  Multi-site value-representation trace; very low value (negative-zero display).
  Deferred.
- **DONE 2026-06-25**: `reversed(dict)` yielded `<null>` per element (it
  reversed by positional `obj[i]`, which is key-lookup `dict[i]` for a dict).
  Fixed (`list_builtin_lowering.py` `_emit_reversed_builtin` DictType branch →
  reverse the `py_dict_keys` insertion-ordered list). gc0 bootstrap green.
  `tests/python/test_native_reversed_dict.py`.
- **DONE 2026-06-25**: `pow(int, negative_int)` returned `0` (should be float,
  `pow(2,-2)==0.25`). Fixed (`call_expression_lowering.py` 2-arg pow int path →
  `_emit_runtime_int_binop_value("**")` keeping the object, like the `**`
  operator, instead of `_emit_binop_int` which force-unboxed to i64). gc0
  bootstrap green. `tests/python/test_native_pow_negative_exponent.py`.
- **DONE 2026-06-25**: `str.rsplit()` 0-arg (whitespace) forced a libpython
  fallback (both rsplit sites gated `1 <= len(args) <= 2`). Fixed
  (`string_method_lowering.py` both sites → 0-arg routes to `py_str_split`
  NULL-sep, same as `split()`). gc0 bootstrap green.
  `tests/python/test_native_rsplit_no_args.py`. (Note: `rsplit(None, n)`
  whitespace+maxsplit is a separate gap, still falls back.)
- **DONE 2026-06-25**: bytes/bytearray `.startswith`/`.endswith` (prefix or
  tuple) raised `AttributeError` (not dispatched at all). Fixed
  (`method_call_expression_lowering.py` BytesType/ByteArrayType branch →
  `py_str_startswith`/`py_str_endswith`, which are already bytes+tuple-aware;
  box i64→bool object). gc0 bootstrap green.
  `tests/python/test_native_bytes_startswith_endswith.py`.

- **DONE 2026-06-25**: `hex/bin/oct(bignum)` — was a **C↔port DRIFT bug**
  (`py_int_based_repr` in `py_dunder.c` RAISED "not yet supported"; the port
  `py_dunder.py` silently returned the DECIMAL value). Fixed with a bignum
  base-N converter `py_bigint_to_base_cstr` in BOTH tiers
  (`py_int_decimal.{c,py}`, mirroring the decimal `py_bigint_to_cstr` via
  repeated divmod by the base — no PyObject refcounting, works on a bigint copy)
  + the `py_int_based_repr` bignum branch wired to it. Both tiers byte-match
  CPython (incl. negative bignum + `2**128-1`); gc0 bootstrap green + fallback
  baselines 18 passed. `tests/python/test_native_hex_bin_oct_bignum.py` (port +
  cc). **Bignum cluster COMPLETE** (abs / float / divmod / hex-bin-oct).

Bignum cluster remaining: NONE (complete).
- `round(int, -2)` → must return int (low; separate from the bignum cluster).

Other re-verified-real this session: `round(int,-2)`. The bignum quick wins
(abs/float/divmod) + bytes startswith are the cheap frontend ones; hex/bin/oct
is the remaining runtime-heavy one.

## Ranked backlog (high-confidence, frontend-only first)

`newfn` = needs a new runtime fn; `2tier` = runtime change needing C+port mirror.

| conf | kind | newfn | 2tier | idiom | owning file(s) |
|---|---|---|---|---|---|
| high | MISMATCH | 0 | 0 | `b"x".startswith((b"he", b"xx"))` / endswith tuple | method_call_expression_lowering.py |
| high | MISMATCH | 0 | 0 | `itertools.chain(a, b)` | native_modules.py |
| high | MISMATCH | 0 | 0 | `reversed(dict)` | list_builtin_lowering.py |
| high | MISMATCH | 0 | 0 | nested `try/except` inner-raise matched by outer | exception_lowering.py |
| high | MISMATCH | 0 | 0 | ~~`abs(bignum)`~~ **DONE** | numeric_builtin_lowering.py |
| high | MISMATCH | 0 | 0 | `-0.0` repr/str/literal | unary_call_lowering.py |
| high | MISMATCH | 0 | 0 | `float(2**70)` bignum→float exceeds i64 | coercion_lowering.py |
| high | MISMATCH | 0 | 0 | `pow(2, -2)` negative int exponent → float | call_expression_lowering.py |
| high | MISMATCH | 0 | 0 | `divmod(bignum, n)` | call_expression_lowering.py |
| high | COMPILE-ERR | 0 | 0 | `"  x ".rsplit()` 0-arg whitespace | string_method_lowering.py |
| high | MISMATCH | 0 | 1 | exception `.args` tuple unpack `raise V("m", 42)` | py_obj_ops_dispatch.c + port |
| high | MISMATCH | 0 | 1 | `str.join` over a **tuple** | py_str_accessors.c + port |
| high | COMPILE-ERR | 1 | 1 | `str.maketrans(x, y, z)` 3-arg (delete set) | unary_call_lowering.py + py_str_accessors {c,py} |
| high | COMPILE-ERR | 1 | 1 | `str.count(sub, start, end)` | string_method_lowering.py + runtime |
| medi | MISMATCH | 0 | 0 | `filter(None, xs)` | list_builtin_lowering.py |
| medi | MISMATCH | 0 | 0 | `set.difference_update(b)` | set_lowering.py |
| medi | MISMATCH | 0 | 0 | `finally` with `continue` in a loop | exception_lowering.py |
| medi | MISMATCH | 0 | 0 | `1e100` large-exponent float literal | parse/py_parse.py |
| medi | MISMATCH | 1 | 1 | `b"ABC".lower()` (bytes case methods) | method_call_expression_lowering.py + runtime |
| medi | MISMATCH | 1 | 1 | `b"..".rfind` / `.count` | method_call_expression_lowering.py + runtime |
| medi | MISMATCH | 1 | 1 | `b"  hi  ".strip()` | method_call_expression_lowering.py + runtime |
| medi | MISMATCH | 1 | 1 | `bytearray.append(x)` (+ extend/insert) | stmt_misc_lowering.py + runtime |
| medi | MISMATCH | 1 | 1 | `"a b c".rsplit(None, 2)` whitespace+maxsplit | py_str_accessors.c + port |
| low | MISMATCH | 0 | 0 | `collections.defaultdict(int)` | method_call_expression_lowering.py |
| low | MISMATCH | 0 | 0 | `super().__init__(*args)` shape | method_call_expression_lowering.py |
| low | MISMATCH | 0 | 0 | `collections.Counter` | class_gen.py |
| low | MISMATCH | 0 | 0 | exception `__context__` chaining | exception_lowering.py |
| low | COMPILE-ERR | 0 | 0 | `math.factorial(5)` | call_expression_lowering.py |
| low | MISMATCH | 0 | 1 | custom-exc `raise KeyError` matched as Exception | py_exc_objects.c + port |
| low | MISMATCH | 0 | 1 | `int("1_000")` underscore separators | py_int_parse.c + port |
| low | MISMATCH | 1 | 1 | `b"a,b".split(b",")` / bare split | method_call_expression_lowering.py + runtime |
| low | MISMATCH | 1 | 1 | `b"a=b".partition(b"=")` | method_call_expression_lowering.py + runtime |
| low | MISMATCH | 1 | 1 | `bytearray.pop()` | assignment_statement_lowering.py + runtime |
| low | MISMATCH | 1 | 1 | `b"val=%d" % x` bytes %-format | binary_op_lowering.py + runtime |
| low | MISMATCH | 1 | 1 | `str(ValueError("a","b","c"))` multi-arg repr | exception_lowering.py + runtime |
| low | COMPILE-ERR | 1 | 1 | `complex("3+4j")` from string | call_expression_lowering.py + runtime |
| low | MISMATCH | 1 | 1 | `round(int, -2)` must return int | call_expression_lowering.py + runtime |
| low | MISMATCH | 1 | 1 | `hex/bin/oct(bignum)` exceeding i64 | py_dunder.{c,py} |

## Notable clusters

- **bignum i64-truncation** (same class as the landed abs fix): `float(bignum)`,
  `divmod(bignum)`, `hex/bin/oct(bignum)`, `round(int,-2)`. All stem from a
  value-lane i64 reduction that should preserve/promote bignums. Likely each is
  a reroute-through-object-runtime fix like abs.
- **bytes methods** (largest cluster, 9): startswith/endswith-tuple, lower,
  rfind/count, strip, split, partition, %-format, bytearray mutators. Several
  runtime helpers already exist (the C `stringlike_bytes()` dispatch handles
  bytes), so many are frontend-dispatch-only.
- **exception edges**: nested re-raise matching, `.args` unpack, `__context__`,
  multi-arg `str(exc)`.

## Separate pre-existing bug found while landing abs (FILE/FOLLOW-UP)

`print(abs(True))` / `abs(bool)` typing: `type_infer` types `abs(bool)` as
`BoolType`, but CPython `abs(True)` is `int` (`1`). Because the result stays
BoolType, `print()` uses the bool-print path which emits `select <cond>, "True",
"False"` with a non-`i1` condition (the abs result, i64 or ptr) → **invalid IR**
(`select i64`/`select ptr`), which the self backend correctly rejects
(`BackendUnavailable: could not split '%.NN'`). NOT widened in the abs fix to
keep scope. Real fix: `type_infer` should infer `abs(int_or_bool)` → `int`, and
the bool-print path must only fire for an actual i1. Low priority (rare idiom).
