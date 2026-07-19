# M2-NUMPY-L4 frontier advance (2026-07-16)

Mode labels for every claim below: host-current-source pcc (NOT pcc1),
`--backend self`, `--python-libpython=off`, `--ir-scaffold=on`, pcc-native ABI
extension, no libpython / no LLVM link edge. Host pcc != pcc1; a host-source
frontier advance is not a pcc1 fixed-point proof.

## Continuity

Continues investigation `docs/investigations/m2-numpy-l4-import.md` (proposals
No.1..No.38). The real L4 program is `build/head-truth/numpy-l4/main.py`:

```python
import numpy as np

print(np.__version__)
```

Predecessors DONE_STRONG: M2-NUMPY-HEAD-GATE, M2-NUMPY-PCC-NATIVE-ARTIFACT,
M2-NUMPY-FIRST-BLOCKER-RATCHET, M2-NUMPY-MODULE-GRAPH.

## Reproduce command (host L4 artifact rebuild)

The Python-closure rebuild reuses the already-built pcc-native
`_multiarray_umath` extension (136/137 objects) at
`build/head-truth/numpy-l4/site/numpy/_core/`; it does not recompile the
NumPy C/C++ objects. Package-site precedence mirrors the loader gate:

```bash
PCC_PACKAGE_SITE="build/head-truth/numpy-l4/site:projects/numpy-2.4.4/build/pcc-package/meson-build:projects/numpy-2.4.4" \
  gtimeout 300s env -u LC_ALL uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on build/head-truth/numpy-l4/main.py -o build/head-truth/numpy-l4/host-app-fresh
./build/head-truth/numpy-l4/host-app-fresh
```

## Before -> after first blocker (host-current-source mode)

HEAD at this session: `646310a5`. Working tree clean (the previously-modified
frontend/runtime files were committed in `646310a5`; that commit's source
already advances the host-source frontier well past investigation proposal
No.38, the `empty_like` tuple boundary in `numpy._core.multiarray`).

Reproduced first blocker, step 1 (using the minimal L4 site that only carries
the `_multiarray_umath` pcc-native extension,
`PCC_PACKAGE_SITE=build/head-truth/numpy-l4/site:...`):

```text
AttributeError: _umath_linalg
```

Source location: `numpy/linalg/_linalg.py:81` executes
`from numpy.linalg import _umath_linalg`. `import numpy` now initializes the
entire `numpy._core` chain (multiarray, umath, numeric, numerictypes, ...) and
eagerly imports `numpy.linalg`, which requires a SECOND pcc-native C-extension,
`_umath_linalg`. The minimal L4 site did not contain that extension, so the
generic native-extension resolver could not satisfy the explicit Python import.

The complete pcc-native site produced by the M2 head-gate build,
`build/head-truth/numpy-core/site/`, already contains BOTH pcc-native
extensions (no NumPy-name special-casing; both built through the generic
meson package build):

```text
build/head-truth/numpy-core/site/numpy/_core/_multiarray_umath.pcc3-pcc_native-macosx_14_0_arm64.so
build/head-truth/numpy-core/site/numpy/linalg/_umath_linalg.pcc3-pcc_native-macosx_14_0_arm64.so
```

Rebuilding the same `main.py` against the complete pcc-native site advanced the
frontier from the `_umath_linalg` blocker to full success:

```bash
PCC_PACKAGE_SITE="build/head-truth/numpy-core/site:projects/numpy-2.4.4/build/pcc-package/meson-build:projects/numpy-2.4.4" \
  gtimeout 300s env -u LC_ALL uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on build/head-truth/numpy-l4/main.py -o build/head-truth/numpy-l4/host-app-coresite
./build/head-truth/numpy-l4/host-app-coresite
# -> prints: 2.4.4   (exit 0)
```

The `from numpy.linalg import _umath_linalg` statement is an explicit Python
source import that the generic native-extension resolver
(`pcc/py_frontend/pipeline.py::_resolve_pcc_native_extension_path`, documented
as generic package-site logic, not NumPy logic) satisfies once the site carries
the pcc-native artifact. This is the generic import/module-graph mechanism, not
a package special case.

## No-libpython / no-host-edge verification (host artifact)

Artifact and extension link sets (otool -L):

```text
host-app-coresite:              /usr/lib/libSystem.B.dylib
_multiarray_umath (pcc-native): Accelerate, libSystem, libc++    (no libpython, no LLVM)
_umath_linalg     (pcc-native): Accelerate, libSystem           (no libpython, no LLVM)
```

Runtime process-edge proof:

- `ps` descendant-process check while the binary runs: it forks NO child
  processes (no host python/pcc edge).
- Re-run with `PCC_HOST_PYTHON=/usr/bin/false PYTHONPATH= PCC_PACKAGE_SITE=`
  still prints `2.4.4`, exit 0. The extension paths are baked in at compile
  time, so the binary is self-contained and never shells out to host Python.

## Claim boundary (host-source vs pcc1)

This PROVES: under **host-current-source pcc** (NOT pcc1), `--backend self`,
`--python-libpython=off`, `--ir-scaffold=on`, the real program
`import numpy as np; print(np.__version__)` runs end-to-end and prints `2.4.4`
with no libpython/LLVM link edge and no host Python/pcc process edge, using the
generic pcc-native package site.

