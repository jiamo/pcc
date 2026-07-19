# `self.<class_attr>` inside an inherited method ignores subclass override

## Status

**FIXED 2026-06-26 (focused loop tick) — verified, shippable.**
Found 2026-06-26 by a fresh no-libpython idiom-diff sweep; fixed the same day as a
focused primary task with the full gate. The fix is surgical: it only changes
lowering when a subclass *actually* redeclares the attribute (the common
no-override case keeps the static fast path → emitted IR unchanged there), which
is why a high-blast-radius attr-resolution change still reaches the byte-identical
self-host fixed point.

### Fix (CONFIRMED)

`pcc/py_frontend/codegen/class_gen.py`:
- Added `ClassLowering._derives_from(info, base_name)` (transitive base walk,
  mirrors `lookup_class_attr`) and `ClassLowering.class_attr_overridden_by_subclass(info, attr_name)`
  (True iff some subclass of `info` declares its own `attr_name`).
- `emit_self_attr_load` now gates the static `emit_class_attr_load` behind
  `not class_attr_overridden_by_subclass(...)`; when overridden it falls through
  to the runtime `py_obj_getattr` MRO lookup (the `self.<attr>` path — the repro).

`pcc/py_frontend/codegen/attr_load_lowering.py` (~line 1333):
- The hinted-`Name`-receiver class-attr fast path (`obj.kind` where `obj` is a
  local typed as a base) gets the same gate, since the local may hold a subclass
  instance.

Both static load and `py_obj_getattr` return a `_PTR` (PyObject*), so routing to
the runtime path is type-safe (the class-attr global is always `_PTR`, holding a
marshaled object — verified at the declaration site `class_gen.py:3248` and the
store site).

### Verification (all run 2026-06-26)

- **Repro vs CPython** (`--python-libpython=off`, default tier, `--backend self`):
  `method`/`prop`/`hinted-local` now print `sub` (were `base`); `base method`
  stays `base`; non-overridden `Plain().get_only()` stays correct (fast path
  retained). Byte-identical to CPython.
- **Regression test**: `tests/python/test_native_class_attr_subclass_override.py`
  (PASSED).
- **gc0 full three-stage bootstrap** (`tests/python/gc/test_pcc_bootstrap_full_gc0.py::test_full_three_stage_bootstrap_self_gc0`):
  PASSED, 124s — pcc1→pcc2→pcc3 byte-identical. This is the strict
  `ir_scaffold=on` / `--python-libpython=off` path and the real no-libpython
  criterion; it is GREEN.

### Independent pre-existing finding (NOT caused by this fix) — see "Open" below

`tests/python/test_fallback_baseline.py` is RED in this worktree (7 of 18):
the `ir_scaffold=off` **legacy** closure shows **12 `py_cpy_*`** in
`pcc.py_frontend.pipeline` (baseline pinned 0, captured 2026-05-01). Proven
independent of this fix: forcing `class_attr_overridden_by_subclass` to always
return `False` (pre-change behavior) yields the *same* 12. The 12 are all inside
`compile_python`'s GPU optional-feature dispatch — `getattr(gpu_module, "fn")(args)`
on the lazily `__import__`-ed `pcc.gpu_kernel`/`gpu_metal` user modules — which
the legacy lowering bridges through CPython. A minimal `__import__`+`getattr`
(no call) emits 0 in both modes, so the trigger is the dynamic call on a
getattr'd user-module attribute. By code-path analysis, none of this session's
frontend edits touch dynamic-getattr-call-on-user-module lowering. The strict
`ir_scaffold=on` bootstrap is unaffected (green). Regression *timing* relative to
the 2026-05-01 baseline is not yet attributed → flagged for a causality audit
(do NOT recapture the baseline to paper over it). Tracked as its own task in
`docs/current-goal-state.md`.

## Symptom

A method/property defined in a base class that reads `self.<class_attr>` returns
the **base** class's value even when the instance's actual class **overrides**
that class attribute. Direct `instance.<class_attr>` is correct.

## Repro (`docs`-local; CONFIRMED 2026-06-26 under `--python-libpython=off`)

```python
class Base:
    kind = "base"
    def via_method(self):
        return self.kind
    @property
    def via_prop(self):
        return self.kind

class Sub(Base):
    kind = "sub"

def main() -> None:
    s = Sub()
    print("direct:", s.kind)          # pcc: sub   CPython: sub   (OK)
    print("method:", s.via_method())  # pcc: base  CPython: sub   (BUG)
    print("prop:", s.via_prop)        # pcc: base  CPython: sub   (BUG)
    b = Base()
    print("base method:", b.via_method())  # pcc: base  CPython: base (OK)
main()
```

pcc (`--python-libpython=off`, default + cc tiers) prints `base` for the method
and property cases; CPython prints `sub`. (First surfaced as a `@property`
`f"{self.kind}:{self.name}"` printing `animal:Rex` instead of `dog:Rex`.)

## Isolation

- NOT property-specific — a plain instance **method** reading `self.kind` has the
  same bug. So it is not the property machinery.
- Direct `instance.kind` (outside a method) resolves correctly via the runtime
  type. Only `self.<class_attr>` **inside an inherited method** is wrong.
- Instance attributes (`self.name`) are unaffected (loaded from the instance).

## Root-cause direction (not yet pinned to a line)

Class attributes are stored in a **per-class global** named by
`class_gen.py::_class_attr_global_name(class_name, attr_name)`. The instance
attr-load path in `attr_load_lowering.py::_emit_attr` appears to resolve
`self.<class_attr>` statically against the **method's defining class** (`info`,
here `Base`) — loading `Base`'s class-attr global — instead of doing a runtime
lookup via the instance's actual type MRO (which would find `Sub.kind`).

CPython semantics: `self.kind` for a class attribute is `type(self).__mro__`
lookup, so a subclass override wins.

## Fix options (and why deferred)

1. **Runtime lookup**: lower `self.<class_attr>` (when the name is a class attr,
   not an instance attr / bound method / property) through
   `py_obj_getattr(self, name)` (already used for the `ClassName.X` path; it
   respects the instance's type). Correct, but:
   - HIGH blast radius — `self.attr` is pervasive; changes IR for every
     `self.<class_attr>` site (incl. pcc's own runtime/frontend code).
   - Must NOT disturb instance-attr loads (`self.name`), bound-method values, or
     properties/descriptors.
   - Verification bar: full `tests/python/gc/test_pcc_bootstrap_full_gc0.py`
     (byte-identity is internally consistent across pcc1/2/3, so a uniformly
     applied correct change should still reach the fixed point) **plus** the
     focused class/inheritance suites and ideally the 5-GC matrix, since attr
     resolution is shared by every backend.
2. **Compile-time MRO resolution**: when the class is closed-world and an
   overriding subclass exists, bind to the runtime type — more complex, partial.

Option 1 is the correct semantics; it is deferred only because it should be done
as a focused change with the full gate, not autonomously mid-loop.

## Where to look

- `pcc/py_frontend/codegen/attr_load_lowering.py::_emit_attr` (instance-receiver
  class-attr branch).
- `pcc/py_frontend/codegen/class_gen.py::_class_attr_global_name` (per-class
  class-attr storage).
- Runtime `py_obj_getattr` / `py_instance_getattr` / `py_class_getattr`
  (`pcc/py_runtime/src/py_obj_ops_dispatch.c`) already do MRO-correct lookup.
