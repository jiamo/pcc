# Investigation: pcc1 self-host loses `_generator_ctx` after first assignment

## Status
resolved

## Problem Description

User-reported regression on 2026-05-11:

```
$ ./pcc1 --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py -o pcc2
error: PCC-PY-COMPILE-001: [python-frontend] Layer 1 unknown function _yield;
  builtins other than print/range/len/str need L2/L3
  note: exception_type=Exception
```

Last green bootstrap baseline (`tests/bootstrap_gate_baseline.json`) is
2026-05-01. ~30 commits since then. pcc1 stage1 binary size grew 4.4 MB →
7.0 MB (+60%).

The error is raised from
[`layer1.py:21893`](../../pcc/py_frontend/codegen/layer1.py#L21893) inside
`_emit_call`'s "unknown user function" branch. So pcc1's emit-time call
lowering treats lifted yield sentinels (`_yield(arg)` from
[`py_lift.py:691`](../../pcc/parse/py_lift.py#L691)) as ordinary calls
instead of intercepting them through the generator path.

Stage0 (CPython running pcc) compiles `pcc/__main__.py` and any generator
fine. Same source, different runtime → different behavior. This is the
same self-host divergence class documented in
[`python-self-host-no-libpython-runtime-holes.md`](python-self-host-no-libpython-runtime-holes.md).

## Repro

```bash
cat > /tmp/gen_tiny.py <<'EOF'
def counter():
    i = 0
    while i < 3:
        yield i
        i = i + 1

def main() -> None:
    for v in counter():
        print(v)

if __name__ == "__main__":
    main()
EOF

# Build pcc1 via stage0 (succeeds).
env -u LC_ALL uv run python -m pcc \
    --python-libpython off --backend self \
    pcc/__main__.py -o /tmp/pcc1

# pcc1 fails on any generator (3-line counter included).
/tmp/pcc1 --python-libpython=off --backend self /tmp/gen_tiny.py -o /tmp/out
# error: PCC-PY-COMPILE-001: Layer 1 unknown function _yield ...
```

## Test [CONFIRMED]

```bash
/tmp/pcc1_stack /tmp/gen_tiny.py -o /tmp/gen_tiny.out --backend self \
    --python-libpython=off
/tmp/gen_tiny.out
# Expected: 0\n1\n2
# Observed: 0\n1\n2 ✓
```

The `tests/python/test_self_host_oracle_diff.py::test_pcc1_matches_stage0_for_python_idioms`
suite added 2026-05-10 (commit 4bdd62d1) covers the generator idioms
`generator_next`, `generator_yield_from`, `generator_inner_for` — they
reproduce the same failure end-to-end.

## Proposals

- No.1 disable yield-sentinel cache to rule out cache corruption [DENIED]
- No.2 add `self._generator_ctx = None` slot in `__init__` [DENIED]
- No.3 use list-as-stack for `_generator_ctx` [CONFIRMED]

## No.1 disable yield-sentinel cache

### Code Change

Skip the dict-cache lookup at the top of
[`layer1.py:_funcdef_has_yield_sentinel`](../../pcc/py_frontend/codegen/layer1.py#L7626)
and always run the iterative AST search.

### DENIED

Even with the cache short-circuited, pcc1 still fails with the same
`_yield` error. The cache is not the regression. With debug stderr probes
the iterative search itself returns True correctly on every call — the
generator-detection helper works. Symptom must be downstream of generator
detection, in the emission path.

## No.2 add `self._generator_ctx = None` slot in `__init__`

### Code Change

In `L1CodeGen.__init__` (layer1.py:2517 area):

```python
self._generator_ctx = None
```

so the attribute exists at construction time, instead of being created
implicitly the first time `_emit_generator_resume_function` runs.

### DENIED

After rebuilding stage0→pcc1 with the slot pre-allocated, inserting a
debug stderr probe at `_emit_stmt`:

```python
gen_ctx = getattr(self, "_generator_ctx", None)
sys.stderr.write("[gen_ctx] gen_ctx_is_none=" + (
    "yes" if gen_ctx is None else "no"
) + "\n")
```

shows pcc1 still reads `gen_ctx is None == yes` for every statement
emitted inside the generator's resume function — even though
`_emit_generator_resume_function` does
`self._generator_ctx = {"gen": ..., "frame": ...}` at line 8048 right
before `_emit_stmts(fd.body)`.

So the assignment at 8048 does not persist into reads at 8661. Adding the
init slot didn't help. Matches the "default-None slot, setattr clobbers
neighbor" pattern recorded in
[`feedback_pcc_dataclass_default_none_setattr`](../../.claude/...).
The mitigation in that memory says "pass at construction" — i.e., never
mutate from None to a real value via `obj.attr = X`.

## No.3 list-as-stack for `_generator_ctx`

### Code Change

[`pcc/py_frontend/codegen/layer1.py`](../../pcc/py_frontend/codegen/layer1.py)
(diff stat: 22 inserts / 15 deletes):

1. `__init__` (line ~2524): `self._generator_ctx_stack: list = []`
   replaces the implicit attr.
2. `_emit_generator_resume_function` (line ~8036, ~8059):
   `self._generator_ctx = {...}` → `self._generator_ctx_stack.append({...})`,
   restore via `.pop()` instead of writing back the saved scalar.
3. All readers: direct `self._generator_ctx` → `self._generator_ctx_stack[-1]`,
   and `getattr(self, "_generator_ctx", None) is not None` →
   `len(self._generator_ctx_stack) > 0`.

The stack slot type stays `list` for the entire L1CodeGen lifetime. We
mutate via `append`/`pop`, never reassign the attribute. That sidesteps
the pcc-py setattr-clobber bug because we never overwrite an instance
attribute — we mutate the list object the slot already points at.

### CONFIRMED

```
$ stage0 → /tmp/pcc1_stack: build OK
$ /tmp/pcc1_stack /tmp/gen_tiny.py -o /tmp/gen_tiny.out --backend self \
    --python-libpython=off
$ /tmp/gen_tiny.out
0
1
2
```

`pcc1→pcc2` no longer fails on `_yield`. It now hits a different,
narrower error:
```
error: PCC-PY-COMPILE-001: [python-frontend] Value.bitcast: too many
positional args: got 2, expected at most 1
```
That is a separate self-host regression in the IRBuilder layer, tracked
separately (see follow-up investigation `pcc1-self-host-value-bitcast-arity.md`
once filed).

## Update 2026-05-11 (after generator-ctx fix landed)

After the No.3 stack fix landed, `pcc1→pcc2` now hits the next blocker:

```
error: PCC-PY-COMPILE-001: [python-frontend] Value.bitcast: too many
  positional args: got 2, expected at most 1
```

### Root cause (No.4)

Traced via the same stderr-probe technique. Instrumenting
`_emit_direct_method_call` showed pcc1 dispatching
`tmp_builder.bitcast(frame_map, _CSTR, name=...)` against
`info.name == "Value"` (1-arg `bitcast`) instead of
`info.name == "IRBuilder"` (3-arg `bitcast`).

The receiver `tmp_builder` is set on the line above:

```python
fn = self.current_function
entry = fn.blocks[0]
cur = self.builder._block
tmp_builder = ir.IRBuilder(entry)   # local IRBuilder alias
```

In stage0, `_emit_assign` for that line sets
`self._ir_builder_env_flags["tmp_builder"] = True`, and later
`_ir_scaffold_target` recognizes `tmp_builder.bitcast(...)` as an
IRBuilder call and routes it through scaffold lowering before reaching
the generic class-method-dispatch fallback at
[`layer1.py:24220`](../../pcc/py_frontend/codegen/layer1.py#L24220):

```python
for info in self.class_lowering.classes.values():
    if attr.name in info.methods:
        ...
        return self._emit_direct_method_call(...)
```

This loop iterates *every* class in the module and picks the first one
whose method dict has the matching name. For modules where both `Value`
(in `pcc.llvm_capi.py_ast` / `pcc.py_frontend.py_ast`) and `IRBuilder`
(in `pcc.llvm_capi.ir`) are visible, the iteration order is non-
deterministic and `Value.bitcast` (1-arg) wins; the call has 2 args,
the resolver raises.

In pcc1, the assignment-time registration into
`_ir_builder_env_flags["tmp_builder"]` doesn't take effect, so scaffold
dispatch never fires for `tmp_builder.bitcast`. The reason is the same
root cause as the generator-ctx slot: even though
`_ir_builder_env_flags` is initialized as `{}` in `__init__`, the
`_emit_assign` code path for `tmp_builder = ir.IRBuilder(entry)` doesn't
reach the registration line under pcc1's runtime — debug stderr probes
inserted at both the registration site and the lookup site emitted
nothing at all when running the failing case. (In stage0 they fire
normally.) The exact pcc-py self-host divergence in this `_emit_assign`
codepath wasn't isolated; the workaround is to not rely on the env-flag
in the first place.

### Workaround (No.4) — drop the local IRBuilder alias

Refactor `_emit_entry_gc_frame_enter` to use `self.builder` directly,
saving and restoring its insertion point around the entry-block
emission. This sidesteps the local-alias env-flag dependency entirely.

Diff:

```python
-        cur = self.builder._block
-        tmp_builder = ir.IRBuilder(entry)
-        ...
-        if terminator is not None:
-            tmp_builder.position_before(terminator)
-        else:
-            tmp_builder.position_at_end(entry)
-        ...
-        frame_map_ptr = tmp_builder.bitcast(frame_map, _CSTR, ...)
-        slots_ptr = tmp_builder.bitcast(slots, _CSTR, ...)
-        tmp_builder.call(...)
-        self.builder.position_at_end(cur)
+        saved_block = self.builder._block
+        ...
+        if terminator is not None:
+            self.builder.position_before(terminator)
+        else:
+            self.builder.position_at_end(entry)
+        ...
+        frame_map_ptr = self.builder.bitcast(frame_map, _CSTR, ...)
+        slots_ptr = self.builder.bitcast(slots, _CSTR, ...)
+        self.builder.call(...)
+        self.builder.position_at_end(saved_block)
```

`self.builder.X(...)` is recognized by `_ir_scaffold_target` directly
(the leading-`self.builder` shape, not the env-flag check), so the
scaffold dispatch fires correctly under both stage0 and pcc1.

`_emit_current_gc_frame_enter` (immediately below
`_emit_entry_gc_frame_enter` in `layer1.py`) already used this shape;
this fix aligns the two siblings.

### Test [CONFIRMED]

```bash
$ /tmp/pcc1_v2 --backend self --python-libpython=off /tmp/gen_tiny.py \
    -o /tmp/gen_v2 && /tmp/gen_v2
0
1
2
```

`pcc1→pcc2` no longer raises `Value.bitcast`. It now hits the next,
unrelated layer:

```
error: PCC-PY-COMPILE-001: Python pipeline requires libpython fallback for
  multi-file compile (modules: pcc.cli_bootstrap, pcc.py_frontend.pipeline,
  pcc.py_frontend.codegen.layer1, pcc.py_frontend.types,
  pcc.llvm_capi.ir); rerun with --python-libpython=auto/on
```

### Follow-up — the `_emit_assign` divergence is still unidentified

The fix above is a *workaround*, not the underlying repair. The actual
self-host divergence — why `_emit_assign`'s registration path doesn't
fire for `tmp_builder = ir.IRBuilder(entry)` under pcc1 — is still
unisolated. Other local-IRBuilder aliases will trip the same bug. Audit
candidates: any `<name> = ir.IRBuilder(...)` in
`pcc/py_frontend/codegen/`. The audit should also pick out other
local-alias-then-method-call shapes (e.g.,
`m = self.module; m.do_thing(...)`) that may rely on the same
`_ir_builder_env_flags` / local-classification mechanism.

## Report

Two related fixes landed in `pcc/py_frontend/codegen/layer1.py`:

1. `_generator_ctx` slot replaced with `_generator_ctx_stack` list
   (~22 inserts / 15 deletes; covers `__init__`,
   `_emit_generator_resume_function`, all 6 readers).
2. `_emit_entry_gc_frame_enter` refactored to use `self.builder`
   directly instead of `tmp_builder = ir.IRBuilder(...)` (~16 inserts /
   8 deletes).

**Pros vs. denied alternatives:**
- vs. No.1 (cache disable): cache wasn't the bug; disabling it just slows
  the search, fix isn't there.
- vs. No.2 (init slot to None): the documented pcc-py bug is exactly
  "default-None slot + later `obj.x = real`"; pre-allocating None doesn't
  reserve a writable slot, just plants the trap earlier.
- vs. No.3 (stack mutation): never reassigns the attribute. Survives the
  pcc-py setattr-clobber bug because the slot's *value identity* (the
  list object) doesn't change; we mutate it in-place.

**Why baseline was green on 2026-05-01:** the baseline JSON only locks
binary sizes and libpython linkage, not generator behavior. pcc1's
generator path was *probably* always fragile but exercised only
incidentally. The 2026-05-09 commit `2833f170` rewrote
`_dataclass_field_names` from `__dataclass_fields__` introspection to
explicit isinstance dispatch, and added the
`_funcdef_yield_sentinel_cache`, which raised the call frequency of
`_funcdef_has_yield_sentinel` and made the second/third-call code paths
visible. That commit is *not* the code change that broke generators —
the latent slot-allocation bug was always there — but it changed the
exposure surface enough that user-visible failure regressed between then
and 2026-05-11.

**Follow-up:**
- ~~`Value.bitcast` arity error from `pcc1→pcc2` next.~~ Resolved via
  `_emit_entry_gc_frame_enter` refactor (see Update 2026-05-11 section
  above).
- `pcc1→pcc2` next blocker: `Python pipeline requires libpython fallback
  for multi-file compile (modules: pcc.cli_bootstrap,
  pcc.py_frontend.pipeline, pcc.py_frontend.codegen.layer1,
  pcc.py_frontend.types, pcc.llvm_capi.ir)`. Trigger:
  `_ir_needs_libpython(ir_text)` at
  [`pipeline.py:4106`](../../pcc/py_frontend/pipeline.py#L4106) detects
  `py_cpy_*` symbols in the IR pcc1 produced for each of those modules.
  Stage0 produces clean closed-world IR for the same inputs. Likely the
  same self-host divergence pattern: pcc1's codegen falls back to
  generic dynamic dispatch for some method calls / attribute accesses
  that stage0 lowers natively. Filed as separate investigation.
- `tests/python/test_self_host_oracle_diff.py` (added 2026-05-10) is a
  good aspirational gate: every Python idiom that pcc1 still mishandles
  is observable as a `compile failed` row. Most of the listed failures
  (generators, kwargs/defaults, lambdas, closures, try/except) are the
  same self-host divergence class — pcc1 has the source code, but
  emitting it under self-host runtime still hits one or two more of
  these slot-allocation / cross-module-isinstance / IRBuilder-arity
  holes. Each is a separate investigation file.
- Audit other transient `self.X = ...` assignments in `L1CodeGen` for
  the same pattern. Quick grep candidates: `self._generator_func_names`
  (now annotated in `__init__`), `self._owned_local_*`,
  `self._exact_int_env_flags`, etc. — any attribute set inside a method
  but not also set in `__init__`.
