# Class-attribute method-value load: candidate-path result not GC-rooted

## Status

ROOT-CAUSED, NOT FIXED (2026-06-22). Reproduces on host pcc (pcc0). Fix is a
bootstrap-critical ownership change in `attr_load_lowering` + `ownership_lowering`
and must not be applied without full bootstrap (gc0..4) + leak validation.

Regression introduced by HEAD `2ecdb469` ("GC-root, relocation, zpage &
value-class-payload rework"); the failing oracle-diff cases pre-date that commit.

## Symptom

`tests/python/test_self_host_oracle_diff.py` — the
`class_attr_method_replacement_*` family (finally_delete, loop_break_delete,
untaken_branch, loop_taken_delete, try_except_taken_delete, both
`pcc1_no_host` and `pcc2_pcc3` variants):

```
pcc:      'child:direct:base\n<null>\n'
expected: 'child:direct:base\nchild:value:base\n'
```

First line (the CALL form `Child.label(Child(), ":direct")`) is correct.
Second line (the VALUE-load form `fn = Child.label; fn(Child(), ":value")`)
prints `<null>`.

## Repro (host pcc, no bootstrap needed)

```python
def replacement(self, suffix):
    return self.name + suffix + ":new"
def choose():
    return False
class Base:
    def label(self, suffix):
        return self.name + suffix + ":base"
class Child(Base):
    def __init__(self) -> None:
        self.name = "child"
def main() -> None:
    if choose():               # NOT taken -> Child.label stays inherited
        Child.label = replacement
    fn = Child.label
    print(fn(Child(), ":value"))   # pcc: <null> ; cpython: child:value:base
if __name__ == "__main__":
    main()
```

`env -u LC_ALL .venv/bin/pcc repro.py` → `<null>`. `python3 repro.py` →
`child:value:base`. The `del Child.label` variants reproduce identically.

### Isolation (what narrows it to ownership/GC, not getattr)

- No store to `Child.label` anywhere → **works** (direct cached-immortal path).
- `Child.label = replacement` live (no del / taken branch) → **works**
  (returns `replacement`).
- Store present but own-dict MISS at read time (untaken branch / after `del`)
  → **`<null>`**.
- Add `print(fn)` *before* the call → **works** (`fn` is `<object tag=9>`,
  the value is correct). Using `fn` before the next allocation keeps it alive.
- Pre-allocate the receiver (`c = Child(); fn = Child.label; fn(c, ...)`,
  no allocation between load and call) → **works**.

=> The value-load returns the right object; it is freed by the GC triggered by
the `Child()` allocation between the load and the call. A lifetime/rooting bug,
not a lookup bug.

## Root cause

`Child.label` value-load is lowered in `attr_load_lowering.py` (~1123-1234).
When a store to `Child.<name>` exists, `class_attr_runtime_candidate` is true
and lowering takes the runtime-getattr + select path:

```
runtime_value  = py_obj_getattr(cls, name)         # see below
static_fallback = _emit_unbound_instance_method_value(owner, name, fn, cache=False)
result = select(runtime_value == raw_method_ptr, static_fallback, runtime_value)
```

Ownership of `result` is **runtime-path-dependent**:

- own-dict miss (untaken / deleted): `py_obj_getattr` falls through
  `py_class_getattr` (py_class_attrs.c:784) to `py_class_lookup`, which returns
  the **raw method code pointer** (`m->methods[j].func`, not a heap object).
  `is_raw` is true, so `select` picks `static_fallback` — a **fresh +1 owned**
  `py_func_new_named` object (cache=False). It MUST be GC-rooted.
- live store: `py_obj_getattr` returns the attrs-dict value (the stored
  function), `is_raw` false, `select` picks `runtime_value` (a dict value —
  borrowed-ish).

The owned-local classifier `ownership_lowering._attr_expr_returns_owned_object`
(line 238) returns **False** for `Class.method` (obj is a class name, not
`self`, not an instance hint → falls to `return False` at 271). With
`--ir-scaffold=on` (default), `_raw_scaffold_object_rhs_is_owned` therefore
treats `fn = Child.label` as non-owned, so `_ensure_owned_local_gc_root` is not
called. The fresh owned func object from `static_fallback` is collected by the
GC fired during `Child()` construction → `fn` dangles → `<null>`.

`False` is correct for the no-store path (cached immortal value) and arguably
for the live path (borrowed dict value), but wrong for the own-dict-miss
candidate path (fresh +1 owned).

## Why a naive classifier flip is unsafe

Making `_attr_expr_returns_owned_object` return True for `Class.method`
uniformly (or even just for state in {unknown, deleted}) is **not** safe: in the
"unknown" state the store may actually have executed at runtime, in which case
`select` picks the (borrowed) `runtime_value` and an owned-release would
over-release. The select result's ownership genuinely differs per runtime
branch, so a static classifier cannot be correct for both.

## Correct fix (proposed, unimplemented)

Make the candidate-path result **uniformly +1 owned** at the `attr_load`
site, then have the classifier return True for that case:

1. In the candidate path, ensure the `runtime_value` branch contributes a +1
   reference when it is an object (incref the override value), so both `select`
   inputs are +1 owned and `result` is uniformly owned.
2. Release the unselected `select` operand (currently leaked — `static_fallback`
   and `runtime_value` are both materialized but only one is used).
3. Mark the load result owned so `_ensure_owned_local_gc_root` roots `fn`.

Validation REQUIRED before claiming fixed: host repro; the full
`class_attr_method_replacement_*` set in `test_self_host_oracle_diff.py`;
`tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py`; and a leak check
(`leaks --atExit` / `__del__` canary) to confirm no new leak/double-free,
since `ownership_lowering` is consulted for every attribute assignment in the
self-host compiler itself.

## Update 2026-06-23 — it is a RUNTIME GC-root bug, not a frontend ownership gap

Tried the frontend fix above (make `_attr_expr_returns_owned_object` return
True for the candidate path so `fn` is owned-rooted). Result: **did not fix it**,
and exposed the true mechanism via a standalone `-g` binary +
`PCC_DEBUG_BAD_BACKTRACE=1`:

```
<null>
[BAD_INCREF] o=0x... tag=0
[BAD_INCREF_HEADER] refcount=-1 type_tag=0 flags=0
```

- The fresh func object A2 (`py_func_new_named`, +1) is stored into `fn`.
- During the `py_instance_new(Child)` allocation that happens *between* the
  store and the call, A2's refcount is decremented to 0 and it is freed —
  **even when `fn` is registered as an owned GC frame-slot root**
  (`pcc_gc_frame_enter` with the owned map). So the allocation-time collection
  is **not honoring the frame root / is over-decrementing frame-slot
  contents**. With frontend owned-rooting added, the explicit end release then
  drives refcount to -1 (BAD_INCREF, refcount=-1, tag=0 = already-freed).
- Reproduces on `PCC_GC_BACKEND=0,1,2,3` (backend 4 crashes differently). So it
  is **backend-agnostic**, i.e. in the shared collection/refcount path, not a
  relocating-only issue.
- `print(fn)` or pre-allocating the receiver (no allocation between load and
  call) both avoid it — consistent with "freed by the intervening allocation".

Conclusion: this is a **runtime GC bug in HEAD `2ecdb469`** (the collection
triggered by allocation drops the refcount of a freshly-created object that is
only reachable through a stack/frame slot). The frontend emits reasonable code;
no frontend-only change fixes it. The frontend classifier change was reverted
(unvalidated, ineffective, bootstrap-critical). Fix belongs in the runtime
allocation/collection + frame-root path and should be done together with the
cluster-C `[BAD_INCREF]` investigation (same family).

## Correction: `bytes_hex` is NOT this bug (it was a dispatch bug, now fixed)

An earlier revision of this file claimed `bytearray(b'abz').upper()` →
`\x04\x00\x00` was the same GC-lifetime family as B, inferred from a similar
local-vs-inline split. **That was wrong** — reading the emitted IR (not
inferring from symptoms) showed:

```
%bytearray.from = py_bytearray_from_obj(...)
%dyn.str.upper  = py_str_upper(%bytearray.from)   ; py_STR_upper on a BYTEARRAY
```

i.e. a **method-dispatch bug**: an inline `bytearray(...)` Call receiver routed
`.upper()` through `_maybe_emit_str_method_via_dyn` (→ `py_str_upper`), which
forces the receiver onto the StrType path and reads the bytearray's raw bytes
as a string. The local form `x = bytearray(...); x.upper()` worked because the
local's type is ByteArrayType, hitting the precise bytes branch
(`py_bytes_upper`). Fixed by making `_maybe_emit_str_method_via_dyn` bail for
statically `BytesType`/`ByteArrayType` receivers so they fall through to the
precise bytes branch. The value was wrong from creation — NOT freed — so it
never was a lifetime/GC issue. Lesson: read the IR, don't analogize symptoms.

B itself remains the GC-lifetime bug described above (the value IS a valid
object, tag 9, that gets freed/over-released across an allocation).

## Update 2026-06-23 (later) — CORRECTED root cause: borrowed method-table getattr over-released by the candidate path (NOT a GC bug)

The "RUNTIME GC-root bug" conclusion above is **wrong / stale**. It assumed
`py_class_lookup` returns a raw code pointer (`m->methods[j].func` == raw symbol).
After the HEAD `2ecdb469` registration rework, `py_class_add_method` now stores a
**`py_func_new_named` object** in the method table (`class_gen.py:4090-4098`,
`_emit_method_pyfunc_object`), so `py_class_lookup` returns that **func object,
borrowed** (no incref; the explicit contract is documented at
`py_class.c:887-888`). This is a deterministic over-release, not allocation-time
GC collection.

### Decisive evidence (read the IR + the deterministic minimal repro)

Repro `repro_cand2.py` (store to `Child.label` in an UNtaken branch, then the
candidate-path CALL form **twice**, no value form):

```python
def choose(): return False
class Base:
    def label(self, suffix): return self.name + suffix + ":base"
class Child(Base):
    def __init__(self) -> None: self.name = "child"
def main() -> None:
    if choose(): Child.label = replacement   # makes class_attr_state != live -> candidate path
    print(Child.label(Child(), ":one"))      # works
    print(Child.label(Child(), ":two"))      # <null>
```

Output: `child:one:base` then **`<null>`** — first call works, second fails.
No `[BAD_INCREF]` fires; `print(fn)` BEFORE any alloc already shows `None`
(the load itself is dangling), so it is NOT "freed by the intervening
`Child()` allocation".

Mechanism (IR `main` of the full untaken-branch program):
- `py_obj_getattr(Child,"label")` -> `py_class_getattr` -> own-dict MISS ->
  `py_class_attrs.c:838 return py_class_lookup(cls,name)` -> the table func
  object, **borrowed** (refcount stays 1; its only +1 is the unreleased
  `py_func_new_named` from class-init, owned by the table slot).
- candidate-path CALL form (`method_call_expression_lowering.py:1085-1107`):
  `callable_obj = self._emit_attr(attr)` (the borrowed obj) -> `py_obj_call`
  -> `self._gc_release(callable_obj)` at **line 1107** -> refcount 1->0 ->
  **frees the table slot's func object**.
- the next `Child.label` getattr (the VALUE form, or a 2nd CALL) returns the
  freed/dangling slot -> the select yields a dead pointer -> `<null>`.

The `select(runtime_value == bitcast(method_fn), static_fallback,
runtime_value)` (attr_load_lowering.py:1186-1202) **always** picks
`runtime_value` now, because the table holds a func object, never the raw
`@user_..._method` symbol -> `static_fallback` (`_emit_unbound_instance_method_value`,
+1 owned) is **always created and always leaked, never selected**.

### Why owned/borrowed is split (the real defect)

`py_class_getattr` is **inconsistent**: the attrs-dict hit branch
(`py_class_attrs.c:819-834`) returns **owned** (`py_dict_get` increfs,
line `py_dict.c:332`), but the method-table fallthrough (line 838) returns
**borrowed**. The frontend treats every `py_obj_getattr` result as owned and
releases it -> correct for the live-store (dict-hit) case (31 oracle-diff
cases pass), over-release for the own-dict-miss/method-table case (5 fail:
`untaken_branch`, `loop_taken_delete`, `try_except_taken_delete`,
`finally_delete`, `loop_break_delete` — all `_no_host`).

### Why reverting registration to `bitcast` (fix "a") is the wrong axis

`pcc_instance_bound_method_entry` (`py_class_attrs.c:323-407`) STILL supports
raw code pointers (arity 0-3 branch, lines 362-406), so raw-pointer
registration would not crash instance dispatch — but it would regress
arbitrary-arity / kwargs / defaults / `*args` method dispatch (the func-object
branch, lines 335-361) and `getattr(obj,'m')`/value-load returning a callable
object. The func-object registration is the intended design. Tests
`test_class_method_registration_uses_stable_function_ref` and
`test_unbound_call...count(...)==1` assert the OLD raw-pointer IR shape and are
now **stale IR-shape assertions** (cf. the already-rewritten
`test_pcc_cross_module_class_schema_matches_local_layout` in the same file).

### Correct fix (proposed, READ-ONLY this session)

1. RUNTIME (authoritative; both default and no-libpython route through the C
   `py_class_getattr` — the port has no mirror, it `extern`s it):
   `py_class_attrs.c:838` — incref the method-table fallthrough so
   `py_class_getattr` is uniformly owned, matching the dict-hit branch and the
   public getattr contract:
   ```c
   PyObject *m = py_class_lookup(cls, name);
   if (m != NULL) py_incref(m);
   return m;
   ```
   Blast radius: only `py_obj_getattr`/`py_obj_getattr_default`
   (`py_obj_ops_dispatch.c:696,792`) call it; both `return result` without their
   own incref, so no double-free; worst case for any non-releasing internal
   caller is a leak, never a crash. This alone makes all 5 oracle-diff cases
   pass output-wise (CALL-form release becomes balanced, slot survives).
2. FRONTEND (leak-clean the candidate value-load): with (1), the VALUE form
   `fn = Child.label` stores a +1 owned obj into `fn.addr`, which sits in the
   BORROWED frame map -> leak. Make
   `ownership_lowering._attr_expr_returns_owned_object` (line 238-271) return
   True for the `KnownClass.method` candidate case so `fn` is owned-rooted and
   released at frame leave; and in the candidate path
   (`attr_load_lowering.py:1144-1211`) release the UNSELECTED select operand
   (`static_fallback`, currently always leaked). This is the part the earlier
   (stale-model) attempt got wrong because it lacked (1): increfing/rooting
   without the runtime ownership fix double-released.
3. TESTS: update `test_class_method_registration_uses_stable_function_ref`
   and `test_unbound_call_cpy_base_compiles_via_dynamic_getattr` to the
   func-object registration shape (they are stale), or confirm with the user.

VALIDATION REQUIRED before claiming fixed (runtime + ownership_lowering are
bootstrap-critical): host repro `repro_cand2.py` + the full
`class_attr_method_replacement_*` set; `__del__`-canary / `leaks --atExit`
to confirm no new leak/double-free on the candidate path AND the live-store
path; `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py`; the editing of
`py_class_attrs.c` requires wiping `libpy_runtime*.a` (stale-archive trap).

## Siblings (same HEAD commit)

`2ecdb469` also touched GC relocation / GC-root / value-class payload. Likely
related failures from the same run, plausibly the same family: GC backend #4
`[BAD_INCREF] tag=104` (`test_mixed_reachability`, `test_slot_graphs`),
value-class unboxed projection, relocation substrate-spike. Not yet confirmed
to share this exact mechanism.
