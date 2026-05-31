# Investigation: `enum` recursive stdlib pull blocks native Enum support

## Status
resolved

## Problem Description

`tests/python/data_model/test_final_language_compiled_acceptance.py::test_t1_metaclass_type_enum_abcmeta_compiled`
had been failing since the file was added — long-standing multi-iter
blocker — with:

```
error: PCC-PY-COMPILE-001: Python pipeline requires libpython fallback
  for multi-file compile (module enum generated IR still calls py_cpy_*
  helpers); rerun with --python-libpython=auto/on or
  PCC_PYTHON_LIBPYTHON=auto/on
```

The test source includes:

```python
from enum import Enum, auto

class Color(Enum):
    RED = auto()
    BLUE = auto()

print(Color.RED.name)
print(Color.BLUE.value)
```

Surprise finding: pcc *already* has native `Enum` / `IntEnum` /
`auto()` support:
- `pcc/py_frontend/codegen/class_gen.py::_is_enum_like_class` detects
  the base.
- `_enum_member_value` walks the class body, treats
  `NAME = auto()` as the next sequential integer and
  `NAME = <int_literal>` as an explicit value.
- Members register in `ClassInfo.enum_members: dict[str, int]`.
- `pcc/py_frontend/codegen/dynamic_type_lowering.py::_maybe_emit_enum_member_attr`
  handles `Color.RED.name` (returns the str) and `Color.BLUE.value`
  (returns the int) without any heap allocation.
- `auto()` lowers to `None` literal in
  `call_expression_lowering.py:644` (the value is computed
  separately by `_enum_member_value`).

The blocker wasn't missing support — it was that the multi-file
recursive stdlib walker (`pcc.py_frontend.pipeline._expand_recursive_stdlib`)
was pulling `pcc/py_stdlib/enum.py` into the native compile set.
That module's `class _EnumMeta(type)` metaclass + `super().__new__`
+ `int.__new__(cls, v)` emits `py_cpy_*` fallbacks. The presence of
those fallbacks in any compiled module fails the strict
`--python-libpython=off` gate, even though `pcc.py_stdlib.enum`
wasn't actually needed at runtime (class_gen handles everything
natively).

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/data_model/test_final_language_compiled_acceptance.py::test_t1_metaclass_type_enum_abcmeta_compiled \
  -q -n0
```

Pre-fix: PyPipelineError listing the `enum` module's `py_cpy_*` count.

```bash
cat > /tmp/enum_test.py <<'EOF'
from enum import Enum, auto

class Color(Enum):
    RED = auto()
    BLUE = auto()

print(Color.RED.name)
print(Color.BLUE.value)
EOF
env -u LC_ALL uv run pcc --python-libpython=off --ir-scaffold=on \
  /tmp/enum_test.py -o /tmp/enum_test.out
/tmp/enum_test.out
```

Post-fix prints:

```
RED
2
```

## Test [CONFIRMED]

Both the pytest target and the minimal probe pass.

## Proposals

- No.1 Add `"enum"` to `_NATIVE_BUILTIN_IMPORTS` so the recursive
  stdlib walker skips compiling `pcc/py_stdlib/enum.py`         [CONFIRMED]

## No.1 enum skip
### Code Change

`pcc/py_frontend/pipeline.py::_NATIVE_BUILTIN_IMPORTS`:

```python
_NATIVE_BUILTIN_IMPORTS = frozenset(
    {
        ...,
        # ``enum`` has native ``Enum`` / ``IntEnum`` / ``auto`` support
        # via ``pcc/py_frontend/codegen/class_gen.py::_is_enum_like_class``
        # + ``_enum_member_value`` and the
        # ``_maybe_emit_enum_member_attr`` lookup in dynamic_type_lowering.
        # Skipping recursive compile of ``pcc/py_stdlib/enum.py`` avoids
        # pulling its heavy metaclass machinery into the closure (which
        # otherwise emits ``py_cpy_*`` fallbacks). The class_gen path
        # handles ``class X(Enum)`` / ``class X(IntEnum)`` natively.
        "enum",
    }
)
```

### CONFIRMED
- `test_t1_metaclass_type_enum_abcmeta_compiled` 1 passed in 0.85s
  (was the only `data_model` failure for many iterations).
- `tests/python/data_model/` 82 / 82 (was 81 / 82).
- `test_py_corpus.py` 177 / 177.
- `test_fallback_baseline.py` + `_ir_py_fallback_baseline.py` +
  `_bootstrap_gate_baseline.py` — 17 passed, 4 skipped.
- Total broad check: 275 passed, 4 skipped.

### Why this is the correct minimal fix
The native enum machinery was already in place. The recursive
stdlib walker is opt-in via `recursive_stdlib`, but it auto-enables
when the closure contains generic imports it can't otherwise
resolve. `enum` belongs to the same category as `sys` / `os` /
`re` / `gc` / `weakref` / `copy` — modules pcc handles natively
without needing the pure-Python stdlib port compiled in. The
allowlist addition just records that fact.

The IntEnum case (`class X(IntEnum):`) is also covered by
`_is_enum_like_class` (it checks both `Enum` and `IntEnum`); no
additional change needed.

## Report
Landed via a single-line allowlist addition. Closes a multi-iteration
blocker (`t1_metaclass`) that has been carried in `docs/current-goal-state.md`
across multiple sessions. The fix surfaced once I noticed that
`pcc/py_frontend/codegen/class_gen.py::enum_members` already
existed — the failing test wasn't waiting on new lowering, it was
waiting for the recursive walker to stop compiling a module that
isn't actually needed at runtime.
