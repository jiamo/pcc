# Investigation: Windows-GNU is classified as a Linux self-backend target

## Status
active — focused host classifier/dispatch gates pass; fresh pcc1 qualification pending

## Problem Description
Issue [#192](https://github.com/allstoalls/pcc/issues/192) records that
`x86_64-pc-windows-gnu` is reported as supported by the Linux emitter despite
there being no registered Windows backend. The matcher treated any `gnu`
substring as evidence of Linux. The Darwin matcher similarly searched vendor
and OS names anywhere in a triple rather than checking their components.

The routed [Linux harness investigation](linux-x86-64-docker-harness-rot.md)
was read end to end. Its existing x86_64/amd64 Linux execution corpus must
remain supported; its historical Docker gates are not Windows or Linux Python
self-host evidence. This is a separate classification defect.

## Repro
The public classifier returned `status=SUPPORTED` and
`target_identity=self-x86_64-linux-v0` for `x86_64-pc-windows-gnu`. No emitter
or runtime execution is needed to observe the error.

## Test [CONFIRMED]
Before the fix:

```sh
gtimeout 30s env -u LC_ALL uv run pytest tests/c/test_self_backend_platform_verdict.py -q -x -n0 --tb=short -k classifier_rejects
```

The first Windows-GNU case failed with `SUPPORTED != UNSUPPORTED`.

## Proposals
- No.1 Match explicit architecture/vendor/OS components [CONFIRMED — host boundary]

## No.1 Match explicit architecture/vendor/OS components
### Code Change
The shared matcher module parses nonempty target components and validates
numeric OS version suffixes. Linux selection uses its explicit OS component,
with unambiguous compact Linux/GNU and Linux/musl aliases retained. GNU alone
never establishes the OS. Darwin requires the exact Apple vendor component
and a Darwin/macOS-X OS component. No emitter or runtime implementation changes.

### CONFIRMED
The performance lock was inactive and a nonblocking flock probe succeeded
before the backend edit. Focused host validation:

```sh
gtimeout 30s env -u LC_ALL uv run pytest tests/c/test_self_backend_platform_verdict.py tests/c/test_self_backend.py::test_self_backend_dispatch_resolves_aarch64_darwin_target tests/c/test_self_backend.py::test_self_backend_dispatch_resolves_x86_64_linux_target tests/c/test_self_backend.py::test_self_backend_target_registry_lists_current_target_identities tests/c/test_self_backend.py::test_self_backend_target_matchers_cover_current_aliases -q -x -n0 --tb=short
```

Result: **44 passed in 0.18s**. Controls cover Windows GNU/MSVC, MinGW,
unrelated GNU systems, misleading vendor/environment substrings, malformed
components, supported architecture aliases, GNU/musl Linux and Darwin versions.
A retained-object regression proves Windows rejection occurs before assembly
emission or object publication. The existing Linux and Darwin dispatch tests
still emit their expected assembly. `git diff --check` is clean.

## Remaining gate
This is host-Python classifier/dispatch evidence. No native compiler, Docker,
bootstrap chain or full suite was run. The release owner will run a fresh
current-pcc1 gate after overlapping source changes are stable before closing
the qualification boundary.
