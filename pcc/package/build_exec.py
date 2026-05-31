"""Generic package build action execution for pcc.

This is a package-agnostic execution harness. NumPy can use it as one input
to a future build, but the module deliberately works from generic source
metadata and explicit toolchain paths rather than package-specific rules.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
from pathlib import Path

from .build_plan import build_plan_for_artifact, load_meson_introspection_commands
from .linkage import linkage_report
from .metadata import inspect_artifact, is_build_relevant_path
from .toolchain import toolchain_report

_C_SUFFIXES = {".c"}
_FORTRAN_SUFFIXES = {".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08"}
_LIBRARY_SUFFIXES = (".so", ".dylib", ".a", ".dll")
_CYTHON_REQUIREMENT_RE = re.compile(
    r"cython\s*>=\s*([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE
)
_GENERATED_TARGET_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".c++",
    ".h",
    ".hh",
    ".hpp",
    ".inc",
    ".py",
    ".pxd",
    ".pyx",
    ".json",
    ".txt",
}

# pcc's self-contained Python C-API headers. pcc's Python.h declares the pcc
# object model (PyObjectHeader/type_tag), NOT CPython's ABI; numpy compiled
# against these emits a pcc-native extension instead of a CPython-ABI one.
_PCC_CAPI_HEADERS = (
    "Python.h",
    "structmember.h",
    "pymem.h",
    "frameobject.h",
    "pythread.h",
    "pyerrors.h",
    "abstract.h",
    "datetime.h",
)
# Match a CPython header include dir (so it can be dropped in pcc-native mode):
# .../pythonX.Y, .../Python.framework/..., .../include/pythonX.Y[suffix].
_CPYTHON_INCLUDE_DIR_RE = re.compile(
    r"(?:^|/)(?:python3\.\d+|Python\.framework)(?:/|$)|/include/python3?\.?\d"
)


def _repo_root() -> Path:
    # build_exec.py lives at <root>/pcc/package/build_exec.py.
    return Path(__file__).resolve().parents[2]


def _pcc_capi_source_dir() -> Path | None:
    candidate = _repo_root() / "utils" / "fake_libc_include"
    return candidate if (candidate / "Python.h").is_file() else None


def _pcc_runtime_include_dir() -> Path | None:
    candidate = _repo_root() / "pcc" / "py_runtime" / "include"
    return candidate if candidate.is_dir() else None


def _materialize_pcc_capi_include(build_dir: Path, *, execute: bool) -> Path | None:
    """Curated include dir holding ONLY pcc's Python C-API headers.

    Deliberately NOT the whole ``utils/fake_libc_include``: its stub ``math.h``
    / ``complex.h`` would shadow the system libm that numpy's C core needs.
    Mirrors the validated ``/tmp/pcc_capi`` probe shape (Python.h + the three
    directly-included helpers). Returns the dir path (always, so the include
    flag can be planned without executing); copies the headers only when
    ``execute`` is set.
    """
    src = _pcc_capi_source_dir()
    if src is None:
        return None
    dest = build_dir / "pcc-capi-include"
    if execute:
        dest.mkdir(parents=True, exist_ok=True)
        for header in _PCC_CAPI_HEADERS:
            header_src = src / header
            if header_src.is_file():
                shutil.copyfile(header_src, dest / header)
    return dest


def _is_cpython_include_dir(directory: str) -> bool:
    return bool(_CPYTHON_INCLUDE_DIR_RE.search(directory))


def _redirect_pcc_native_includes(
    command: list[str], capi_dir: Path, runtime_include: Path | None
) -> list[str]:
    """pcc-native include redirect for a single compile command.

    Drops CPython header include dirs (``-I``/``-isystem``) and appends pcc's
    C-API + runtime includes, so ``#include <Python.h>`` resolves to pcc's
    object model rather than CPython's ABI. System libc and the package's own
    include dirs are left untouched (pcc includes are appended last, so a real
    package header always wins; they only fill the Python.h gap left by the
    dropped CPython dirs). Generic — no package-specific rules.
    """
    if not command:
        return command
    out = [command[0]]
    i = 1
    while i < len(command):
        tok = command[i]
        if tok in ("-I", "-isystem") and i + 1 < len(command):
            if _is_cpython_include_dir(command[i + 1]):
                i += 2
                continue
            out.append(tok)
            out.append(command[i + 1])
            i += 2
            continue
        if tok.startswith("-I") and len(tok) > 2:
            if _is_cpython_include_dir(tok[2:]):
                i += 1
                continue
        out.append(tok)
        i += 1
    out.append("-I" + str(capi_dir))
    if runtime_include is not None:
        out.append("-I" + str(runtime_include))
    return out


def _meson_intro_targets_path(root: Path) -> Path | None:
    candidates = (
        root / "meson-info" / "intro-targets.json",
        root / "build" / "meson-info" / "intro-targets.json",
        root
        / "build"
        / "pcc-package"
        / "meson-build"
        / "meson-info"
        / "intro-targets.json",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _meson_build_dir_from_intro_path(root: Path, intro_path: Path | None) -> Path:
    if intro_path is None:
        return root / "build" / "pcc-package" / "meson-build"
    if intro_path.parent.name == "meson-info":
        return intro_path.parent.parent
    return root / "build" / "pcc-package" / "meson-build"


def _meson_ninja_target(build_dir: Path, raw_path: str) -> str:
    path = Path(raw_path)
    try:
        if path.is_absolute():
            return str(path.relative_to(build_dir))
    except ValueError:
        pass
    return str(path)


def _load_meson_generated_targets(root: Path) -> tuple[str, ...]:
    intro_path = _meson_intro_targets_path(root)
    if intro_path is None:
        return ()
    build_dir = _meson_build_dir_from_intro_path(root, intro_path)
    try:
        entries = json.loads(intro_path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    if not isinstance(entries, list):
        return ()
    targets: list[str] = []
    for target in entries:
        if not isinstance(target, dict):
            continue
        if str(target.get("type") or "").lower() == "custom":
            filenames = target.get("filename")
            if not isinstance(filenames, list):
                filenames = ()
            for filename in filenames:
                targets.append(_meson_ninja_target(build_dir, str(filename)))
        groups = target.get("target_sources")
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            generated_sources = group.get("generated_sources")
            if not isinstance(generated_sources, list):
                generated_sources = ()
            for generated in generated_sources:
                targets.append(_meson_ninja_target(build_dir, str(generated)))
    return tuple(dict.fromkeys(targets))


def _is_file_custom_target(target: str) -> bool:
    """Return whether a Ninja custom target names a generated file.

    `ninja -t targets all` also exposes Meson control targets such as
    `meson-internal__test`. Those can depend on `all`, so executing them here
    accidentally turns "materialize generated inputs" into a full project
    build. Keep this filter file-shaped and package-agnostic.
    """
    if not target or target.startswith("meson-internal__"):
        return False
    if target in {"all", "test", "install", "benchmark", "clean", "uninstall"}:
        return False
    path = Path(target)
    return path.suffix.lower() in _GENERATED_TARGET_SUFFIXES


def _load_ninja_custom_targets(
    build_dir: Path,
    search_paths: list[str] | tuple[str, ...],
    timeout: int,
) -> tuple[str, ...]:
    ninja = _find_tool(("ninja",), search_paths) or "ninja"
    env = os.environ.copy()
    env["PATH"] = _path_env(search_paths)
    try:
        proc = subprocess.run(
            [ninja, "-C", str(build_dir), "-t", "targets", "all"],
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except Exception:
        return ()
    if proc.returncode != 0:
        return ()
    targets: list[str] = []
    for line in proc.stdout.splitlines():
        target, sep, kind = line.partition(":")
        target = target.strip()
        if (
            sep
            and kind.strip().startswith("CUSTOM_COMMAND")
            and _is_file_custom_target(target)
        ):
            targets.append(target)
    return tuple(dict.fromkeys(targets))


def _iter_source_files(root: Path, suffixes: set[str]) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in suffixes
        and is_build_relevant_path(root, path)
        and "__pycache__" not in path.parts
        and ".git" not in path.parts
    )


def _path_env(search_paths: list[str] | tuple[str, ...]) -> str:
    prefix = os.pathsep.join(str(Path(path).expanduser()) for path in search_paths)
    existing = os.environ.get("PATH", "")
    if prefix and existing:
        return prefix + os.pathsep + existing
    return prefix or existing


def _find_tool(
    names: tuple[str, ...], search_paths: list[str] | tuple[str, ...]
) -> str | None:
    path_env = _path_env(search_paths)
    for name in names:
        found = shutil.which(name, path=path_env)
        if found:
            return found
    return None


def _meson_setup_command(
    root: Path,
    build_dir: Path,
    search_paths: list[str] | tuple[str, ...],
) -> list[str]:
    meson_tool = _find_tool(("meson",), search_paths)
    if meson_tool is not None:
        return [meson_tool, "setup", str(build_dir), str(root)]
    vendored = root / "vendored-meson" / "meson" / "meson.py"
    if vendored.exists():
        return [sys.executable, str(vendored), "setup", str(build_dir), str(root)]
    return ["meson", "setup", str(build_dir), str(root)]


def _resolve_command_tool(token: str, search_paths: list[str] | tuple[str, ...]) -> str:
    if "/" in token or "\\" in token:
        return token
    found = shutil.which(token, path=_path_env(search_paths))
    return found or token


def _run_action(
    *,
    kind: str,
    command: list[str],
    source: Path | None,
    output: Path | None = None,
    cwd: Path,
    execute: bool,
    search_paths: list[str] | tuple[str, ...],
    timeout: int,
) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": kind,
        "command": command,
        "output": str(output) if output is not None else None,
        "source": str(source) if source is not None else None,
        "status": "planned",
        "returncode": None,
        "stdout": "",
        "stderr": "",
    }
    if not execute:
        return row
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PATH"] = _path_env(search_paths)
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        row["status"] = "blocked"
        row["stderr"] = str(exc)
        return row
    except subprocess.TimeoutExpired as exc:
        row["status"] = "timeout"
        row["stdout"] = exc.stdout or ""
        row["stderr"] = exc.stderr or ""
        return row
    row["returncode"] = proc.returncode
    row["stdout"] = proc.stdout
    row["stderr"] = proc.stderr
    row["status"] = "passed" if proc.returncode == 0 else "failed"
    return row


def _library_names_for_request(name: str) -> tuple[str, ...]:
    lowered = name.lower()
    if lowered == "blas":
        return ("openblas", "blas")
    return (lowered,)


def _find_library_binding(
    request: str,
    library_dirs: list[str] | tuple[str, ...],
) -> dict[str, object]:
    names = _library_names_for_request(request)
    for directory in library_dirs:
        root = Path(directory).expanduser()
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_file():
                continue
            lower = child.name.lower()
            if not lower.endswith(_LIBRARY_SUFFIXES):
                continue
            for candidate in names:
                if lower.startswith("lib" + candidate) or lower.startswith(candidate):
                    stem = child.name
                    if stem.startswith("lib"):
                        stem = stem[3:]
                    for suffix in _LIBRARY_SUFFIXES:
                        if stem.endswith(suffix):
                            stem = stem[: -len(suffix)]
                            break
                    return {
                        "request": request,
                        "found": True,
                        "link_name": stem,
                        "path": str(child),
                    }
    return {
        "request": request,
        "found": False,
        "link_name": request,
        "path": None,
    }


def generated_c_provenance(root: str | Path) -> list[dict[str, object]]:
    source_root = Path(root).expanduser().resolve()
    rows: list[dict[str, object]] = []
    for pyx in _iter_source_files(source_root, {".pyx"}):
        generated = pyx.with_suffix(".c")
        status = "missing"
        if generated.exists():
            try:
                status = (
                    "up_to_date"
                    if generated.stat().st_mtime >= pyx.stat().st_mtime
                    else "stale"
                )
            except OSError:
                status = "unknown"
        rows.append(
            {
                "pyx": str(pyx),
                "generated_c": str(generated),
                "exists": generated.exists(),
                "status": status,
            }
        )
    return rows


def _cython_min_version(requires: tuple[str, ...]) -> str | None:
    for requirement in requires:
        match = _CYTHON_REQUIREMENT_RE.search(requirement)
        if match is not None:
            return match.group(1)
    return None


def execute_build_actions(
    name: str,
    path: str | Path,
    *,
    search_paths: list[str] | tuple[str, ...] = (),
    include_dirs: list[str] | tuple[str, ...] = (),
    library_dirs: list[str] | tuple[str, ...] = (),
    execute: bool = False,
    regenerate_cython: bool = False,
    run_f2py: bool = False,
    link_output: str | Path | None = None,
    libraries: list[str] | tuple[str, ...] = (),
    abi_mode: str = "pcc-native",
    from_compile_commands: bool = False,
    from_meson_introspection: bool = False,
    configure_meson: bool = False,
    enforce_generated_c: bool = False,
    timeout: int = 30,
) -> dict[str, object]:
    root = Path(path).expanduser().resolve()
    metadata = inspect_artifact(name, root)
    plan = build_plan_for_artifact(name, root)
    actions: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    generated_c_rows = generated_c_provenance(root)
    native_fallbacks = set(metadata.native_library_fallbacks)

    toolchain = toolchain_report(
        search_paths=search_paths,
        library_dirs=library_dirs,
        require_fortran=bool(metadata.fortran_sources),
        require_cython=(regenerate_cython or configure_meson)
        and bool(metadata.cython_sources),
        require_f2py=run_f2py and bool(metadata.fortran_sources),
        require_blas=bool(metadata.blas_indicators) and "blas" not in native_fallbacks,
        require_lapack=bool(metadata.lapack_indicators)
        and "lapack" not in native_fallbacks,
        min_cython_version=_cython_min_version(metadata.pyproject_requires),
    )
    diagnostics.extend(toolchain["diagnostics"])  # type: ignore[arg-type]

    build_dir = root / "build" / "pcc-package"
    meson_build_dir = build_dir / "meson-build"
    if execute:
        build_dir.mkdir(parents=True, exist_ok=True)
    object_outputs: list[Path] = []

    cython_tool = _find_tool(("cython", "cython3"), search_paths)
    if (
        metadata.cython_sources
        and not metadata.generated_c_artifacts
        and not regenerate_cython
        and not from_compile_commands
        and not from_meson_introspection
    ):
        diagnostics.append(
            {
                "code": "PCC-PKG-CYTHON-REGENERATION-REQUIRED",
                "message": "cython sources exist without generated C; pass --regenerate-cython",
            }
        )
    if regenerate_cython:
        for rel in metadata.cython_sources:
            source = root / rel
            output = source.with_suffix(".c")
            if cython_tool is None:
                actions.append(
                    {
                        "kind": "cython_regenerate",
                        "command": ["cython", str(source), "-o", str(output)],
                        "source": str(source),
                        "status": "blocked" if execute else "planned",
                        "returncode": None,
                        "stdout": "",
                        "stderr": "cython executable not found" if execute else "",
                    }
                )
            else:
                actions.append(
                    _run_action(
                        kind="cython_regenerate",
                        command=[cython_tool, str(source), "-o", str(output)],
                        source=source,
                        output=output,
                        cwd=root,
                        execute=execute,
                        search_paths=search_paths,
                        timeout=timeout,
                    )
                )

    if regenerate_cython and execute:
        generated_c_rows = generated_c_provenance(root)
    if enforce_generated_c:
        for row in generated_c_rows:
            if row["status"] == "missing":
                diagnostics.append(
                    {
                        "code": "PCC-PKG-GENERATED-C-MISSING",
                        "message": "generated C artifact is missing for Cython source",
                        "pyx": str(row["pyx"]),
                        "generated_c": str(row["generated_c"]),
                    }
                )
            elif row["status"] == "stale":
                diagnostics.append(
                    {
                        "code": "PCC-PKG-GENERATED-C-STALE",
                        "message": "generated C artifact is older than its Cython source",
                        "pyx": str(row["pyx"]),
                        "generated_c": str(row["generated_c"]),
                    }
                )

    f2py_tool = _find_tool(("f2py", "f2py3"), search_paths)
    if run_f2py:
        for source in _iter_source_files(root, _FORTRAN_SUFFIXES):
            if f2py_tool is None:
                actions.append(
                    {
                        "kind": "f2py_build",
                        "command": ["f2py", "-c", str(source)],
                        "source": str(source),
                        "status": "blocked" if execute else "planned",
                        "returncode": None,
                        "stdout": "",
                        "stderr": "f2py executable not found" if execute else "",
                    }
                )
            else:
                actions.append(
                    _run_action(
                        kind="f2py_build",
                        command=[f2py_tool, "-c", str(source)],
                        source=source,
                        cwd=root,
                        execute=execute,
                        search_paths=search_paths,
                        timeout=timeout,
                    )
                )

    cc_tool = _find_tool(("cc", "clang", "gcc"), search_paths)
    fortran_tool = _find_tool(("gfortran", "flang", "ifx", "ifort"), search_paths)
    graph_commands = ()
    graph_kind_prefix = "compile_command_"
    meson_graph_pending = False
    if from_compile_commands and plan.commands:
        graph_commands = plan.commands
    elif from_meson_introspection:
        graph_commands = load_meson_introspection_commands(root)
        graph_kind_prefix = "meson_compile_"
        if not graph_commands and configure_meson:
            setup_action = _run_action(
                kind="meson_setup",
                command=_meson_setup_command(root, meson_build_dir, search_paths),
                source=None,
                output=meson_build_dir,
                cwd=root,
                execute=execute,
                search_paths=search_paths,
                timeout=timeout,
            )
            actions.append(setup_action)
            if execute and setup_action["status"] == "passed":
                graph_commands = load_meson_introspection_commands(
                    meson_build_dir, root
                )
        if not graph_commands:
            if not configure_meson or execute:
                diagnostics.append(
                    {
                        "code": "PCC-PKG-MESON-INTROSPECTION-MISSING",
                        "message": "Meson intro-targets.json did not provide C/C++/Fortran compile sources",
                    }
                )
            else:
                meson_graph_pending = True

    if graph_commands:
        if from_meson_introspection:
            build_root = _meson_build_dir_from_intro_path(
                root, _meson_intro_targets_path(root)
            )
            generated_targets = _load_meson_generated_targets(root)
            if execute:
                generated_targets = tuple(
                    dict.fromkeys(
                        generated_targets
                        + _load_ninja_custom_targets(build_root, search_paths, timeout)
                    )
                )
            if generated_targets:
                ninja = _find_tool(("ninja",), search_paths) or "ninja"
                actions.append(
                    _run_action(
                        kind="meson_generated_targets",
                        command=[ninja, "-C", str(build_root), *generated_targets],
                        source=None,
                        output=None,
                        cwd=root,
                        execute=execute,
                        search_paths=search_paths,
                        timeout=timeout,
                    )
                )
        pcc_native_capi_dir = None
        pcc_native_runtime_inc = None
        if abi_mode == "pcc-native":
            pcc_native_capi_dir = _materialize_pcc_capi_include(
                build_dir, execute=execute
            )
            pcc_native_runtime_inc = _pcc_runtime_include_dir()
            if pcc_native_capi_dir is None:
                diagnostics.append(
                    {
                        "code": "PCC-PKG-CAPI-INCLUDE-MISSING",
                        "message": (
                            "pcc-native build could not locate pcc's Python C-API "
                            "headers (utils/fake_libc_include/Python.h); compiling "
                            "against the build's CPython includes instead."
                        ),
                    }
                )
        for build_command in graph_commands:
            try:
                command = shlex.split(build_command.command)
            except ValueError:
                command = build_command.command.split()
            if command:
                command[0] = _resolve_command_tool(command[0], search_paths)
            if (
                pcc_native_capi_dir is not None
                and build_command.language == "c"
            ):
                command = _redirect_pcc_native_includes(
                    command, pcc_native_capi_dir, pcc_native_runtime_inc
                )
            cwd = (
                Path(build_command.directory).expanduser().resolve()
                if build_command.directory
                else root
            )
            source = Path(build_command.file).expanduser()
            if not source.is_absolute():
                source = cwd / source
            output = (
                Path(build_command.output).expanduser()
                if build_command.output
                else None
            )
            if output is not None and not output.is_absolute():
                output = cwd / output
            if output is not None:
                object_outputs.append(output)
            actions.append(
                _run_action(
                    kind=graph_kind_prefix + build_command.language,
                    command=command,
                    source=source,
                    output=output,
                    cwd=cwd,
                    execute=execute,
                    search_paths=search_paths,
                    timeout=timeout,
                )
            )
    elif from_compile_commands or from_meson_introspection:
        pass
    else:
        include_flags = ["-I" + str(Path(path).expanduser()) for path in include_dirs]
        for source in _iter_source_files(root, _C_SUFFIXES):
            output = build_dir / (source.stem + ".o")
            object_outputs.append(output)
            command = [
                cc_tool or "cc",
                "-c",
                *include_flags,
                str(source),
                "-o",
                str(output),
            ]
            actions.append(
                _run_action(
                    kind="c_compile",
                    command=command,
                    source=source,
                    output=output,
                    cwd=root,
                    execute=execute and cc_tool is not None,
                    search_paths=search_paths,
                    timeout=timeout,
                )
                if cc_tool is not None
                else {
                    "kind": "c_compile",
                    "command": command,
                    "output": str(output),
                    "source": str(source),
                    "status": "blocked" if execute else "planned",
                    "returncode": None,
                    "stdout": "",
                    "stderr": "C compiler not found" if execute else "",
                }
            )

        for source in _iter_source_files(root, _FORTRAN_SUFFIXES):
            output = build_dir / (source.stem + ".o")
            object_outputs.append(output)
            command = [fortran_tool or "gfortran", "-c", str(source), "-o", str(output)]
            actions.append(
                _run_action(
                    kind="fortran_compile",
                    command=command,
                    source=source,
                    output=output,
                    cwd=root,
                    execute=execute and fortran_tool is not None,
                    search_paths=search_paths,
                    timeout=timeout,
                )
                if fortran_tool is not None
                else {
                    "kind": "fortran_compile",
                    "command": command,
                    "output": str(output),
                    "source": str(source),
                    "status": "blocked" if execute else "planned",
                    "returncode": None,
                    "stdout": "",
                    "stderr": "Fortran compiler not found" if execute else "",
                }
            )

    vendor_bindings = [
        _find_library_binding(library, library_dirs) for library in libraries
    ]
    for binding in vendor_bindings:
        if not binding["found"]:
            diagnostics.append(
                {
                    "code": "PCC-PKG-MISSING-LIBRARY",
                    "message": f"requested package native library is missing: {binding['request']}",
                    "library": str(binding["request"]),
                }
            )

    link_report: dict[str, object] | None = None
    if link_output is not None:
        output_path = Path(link_output).expanduser()
        if not output_path.is_absolute():
            output_path = (root / output_path).resolve()
        if meson_graph_pending:
            actions.append(
                {
                    "kind": "native_link",
                    "command": [
                        "<after-meson-setup>",
                        "-shared",
                        "-o",
                        str(output_path),
                    ],
                    "output": str(output_path),
                    "source": None,
                    "status": "planned",
                    "returncode": None,
                    "stdout": "",
                    "stderr": "",
                }
            )
        else:
            link_inputs = [str(path) for path in object_outputs]
            command = [cc_tool or "cc", "-shared", *link_inputs, "-o", str(output_path)]
            for directory in library_dirs:
                command.append("-L" + str(Path(directory).expanduser()))
            for binding in vendor_bindings:
                if binding["found"]:
                    command.append("-l" + str(binding["link_name"]))
            if cc_tool is None:
                actions.append(
                    {
                        "kind": "native_link",
                        "command": command,
                        "output": str(output_path),
                        "source": None,
                        "status": "blocked" if execute else "planned",
                        "returncode": None,
                        "stdout": "",
                        "stderr": "C compiler/linker not found" if execute else "",
                    }
                )
            elif execute and any(action["status"] != "passed" for action in actions):
                actions.append(
                    {
                        "kind": "native_link",
                        "command": command,
                        "output": str(output_path),
                        "source": None,
                        "status": "blocked",
                        "returncode": None,
                        "stdout": "",
                        "stderr": "compile actions did not all pass",
                    }
                )
            else:
                actions.append(
                    _run_action(
                        kind="native_link",
                        command=command,
                        source=None,
                        output=output_path,
                        cwd=root,
                        execute=execute,
                        search_paths=search_paths,
                        timeout=timeout,
                    )
                )
            artifacts = [str(output_path)] if output_path.exists() else []
            link_report = linkage_report(
                artifacts=artifacts,
                commands=[" ".join(command)],
                abi_mode=abi_mode,
            )
            if not link_report["ok"]:
                diagnostics.extend(link_report["diagnostics"])  # type: ignore[arg-type]

    failed = [
        action
        for action in actions
        if action["status"] in ("blocked", "failed", "timeout")
    ]
    if failed:
        diagnostics.append(
            {
                "code": "PCC-PKG-BUILD-ACTION-FAILED",
                "message": "one or more package build actions did not complete",
            }
        )
    return {
        "ok": not failed
        and not toolchain["diagnostics"]
        and not [
            diag
            for diag in diagnostics
            if isinstance(diag, dict)
            and str(diag.get("code", "")).startswith("PCC-PKG")
        ],
        "name": metadata.name,
        "path": str(root),
        "execute": execute,
        "include_dirs": [str(Path(path).expanduser()) for path in include_dirs],
        "from_compile_commands": from_compile_commands,
        "from_meson_introspection": from_meson_introspection,
        "configure_meson": configure_meson,
        "generated_c_provenance": generated_c_rows,
        "actions": actions,
        "diagnostics": diagnostics,
        "vendor_bindings": vendor_bindings,
        "linkage": link_report,
        "toolchain": toolchain,
        "build_plan": plan.as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcc.package build-exec")
    parser.add_argument("name", nargs="?", default="package")
    parser.add_argument("--path", required=True)
    parser.add_argument("--search-path", action="append", default=[])
    parser.add_argument("--include-dir", action="append", default=[])
    parser.add_argument("--library-dir", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--regenerate-cython", action="store_true")
    parser.add_argument("--run-f2py", action="store_true")
    parser.add_argument("--link-output", default=None)
    parser.add_argument("--library", action="append", default=[])
    parser.add_argument("--abi", dest="abi_mode", default="pcc-native")
    parser.add_argument("--from-compile-commands", action="store_true")
    parser.add_argument("--from-meson-introspection", action="store_true")
    parser.add_argument("--configure-meson", action="store_true")
    parser.add_argument("--enforce-generated-c", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args(argv)
    report = execute_build_actions(
        ns.name,
        ns.path,
        search_paths=ns.search_path,
        include_dirs=ns.include_dir,
        library_dirs=ns.library_dir,
        execute=ns.execute,
        regenerate_cython=ns.regenerate_cython,
        run_f2py=ns.run_f2py,
        link_output=ns.link_output,
        libraries=ns.library,
        abi_mode=ns.abi_mode,
        from_compile_commands=ns.from_compile_commands,
        from_meson_introspection=ns.from_meson_introspection,
        configure_meson=ns.configure_meson,
        enforce_generated_c=ns.enforce_generated_c,
        timeout=ns.timeout,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
