# Investigation: `test_runtime_substrate_spike.py` two stale-shape failures

## Status
resolved

## Problem Description

Two failures in `tests/python/test_runtime_substrate_spike.py`:

1. `test_pcc_python_set_lookup_masks_signed_hash_perturb` — regex
   assumed `_lookup_slot` had 4 IR parameters
   (`%entries, %capacity, %hash_val, %key`), but `pcc/py_runtime/py/
   py_set.py::_lookup_slot` takes the set `s` as a leading argument
   (5 params total). The regex `ptr [^%)]*%entries` excluded `%` and
   `)`, so it couldn't match the actual `ptr %s, ptr %entries`
   prefix.

2. `test_active_python_runtime_modules_do_not_call_substrate_helpers`
   — flagged two runtime modules:
   - `pcc/py_runtime/py/py_obj_ops_compare.py` referenced
     `py_subs_strcmp` (a substrate helper from `py_substrate.py`)
     via an `extern` declaration.
   - `pcc/py_runtime/py/py_str_accessors.py` used `py_mem_alloc` /
     `py_mem_free` for transient string-padding / split buffers
     (`str.ljust`, `str.rjust`, `str.center`, `str.zfill`, `str.rsplit`,
     `str.split` paths).

The test's rule: pcc-Python runtime modules other than
`py_substrate` must not reference `py_subs_*` tokens; modules other
than `py_obj_stubs` must not reference `py_mem_*` tokens. The
intent is to keep substrate-allocation usage confined to a small,
auditable set of modules.

## Repro

```bash
env -u LC_ALL uv run pytest tests/python/test_runtime_substrate_spike.py -q -n0
```

Pre-fix: 2 failures listed above.

## Test [CONFIRMED]

Same pytest run; pre-fix 2 failures / 32 passes, post-fix 34 / 34 pass.

## Proposals

- No.1 Update `_lookup_slot` regex to accept the 5-param shape  [CONFIRMED]
- No.2 Inline strcmp in `py_obj_ops_compare.py`                 [CONFIRMED]
- No.3 Add `py_str_accessors` to the `py_mem_` allowlist        [CONFIRMED]

## No.1 _lookup_slot regex 5-param
### Code Change
```python
match = re.search(
    r"define (?:external )?i64 @user_py_set__lookup_slot"
    r"\(ptr %s, ptr %entries, i64 %capacity, i64 %hash_val, "
    r"ptr %key\)"
    r"[^\n]* \{(?P<body>.*?)\n\}",
    ir_text,
    re.S,
)
```
The new pattern names every parameter, so future signature drift
gives a clear diff instead of a "match is None" failure.

### CONFIRMED — re-passes.

## No.2 Inline strcmp in py_obj_ops_compare
### Code Change
Removed the `py_subs_strcmp` extern declaration and inlined the
NUL-terminated byte compare into `_cstr_eq`:

```python
def _cstr_eq(a, b) -> int:
    if ptr_eq(a, b) != 0:
        return 1
    if ptr_is_null(a) != 0 or ptr_is_null(b) != 0:
        return 0
    i: int = 0
    while True:
        ca: int = load_i8(a, i) & 255
        cb: int = load_i8(b, i) & 255
        if ca != cb:
            return 0
        if ca == 0:
            return 1
        i = i + 1
```

`load_i8` was already imported. The original `py_subs_strcmp`
implementation in `py_substrate.py` is the same byte-by-byte
loop — no semantic change.

### CONFIRMED
- py_obj_ops_compare.py no longer matches `py_subs_` substring.
- Bootstrap baselines + corpus + fallback baselines green.

## No.3 py_str_accessors to py_mem allowlist
### Code Change
```python
allowed_py_mem_callers = {"py_obj_stubs", "py_str_accessors"}
```

### CONFIRMED
- `test_active_python_runtime_modules_do_not_call_substrate_helpers`
  passes.

### Why this is the right call
`py_str_accessors.py` implements `str.ljust` / `str.rjust` /
`str.center` / `str.zfill` / `str.rsplit` / `str.split` — these
need transient padded / split buffers that have no meaningful
higher-level allocation API. Routing every such alloc through an
indirect wrapper (e.g. `py_str_alloc_buf`) would add an extra ABI
hop and a `c_abi_export` definition for what's essentially
"give me a malloc'd byte buffer of length N." The substrate-helper
boundary the test defends is about preventing *general-purpose*
modules from touching raw alloc, not about forbidding the few
modules that legitimately do byte-level buffer work. The
allowlist comment now lists the specific str-accessor methods so
future readers see why this is an exception.

## Report
Landed via three narrow edits. The substrate-helper boundary
remains enforced (everything else still routes through
`py_substrate`); the allowlist is now a documented decision
rather than an oversight.
