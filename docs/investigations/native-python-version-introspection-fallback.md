# Investigation: native sys and platform version queries use CPython fallback

## Status
active — focused native execution passes; fresh pcc1/bootstrap pending

## Problem Description
After fixing shared target provider discovery, the new Python 3.15 canary
still raised `no-libpython function unavailable: semantic_target.describe`.
Version labels alone had not established a usable native introspection path.
This is a second failure, distinct from the missing target-module import.

## Repro
The exact function-bearing version canary compiles and links under host pcc,
self backend, no libpython and C runtime, then fails at execution.
`build/correctness-20260906-a/python-target-host-v2.stdout` and its JSONL report
retain the first failure (4.50s).

## Test [CONFIRMED]
An uncached, temporary in-process capture retained the full pre-stub module:
`build/correctness-20260906-a/semantic-target-before-stub.ll` (59,306 bytes).
`user_semantic_target_describe` begins at line 1843. Its sys.version query
reaches py_cpy_import/py_cpy_getattr at line 1918; platform.python_version()
reaches py_cpy_import/py_cpy_call_noargs at line 1958. The first cached capture
did not produce an artifact and was not evidence. No source instrumentation
was retained.

## Proposals
- No.1 Give both observed version queries explicit native owners [CONFIRMED]

## No.1 Give both observed version queries explicit native owners
### Code Change
Native sys.version attribute lowering emits the shared target string, just
as version_info tuple/fields and compile-time guards use the shared target.
Platform's existing pcc-Python version functions now enter the required
compiled-provider route, alongside the existing subprocess provider. The
provider-closure fix admits the common literal module in both shallow and
recursive configurations.

### CONFIRMED
The exact canary passed in 4.93s; the final three-case execution packet passed
in 8.57s. It executes a def, checks version-condition selection, version_info
tuple/fields, sys.version, sysconfig VERSION and platform version/tuple. It
also sets package selection to 3.11 while requiring runtime introspection to
remain 3.15. Receipt and complete pytest report:
`build/correctness-20260906-a/python-target-native-packet-v1.*`.
Host/static contracts pass as well. These are host-pcc-to-native gates, not a
fresh-pcc1 compiler proof; the latter and the bootstrap chain remain open.
