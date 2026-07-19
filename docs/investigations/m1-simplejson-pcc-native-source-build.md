# Investigation: build the pinned simplejson sdist as pcc-native with no host

## Status

resolved

## Problem Description

`M1-PCC-NATIVE-SOURCE-BUILD` requires the pinned real `simplejson` 4.1.1 sdist
to produce a pcc-native extension through pcc1 while `PCC_HOST_PYTHON` and
`PCC_HOST_PCC` are disabled. The existing install path can place archive
payloads and the existing build executor can compile generic C sources, but the
actual source-build boundary and first failure for this sdist have not been
observed as one install transaction.

This investigation follows the generic C-API include work in
`python-no-libpython-numpy-build-pcc-capi-include-redirect.md`. It must not add
package-name dispatch.

## Repro

Run the current strict pcc1 against the pinned local sdist:

```text
PCC_HOST_PYTHON=/usr/bin/false PCC_HOST_PCC=/usr/bin/false \
  build/bootstrap-compat-runner-pcc1/pcc1 -m pip install \
  /tmp/pcc-m1-canary-probe/simplejson-4.1.1.tar.gz \
  --abi pcc-native --target <fresh-site> --cache-dir <fresh-cache>
```

Expected M1 result: a `simplejson/_speedups.pcc-native-*.so` artifact, manifest
with `execution_mode=pcc-native` and `links_libpython=false`, and no host
process. Before Proposal No.1, pcc1 returned success after copying the unpacked
source and reported `build_report.reason=not_source_tree`; no `.so` existed.
After Proposal No.1, the same command enters the real C compile and returns 2.
The first observed compiler error was `unknown type name 'PyUnicodeWriter'`.
After Proposal No.2, that error disappeared and the first boundary moved to
`PyUnicode_DecodeUTF8`, followed by `PyUnicode_Decode`, `PyDoc_STRVAR`, the
`Py_VISIT` rvalue form, `PyDict_Clear`, and `PyType_GetModuleByDef`.

## Test [CONFIRMED]

The pinned pcc1 command deterministically returned success without an artifact
before the source-build wiring and now deterministically reaches a real compile
failure. The synthetic single-C-source PEP 489 sdist passes through the same
pcc1/no-host build, tag, link, install, and manifest path. The Unicode writer
extension passes under strict self/no-libpython and covers UTF-8, substring,
object conversion, error atomicity, non-ASCII reads, and embedded NUL.

## Proposals

- No.1 Connect pcc1 sdist install to generic extension build execution [CONFIRMED]
- No.2 Implement the first missing public C-API surface revealed by the real compile [CONFIRMED]
- No.3 Implement the next coherent public C-API/header batch [CONFIRMED]
- No.4 Close the final three compile-time C-API gaps without faking writable Unicode [CONFIRMED]

## No.1 Connect pcc1 sdist install to generic extension build execution

### Code Change

Pending the deterministic repro. The change must infer build inputs from
package-neutral source metadata and emit the PCC extension tag; it may not
match the selected distribution name.

### CONFIRMED

The install path now extracts sdists to a bounded staging tree, structurally
selects one package-local C source, injects curated PCC C-API/runtime headers,
emits a pcc-native extension suffix, compiles PIC, links with no libpython, and
copies the artifact into the installed package. The package name is absent from
dispatch. Host synthetic gate passed 5/5; rebuilt strict pcc1 synthetic gate
passed 1/1. The real package now fails in compilation rather than being copied
and mislabeled as installed.

## No.2 Implement the first missing public C-API surface revealed by the real compile

### Code Change

Implement the Python 3.14 writer operations used by the unmodified source plus
codepoint-correct `PyUnicode_DATA`/`PyUnicode_READ` access over pcc's UTF-8
storage.

### CONFIRMED

The real pcc1 compile confirmed the static locator. The runtime now implements
create/finish/discard/write-char/write-UTF8/write-str/write-substring with
strict UTF-8 validation and error atomicity. The strict self/no-libpython
writer extension passed 1/1. Re-running the pinned sdist removed every writer
diagnostic and advanced the first compiler boundary to `PyUnicode_DecodeUTF8`.

## No.3 Implement the next coherent public C-API/header batch

### Code Change

Add generic Unicode decode entrypoints, the public docstring declaration macro,
an rvalue-safe `Py_VISIT`, `PyDict_Clear`, and module-associated heap-type lookup
needed by `PyType_GetModuleByDef`. Each semantic function requires a synthetic
behavior regression; header-only fixes require compile/source guards.

### CONFIRMED

The strict self/no-libpython behavior extension passes with score 15: UTF-8
and Latin-1 decode, invalid UTF-8 error state, dictionary clearing, and
module-associated heap-type lookup all execute. `PyDoc_STRVAR` and
`Py_VISIT(Py_TYPE(self))` compile in that same extension. Re-running the pinned
sdist removed this entire diagnostic batch. The remaining real-source boundary
is exactly `Py_IS_FINITE`, `PyObject_CallMethodObjArgs`, and `PyUnicode_New`.

## No.4 Close the final three compile-time C-API gaps without faking writable Unicode

### Code Change

Add the public finite-number macro, implement generic object-name varargs method
dispatch, and support the semantically complete zero-length case of
`PyUnicode_New`. Reject nonzero writable Unicode construction explicitly:
pcc's canonical strings use immutable UTF-8 storage, so returning an object
whose `PyUnicode_DATA` cannot honor CPython's writable 1/2/4-byte contract would
be a semantic lie. Under the advertised 3.14 source branch, simplejson's only
active `PyUnicode_New` call has size zero; its nonzero legacy calls are excluded
in favor of `PyUnicodeWriter`.

### CONFIRMED

The strict self/no-libpython behavior extension passes with score 15 for
finite/nonfinite classification, two-argument object-name method dispatch,
empty Unicode construction, and explicit rejection of unsupported nonempty
writable Unicode. The same pinned pcc1/no-host install then compiled and linked
the unmodified real source successfully. Its installed artifact is
`_speedups.pcc3-pcc_native-macosx_arm64.so`; the name contains neither a
`cpython-*` nor `abi3` claim. The build linkage report records
`execution_mode=pcc-native` and `links_libpython=false`, and `otool -L` shows
only the artifact install-name entry plus `libSystem`.

This resolves source build/link only. Import, PEP 489 module execution, and the
package behavior oracle remain outside this investigation and belong to
`M1-PACKAGE-IMPORT-BEHAVIOR`.
