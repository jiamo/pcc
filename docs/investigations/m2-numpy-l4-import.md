# Investigation: NumPy L4 import under pcc1/self/no-libpython

## Status

active

## Problem Description

`M2-NUMPY-L4` requires the pinned NumPy 2.4.4 package to run
`import numpy as np; print(np.__version__)` through
pcc1/self/pcc-native/no-libpython with no host Python/pcc process edge. The
predecessor module-graph task proves host-current-source strict loader entry and
now stops in `_multiarray_umath` `Py_mod_exec` at the first missing compiled
package module, `numpy._globals`.

This investigation advances the real package closure without weakening Python
semantics or adding NumPy-name dispatch. Each newly exposed failure is a
separate boundary and must receive a minimized regression before a compiler or
runtime change.

## Repro

Use the loader-only refresh while changing only Python graph/frontend code. It
reuses the already-fresh 136/137-object artifacts and normally completes in
about two seconds:

```text
gtimeout 120s env -u LC_ALL uv run python scripts/numpy_head_gate.py loader \
  --source projects/numpy-2.4.4 \
  --build-root build/head-truth/numpy-core \
  --result build/head-truth/numpy-core/result.json \
  --lane numpy-core-head --loader-timeout 90
```

Current deterministic boundary:

```text
first_missing_module / Py_mod_exec / numpy._globals
```

## Proposals

- No.1 Compile and publish `numpy._globals` plus its ordinary relative closure [CONFIRMED]
- No.2 Expose `__name__` for registered C-extension type objects [CONFIRMED]
- No.3 Return `py_None` from successful pointer-ABI fallthrough/bare return [CONFIRMED]
- No.4 Preserve a registered C-extension type object's custom metaclass [CONFIRMED]
- No.5 Support generic `PyArg_Parse*` `p`, `O!`, and `O&` units [CONFIRMED]
- No.6 Populate/inherit `tp_alloc` during `PyType_Ready` [CONFIRMED]
- No.7 Dispatch C-extension `nb_int`/`nb_index` numeric slots [CONFIRMED]
- No.8 Support `Py_BuildValue` list containers [CONFIRMED]

## No.1 Compile and publish `numpy._globals` plus its ordinary relative closure

### Planned substitution

Seed `numpy._globals` in the generic loader source and let normal package-source
closure discovery pull `. _utils`. Do not alter the compiler until the exact
parse/type/codegen/runtime boundary is observed. Success means the real first
blocker moves to the next import in `initialize_static_globals`; it does not
yet prove full `import numpy`.

### CONFIRMED

The generic compiled-module closure and subsequent focused runtime/frontend
repairs advanced both strict loader lanes through `_globals`,
`_core._exceptions`, `_core.printoptions`, and `os`. The loader now compiles
and publishes `numpy.dtypes`; the first failure is no longer a missing module.

## Update: registered C-extension type metadata boundary

The deterministic core loader refresh now completes in about three seconds and
fails at `numpy/dtypes.py:35` while evaluating `DType.__name__`:

```text
AttributeError: __name__
```

Ordinary pcc-Python class metadata is not the failing layer: the focused
`Child.__base__.__name__` regression is green. NumPy passes C-extension type
objects created/readied through the pcc C-API bridge into `_add_dtype_helper`.
Those objects are present in the existing C-extension type registry, but the
generic object-attribute dispatcher currently treats their compatibility
header's `PY_TYPE_NONE` tag as an ordinary `None` object.

## Test [CONFIRMED]

