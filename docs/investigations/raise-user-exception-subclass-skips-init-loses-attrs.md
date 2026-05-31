# Investigation: raise UserExceptionSubclass(args) skips __init__, loses instance attributes (no-libpython)

## Status
resolved

## Problem Description
A user exception subclass with a custom `__init__` that sets instance attributes
loses those attributes (and gets the wrong message) when raised:

```python
class MyError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
raise MyError(404, "not found")   # except MyError as e: e.code -> AttributeError
```

CPython: `e.code == 404`, `str(e) == "not found"`. pcc: `e.code` →
`AttributeError: code`, and `str(e)` would be `"404"` (the *code*, mis-taken as
the message). Custom exceptions carrying an error code / context are an extremely
common idiom.

Found 2026-05-30 by real19.

## Repro
```bash
cat > /tmp/ea.py <<'PY'
class MyError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
def main():
    try:
        raise MyError(404, "not found")
    except MyError as e:
        print(e.code, str(e))
main()
PY
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/ea.py -o /tmp/ea_bin
/tmp/ea_bin         # AttributeError: code
python3 /tmp/ea.py  # 404 not found
```

## Test [CONFIRMED]
Probe /tmp/gap_probe/exc_attr.py: `e.code`, `str(e)`, direct `MyError(500,"boom").code`,
`.args`, plain-subclass `str(e)` — all wrong/erroring before a fix. Regression to
be added as `tests/python/test_native_user_exception_attrs.py`.

## Root cause (CONFIRMED by IR + runtime read)
- `pcc/py_frontend/codegen/exception_lowering.py` `_emit_exception_value`
  (lines 743-754): `raise UserExcSubclass(args)` loads the class and calls
  `py_exc_new_with_class(cls, msg)` where `msg = _message_cstr(args)` takes
  **`args[0]`** as the message. This BYPASSES the user `__init__` entirely — so
  `self.code = code` never runs, and `args[0]` (the code `404`) is mis-used as
  the message. (Direct instantiation `e2 = MyError(...)` is fine — it uses
  `py_instance_new` + calls `__init__`; only the *raise* path is broken.)
- `pcc/py_runtime/src/py_exc_match.c` `exc_to_class()` projects only
  `PY_TYPE_CLASS` and `PY_TYPE_EXC` to a class; a **user instance**
  (`PY_TYPE_INSTANCE` / `>= PY_TYPE_USER`) returns NULL → `py_exc_matches(inst,
  cls)` returns 0. So even if the raise path produced a proper instance, the
  `except MyError` clause would not match it.