This does NOT prove the L4 exit criterion, which requires the **pcc1**
(self-hosted native compiler) to compile the program. Per claim hygiene,
host pcc != pcc1. The remaining open boundary is the pcc1 execution below.

Baked-in extension paths (from `strings` on the binary) are exclusively the
pcc-native artifacts, never a cpython-abi `.so`:

```text
.../build/head-truth/numpy-core/site/numpy/_core/_multiarray_umath.pcc3-pcc_native-macosx_14_0_arm64.so
.../build/head-truth/numpy-core/site/numpy/linalg/_umath_linalg.pcc3-pcc_native-macosx_14_0_arm64.so
```

`nm -gU` on the linalg extension exports `_PyInit__umath_linalg`; `otool -L`
shows no libpython. Both extensions are genuine pcc-native artifacts.

## Remaining open boundary (pcc1 — NOT attempted this session)

The sole remaining gap for `M2-NUMPY-L4` DONE_STRONG is the pcc1 execution:
compile `build/head-truth/numpy-l4/main.py` with a freshly bootstrapped pcc1
(`--backend self --python-libpython=off --ir-scaffold=on`,
`PCC_HOST_PYTHON=/usr/bin/false`) against the complete pcc-native site and
confirm the produced binary prints `2.4.4`.

No fresh pcc1 exists for the current source: HEAD `646310a5` was committed today
(2026-07-16 14:33) and changed frontend/runtime; the on-disk pcc1 binaries are
all stale (Jul 10 or earlier). Producing a current pcc1 requires a from-cold
`scripts/bootstrap.sh --backend self --stage 1` (a bootstrap-class run, not the
~16s warm-cache case). This was deliberately NOT started autonomously; it is
the precise next step and is proposed for explicit go-ahead.

Once a current pcc1 exists, the pcc1 numpy compile reuses the already-built
136/137-object `_multiarray_umath` and `_umath_linalg` pcc-native extensions
(no NumPy C/C++ recompile), so only the pcc1 Python-closure compile + run
remain. pcc1 may still expose pcc1-vs-host lowering divergences (see repo
memory on `dict.get` mis-lowering and pcc-Python codegen quirks under pcc1);
any such divergence is a new, separately-recorded boundary.

## Gate / regression pointers

No compiler/runtime source was changed this session (the frontier advance came
from the committed `646310a5` source plus using the complete pcc-native package
site). Relevant existing gates:

- `scripts/numpy_head_gate.py loader ...` (numpy-core loader refresh; first
  blocker already null at frontier 6).
- `tests/integration/test_numpy_l5_package_gate.py` (pip-install + pcc-native
  compile L5 gate, `PCC_RUN_NUMPY_L5_INTEGRATION=1`).
- `tests/test_numpy_first_blocker.py`, `tests/test_numpy_head_gate.py`.

## Leftover-process check

`ps` after the runs: no leftover `pcc`/`pcc1`/`pytest`/`bootstrap` children.

## Update — pcc1 leg CLOSED (2026-07-16, same session)

The remaining pcc1 boundary is now closed with a source-consistent this-session
pcc1 (`build/bootstrap/pcc1`, built via `scripts/bootstrap.sh` this session; the
working tree changed only docs afterwards, so it matches HEAD compiler source —
no from-cold bootstrap was needed). The earlier "no fresh pcc1" note reflected
the test-harness candidate path (`build/bootstrap-pytest-self/pcc1`, stale), not
`build/bootstrap/pcc1`.

Command (pcc1, not host pcc):

```bash
PCC_PACKAGE_SITE="build/head-truth/numpy-core/site:projects/numpy-2.4.4/build/pcc-package/meson-build:projects/numpy-2.4.4" \
  build/bootstrap/pcc1 --backend self --python-libpython=off --ir-scaffold=on \
  build/head-truth/numpy-l4/main.py -o build/head-truth/numpy-l4/pcc1-app
PCC_HOST_PYTHON=/usr/bin/false PYTHONPATH= PCC_PACKAGE_SITE= build/head-truth/numpy-l4/pcc1-app
```

Result: compile exit 0 (14.7 MB binary); isolated run prints `2.4.4`, exit 0.
`otool -L` shows only `libSystem`/Accelerate — no libpython, no LLVM, no
`python3`. The `no-libpython function unavailable: numpy.lib...` strings baked
into the binary are latent diagnostics for functions the import path does not
call; `import numpy` + `__version__` never touches them (they bound future
numpy-runtime tasks, not L4).

Pinned committed gate: `tests/integration/test_numpy_l4_pcc1_gate.py`
(`pytest.mark.integration`, `PCC_RUN_NUMPY_L4_INTEGRATION=1`) reproduces the
compile+run+version+no-libpython assertions. Observed:
`1 passed in 80.01s` with `PCC1_BINARY=build/bootstrap/pcc1`.

Claim: L4 exit criteria are met — `import numpy as np; print(np.__version__)`
prints `2.4.4` through pcc1 / `--backend self` / `--python-libpython=off`, the
artifact links no libpython/host edge, and the L4 first blocker is empty. This
proves NumPy *import + version* only; NumPy array runtime semantics and the
latent no-libpython function gaps remain future work (M2-NUMPY-L5 onward).