The existing `specdemo` extension fixture now publishes a type created by
`PyType_FromSpec`; the following focused gate reproduces the NumPy boundary:

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_pcc_native_extension_loader.py::test_pcc_native_custom_type_name_under_self_backend_no_libpython
```

Observed before the runtime change: `1 failed in 0.79s` with
`AttributeError: __name__` at `print(specdemo.Spec.__name__)`.

## No.2 Expose `__name__` for registered C-extension type objects

### Code Change

Reuse the C-API shim's existing type-object registry predicate and add one
attribute bridge that derives Python `__name__` from the final component of
`tp_name`. Route both the C semantic runtime and its pcc-Python mirror through
that bridge before ordinary type-tag dispatch. This is generic C-API type
metadata behavior; it contains no NumPy-name dispatch.

### CONFIRMED

The focused regression passes (`1 passed in 6.25s`) and the adjacent
`PyType_FromSpec` trio passes (`3 passed in 1.40s`). The cached core loader then
advanced past `DType.__name__` in 2.65 seconds. This confirms the bridge while
leaving full `import numpy` unclaimed.

## Update: successful Python-call return is a NULL C-API result

The next cached loader failure is `TypeError: object is not callable` while
`numpy.dtypes._add_dtype_helper` is called through `PyObject_Call`. Existing
runtime dispatch/exception logging proves both C-extension type `__name__`
lookups succeed and the function call itself then returns NULL without a
pending exception.

The self-backend input IR provides the exact boundary. The normal fallthrough
block of `user_numpy_dtypes__add_dtype_helper` runs GC-frame cleanup and ends
in `ret ptr null`; its separate `err.exit` also ends in `ret ptr null`. The
former is a successful Python `None` return and must use the `py_None`
singleton; only the latter is the C-API failure sentinel.

## No.3 Return `py_None` from successful pointer-ABI fallthrough/bare return

### Planned substitution

Add a focused extension regression whose C method invokes a pcc-Python
callable with `PyObject_Call` and checks that both implicit fallthrough and a
bare `return` produce `Py_None`. Then change only the normal user-function
fallthrough and bare-return lowering for pointer ABI results from a null
constant to `_emit_none_literal()`. Error-exit NULL returns remain unchanged.

### CONFIRMED

The focused strict self/no-libpython regression compiled successfully and
failed at runtime before the change with `TypeError: object is not callable`.
After changing the two successful pointer-return paths, it passes (`1 passed
in 0.74s`); the adjacent class/call regression pair passes (`2 passed in
1.26s`). Error exits were not changed.

The cached NumPy core loader refreshed in 3.67 seconds and advanced to a new,
independent C-extension semantic boundary:

```text
TypeError: ArrayMethod provided object None is not a DType.
(method: numeric_copy_or_byteswap)
```

This proves the prior call boundary moved. It does not prove `import numpy`, so
the task remains active and the first-blocker baseline is not promoted across
this semantic mismatch.

## Update: registered type objects lose custom metaclass identity

NumPy's `validate_spec` rejects one of its generated legacy DType classes at
`PyObject_TypeCheck(spec->dtypes[i], &PyArrayDTypeMeta_Type)`. The rejected
object is a real DType class produced by `dtypemeta_wrap_legacy_descriptor`:
its prototype stores `&PyArrayDTypeMeta_Type` in the compatibility `ob_type`
slot, and the descriptor is then stamped with that DType class by
`Py_SET_TYPE`.

The pcc bridge loses that information when it queries the class object itself.
`pcc_capi_type()` recognizes every registered C-extension type object but
unconditionally returns `&PyType_Type`; it does not read the type object's
already-populated `ob_type`. Consequently a custom-metaclass class fails
`PyObject_TypeCheck(class_object, custom_metaclass)`. The `None` in NumPy's
error rendering is a separate representation symptom of the same type object,
not the value stored in the DType table.

## No.4 Preserve a registered C-extension type object's custom metaclass

### Planned substitution

Add a generic C-extension regression with a registered custom metaclass and a
registered class object whose type is set to that metaclass. Confirm that
`PyObject_TypeCheck(class_object, custom_metaclass)` is red. Then make
`pcc_capi_type()` return the registered type object's non-NULL compatibility
`ob_type`, falling back to `&PyType_Type` only when it has no explicit
metaclass. This preserves builtin/static type behavior and contains no NumPy
dispatch.

### CONFIRMED

The focused regression first compiled and ran under strict self/no-libpython but
returned `1` instead of `3`: direct `Py_TYPE` was correct while
`PyObject_TypeCheck` was false. After preserving the non-NULL compatibility
`ob_type`, the custom-metaclass regression and three adjacent type-object tests
pass (`4 passed in 9.67s`).

The cached NumPy loader refreshed in 3.16 seconds and advanced beyond
`numeric_copy_or_byteswap` to a new semantic boundary:

```text
TypeError: argument type mismatch
```

The first-blocker baseline remains unpromoted because this is another semantic
mismatch, not a reviewed missing-module frontier.

## Update: optional `O&` is rejected before argument lookup

A temporary, tagged diagnostic appended the active parse format to the generic
error and was removed immediately after one cached loader run. It identified
the exact caller and format:

```text
stringdtype_new: |$pO&:StringDType
```

This constructor has no required arguments. pcc's current `PyArg_ParseTuple`
and `PyArg_VaParseTupleAndKeywords` loops reject every `O!` or `O&` unit before
checking whether an optional argument was supplied. The parser also lacks the
`p` truth-value unit and cannot consume the correct vararg shapes when an
optional `O!`/`O&` is absent. Thus `StringDType()` fails despite providing no
bad argument.

## No.5 Support generic `PyArg_Parse*` `p`, `O!`, and `O&` units

### Planned substitution

Extend the existing keyword-method fixture with a package-neutral
`|$pO&` parser and prove the no-argument call is red. Implement the standard
truth-value destination for `p`, expected-type plus output destinations for
`O!`, and converter callback plus opaque output destination for `O&`, including
correct vararg consumption for absent optional values. Exercise absent and
present keyword values; do not special-case `StringDType` or NumPy.

### CONFIRMED

The package-neutral focused regression failed at its no-argument call before
the change. After implementing all three units and their absent-optional
vararg shapes, it passes with absent/present `p`, absent/present `O&`, and a
present `O!` type constraint (`1 passed in 0.71s`). The existing keyword parser
regression also passes (`2 passed in 1.06s` when run with the new test).

The cached loader refreshed in 2.77 seconds and no longer raises the
`StringDType` argument mismatch. It now exits with `-11` and no Python
exception text, exposing a distinct native crash. This confirms the parser
boundary only; the crash requires its own evidence chain.

## Update: native crash after StringDType parsing

The first post-parser run terminates with SIGSEGV (`run_returncode=-11`) after
2.77 seconds. There is no stderr traceback. Per the debugging playbook, this is
now a separate crash investigation: capture the native stack before changing
runtime semantics.

LLDB stops on a NULL call target and gives the exact stack:

```text
frame #0  0x0
frame #1  arraydescr_new at descriptor.c:2523
frame #2  new_stringdtype_instance at dtype.c:30
frame #3  stringdtype_new at dtype.c:812
frame #4  pcc_capi_call_type_object
```

`descriptor.c:2523` calls `subtype->tp_alloc(subtype, 0)`. The StringDType
subtype leaves `tp_alloc` unset and expects `PyType_Ready` to inherit the base
type's allocation slot. pcc's `PyType_Ready` currently only registers the type
and sets the READY flag; `PyType_FromSpec` fills `tp_alloc` for its own heap
types, but static/readied types do not receive the standard default/inherited
slot.

## No.6 Populate/inherit `tp_alloc` during `PyType_Ready`

### Planned substitution

Extend the existing package-neutral base/derived C-extension fixture to inspect
the allocation slots after both types are readied. Confirm they remain NULL
before the change. Then make `PyType_Ready` inherit a non-NULL base `tp_alloc`,
or use `PyType_GenericAlloc` when neither the type nor its base provides one.
This is generic static-type readiness behavior and contains no DType/NumPy
dispatch.

### CONFIRMED

Before the change, the focused readied base/derived fixture returned `4`: both
allocation slots were NULL, so only their equality bit was set. After defaulting
the base slot and inheriting it into the derived type, the focused test plus the
existing subtype and custom-type tests pass (`3 passed in 1.81s`).

The next cached loader run took 11.42 seconds because the shim mtime triggered a
one-time runtime archive rebuild. It advanced beyond the native crash and now
reports a normal exception boundary:

```text
TypeError: unsupported operand type(s) for int()
```

No NumPy C/C++ objects were rebuilt. The slot-inheritance fix is confirmed; the
new conversion failure is independent.

## Update: `PyNumber_Long` rejects a C-extension numeric scalar

An LLDB breakpoint on the first non-tagged `PyNumber_Long` argument captures
the failing call directly:

```text
PyNumber_Long
LONG_setitem at arraytypes.c.src:365
PyArray_Pack
get_initial_from_ufunc
PyArray_NewLegacyWrappingArrayMethod
InitOperators
```

The object's pcc header has dynamic C-extension type tag `0x10013`. It is not a
pcc builtin int/float; its registered type supplies Python numeric protocol
slots through `tp_as_number`. The shim's `PyNumber_Long`, `PyNumber_Index`,
`PyIndex_Check`, and `PyNumber_Check` currently recognize only pcc builtin
tags and never consult a C-extension type's `nb_int`/`nb_index` slots.

## No.7 Dispatch C-extension `nb_int`/`nb_index` numeric slots

### Planned substitution

Add a generic extension fixture with one custom numeric type implementing
`nb_int` and another implementing `nb_index`. Confirm `PyNumber_Long` is red
on the first custom instance. Mirror the public `PyNumberMethods` layout in the
shim, dispatch `nb_int` with `nb_index` fallback, dispatch `PyNumber_Index`
through `nb_index`, and make the check predicates recognize those slots.
Require slot results to be pcc int-compatible objects.

### CONFIRMED

The focused extension first failed with the same `unsupported operand type(s)
for int()` error. After generic numeric-slot dispatch, that regression and the
existing full number-protocol C-API smoke test pass (`2 passed in 1.31s`).

The cached loader refreshed in 3.10 seconds and advanced beyond
`LONG_setitem`/`PyNumber_Long` to:

```text
ValueError: unsupported Py_BuildValue format: [
```

The first-blocker baseline remains unpromoted across this semantic boundary.

## Update: nested list container in `Py_BuildValue`

The linked NumPy binary contains the active formats and source maps them to
`multiarray/number.c` static initialization:

```text
{s, [(i), (i, i), (i)]}
{s, [(i, i), (i, i), (i, i)]}
```

pcc's recursive builder already handles tuple `(...)` and dict `{...}`
containers, but `pcc_capi_build_one` has no `[...]` branch, so the nested list
value is rejected before its tuple elements are read.

## No.8 Support `Py_BuildValue` list containers

### Planned substitution

Add a generic nested `Py_BuildValue("{s, [(i), (i, i)]}")` extension
regression. Reuse the existing recursive tuple-element builder through the
matching `]` terminator, convert the resulting tuple to a real list, and retain
the existing ownership/error behavior. No NumPy format string is special-cased.

### CONFIRMED

The focused nested dict/list/tuple regression first failed on `[` with the same
ValueError. After adding the recursive list branch, it and the adjacent dict
builder regression pass (`2 passed in 1.11s`).

The cached loader refreshed in 3.05 seconds and advanced to:

```text
AttributeError: __array_finalize__
```

This confirms the builder feature while leaving the full import claim open.

## Update: C-extension type objects do not expose `tp_methods`

NumPy initializes its cached base finalizer with:

```c
PyObject_GetAttrString((PyObject *)&PyArray_Type, "__array_finalize__")
```

`PyArray_Type.tp_methods` contains that `METH_O` entry, but pcc's generic
`pcc_capi_type_object_getattr` bridge currently recognizes only `__name__`.
Reusing the existing module/instance method wrapper directly would be wrong:
it binds the type object as `self`, and it creates a fresh function on every
lookup.  NumPy later relies on inherited `__array_finalize__` returning the
same descriptor identity as the base lookup so it can skip the no-op base
finalizer.

## No.9 Expose stable unbound descriptors from C-extension `tp_methods`

### Planned substitution

Add a generic `PyType_FromSpec` fixture whose `Py_tp_methods` table contains a
normal instance method.  First prove that class lookup is red.  Then walk the
type/base chain, create one unbound callable per defining `PyMethodDef`, cache
and pin it as runtime-owned descriptor state, and return a new reference to
that stable object on every lookup.  The callable must accept an explicit
instance as its first argument rather than binding the type object as `self`.
The focused gate will check lookup, repeated-lookup identity, and an explicit
unbound call.  No NumPy type or method name is special-cased.

### CONFIRMED

The focused fixture first failed with `AttributeError: marker`.  After the
generic type/base method-table lookup and stable unbound descriptor cache, the
same test passes; the four adjacent type bridge tests also remain green
(`5 passed in 4.47s`).

The cached real loader then completed successfully:

```text
run_returncode: 0
run_stdout: numpy-core-import-complete
first_blocker: null
links_libpython: false
links_llvm: false
```

All 137 planned translation units remained compiled and linked; no NumPy
object rebuild was needed.  The gate still reported FAIL because the persistent
ratchet schema rejected `first_blocker: null` instead of representing terminal
import completion.

## Update: first-blocker ratchet cannot represent completion

The ratchet validates both observations and each lane's `current` field as an
exactly-one blocker object.  That invariant was correct while import always
failed, but contradicts the L4 exit criterion that a successful command has an
empty first-blocker record.  Treating the successful loader as an arbitrary
synthetic blocker would make the evidence less truthful.

## No.10 Add an explicit terminal empty-blocker state

### Planned substitution

Permit explicit `null` only for a lane's current observation/current baseline,
never inside resolved blocker history.  Clearing a non-empty baseline remains
an unreviewed forward change requiring explicit promotion.  Once promoted,
the empty state is stable and any reappearing blocker is a regression.  Update
the head-truth manifest validator to require the `first_blocker` field while
accepting either one classified blocker or explicit `null`, with an accepted
ratchet in both cases.

### CONFIRMED

The focused ratchet/head-truth tests pass (`19 passed in 0.23s`).  Each real
loader result was then reviewed and promoted independently.  Both lanes are
now stable at frontier 6 with an explicit empty blocker:

```text
numpy-core-head:          PASS / first_blocker=null / refresh 3.851s
numpy-package-artifact:   PASS / first_blocker=null / refresh 3.167s
```

This closes the core-extension loader frontier but not L4: neither command is
the required pcc1 execution of full `numpy/__init__.py` and `np.__version__`.

## Update: full package closure omits imports nested in dependency modules

The real L4 source `import numpy as np; print(np.__version__)` fails strict
self/no-libpython compilation before execution:

```text
PCC-PY-COMPILE-001: Python pipeline requires libpython fallback for
multi-file compile (modules: numpy)
```

An exact combined-IR diagnostic built from the same closure contains only
seven modules.  `numpy/__init__.py` is a discovered dependency, so closure
collection scans only its unindented imports.  Its actual `_core`, `lib`, and
other initialization imports are inside a module-level `else:` block, while
lazy imports also occur inside `__getattr__`; those unresolved imports emit
`py_cpy_import` in the otherwise no-libpython module.  This is a generic
transitive package-closure bug, not a NumPy module-name issue.

## No.11 Close imports nested in transitive package modules

### Planned substitution

Add a two-level package regression where the entry imports a package and the
package imports a present sibling inside a function.  Confirm the current
closure skips the sibling and strict compilation fails.  Then scan imports at
all indentation levels for every recursively discovered package module, as is
already done for the entry module.  Resolution remains bounded to real module
files under the configured package roots; absent modules remain absent and no
package name is special-cased.  Re-run the real L4 host-source compile to
measure the next boundary before rebuilding pcc1.

## Update: `__new__` method misses private class-attribute mangling

The first complete host-current-source L4 artifact is a standalone arm64
Mach-O that links only `libSystem`; its pinned native NumPy extension links
Accelerate, libc++, and libSystem, with no libpython or LLVM dependency.
Compilation completed in about 21 seconds, but direct execution fails at
`numpy/_globals.py:56`:

```text
AttributeError: __instance
```

The class body declares `__instance`. Contextual IR corrected the initial
inspection: pcc stores that declaration under the raw `__instance` spelling,
and the failing load plus adjacent store also send that raw spelling to the
runtime. All three boundaries therefore agree with each other but disagree
with Python's lexical `_NoValueType__instance` spelling. The load and store are
inside an undecorated `__new__`, which correctly remains an ordinary method in
the method table even though class construction passes the class object as its
first argument.

## No.12 Mangle private attributes on the current class receiver in `__new__`

### Planned substitution

Add a package-neutral singleton whose undecorated `__new__` loads and stores a
private class attribute through `cls`. After confirming the failure, use the
existing private-name helper consistently for class-attribute declaration,
lookup, store, and source attribute access in a lexical class context. Keep
module-level dynamic objects and dunder names ending in `__` unchanged.

### CONFIRMED

The focused singleton first failed on line 5 with `AttributeError:
__instance`. After the consistent lexical mangling change, contextual IR uses
`_NoValueType__instance` for the class initializer, load, and store, and the
initial load succeeds. Execution now reaches line 6 and fails while evaluating
the assignment RHS. The IR identifies a separate boundary:
`super().__new__(cls)` is lowered to `ptr null`, which is then passed to
`py_obj_setattr` as the value.

## Update: base `object.__new__` call lowers to null

`_NoValueType.__new__` uses Python's ordinary singleton pattern and delegates
allocation to `super().__new__(cls)`. pcc recognizes the enclosing `__new__`
enough to invoke it during class construction, but does not yet materialize the
base `object.__new__` result for the zero-argument `super()` call.

## No.13 Lower `super().__new__(cls)` through the native object allocator

### Planned substitution

Keep the same package-neutral singleton as the red test. Trace the existing
`super()` method-call dispatch and native instance allocation helper, then
route the builtin `object.__new__` case through that generic allocator for the
runtime class argument. Do not special-case NumPy or `_NoValueType`; retain
the normal subclass/class-layout behavior and verify repeated construction
returns the stored singleton.

### CONFIRMED

The focused singleton now passes (`1 passed in 0.48s`), including two class
calls and identity of the stored result. The adjacent callable-attribute file
also passes (`7 passed in 2.41s`). The rebuilt host-current-source NumPy L4
artifact compiled in 14.79 seconds and moved past `numpy._globals`; its next
first boundary is stdlib `copyreg.py:51` during module initialization.

## Update: builtin type `__new__` is not a first-class callable value

`copyreg` initializes `_new_type = type(int.__new__)`. pcc already lowers a
direct `int.__new__(...)` call and exposes canonical native builtin type
objects, but attribute lookup on that type object raises `AttributeError:
__new__`. This happens before `_reduce_ex` is called and prevents stdlib import.

## No.14 Materialize builtin type `__new__` callable values

### Planned substitution

Add a package-neutral regression that binds `new = int.__new__` and calls
`new(int, "41")`. Materialize a native function value that captures the
canonical builtin type, checks the explicit class argument, removes it, and
uses the existing native builtin constructor call path. Reject unsupported
builtin-subclass allocation explicitly rather than returning the wrong
representation.

### CONFIRMED

The focused test first failed at line 1 with `AttributeError: __new__`. With
the native callable bridge it passes and prints `41` (`1 passed in 0.47s`).

## Update: class-scope literal method default resolves as a module name

The rebuilt host L4 artifact moved past `copyreg` and fails while initializing
`pcc/py_stdlib/inspect.py:15`. `Parameter.__init__` declares
`kind=POSITIONAL_OR_KEYWORD` after assigning that class constant to `1`.
Contextual IR shows method signature objects are currently built before class
attribute initialization, so the default Name emits a module-scope NameError.

## No.15 Resolve preceding immutable class constants in method defaults

### Planned substitution

Add a package-neutral class with an integer class constant used as a later
method default. Resolve a default Name only when its definition precedes that
method in the same class body and the definition is an immutable literal;
substitute the definition-point expression into the signature object. Do not
use the class's final attribute map, which would incorrectly observe later
rebindings, and do not re-evaluate mutable class values.

### CONFIRMED

The focused regression first failed at the method definition with `NameError:
POSITIONAL_OR_KEYWORD`. After definition-point literal resolution it passes;
the test rebinds the class attribute from `1` to `2` after the method and
proves the instance default remains `1` while the final class attribute is
`2`. The full callable/class regression file passes (`9 passed in 2.74s`).

## Update: native extension literal import is absent from the AOT closure

The rebuilt host L4 artifact moved past the inspect shim and fails inside the
pcc-native `_multiarray_umath` initializer with `PCC-PYEXT-IMPORT-001` for
`numpy._core._exceptions`. The module exists as Python source but no Python
module imports it; NumPy's C source uses `PyImport_ImportModule`, and the
compiled pcc-native extension contains the literal module name. The current
closure scanner only follows Python source imports.

## No.16 Discover resolvable literal module dependencies in native extensions

### Planned substitution

Add a package-neutral fake pcc-native extension containing a dotted ASCII
module literal and a matching package-site Python module. Extract identifier
candidates from extensions actually referenced by the Python source graph,
then add only candidates that resolve to real Python modules under configured
package roots. Recursively close package-local imports from those additions;
discard binary noise that has no source provider. Do not special-case NumPy or
the `_exceptions` spelling.

### CONFIRMED

The fake-extension closure regression failed before the scanner change and
passes afterward (`1 passed in 0.33s`). The real pinned closure expands from
100 to 104 strict modules by adding four source-backed candidates found in the
referenced extension; all 104 modules have zero fallback, unresolved-import,
and unsupported-node counts. The rebuilt host artifact moved past
`numpy._core._exceptions` and now fails while executing
`numpy._core.multiarray` with `AttributeError: add_docstring`.

## Update: `METH_FASTCALL` module methods are silently omitted

NumPy's `_multiarray_umath` method table declares `add_docstring` with
`METH_FASTCALL`. The generic pcc-native module constructor walks every
`PyMethodDef`, but its callable factory supports only `METH_VARARGS`,
`METH_VARARGS | METH_KEYWORDS`, `METH_NOARGS`, and `METH_O`; unsupported flags
return null and the constructor silently skips the attribute. Contextual IR
then correctly attempts to publish the extension attribute through
`from _multiarray_umath import *`, but the source module has no attribute to
read.

## No.17 Adapt `METH_FASTCALL` module methods to native tuple calls

### Planned substitution

Add a package-neutral extension with a `METH_FASTCALL` method, star-import it
through a compiled Python bridge, and confirm the attribute is absent. Extend
the generic C-API callable adapter to unpack pcc's call tuple into the
`PyObject *const *args, Py_ssize_t nargs` convention while retaining argument
objects for the duration of the C call. Cover publication and a real call;
leave the separate FASTCALL-with-keywords convention unsupported until a
focused consumer requires it.

### CONFIRMED

The package-neutral method was initially omitted. The tuple-to-vector adapter
now publishes and calls it through a compiled star-import bridge; direct calls
produce `5` and `9`. Splitting the original reproducer exposes a second,
stacked failure: assigning `fast_sum.__module__` still raises
`AttributeError: __module__`.

## Update: native function values have no mutable attribute storage

`PY_TYPE_FUNC` currently exposes only its immutable C name and bound-self
pointer. Generic `py_obj_setattr` rejects all function attributes, although
Python function values have a mutable attribute dictionary and NumPy relies on
rewriting `add_docstring.__module__` during package initialization. This is
independent of FASTCALL publication.

## No.18 Add GC-traced mutable attributes to native function values

### Planned substitution

Keep the now-green FASTCALL publication/call regression and add a separate red
test for assigning and reading `__module__`. Give native function objects a
lazily allocated attribute dictionary, consult it before built-in function
metadata, and route function setattr through it. Add the new owner slot to the
shared GC visit/deallocation contract and mirror the exact layout and behavior
in the pcc-Python runtime before rebuilding the real host artifact.

### CONFIRMED

The separated metadata regression first raised `AttributeError: __module__`
and now passes alongside the FASTCALL call test (`2 passed in 8.45s`). The
attribute dictionary is an owned GC slot in both runtime implementations. The
host artifact rebuilt in 17.82 seconds and advanced through the assignment,
then deterministically crashed on the second real `add_docstring` call.

## Update: C-function wrappers do not expose the declared C ABI prefix

LLDB places the new crash in `arr_add_docstring` while it directly reads
`PyCFunctionObject.m_ml->ml_doc` for `_get_implementing_args`. The first call,
whose object is a NumPy type, returns normally. On the second call pcc reports
the wrapped extension method as `PyCFunction_Type`, but the object at offset
16 contains pcc's native entry pointer rather than the `PyMethodDef *` promised
by the fake-header `PyCFunctionObject` layout. NumPy therefore interprets code
bytes as `ml_doc` and passes an invalid pointer to `strcmp`.

## No.19 Make native C-method wrappers ABI-prefix compatible

### Planned substitution

Extend the package-neutral FASTCALL extension with a method that checks
`PyCFunction_Type`, casts another exported method to `PyCFunctionObject`, and
reads `m_ml->ml_name`; confirm the current wrapper fails. Prefix
`PyFuncObject` with the exact fake-header C-function fields, mark only wrappers
with their real `PyMethodDef` and bound self, and move pcc's private entry,
captures, name, bound-self, and attribute fields after that prefix. Map ordinary
pcc functions to a distinct `PyFunction_Type` token. Update all C and
pcc-Python offsets, GC owner slots, deallocation, and C-function accessors as
one ABI change.

### CONFIRMED

The direct-layout regression first crashed its child with `SIGSEGV` and now
prints the wrapped method's real `ml_name` (`fast_sum`). The FASTCALL and
metadata tests pass together, and five adjacent calling-convention,
reference-balance, and GC-slot tests pass. The host artifact rebuilt in 18.17
seconds, moved past both `add_docstring` calls without a crash, and now raises
`AttributeError: from_dlpack`.

## Update: FASTCALL-with-keywords methods are omitted

`numpy._core.multiarray` imports `from_dlpack` from `_multiarray_umath` and
immediately assigns its `__module__`. Its native method table declares
`METH_FASTCALL | METH_KEYWORDS`; the generic callable factory currently admits
FASTCALL only when `METH_KEYWORDS` is absent, so this method is skipped exactly
as the earlier plain-FASTCALL method was.

## No.20 Adapt FASTCALL-with-keywords vector calls

### Planned substitution

Add a package-neutral method using the four-argument
`self, args-vector, positional-count, keyword-names` convention and prove both
publication and a mixed positional/keyword call. Reuse the native function
keyword-signature bridge to receive pcc's `(args, kwargs)`, construct the
CPython vector layout with positional values followed by keyword values, and
pass an ordered keyword-name tuple. Retain all vector objects across the C
call and release them afterward.

### CONFIRMED

The mixed-call regression first ran the preceding plain FASTCALL calls and
then failed because the keyword-capable method was absent. It now observes one
positional value, one keyword value, and one keyword name (`791`), while the
plain keyword convention remains green (`3 passed in 8.59s`). The rebuilt host
artifact completed in 20.07 seconds and moved past `from_dlpack`; the next
failure is a ufunc `__module__` assignment in `_override___module__`.

## Update: generic C-extension instances do not dispatch attribute slots

NumPy ufuncs have dynamic C-extension type tags. `PyUFunc_Type` provides
`tp_getattro = PyObject_GenericGetAttr`, `tp_setattro =
PyObject_GenericSetAttr`, and a `tp_dictoffset`, but pcc's generic object
dispatch currently handles C-extension callability only. Setattr therefore
falls through before the type slot or instance dictionary is consulted.

## No.21 Dispatch generic C-extension get/set attributes through type slots

### Planned substitution

Extend the package-neutral static custom-type regression with a dict pointer,
`tp_dictoffset`, and generic get/set slots, then assign and read `__module__`
and `__qualname__` from compiled Python. Route dynamic C-extension tags through
their `tp_getattro`/`tp_setattro`, implement non-recursive generic dictionary,
getset, member, and method lookup, and store a newly created instance dict with
the shared write barrier so all collectors see it.

### CONFIRMED

The custom dynamic-type test initially routed its high type tag through the
ordinary user-instance branch and raised `AttributeError: __module__`. After
prioritizing registered C-extension tags, it assigns and reads both metadata
fields (`1 passed in 7.23s`); the old module GenericGet/SetAttr fallback and
FASTCALL regression also remain green. The host artifact rebuilt in 20.16
seconds and advanced into `_string_helpers.py`.

## Update: eager `tuple(map(chr, ...))` misses the native map consumer path

`_string_helpers.py` constructs `_all_chars = tuple(map(chr, range(256)))` at
module scope. pcc already has generic `chr` callable support and eager native
map/filter consumption for `list(map(...))`, but tuple construction does not
reuse that consumer and the map specialization only accepts user functions or
`str`. The unrecognized inner call falls through to a global-name load and
raises `NameError: map`.

## No.22 Reuse native map consumption for tuple and builtin `chr`

### Planned substitution

Add a package-neutral top-level regression for `tuple(map(chr, range(...)))`.
Extend the existing eager map consumer with the native `chr` operation, then
let tuple construction materialize a recognized map/filter call through the
same temporary list before converting it to a tuple. Preserve the existing
generic fallback for unsupported map callables and do not introduce a package
name check.

### CONFIRMED

The focused tuple/map test first raised `NameError: map` and now prints the
four-character tuple. Two existing list map/filter consumers also pass (`3
passed in 33.99s`; each test builds a separate self-backend artifact). The host
artifact rebuilt in 18.58 seconds and advanced to dtype metadata processing.

## Update: generic C-extension member lookup handles only object pointers

`dtype(longdouble_type)` succeeds, but reading `.itemsize` raises in
`_type_aliases.py`. NumPy declares dtype `itemsize` as a read-only
`PyMemberDef` with type `T_PYSSIZET` at the `elsize` offset. The new generic
C-extension lookup walks the member table but currently materializes only
`T_OBJECT` and `T_OBJECT_EX`, so it deliberately falls through for the numeric
member.

## No.23 Materialize primitive `PyMemberDef` fields generically

### Planned substitution

Add a read-only numeric member to the package-neutral custom C type and read it
from compiled Python. Implement the standard integer, floating, character,
string, object, and `T_NONE` member codes by loading the declared C offset and
boxing with existing C-API/runtime constructors. Keep mutation of read-only
members rejected; this slice only expands generic reads required by dtype
metadata.

### CONFIRMED

The package-neutral custom type now exposes a read-only `T_LONG` member and
compiled Python reads the initial value as `0` (`1 passed in 8.04s`). The host
artifact rebuilt in 19.42 seconds, advanced past dtype `itemsize`, and reached
an independent failure in `_type_aliases.py`: a registered native extension
type passed as `issubclass()` argument 1 is rejected as not being a class.

## Update: `issubclass()` accepts only pcc class objects

The semantic runtime validates both `issubclass()` operands by reading pcc's
`PY_TYPE_CLASS` header before doing an MRO walk. Static C-extension
`PyTypeObject` values are instead tracked by the C-API type registry and use
their `tp_base` chain. NumPy's dtype aliases compare registered concrete and
abstract scalar type objects, so both operands are valid classes even though
neither has a pcc class header.

## No.24 Dispatch registered C-extension types through `tp_base`

### Planned substitution

Give the package-neutral custom extension a registered base and derived type,
then call Python's `issubclass(Derived, Base)` and its false inverse. Expose a
registry-safe type-object predicate plus subtype bridge from the C-API shim;
route both the C semantic runtime and pcc-Python mirror through it before
reading pcc object headers. Preserve the existing pcc-class MRO path, and
reject operands that belong to neither representation.

### CONFIRMED

The focused test first failed with the same `TypeError` as NumPy and now
reports the registered `tp_base` relationship correctly (`True`, `False`; `1
passed in 8.87s`). The existing pcc-class dynamic `issubclass` acceptance test
also passes (`1 passed in 0.84s`). The host rebuilt in 26.32 seconds and moved
past the scalar-type classification loop to line 128, where a dynamically
typed list fails to expose `sort`.

## Update: generic list attribute lookup omits `sort`

The failing source builds `sctype_list` dynamically and calls
`sctype_list.sort(key=lambda x: dtype(x).itemsize)`. That key expression is
deliberately outside the frontend's small inline-key whitelist, so the IR
correctly uses generic method lookup. The C-API shim already implements the
same arbitrary-callable `list.sort` behavior for `PyObject_GetAttrString`, but
the semantic runtime's generic `py_obj_getattr` surface exposes only `pop`.

## No.25 Publish generic bound `list.sort` for dynamic calls

### Planned substitution

Add a package-neutral module-scope regression that obtains a list through a
dynamic dictionary/set path and sorts it with a non-inlineable callable key.
Publish the existing no-libpython bound sort method through one generic
builtin-attribute bridge consumed by both the C semantic runtime and the
pcc-Python mirror. Retain the frontend's inline fast paths; this is their
semantics-preserving dynamic slow path.

### CONFIRMED

The reduced module-scope program first raised `AttributeError: sort`. The
generic bound method now sorts through a non-inlineable callable key and the
focused regression passes (`1 passed in 1.10s`); it also passes beside the
existing inline-key path (`2 passed in 1.12s`). A cold C-runtime archive rebuild
took about 40 seconds, while the cached gate returned to about one second. The
updated host moved past `_type_aliases.py` and now fails at
`numerictypes.py:79` because no native `numbers` stdlib provider exists.

## Update: recursive stdlib closure has no `numbers` provider

`numerictypes.py` imports the standard `numbers` module and later calls
`Integral.register`, `Complex.register`, `Real.register`, and
`Number.register`. The resolver already has a generic first-class mechanism
for `pcc/py_stdlib/<name>.py`; unlike the other imported stdlib modules,
`numbers.py` is absent, so the strict closure cannot provide it and runtime
import correctly refuses a host fallback.

## No.26 Add the native-compilable numeric ABC surface

### Planned substitution

Add a package-neutral strict import regression for `numbers`, its standard
class hierarchy, and `register` return contract. Provide the module through the
existing native stdlib registry, with `Number`, `Complex`, `Real`, `Rational`,
and `Integral` in the standard inheritance order and a shared classmethod
registration surface. This slice proves import and registration calls needed
by package initialization; virtual-subclass effects remain a separate semantic
claim unless directly proven.

### CONFIRMED (provider slice)

Before the provider existed, the strict executable raised `No module named
'numbers'`. The new module's hierarchy and direct `register` surfaces compile
and run without libpython (`1 passed in 1.12s`). Adding the provider also makes
the recursive closure include the next previously unreachable consumer,
`numpy._core.numeric`; the full host compile now stops early (10.63 seconds)
because that module has three fallback calls for `sys.byteorder`. Therefore the
provider slice is proven, while full NumPy import remains open.

## Update: native `sys` provider omits `byteorder`

Contextual strict compilation reports exactly three calls in
`numpy._core.numeric`: `py_cpy_ensure_init`, `py_cpy_import("sys")`, and
`py_cpy_getattr("byteorder")`. The source uses the value once to define
`little_endian`. The native stdlib provider already owns target-facing `sys`
constants but does not publish `byteorder`.

## No.27 Publish target byte order from native `sys`

### Planned substitution

Add a generic native-stdlib IR regression proving `import sys;
sys.byteorder` has no CPython calls, plus a direct provider assertion. Publish
the standard string constant through `pcc/py_stdlib/sys.py`; pcc's supported
AArch64 and x86_64 targets are both little-endian. Re-run only the contextual
`numpy._core.numeric` fallback counter before paying for another full host
build.

### CONFIRMED

The provider assertion and package-neutral IR regression pass (`2 passed in
0.48s`). Contextual strict compilation of `numpy._core.numeric` reduced its
CPython fallback count from three calls to zero in 6.9 seconds. The full host
artifact then rebuilt successfully in 21.07 seconds and advanced to
`numerictypes.py:107`, where execution rejects `from builtins import bool,
bytes, complex, float, int, object, str` because the native import-from path
only recognizes `int`.

## Update: native `builtins` import-from handles only `int`

The frontend already owns canonical no-libpython type objects for all seven
names and classifies `builtins` as a native module. However, both recursive
closure filtering and import-from alias registration whitelist only `int`.
Because one unsupported name makes the whole import statement dynamic, the
compiled program attempts to import a runtime `builtins` module that should
never exist on the strict path.

## No.28 Generalize native builtin-type imports

### Planned substitution

Add a package-neutral strict executable that imports all seven type names with
aliases and proves each alias is the canonical builtin type object. Extend the
native import-from whitelist, alias registration, and callable-value lowering
to use the existing canonical type-object mapping. This changes no constructor
semantics; it makes standard builtin publication use the already-native values.

### Code Change

Generalize the recursive-closure whitelist and import-from alias registry from
`int` to the builtin type values already backed by
`py_builtin_type_for_tag`. Canonicalize imported aliases before emitting their
native type object. The strict regression imports the seven NumPy-facing names
under distinct aliases and compares each with its canonical builtin.

### CONFIRMED

The regression first executed the same dynamic module path and failed with
`No module named 'builtins'` (`1 failed in 0.68s`). It now passes cold in
39.13 seconds and cached in 0.69 seconds. Contextual IR confirms that the
import-from calls are gone. The module still reports seven CPython calls, but
full IR attribution proves all seven belong to the later, independent
`sorted(dict.fromkeys(...), key=_scalar_type_key)` plus list-concatenation
expression. A strict host rebuild therefore stops at compile time after 11.60
seconds with `numpy._core.numerictypes` as the only fallback module.

## Update: `sorted(..., key=<named callable>)` lacks the native callable path

Native list `.sort(key=callable)` already handles arbitrary first-class pcc
callables by invoking the key through `py_obj_call`. The non-mutating
`sorted()` sibling only accepts a structural lambda or builtin `len`; a named
function falls through to CPython even though the frontend has already emitted
its native callable object. NumPy passes `_scalar_type_key` over the iterable
returned by `dict.fromkeys`, then concatenates the resulting list.

## No.29 Reuse callable-key sorting for `sorted()`

### Planned substitution

Add a package-neutral module-scope regression that sorts a dictionary iterable
with a named function and concatenates the result with a list. Copy the
iterable into a fresh list, as `sorted()` requires, then reuse the existing
arbitrary-callable insertion-sort path that list `.sort()` already exercises.
Keep structural lambda fast paths unchanged and propagate key-call exceptions.

### Code Change

When structural key analysis cannot inline the callable, materialize its
already-native callable object once and pass the existing `("callable", key)`
specification to the insertion-sort helper. The copy is still populated through
generic iteration, and list concatenation remains in the ordinary pcc binop
path.

### CONFIRMED

The minimized strict compile first failed with `PCC-PY-COMPILE-001` (`1 failed
in 1.34s`). The frontend change passes under the host-cc runtime oracle (`2
passed in 1.19s`) and under the default pcc-Python runtime after its compiler-
staleness rebuild (`2 passed in 44.14s`). Contextual strict compilation of
`numpy._core.numerictypes` reduced its fallback count from seven to zero in
8.00 seconds. The complete host artifact compiled in 29.98 seconds and direct
execution advanced through the sort before failing at line 600 because the
builtin `memoryview` name has no value-position lowering.

## Update: `memoryview` exists only in call position

The frontend lowers `memoryview(obj)` to `py_memoryview_new`, and both runtime
implementations already map `PY_TYPE_MEMORYVIEW` (tag 19) to a canonical
`memoryview` class. Unlike `bytes` and `bytearray`, the name is absent from the
builtin callable/type value tables, so a list containing the class object raises
`NameError` even though constructing an instance is native.

## No.30 Publish the canonical `memoryview` type value

### Planned substitution

Extend the package-neutral builtin-type regression to construct a memoryview
and compare the bare `memoryview` value with `type(view)`. Add tag 19 to the
existing canonical type-object emission and keep import-from support consistent
with the other native builtin type values.

### Code Change

Add `memoryview` to the native builtin type/callable/import tables and map it to
the runtime's existing tag 19 canonical type class. No runtime object layout or
constructor changed.

### CONFIRMED

The focused test first constructed the instance successfully and then failed on
the bare value with the same `NameError` (`1 failed in 0.91s`). It passes under
the host-cc runtime oracle (`1 passed in 1.88s`) and, after the complete host
build refreshed the default pcc-Python runtime archive, under default mode (`1
passed in 0.63s`). The complete host artifact advanced to
`numerictypes.py:627`, where a name dynamically installed through `globals()`
is not visible to an ordinary global-name load.

## Update: `globals()[key]` writes are invisible to bare global-name loads

`globals()` correctly exposes the shared module-attribute dictionary, and the
loop stores all names from `allTypes` into that dictionary. The generated
`_register_types` function nevertheless compiles each unresolved bare name
(`integer`, `inexact`, `floating`, `number`) into an unconditional `NameError`.
Static module globals are read from LLVM globals; the final unresolved-name
path never consults the dynamic module namespace.

## No.31 Read unresolved global names from the module namespace

### Planned substitution

Add a package-neutral regression that stores a value through
`globals()["dynamic_value"]` and reads it both at module scope and inside a
function. Before raising `NameError` for an otherwise unresolved name, query
the current module's shared attribute dictionary. Preserve `NameError` when the
key is absent and return the module-owned value without changing static-global
fast paths.

### Code Change

The final unresolved-name branch now queries `py_module_attr_get` for the
current module, releases only the lookup ownership on a hit, and otherwise
constructs `NameError` with the ordinary try/error target. Dynamic names use a
safe pooled string global: the first full-package attempt exposed compiler
sentinel `Name("**")`, whose raw spelling cannot be embedded in an LLVM symbol.

### CONFIRMED (host-cc runtime oracle)

The package-neutral program first failed at its module-scope bare read (`1
failed in 0.73s`). It now proves both module/function hits and a catchable miss;
the complete globals file passes under the host-cc runtime oracle (`7 passed in
4.01s`). Contextual NumPy IR contains module-attribute lookups for all four
dynamic names and zero CPython fallback calls. The first complete build denied
the raw-symbol version with `unsupported global symbol syntax ...
@.pyattr.**`; safe pooling then passed the full self emitter in the C-runtime
oracle build (22.52 seconds). That artifact advanced through `_register_types`
to an independent missing `contextlib.nullcontext` export. Default pcc-Python
runtime confirmation remains deferred until the current frontend slices are
batched into one archive refresh.

## Update: native `contextlib` provider omits `nullcontext`

`numpy._core._methods` imports `nullcontext` from the compiled native stdlib
module. Its IR imports `contextlib` through `py_compiled_module_import_by_name`
and performs an ordinary attribute lookup, so the observed `AttributeError` is
not a fallback or resolver failure. The provider implements `suppress`,
`ExitStack`, and a `contextmanager` boundary, but publishes no `nullcontext`.

## No.32 Add the standard no-op context manager

### Planned substitution

Add a strict package-neutral regression for `from contextlib import
nullcontext`, its optional enter-result identity, default `None`, and false
exception suppression. Implement the ordinary provider class with
`__enter__`/`__exit__`; no package-specific import logic is required.

## Update: `PyVectorcall_Call` recurses through its own `tp_call`

NumPy's ufunc and array-function dispatcher types set
`tp_call = PyVectorcall_Call`, store a `vectorcallfunc` in each instance, and
publish its offset through `tp_vectorcall_offset`. The shim implemented
`PyVectorcall_Call` as `PyObject_Call`, which re-entered the same `tp_call` and
overflowed the native stack.

## No.33 Dispatch the C-extension instance vectorcall slot

### Planned substitution

Use a reduced C-extension type with NumPy's exact vectorcall shape. Exercise
both positional and keyword calls under strict self/no-libpython mode. Read the
validated instance slot directly and convert the tuple/dict `tp_call` ABI into
vectorcall's positional-values, keyword-values, and `kwnames` layout.

### Code Change

The shim now validates `Py_TPFLAGS_HAVE_VECTORCALL`,
`tp_vectorcall_offset`, and `tp_basicsize`, loads the per-instance function,
and invokes it directly. `PyVectorcall_Call` builds the vector argument layout
without borrowing beyond the live tuple/dict; `PyObject_Vectorcall` also uses
the direct slot when present. Non-vectorcall callables retain the existing
`PyObject_Call` fallback.

### CONFIRMED

The reduced type first compiled successfully and exited with signal 11 in the
same recursive call path as NumPy. It now passes positional and keyword calls
(`1 passed in 9.98s`), and the existing PyCFunction fallback plus the new type
test pass together (`2 passed in 2.15s`). The complete strict host artifact
compiled successfully and advanced in 0.05 seconds to NumPy's sanity-check
subtraction; the former vectorcall stack overflow is gone.

## Update: NumPy scalar subtraction does not reach `nb_subtract`

LLDB at `py_obj_sub` proves both operands of
`x.dot(x) - float32(2.0)` carry the same dynamic C-extension type tag
`0x1001d`. The generic object dispatcher treats every tag above
`PY_TYPE_USER` as a pcc-authored instance and looks for Python `__sub__`, but
C-extension numeric semantics live in `tp_as_number->nb_subtract`.

## No.34 Bridge C-extension binary number slots

### Planned substitution

Create a minimal package-neutral C-extension type with an `nb_subtract` slot
and prove `a - b` under strict self/no-libpython mode. Add a C-API-shim bridge
that follows the binary numeric-slot protocol: call the left slot, honor
`NotImplemented`, then try a distinct right/reflected slot when appropriate.
Route only dynamic C-extension tags through that bridge, while keeping ordinary
pcc instance dunder dispatch unchanged. Mirror the dispatch call in the
pcc-Python runtime port.

### Code Change

`pcc_capi_cext_subtract` now reads the registered types' number tables, applies
subtype/reflected-slot ordering, consumes `NotImplemented`, and preserves
slot-raised exceptions. Both `py_obj_sub` implementations branch to it only for
C-extension tags; `PyNumber_Subtract` uses the same bridge.

### CONFIRMED

The reduced extension first failed with the same unsupported-operands
`TypeError` (`1 failed in 1.66s`). It now returns the slot-computed result under
strict self/no-libpython mode (`1 passed in 12.35s`); paired with the vectorcall
regression it passes from cache in 1.95 seconds. The complete artifact advanced
past subtraction and now fails at the following `abs(...)` operation, proving
these are separate protocol boundaries.

## Update: C-extension unary absolute misses `nb_absolute`

After `nb_subtract` returns NumPy's scalar result, `py_obj_abs` again classifies
the dynamic C-extension tag as a pcc-authored instance and tries Python
`__abs__`. The extension ABI publishes this operation through
`tp_as_number->nb_absolute`.

## No.35 Bridge the C-extension absolute slot

### Planned substitution

Extend the reduced numeric type with `nb_absolute` and prove `abs(value)` under
strict self/no-libpython mode. Add a package-neutral unary-slot shim with
`NotImplemented` and NULL-without-exception handling, route C-extension tags to
it from both `py_obj_abs` runtimes, and reuse it from `PyNumber_Absolute`.

### Code Change

`pcc_capi_cext_absolute` now calls `nb_absolute`, rejects `NotImplemented`, and
preserves slot exceptions. The C and pcc-Python `py_obj_abs` implementations
route only C-extension tags through it; `PyNumber_Absolute` shares the bridge.

### CONFIRMED

The extended numeric fixture first passed subtraction but failed on
`abs(negative)` with the same `TypeError` (`1 failed in 1.50s`). It now proves
both operations under strict self/no-libpython mode (`1 passed in 16.31s`).

## Update: rich comparison and its C-extension truth result are disconnected

The following sanity-check operation is NumPy scalar `<` pcc float. The generic
three-way comparator does not call C-extension `tp_richcompare`; its C runtime
falls back to pointer order while its pcc-Python mirror falls back to equality.
NumPy scalar rich comparison returns `numpy.bool_`, whose truth value is in
`tp_as_number->nb_bool`; `py_obj_truthy` likewise treats the extension as an
ordinary pcc instance and misses that slot.

## No.36 Bridge C-extension rich comparison and `nb_bool`

### Planned substitution

Extend the package-neutral numeric fixture with `tp_richcompare` returning an
instance of the same type and with `nb_bool` defining its truth value. Prove
value-based `<`, false zero, and true nonzero behavior. Add a generic
richcompare bridge with reflected-op/subtype ordering and `NotImplemented`
handling, plus a C-extension `nb_bool` truth bridge. Route the C and pcc-Python
ordering functions through these only when either operand has a C-extension
tag.

### Code Change

The richcompare shim now applies subtype/reflected-op ordering, consumes
`NotImplemented`, and truth-tests the returned object. C-extension truth uses
`nb_bool` with error validation. Both runtime implementations route ordering
and truth through these bridges only for registered extension tags, and compare
lowering now checks pending exceptions after runtime calls.

### CONFIRMED

Before the change, the reduced fixture completed but printed the semantically
wrong `True True False` for zero truth, nonzero truth, and `9 < 10`
(`1 failed in 1.92s`). It now prints `False True True` and passes in strict
self/no-libpython mode (`1 passed in 12.05s`). Six adjacent vectorcall, unary
dunder, and bignum-abs regressions pass. The combined complete build took
27.19 seconds and NumPy's full `_sanity_check()` passed.

## Update: `linspace` needs the C-extension true-division slot

The complete artifact next fails in `_mac_os_check` while executing
`linspace`. LLDB at `py_obj_truediv` shows a C-extension scalar tag `0x1001e`
divided by tagged int 4. The source performs `step = delta / div`, followed by
`y *= step` and `y += start`; `polyval` later performs `y = y * x + pv`.
These are the same binary number-slot protocol, not independent package
special cases.

## No.37 Parameterize the required C-extension binary number slots

### Planned substitution

Extend the reduced numeric type with `nb_add`, `nb_multiply`, and
`nb_true_divide`; prove forward and reflected operations against extension and
builtin-int operands. Refactor the subtraction bridge into a package-neutral
operation-selected dispatcher preserving the existing subtype,
`NotImplemented`, and exception rules. Route dynamic add/multiply/true-divide
through it in both runtimes and reuse it from the matching `PyNumber_*` APIs.

### Code Change

`pcc_capi_cext_binary_number` selects add/subtract/multiply/true-divide slots
while sharing one subtype/reflection and exception implementation. The former
subtract symbol remains as a compatibility wrapper. C and pcc-Python dynamic
operators plus the corresponding `PyNumber_*` APIs use the shared bridge only
when an operand is a registered C-extension value.

### CONFIRMED

The expanded fixture first stopped at its first addition with the expected
unsupported-operands error (`1 failed in 1.84s`). Forward/reflected add,
multiply, and true-divide now pass together with all earlier numeric protocol
checks (`1 passed in 15.59s`). The full artifact passed `linspace` and entered
`polyval`; it then exposed a tuple returned by the `empty_like` dispatcher.

## Update: a semantic decorator factory is incorrectly discarded as metadata

`numpy._core.multiarray.empty_like` is intentionally a dispatcher function
whose body returns `(prototype,)`. Its decorator expression is produced by a
module-global `functools.partial` and must replace the name with a callable
wrapping `_multiarray_umath.empty_like`. Disassembly proves compiled
`zeros_like` calls the undecorated dispatcher directly; LLDB proves its result
has tuple tag 7. The previously fixed C-extension `dtype` getter is not the
owner of this failure.

## No.38 Apply call-shaped module-global partial decorators semantically

### Planned substitution

Add a package-neutral strict self/no-libpython regression where a module-global
`functools.partial` creates `@factory(implementation)` and the decorator
returns the implementation instead of the dispatcher. Classify module-global
partial-produced factories as runtime decorators rather than imported metadata,
evaluate the factory expression, apply its returned decorator to the native
function object, and invoke the replacement callable. Preserve explicit
metadata/no-op decorators and the existing same-module bare-decorator path.

## Update: host-current-source `import numpy` now prints the version end-to-end (2026-07-16)

At HEAD `646310a5` the committed source advances the host-current-source
frontier well past No.38 (the `empty_like` tuple in `numpy._core.multiarray`).
Rebuilding the real L4 program `build/head-truth/numpy-l4/main.py`
(`import numpy as np; print(np.__version__)`) with `--backend self
--python-libpython=off --ir-scaffold=on` reproduced the current first blocker:

```text
AttributeError: _umath_linalg      # numpy/linalg/_linalg.py:81 from numpy.linalg import _umath_linalg
```

`import numpy` now initializes the entire `numpy._core` chain and eagerly
imports `numpy.linalg`, which requires a SECOND pcc-native C-extension. The
minimal L4 site (`build/head-truth/numpy-l4/site`) carried only
`_multiarray_umath`. The complete pcc-native site produced by the M2 head-gate
build (`build/head-truth/numpy-core/site`) already contains BOTH
`_multiarray_umath` and `_umath_linalg` as genuine pcc-native artifacts
(`_PyInit__umath_linalg` exported; otool shows Accelerate/libSystem only, no
libpython). Rebuilding against that complete site:

```bash
PCC_PACKAGE_SITE="build/head-truth/numpy-core/site:projects/numpy-2.4.4/build/pcc-package/meson-build:projects/numpy-2.4.4" \
  env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  build/head-truth/numpy-l4/main.py -o build/head-truth/numpy-l4/host-app-coresite
./build/head-truth/numpy-l4/host-app-coresite      # -> 2.4.4 (exit 0)
```

The generic explicit import `from numpy.linalg import _umath_linalg` is
satisfied by the generic native-extension resolver
(`pipeline._resolve_pcc_native_extension_path`), no package-name dispatch. The
binary links only libSystem, bakes in only the two pcc-native `.so` paths, forks
no child process, and still prints `2.4.4` with `PCC_HOST_PYTHON=/usr/bin/false`
and all package/python paths cleared.

Claim boundary: this is HOST-current-source pcc, NOT pcc1. The full host-source
`import numpy` blocker is now empty. The remaining L4 exit gap is the pcc1
execution, which needs a from-cold self-backend bootstrap (HEAD 646310a5 has no
fresh pcc1). Evidence: `docs/goal/evidence/2026-07-16-m2-numpy-l4-frontier.md`.
