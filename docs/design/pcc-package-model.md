# pcc package model: acquire / build / run contract

Status: ACTIVE design contract (updated 2026-07-21). Governs `pcc -m pip`, the package
executor, and the pcc-native extension ladder (`PKG-P1-NATIVE-EXTENSION-LADDER`).

## Problem

Discussions of "pip support" often conflate three different capabilities,
which makes both scope and claims ambiguous:

```text
A acquire   Download source/wheels and resolve versions and dependencies
            (network + resolver + build isolation).
B build     Turn source/artifacts into pcc-runnable output (compile C
            extensions against pcc's C-API shim for the pcc-native ABI).
C run       Execute the module graph, dlopen extensions, and provide the object
            model in pcc1's no-libpython runtime.
```

## Contract

**pcc owns B and C. A is an explicit, replaceable acquisition backend. The
default `auto` mode selects `owned`; explicit `host` compatibility and strict
`offline` modes are also available.**

Rationale, aligned with the project north star:

1. Build and run remain the execution-ownership path, but the command UX must
   not force users to manually split `pip download` from `pcc install`.
   Therefore `auto` uses pcc1's narrow Simple Repository path and then
   immediately continues into pcc's build/install/run path. This also avoids
   pip invoking a package's PEP 517 backend merely to obtain sdist metadata.
2. `owned` does not embed pip or resolvelib in pcc1. pcc1 parses the Simple
   Repository API, selects ABI-compatible artifacts, verifies repository-
   published SHA-256 digests, and publishes them to a content-addressed
   immutable cache. HTTPS/libcurl is C-level kernel transport; it owns no
   package-selection semantics and requires no libpython.
3. `owned` does not implement dependency backtracking or PEP 517 build
   isolation. Explicit owned mode fails closed on those shapes. In coordinated
   `auto` mode, a verified source artifact may be handed to pcc's supported
   native source builder; that is labeled as build delegation and still fails
   closed when the pcc builder cannot satisfy the source contract.
4. The ecosystem obligation in AGENTS.md requires reusable mechanisms. No
   backend may contain package-name special cases.

Thus **pcc1 owns a narrow but real acquisition path without claiming to have
reimplemented all of pip.**

## Acquisition backend contract

| Mode | Selection/download owner | Host Python/pip | Current boundary |
|---|---|---:|---|
| `auto` (default) | pcc1 Simple API selector + pcc runtime HTTPS transport, then the supported pcc-native source builder | No for acquisition | Best one-command UX; records owned acquisition and any native-build delegation separately |
| `host` | Host pip `download --no-deps`; pcc verifies ABI and moves the artifact into immutable cache | Yes | Makes no owned-acquisition, dependency-resolution, or build-isolation claim |
| `owned` | pcc1 Simple API selector + pcc runtime HTTPS transport | No | Bare names or `name==version` only; SHA-256 required; dependency/resolver/build-isolation shapes fail closed |
| `offline` | Searches only paths, cache, and `--find-links` | No | A miss reports `PCC-PKG-ACQUIRE-OFFLINE` |

Every acquisition report must separately record `acquire_mode_requested`, the
actual `acquire_mode`, `host_assisted`, index, `artifact_origin`/URL, resolved
version, SHA-256, whether the digest was verified, and the transport provider.
This evidence is separate from install/import/runtime claims: a successful
download does not prove that an artifact builds or imports.

## Explicit `pcc -m pip` behavior

| Input shape | Behavior |
|---|---|
| Local source tree, local wheel, or a name resolved by a local `--find-links` directory | Build for `--abi` (default `pcc-native`) and install into the `--target` site |
| `--dry-run` / `--report` | Report the plan without installing |
| Bare name not resolved locally | Execute `--acquire`: default `auto -> owned`, explicit host pip compatibility for `host`, or an explicit failure for `offline` |

`--index-url` and `--extra-index-url` are explicit acquisition-backend inputs.
Online modes use `https://pypi.org/simple` when neither is supplied.
`--no-index` forces offline acquisition and cannot be silently overridden by
another mode.

## ABI boundary

- `pcc-native` (default): build against pcc's narrow `Python.h`/C-API shim
  without linking libpython. `PCC-PKG-004` rejects CPython-ABI artifacts.
- `cpython-compat` / `libpython`: explicit compatibility modes that link
  libpython. They are **separate claims** and cannot substitute for
  pcc-native evidence (§0.10).

## End-to-end ladder

The pieces exist but their evidence has historically been separate: install
can build pcc-native artifacts from local source (`install.py` ->
`build_exec` Meson replay + include redirection); the focused build gate
(`scripts/numpy_package_artifact_gate.py`) uses the same executor; and L4/L5
prove import plus array execution from a prepared site. The missing proof is
one command spanning the whole path:

```text
pcc -m pip install <real-local-source> --abi pcc-native --target <site>
  -> site contains pcc-native extensions and Python modules
  -> pcc1 --backend self --python-libpython=off compiles an import program
  -> run, assert output, and prove with otool that libpython is absent
```

The first rung uses NumPy because it is the nearest industrial target. A
second rung uses a pure-Python package or a small C extension to prove that the
mechanism is generic. Gates follow the L4/L5 form: `pytest.mark.integration`
plus an environment-variable opt-in. Missing tools or sources produce a skip,
never a false success. **`if package == "numpy"` is forbidden.**

## Claim hygiene

`pcc-native import X works` is not the same claim as `local pip install X
works`, `cpython-compat installs X`, or `PyPI is supported`. Evidence for
these four claims remains separate.

## Non-goals

- Do not compile all of pip, an upstream backtracking resolver, or PEP 517
  build isolation into pcc1.
- `owned` does not yet accept extras, markers, range constraints, or artifacts
  that require runtime dependencies/build isolation. These produce stable
  diagnostics, not silent fallback.
- Do not claim that arbitrary PyPI wheels install and run directly.
- Do not add package-name special cases to pass a gate.
