# Investigation Report: SQLite Broke on Forward-Declared Bitfield Struct Tags

## Executive Summary

This SQLite failure was not an MCJIT scalability problem.

The real bug was in `pcc`'s handling of named structs that:

- are forward-declared first
- are later defined with bitfields
- participate in recursive type graphs

SQLite's `sqlite3` / `Vdbe` pair hits exactly that pattern.

Two compiler bugs combined:

1. standalone tag definitions like `struct Vdbe { ... };` were not always
   registered as type definitions
2. when a named struct with bitfields was defined, `pcc` could create a fresh
   layout-backed LLVM type instead of reusing the forward-declared tag type

The result was a split type graph:

- some code still referenced an opaque `struct_Vdbe`
- other code used a separately created layout-backed `layout_Vdbe`

That mismatch corrupted field access in real helper functions such as:

```c
static int same_db(Vdbe *p, sqlite3 *db) { return p->db == db; }
```

With the bug present, even this helper returned false inside SQLite.


## Symptom

The observable SQLite failure looked like this:

- `sqlite3_step()` on an `INSERT` returned `0` instead of `SQLITE_DONE`
- `sqlite3Step()` itself also returned `0`
- `db->errMask` became `0`, so later result codes were masked down to `0`

At first this looked like a VDBE/runtime bug or an MCJIT problem.
It was neither.


## Narrowing Strategy

The useful reductions were:

1. prove the failure was real outside of `printf`-based debugging
2. show that `sqlite3Step()` itself returned `0`
3. show that `db->errMask` became `0`
4. show that the corruption first appeared on the `CREATE TABLE` statement
   teardown path
5. reduce that path to a tiny helper on real SQLite types

The decisive probe was not the whole `sqlite3VdbeReset()` function.
It was a tiny helper:

```c
static int same_db(Vdbe *p, sqlite3 *db) {
  return p->db == db;
}
```

That helper returned false under `pcc` and true under native compilers.

Once a first-field pointer read on `Vdbe *` was wrong, the problem was clearly
in type lowering / struct access, not in SQLite logic.


## Minimal Structural Cause

SQLite contains mutually recursive named types:

- `struct sqlite3` contains `struct Vdbe *`
- `struct Vdbe` contains `sqlite3 *`

`Vdbe` also contains bitfields, so `pcc` lowers it through the custom-layout
path instead of a normal LLVM struct body.

That combination exposed two gaps:

### 1. Standalone tag definitions were not finalized correctly

Declarations like:

```c
struct Vdbe { ... };
```

have no declared object name. They are type definitions only.

`codegen_Decl()` was letting some of these fall through the ordinary `TypeDecl`
object-declaration path instead of treating them as pure tag definitions.

### 2. Forward-declared named bitfield structs could split into two LLVM types

For normal named structs, `pcc` reuses the identified type for the tag.

For layout-backed structs with bitfields, it could create a separate
`layout_*` identified type instead of reusing the existing forward-declared
`struct_*` tag type.

That is fatal in recursive graphs.


## IR Signature

The broken IR for a reduced reproducer showed:

```llvm
%"..._struct_B" = type opaque
```

and helper functions lowered field access on `struct B *` by bitcasting raw
bytes instead of using a finalized layout-backed type.

That was the clearest sign that the named tag never became a proper concrete
type in the places that mattered.


## Fix

The fix in `pcc/codegen/c_codegen.py` had two parts:

1. Treat standalone tag declarations as type definitions only:

   - `struct S { ... };`
   - `union U { ... };`
   - `enum E { ... };`

   These must register the type and return without trying to declare an object.

2. Reuse the same identified type for named layout-backed structs:

   - if a forward-declared tag already exists, set the raw layout body on that
     existing type
   - do not create a fresh `layout_*` named type for the same source-level tag


## Regressions Added

Two regression layers were added.

### Small focused regression

A small C test now covers:

- forward declaration
- standalone named bitfield struct definition
- recursive pointer references
- helper-function nested field access

This lives in `tests/test_bitfields.py`.

### SQLite runtime integration

The full SQLite integration tests in `tests/test_sqlite.py` remain in place.
They are no longer carrying a separate internal `Vdbe` probe, because the
generic regression above covers the structural bug directly and the runtime
tests already exercise the real SQLite execution path end to end.


## Validation

The following now pass:

```bash
env -u LC_ALL uv run pytest tests/test_bitfields.py tests/test_sqlite.py -q -n0
env -u LC_ALL uv run pytest -q
```


## Lessons

- When a real project fails, reduce to the smallest helper that still uses the
  real project types.
- For recursive aggregates, "same source-level struct tag" must imply "same
  LLVM identified type". Any split there is a compiler bug.
- Standalone tag definitions are easy to mishandle because they look like
  declarations in the AST but do not declare storage.
