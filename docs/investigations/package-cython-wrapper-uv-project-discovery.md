# Package Cython wrapper accidentally builds the source project

## Failure boundary

The default-environment NumPy gate used owned acquisition, then entered pcc's
bounded `build_exec --jobs 2` path. During Meson setup it nevertheless spawned
an unrelated full Meson/Ninja build with roughly forty compiler processes.

## Root cause

The generated Cython wrapper ran `uv run --with Cython ...` from the acquired
NumPy source directory. `uv run` discovered that directory's `pyproject.toml`,
built NumPy editable, and only then invoked Cython. That work was redundant
with pcc's following native target replay and did not honor the pcc package job
limit.

## Repair and claim boundary

The wrapper now uses `uv run --no-project --with Cython ...`, so uv provisions
only the declared tool and cannot build the package being compiled. A focused
test asserts the wrapper contract. This removes the accidental pre-build; the
later pcc-native Meson target replay remains bounded independently.