## Proposals
- No.1 Raise a properly-constructed instance + teach exc machinery about instances   [CONFIRMED #40]

## No.1 Construct the instance (run __init__) and raise it; project instances to class
### Plan (multi-part: frontend raise + runtime exc model)
1. **Frontend** (`exception_lowering.py` 743-754): when `cls_name` is a user
   class (info is not None) AND it is an exception subclass, instantiate it
   properly — `self.class_lowering.emit_instantiate(cls_name, exc_expr.args,
   self)` (runs `__init__`, so `self.code` is set and `super().__init__(msg)`
   stores the message/args) — and raise that instance instead of
   `py_exc_new_with_class`.
2. **Runtime** (`py_exc_match.c` `exc_to_class` + pcc-Python port
   `py_exc_match.py`): handle a user instance — `if tag == PY_TYPE_INSTANCE ||
   tag >= PY_TYPE_USER: return (PyClassObject *)inst->cls;` (read via
   `pcc_gc_load_ptr`). Then `py_exc_matches(inst, cls)` walks the instance's
   class MRO (which includes the Exception base via the class's mro), so
   `except MyError` and `except Exception` both match.
3. **str(e) / e.args**: depends on how `super().__init__(msg)` for an Exception
   base stores the message on the instance. MUST verify: after No.1, does
   `str(e)` give the message and `e.args == (msg,)`? If `super().__init__` is a
   no-op for the Exception base, add message/args storage (e.g. set
   `self.args` / a message slot) so `str(e)` and `e.args` work. This is the
   make-or-break sub-item — verify at the probe before claiming the fix.
4. `py_raise` must accept a user instance (the bound-var re-raise fallback at
   line 786-787 already passes arbitrary objects to py_raise, so this likely
   works; verify the TLS store + traceback frame handle a non-EXC object).

### Bootstrap-sensitivity
`exception_lowering.py` (raise is pervasive) and the runtime exc model are
bootstrap-critical. Requires the full FOREGROUND self-host bootstrap
([[feedback_foreground_bootstrap_not_background]]) and the focused exception
gates. Verify the probe (e.code + str(e) + e.args + except-match + plain
subclass) IDENTICAL before bootstrapping. Predecessor:
python-class-init-phantom-symbol-link-fail.md (the no-body-init Exception.args
loss noted at class_gen.py:5610 is the same object-model gap from the other end).

### IMPLEMENTED 2026-05-30 (probe IDENTICAL; bootstrap b12d08mom pending)
All 6 sub-items landed; the exc_attr probe is diff-IDENTICAL vs python3 (e.code,
str(e), e.args, `except Exception` base-class catch, direct instantiation, and
the no-__init__ PlainErr message). Changes:
1. `exception_lowering.py` `_build_exception_value`: `raise UserExc(args)` (no
   kwargs) -> `emit_instantiate` (runs __init__) instead of py_exc_new_with_class.
2. `py_exc_match.c` + `py_exc_match.py` `exc_to_class`/`_to_class`: project a
   user instance (tag 11 / >=100) to its `cls` (offset 16) for MRO matching.
3. `py_exc_tls.c` + `py_exc_tls.py` `py_raise_normalize`/`_normalize_raised`:
   a BaseException *instance* is raised AS-IS (was wrapped into a fresh
   PY_TYPE_EXC, discarding attrs).
4. `method_call_expression_lowering.py` new `_emit_store_exception_args` +
   call at the `super().__init__` builtin-base no-op (line ~820): store
   `self.args = tuple(args)`.
5. `py_obj_stubs.c` + `py_obj_stubs.py` `py_obj_str`: for a no-__str__ exc
   subclass instance, return the BaseException message from `args` (args[0] if
   one, "" if none, the tuple repr otherwise).
6. `class_gen.py` `emit_instantiate` init_fn-None branch + new
   `_class_bases_include_exception`: a no-__init__ exc subclass stores its
   constructor args (so `raise PlainErr("msg")` keeps its message).
test_native_user_exception_attrs.py 1 passed. FULL bootstrap PASSED (b12d08mom, 18 passed/4 skipped, 614s/10:14). CONFIRMED #40.

### pending — CONFIRMED multi-part (deferred to a focused session)
Root cause confirmed. The str(e)/args sub-item (3) is CONFIRMED to need real
work: a user-exc instance defines no __str__/__repr__, so str(e) routes through
py_obj_str -> py_user_str_dispatch -> (#38) py_obj_repr -> default, NOT the
Exception message; and there is no args storage on a PyInstanceObject for the
Exception base. So beyond (1) frontend raise->emit_instantiate and (2)
exc_to_class projecting instances, the fix also needs: (3) super().__init__(msg)
for an Exception base to store args/message on the instance; (4) str(user-exc
instance) to return the joined-args message (BaseException.__str__ semantics);
(5) instance.args; (6) the no-__init__ subclass message path. This is a genuine
multi-part object-model feature (6 sub-items spanning frontend raise + runtime
exc model + BaseException str/args inheritance), NOT a bounded slice. DEFERRED
from the 2026-05-30 session per AGENTS.md Debugging Playbook §9 (do not rush a
broad bootstrap-sensitive multi-subsystem change); implement in a dedicated
focused session with probe-level verification of each sub-item before the
FOREGROUND bootstrap.
