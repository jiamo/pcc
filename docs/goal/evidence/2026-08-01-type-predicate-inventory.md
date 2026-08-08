# String-based type predicates in type_infer.py: inventory

Date: 2026-08-01

Task: `ARCH-P3-TYPE-PREDICATE-INVENTORY`

## Why an inventory first

An external audit claimed "dozens" of string-matched type branches in
`pcc/py_frontend/type_infer.py` (5,038 lines); a narrow probe had found only
4. Neither number could justify touching bootstrap-critical type inference,
so this row required the count before the refactor. This is the count.

## Method

AST walk over the whole module (not a regex): every `Compare` whose right
side is a string literal — or a tuple/list/set of string literals — where the
left side is `<expr>.name`, `str(<expr>)`, or a variable whose name contains
`name`, plus every `getattr(x, "name", ...)`. Literals are only counted when
at least one names a Python type or a pcc AST type class.

## Result: 31 predicates, in four groups

```text
container-name      15   list / dict / tuple / set / frozenset / tuple_variadic
scalar-type-name    12   int / float / bool / complex / str / bytes /
                         bytearray / memoryview / type / None / NoneType
ast-class-name       2   membership tests over pcc AST type CLASS names
defensive-getattr    2   getattr(ty, "name", <default>)
```

Neither prior claim was right: not "dozens" of scattered ad-hoc branches, and
not 4. Thirty-one, clustered in a handful of functions.

### container-name (15)

- `type_infer.py:181` — `if name == "set":`
- `type_infer.py:183` — `if name == "frozenset":`
- `type_infer.py:211` — `if name != "list":`
- `type_infer.py:226` — `if name != "dict":`
- `type_infer.py:653` — `if ty.name == "list":`
- `type_infer.py:655` — `if ty.name == "dict":`
- `type_infer.py:657` — `if ty.name == "tuple":`
- `type_infer.py:1213` — `if bname == "tuple":`
- `type_infer.py:4614` — `if name not in ("tuple", "tuple_variadic"):`
- `type_infer.py:4695` — `if name == "list" and _list_type_elem(got) is not None:`
- `type_infer.py:4697` — `if name == "dict" and _dict_type_parts(got) is not None:`
- `type_infer.py:4699` — `if name == "tuple" and _tuple_type_parts(got) is not None:`
- `type_infer.py:4741` — `if declared.name == "list" and isinstance(got, ListType):`
- `type_infer.py:4743` — `if declared.name == "dict" and isinstance(got, DictType):`
- `type_infer.py:4745` — `if declared.name == "tuple" and isinstance(got, TupleType):`

### scalar-type-name (12)

- `type_infer.py:263` — `return ty.name == "None" or ty.name == "NoneType"`
- `type_infer.py:263` — `return ty.name == "None" or ty.name == "NoneType"`
- `type_infer.py:1125` — `if bname == "type" and len(new_args) == 3:`
- `type_infer.py:1133` — `if bname == "int":`
- `type_infer.py:1141` — `if bname == "bool":`
- `type_infer.py:1149` — `if bname == "float":`
- `type_infer.py:1157` — `if bname == "complex":`
- `type_infer.py:1181` — `if bname == "str":`
- `type_infer.py:1189` — `if bname == "bytes":`
- `type_infer.py:1197` — `if bname == "bytearray":`
- `type_infer.py:1205` — `if bname == "memoryview":`
- `type_infer.py:4721` — `if declared.name == "NoneType" and _is_none_type(got):`

### ast-class-name (2)

- `type_infer.py:3206` — `if class_name in (`
- `type_infer.py:3471` — `if class_name in (`

### defensive-getattr (2)

- `type_infer.py:180` — `name = getattr(ty, "name", "dyn") or "dyn"`
- `type_infer.py:4694` — `name = getattr(declared, "name", "")`

## Classification: what belongs in shared predicates, what does not

**Does not belong in a shared predicate — keep as is (14):** the twelve
`scalar-type-name` branches at lines 1125-1205 are one `if/elif` ladder
dispatching a *builtin call by name* (`int(...)`, `str(...)`, `bytes(...)`).
The string is the call target's name, not a type test; folding it into a
typed predicate would replace a readable dispatch table with indirection and
would not remove a single comparison. The two `defensive-getattr` uses are
tolerating a missing attribute, which a predicate cannot do for the caller.

**Would benefit from a shared predicate (15 container-name):** these repeat
`name == "list"` / `"dict"` / `"tuple"` across four separate regions
(181-226, 653-657, 4614, 4694-4745). Three of them (4741-4745) already pair
the name test with the matching `isinstance` check, which is the shape a
`is_list_type(ty)` / `is_dict_type(ty)` helper would encapsulate exactly.

**Should become isinstance, not a predicate (2 ast-class-name):** lines 3206
and 3471 compare `class_name` against a tuple of pcc AST type class names.
That is an `isinstance` surrogate written as strings, almost certainly to
survive the self-host path where `isinstance` on cross-module dataclasses is
unreliable — the same degradation this session hit in `encode_type`. Changing
them without fixing that underlying self-host issue would break pcc1.

## Decision

Inventory only; **no refactor lands in this row**. The 15 container-name
predicates are a genuine, bounded centralization candidate and are recorded
here as such. The 2 ast-class-name sites are explicitly *not* a cleanup
target until the self-host isinstance degradation is fixed, because they are
a workaround for it, not sloppiness.

## Not proven

- Whether centralizing the 15 container-name predicates changes generated
  code at all. Type inference is bootstrap-critical, so that refactor needs
  its own slice with frontend suites plus a stage1..3 chain, which this row
  deliberately does not attempt.
- Other frontend modules were not inventoried; the row scopes to
  `type_infer.py`.
