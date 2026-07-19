# Investigation: replay one Meson extension target through the pcc-native package executor

## Status

resolved 2026-07-14

## Problem Description

`M2-NUMPY-PCC-NATIVE-ARTIFACT` requires the package pipeline, rather than the
standalone NumPy HEAD gate, to produce a pcc-native `_multiarray_umath` artifact.
The generic `pcc.package build-exec --from-compile-commands` path currently has
no target boundary: it compiles every build-relevant command and links every
object into one output. Its pcc-native header redirect also applies only to C,
so C++ commands retain CPython include directories, and the generic link always
uses the C driver.

The Meson install path is not an alternative yet: pcc1 runs the original Ninja
graph, which emits CPython-tagged artifacts. Correct rejection of those foreign
artifacts is a negative gate, not production of a pcc-native artifact.

## Repro

Current dry package report on pinned NumPy 2.4.4:

```text
execute_build_actions(
    "probe", "projects/numpy-2.4.4",
    execute=False,
    from_compile_commands=True,
    abi_mode="pcc-native",
    link_output="build/pcc-package/probe.so",
)

compile_actions = 177
cxx_actions = 27
link_inputs = 177
cxx_keeps_cpython_include = true
linker = /usr/bin/cc
```

The real `_multiarray_umath` closure proven by `numpy-core-head` is 136 objects,
not 177. Linking all 177 crosses extension boundaries and is not a valid module
build.

## Test [CONFIRMED red boundary]

Add a synthetic Meson/Ninja graph with one shared module, one recursive static
archive dependency, and one unrelated object. The package executor must:

1. select only the shared module's recursive object closure;
2. rewrite every selected output into a fresh gate-owned object root;
3. redirect CPython includes for both C and C++;
4. preserve the target's non-object link flags and choose a C++ linker when the
   closure contains C++;
5. exclude unrelated objects and the old archive paths from the direct-object
   link; and
6. emit the caller-supplied pcc-native output name.

The real dry-plan regression must report 136 compile actions/link inputs for the
pinned NumPy target with no CPython include in C or C++ commands.

## Analysis

The durable input already exists: Meson's `build.ninja` records the target edge,
recursive archive edges, and target-specific `LINK_ARGS`; `compile_commands.json`
records the exact compiler flags for each object. The package executor needs to
join those two generic graphs. No package-name knowledge is required.

The target selector must be explicit (`--meson-target <Ninja output>`). Automatic
selection is unsafe for packages such as NumPy that build many extensions. The
output path is also explicit and must carry the pcc-native suffix. This keeps
the mechanism package-neutral and makes ambiguity a caller-visible error.

## Proposals

- No.1 Generic Meson target replay in `pcc.package build-exec` [CONFIRMED]

## No.1 Generic Meson target replay in `pcc.package build-exec`

### Code Change

Extend `execute_build_actions` and its CLI with `meson_target`. When present with
`from_compile_commands`, parse the Ninja graph, recursively collect `.o` leaves,
filter the compile database to that exact closure, and rewrite outputs under
`build/pcc-package/pcc-native-target/objects`. Apply the existing curated PCC
C-API redirect to C and C++, replay Meson's current AppleClang assertion-flag
migration, and link the fresh objects with the target's non-archive `LINK_ARGS`
using the closure's C++ driver when required.

### Claim boundary

Confirmation requires the synthetic graph regression, the real 136-action dry
plan, a real package-executor compile/link of the pinned target, linkage and
pcc-native tag inspection, strict self/no-libpython loader acceptance through
PEP 489, and the existing CPython-artifact rejection negative gate. It does not
claim pcc1 automatic `pip install numpy`, NumPy L4, or array L5 behavior.

### Result [CONFIRMED]

The generic executor now selects the exact 136-object recursive target closure,
joins rewritten compile databases by output or stable source/target identity,
redirects both C and C++ away from CPython headers, writes fresh objects, drops
old recursive archive paths, and links with the closure's C++ driver. There is
no NumPy-name branch in package/compiler code.

The repeatable gate is:

```text
gtimeout 180s env -u LC_ALL uv run python \
  scripts/numpy_package_artifact_gate.py \
  --jobs 8 \
  --output build/head-truth/numpy-package/result.json
```

It returned PASS in 15.112 seconds: 136/136 fresh compile actions, 136 link
inputs, no retained CPython include, no libpython edge, pcc-native suffix,
`PyInit__multiarray_umath` exported, and strict self/off loader entry through
PEP 489 `Py_mod_exec`. The first subsequent boundary is the already classified
missing native `math` module. Eight-worker replay reduced the measured package
gate from about 65 seconds serial to 15.112 seconds without broadening the
target. Evidence is recorded in
`docs/goal/evidence/2026-07-14-m2-numpy-pcc-native-artifact.md`.
