# DynType attribute ownership — investigation, not a fix — 2026-08-25

## Status

**Open.**  `PY-P1-ATTR-GETATTR-OWNED-VALUE-UNREGISTERED` is not fixed.  A patch
was written, measured to be unreachable for the case it targeted, and
**reverted**; `attr_load_lowering.py` has an empty diff.  No production change
ships from this slice.

## Scope is wider than the row assumed

The row describes "the generic DynType attribute emitter".  There are **19**
`py_obj_getattr` emission sites across the frontend, and only one registers its
result as owned:

```text
registered:      exact_int_lowering.py:458
not registered:  assignment_statement_lowering.py:2010, async_with_lowering.py:530,
                 attr_load_lowering.py:764/1481/1544/1738/1781,
                 builtin_type_attr_lowering.py:79/214,
                 call_expression_lowering.py:1822,
                 import_lowering.py:604/665,
                 list_method_lowering.py:84/1100,
                 method_call_expression_lowering.py:191/1740,
                 native_modules.py:2767, string_method_lowering.py:685
```

Two of the unregistered sites already have a release nearby
(`attr_load_lowering.py:1781`, `import_lowering.py:665`), which is exactly the
double-free hazard the row warns about.  Registering globally is not a
single-slice change.

## What was measured

`attr_load_lowering.py:1781` is confirmed to be the site that runs for a
dynamic receiver — established by temporarily tagging every `py_obj_getattr`
result name with its source line and re-emitting:

```text
%SITE1781_attr.b.
%SITE1781_attr.v.
```

At that site the nearby `_gc_release_if_owned(obj, expr.obj)` releases the
**receiver**, not the result.  The site has a scalar branch that marshals the
result to a native value, and `pcc/py_frontend/codegen/marshal.py` contains
**zero** releases — it only unpacks.  So a release there looked correct.

It was not reached.  Instrumenting the branch reports:

```text
[PROBE] ty=DynType
[PROBE] ty=DynType
```

for both `def get_float(o) -> float: return o.v` and the bool equivalent.  The
attribute expression is `DynType`; the unboxing to `double` happens later, at
the **return boundary**, not in the attribute branch.  The patch was therefore
dead code for the shape it was written for, and was reverted rather than left
in place looking like a fix.

## The leak is real, and it is at the consumer boundary

```text
%attr.v = call ptr @py_obj_getattr(ptr %o, ptr @.pyattr.v)   ; NEW reference
attr.v.ok:
  %m.flt_unbox = call double @py_float_to_f64(ptr %attr.v)
  call void @pcc_gc_frame_leave(...)
  ret double %m.flt_unbox
```

No `pcc_gc_release` of `%attr.v` anywhere in the function, and the frame map is
the borrowed one.  One object leaked per call.

This is the **same family** as the print-consumer leak recorded in
`2026-08-25-print-consumer-ownership-investigation.md`: an owned object handed
to a consumer that unpacks or borrows it and never releases.  The consumers
differ (print, return-with-scalar-annotation) but the shape is identical, so
they should be fixed as one ledger rather than site by site.

## Two things a successor should not redo

- Patching the scalar branch of `attr_load_lowering.py:1781`.  Measured
  unreachable for dynamic receivers: `expr.ty` is `DynType` there.
- Assuming `marshal_from_object` consumes its argument.  It does not;
  `marshal.py` has no release of any kind.

## Nonclaims

- Nothing was fixed and no frontend file was modified.
- The 19-site inventory is a count of emission sites, not a claim that all 19
  leak; their consumers were not individually audited.
- No bootstrap, stage, fixed-point or five-GC gate was run.
