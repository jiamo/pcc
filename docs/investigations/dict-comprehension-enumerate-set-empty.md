# Investigation: dict comprehension over `enumerate(set)` produces an empty dict

## Status

active

## Problem Description

`{name: index for index, name in enumerate(a_set)}` produced `{}` under pcc,
while CPython enumerated every set element. The comprehension-specific
`enumerate` fast path unconditionally used `py_obj_len` plus integer
`py_obj_getitem`; sets have a length but are not subscriptable, so every probe
returned no element and the loop silently published nothing.

This blocked pcc1 worker `.pco` validation. `native_object._validate_source_sections`
builds its relocation lookup with exactly this comprehension; a complete 47-
name set became an empty `known` dict, so `_pcc_gc_pin` was falsely reported as
an unknown relocation target.

Predecessor: `set-and-frozenset-of-dict-lower-to-empty.md` records the same
invalid positional-walk pattern for mappings and notes ordinary comprehension
over a dict was already correct. This investigation owns the distinct
`enumerate(non-indexable)` comprehension path.

## Repro

The retained v27 pcc1 probe reported:

```text
defined=24 undefined=24 seen=47 known=0
_pcc_gc_pin in undefined  True
_pcc_gc_pin in seen       True
_pcc_gc_pin in known      False
```

Focused user-level repro:

```python
names = {'_pcc_gc_pin', '_py_int_add', '_user_main'}
known = {name: index for index, name in enumerate(names)}
print(len(known), '_pcc_gc_pin' in known, sorted(known))
```

CPython prints `3 True` and all three names; pcc printed `0 False []`.

## Test [CONFIRMED]

`tests/python/test_native_comprehension_over_generator.py::`
`test_dict_comprehension_enumerate_set_uses_iterator_protocol` failed red with
`0 / False / []` and passed after the change with `3 / True / three keys`.

## Proposals

- No.1 Restrict the indexed enumerate-comprehension fast path to proven sequences [CONFIRMED focused]

## No.1 Restrict the indexed enumerate-comprehension fast path to proven sequences

### Code Change

The special enumerate loop remains for statically proven list, tuple, dict,
str and bytes-like sources. Set, Dyn and general ClassType sources fall through
to the existing value-position `enumerate` lowering, which uses
`py_obj_iter`/`py_obj_next` and materializes pairs; the ordinary comprehension
then consumes that iterable normally. No set-specific branch or linker name is
introduced.

### CONFIRMED focused

The red-first regression passed. Generator/scope/mapping-family adjacent gates
passed 20/20, and `comprehension_lowering` passed the real Stage1 contextual
zero-fallback gate. A rebuilt pcc1 worker `.pco` canary remains required before
closing this investigation.
