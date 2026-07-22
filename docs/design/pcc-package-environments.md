# First-class pcc package environments

## Status and original problem

The first-class environment-selection contract is implemented for host pcc,
compiled pcc1, default install, and frontend import discovery. The locked uv
sync and cross-profile artifact-manifest slices remain separate task-board
work; this document continues to define their shared boundary.

Before this implementation, pcc had two incompatible implicit destinations:

- the host installer defaults to `~/.cache/pcc/site-packages`;
- the compiled `pcc1 -m pip` installer defaults to
  `/tmp/pcc-site-packages`;
- frontend import discovery searches `PCC_PACKAGE_SITE` and the host default,
  but does not reliably discover the compiled installer's `/tmp` default.

Consequently, an explicit `--target` plus `PCC_PACKAGE_SITE` can prove the
build/import machinery, but it is not an acceptable normal-user workflow.
`PCC_PACKAGE_SITE` remains a useful test and compatibility override; it must
leave the README happy path.

## User contract

The ordinary workflow is environment-relative and needs no package-path
variable:

```bash
uv run pcc1 -m pip install numpy
uv run pcc1 app.py
```

The second command must discover exactly the environment populated by the
first. For a Python input, bare `pcc1` means the self backend, no libpython,
and the strict IR scaffold; LLVM and libpython compatibility are explicit
oracle/compatibility requests. The same environment-selection rule applies to
host `pcc`, installed `pcc1`, `pcc1 -m`, and an emitted application compiled
from that environment.

An inspection command must make all implicit state visible:

```bash
pcc env info
pcc1 env info
```

The machine-readable form reports the selected environment root, why it was
selected, package sites in precedence order, Python semantic target,
pcc-native ABI version, target triple, cache root, and lock provenance.

## Environment selection

One shared, self-hostable resolver owns install and import selection. Host and
compiled CLIs must not maintain separate fallback literals.

Precedence is:

1. `--target` for an explicitly isolated install operation. This does not
   silently activate the target for later commands.
2. An explicitly selected pcc environment.
3. The active `VIRTUAL_ENV`. uv 0.10 sets this for `uv run`; pcc uses a private
   overlay below the environment rather than CPython's `site-packages`.
4. A durable per-user pcc environment below the OS user-data root.

The virtual-environment layout is:

```text
$VIRTUAL_ENV/.pcc/environments/<compatibility-tag>/
  site-packages/
  bin/
  environment.json
  installed.json
```

The user layout is rooted below
`${PCC_DATA_HOME:-${XDG_DATA_HOME:-~/.local/share}/pcc}`. Installed state must
not default to `/tmp` or to a cache directory. Downloaded source artifacts and
reusable build outputs remain content-addressed cache data under
`PCC_PACKAGE_CACHE` or the platform cache root.

`PCC_PACKAGE_SITE` stays as an explicit path-list compatibility override for
CI, forensic reproduction, and externally prepared sites. It is not required
for normal install/run, and diagnostics must label when it changes discovery.

## Compatibility identity

The environment compatibility tag contains only dimensions that can make a
package artifact ABI-incompatible:

- Python semantic major/minor target;
- pcc-native C-API/runtime ABI version;
- target OS, architecture, and object format;
- declared package ABI mode (`pcc-native` versus explicit CPython/libpython
  compatibility).

These execution choices are deliberately **not** environment dimensions:

- GC backend 0 through 4;
- LLVM versus self backend when both emit the same target ABI;
- virtual-thread versus asyncio-compatible scheduling policy;
- the presence of a GPU and the selected GPU device.

Changing one of those execution choices must not require reinstalling NumPy
or another CPU package. A package that includes target-specific GPU kernels
stores them as capability-tagged artifacts in its installation manifest; it
does not fork the whole Python environment. Unsupported capabilities fail with
an explicit diagnostic rather than choosing a different package environment
silently.

## uv ownership boundary

uv owns project resolution, the CPython virtual environment, and installation
of the host `pcc` wheel. The pcc wheel may continue to ship its platform `pcc1`
binary, so `uv run pcc` and `uv run pcc1` select the same uv project without
pretending that pcc-native extensions are CPython wheels.

pcc owns the private `.venv/.pcc` overlay, pcc-native builds, ABI validation,
and no-libpython execution. It never writes pcc-native extensions into
`.venv/lib/python*/site-packages`.

The first uv slice uses `VIRTUAL_ENV` only to select the overlay. The locked
sync slice adds:

```bash
uv lock
uv run pcc sync --locked
uv run pcc1 app.py
```

`pcc sync --locked` consumes the resolved project graph and artifact
provenance from `uv.lock` through a versioned adapter. It does not implement a
second general resolver and does not modify `uv.lock`. It records the lockfile
digest, selected groups/extras/markers, target Python version, each source
artifact digest, and each pcc-native build key. A dependency for which the
lock contains no acceptable source or pcc-native artifact fails closed with a
stable diagnostic.

Sync is transactional and prunes only the private pcc overlay. Re-running it
with an unchanged lock and compiler/package ABI performs no network download
and no native rebuild. Recreating `.venv` may remove the overlay; the next
locked sync reconstructs it from immutable caches.

## Performance and long-running-runtime constraints

Python-like UX cannot come from hiding repeated compilation. Environment
installation therefore publishes from content-addressed acquisition and build
caches, with the build key covering source digest, compiler/package ABI,
semantic Python target, target triple, and build options. Environment changes
link, clone, or copy immutable artifacts transactionally.

Runtime profile selection remains a launch concern. GC choice, scheduler
choice, and accelerator availability are recorded in execution evidence, not
folded into package resolution. This lets the five-GC program compare the same
application artifact, lets a mature virtual-thread runtime replace an asyncio
execution policy without a reinstall, and lets CPU and GPU execution share a
single package graph. It does not by itself claim that full NumPy, virtual
threads, or GPU training/inference are complete; those remain separate gated
tracks.

## Migration and claim boundary

1. Introduce the shared resolver and inspection command while continuing to
   read legacy `PCC_PACKAGE_SITE` sites.
2. Make bare `pcc1 file.py` select self/no-libpython/strict-scaffold and keep
   LLVM or libpython as explicit requests.
3. Change host and pcc1 default installs to the selected environment and add a
   no-variable install-then-run gate.
4. Add uv project-overlay selection without lockfile integration.
5. Add deterministic `uv.lock` projection and cache-only repeat sync.
6. Prove runtime-profile changes reuse the identical installed artifact.
7. Only then replace the explicit target/site README example with the normal
   two-command workflow.

No migration step may relabel CPython wheels as pcc-native, special-case a
package name, weaken import semantics, or claim that uv recognizes pcc1 as a
drop-in CPython interpreter.
