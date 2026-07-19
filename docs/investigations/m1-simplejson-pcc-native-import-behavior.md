# Investigation: import the real simplejson pcc-native extension and cross behavior

## Status

resolved

## Problem Description

`M1-PACKAGE-IMPORT-BEHAVIOR` requires the installed, pinned `simplejson` 4.1.1
package to load its real pcc-native `_speedups` extension under pcc1,
`backend=self`, and `python-libpython=off`. Source build and no-libpython linkage
are already proven, but PEP 489 module execution and package behavior are a
separate boundary. The target oracle is one accelerated function plus a nested
dictionary/list/string round trip matching the CPython build of the same sdist.

This investigation must preserve the distinction between extension load,
module initialization, package import, and behavior. It may not bypass the
extension with the package's pure-Python fallback and call that success.

## Repro

Compile a small application with the current pcc1 while
`PCC_PACKAGE_SITE` points at the fresh real-source install and both host escape
hatches point at `/usr/bin/false`:

```text
PCC_PACKAGE_SITE=<installed-simplejson-root> \
PCC_HOST_PYTHON=/usr/bin/false PCC_HOST_PCC=/usr/bin/false \
  build/bootstrap-compat-runner-pcc1/pcc1 --backend self \
  --python-libpython=off --ir-scaffold=on <probe.py> -o <probe>
```

The probe must prove that `simplejson.encoder.c_make_encoder` is the native
`simplejson._speedups.make_encoder`, then encode and decode a nested payload.

## Test [CONFIRMED]

The original full-package probe exposed several stacked generic frontend and
runtime gaps before and during extension initialization. Each was minimized
and regressed independently. With those fixes in place, the real installed
package now imports under pcc1/self/no-libpython, proves that its scanner,
decoder, and encoder C bindings are active, and matches the CPython
source-package oracle for deterministic encoding plus a nested container
round trip.

## Proposals

- No.1 Identify and minimize the first real pcc-native import boundary [CONFIRMED]
- No.2 Run the direct extension through the bounded host-runtime oracle [CONFIRMED]
- No.3 Bridge C-API imports to already-compiled pcc modules [CONFIRMED]
- No.4 Preserve cross-module exception and nested function metadata [CONFIRMED]
- No.5 Dispatch dynamic `.decode` by runtime receiver semantics [CONFIRMED]
- No.6 Lock the real positive and negative package boundaries [CONFIRMED]

## No.1 Identify and minimize the first real pcc-native import boundary

### Code Change

None until the pcc1/self/no-libpython probe identifies which boundary fails.

### CONFIRMED

The full package compile fails with stable `PCC-PY-COMPILE-001` diagnostics
before extension discovery. This is one stacked boundary with two generic
frontend mechanisms; it is not evidence about the extension loader. A direct
`_speedups` import root is the minimized loader probe.

## No.2 Run the direct extension through the bounded host-runtime oracle

### Code Change

None. Set `PCC_RUNTIME_CC=cc` only for this localization oracle so a stale
pcc-Python runtime archive and its nested build do not mask the extension
load/module-init boundary. Keep the resulting mode label explicit; it cannot
satisfy the later self-runtime task.

### CONFIRMED

With `PCC_RUNTIME_CC=cc` explicitly labeling the localization oracle, pcc1/self
compiles the direct extension probe. The executable enters `_speedups` PEP 489
module execution and then fails deterministically with
`RuntimeError: module not found: simplejson.raw_json`. Importing and compiling
`simplejson.raw_json` and `simplejson.errors` ahead of `_speedups` does not help:
`PyImport_ImportModule` only calls `py_native_extension_import_by_name`, so it
cannot see pcc's compiled-module attribute registry.

## No.3 Bridge C-API imports to already-compiled pcc modules

### Code Change

Teach the generic C-API import entrypoint to return a module proxy backed by an
existing `py_module_attrs_dict` when a compiled pcc module is registered. The
proxy must share, not copy, the attribute dictionary so later module writes
remain visible; cache and pin the proxy so repeated imports preserve module
identity and GC reachability. Native-extension lookup remains the first path.

### CONFIRMED

The compiled-module proxy path is generic and shares the registered module
attribute dictionary. The real `_speedups` PEP 489 execution can therefore
import `simplejson.raw_json`, finish initialization, and publish callable
objects back into the compiled package modules. The positive integration gate
checks that `scanner.make_scanner is scanner.c_make_scanner` and that the
decoder/encoder C bindings are non-null, preventing a pure-Python fallback
from satisfying the test.

## No.4 Preserve cross-module exception and nested function metadata

### Code Change

Use declared bases when recognizing an externally defined exception subclass,
and search the current structural AST module (including pcc1 wire-compatible
`FuncDef` nodes) when resolving user functions.

### CONFIRMED

Cross-module exception subclasses retain constructor text and `args`; nested
simplejson functions such as `JSONObject`, `__nested__scan_once`, and
`__nested__stringify_key` resolve during fresh pcc1 compilation.

## No.5 Dispatch dynamic `.decode` by runtime receiver semantics

### Code Change

Keep the bytes fast path for statically bytes-like receivers. For `Dyn`, inspect
the runtime type tag: bytes and bytearray use the native decode helper, while
other objects resolve and call their ordinary `decode` method. This preserves
the class-constructor case used by `simplejson.loads` instead of assuming that
every dynamic `.decode` call is a byte decode.

### CONFIRMED

The minimized class-constructor regression passes, and the real package prints:

```text
native True
encoded {"items":[1,"two",null],"ok":true}
roundtrip True
```

## No.6 Lock the real positive and negative package boundaries

### Code Change

Add `tests/python/test_m1_simplejson_import_behavior.py`. The positive test
loads the installed pinned artifact and compares behavior with CPython. The
negative test intentionally compiles only a direct extension import, omitting
its compiled Python dependency closure. `PyImport_ImportModule` now reports the
stable mode-labelled diagnostic:

```text
PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython] module not found: simplejson.raw_json
```

### CONFIRMED

Fresh pcc1 plus the real install passes both tests. The gate explicitly uses
`PCC_RUNTIME_HIGH=c`; it proves the pcc1/self/no-libpython package behavior
boundary with the C semantic runtime, not the later S-P0 no-host-process or
pcc1-to-pcc3 fixed-point boundary.

## Resolution Report

The real pinned `simplejson` 4.1.1 `_speedups` extension now completes module
initialization, exposes its accelerated bindings, and crosses deterministic
function plus nested object/container behavior under pcc1, `backend=self`, and
`python-libpython=off`. The negative reduced closure has a stable mode-labelled
diagnostic. No package-name special case was added: the changes are in generic
compiled-module imports, cross-module metadata, method dispatch, object/string
semantics, and the pcc-native C-API/runtime seams.
