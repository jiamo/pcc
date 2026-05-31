"""Generic native build toolchain discovery for pcc package builds."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sysconfig
from pathlib import Path


_TOOL_CANDIDATES = {
    "c_compiler": ("cc", "clang", "gcc"),
    "cxx_compiler": ("c++", "clang++", "g++"),
    "fortran_compiler": ("gfortran", "flang", "ifx", "ifort"),
    "cython": ("cython", "cython3"),
    "f2py": ("f2py", "f2py3"),
}
_TOOL_PROBES = {
    "c_compiler": ("--version",),
    "cxx_compiler": ("--version",),
    "fortran_compiler": ("--version",),
    "cython": ("-V",),
    "f2py": ("--version",),
}
_LIBRARY_CANDIDATES = {
    "blas": ("openblas", "blas"),
    "lapack": ("lapack",),
}
_LIBRARY_SUFFIXES = (".so", ".dylib", ".a", ".dll")
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")


def _path_env(search_paths: tuple[str, ...] | list[str]) -> str | None:
    if not search_paths:
        return None
    return os.pathsep.join(str(Path(path).expanduser()) for path in search_paths)


def _probe_tool(path: str, args: tuple[str, ...]) -> tuple[bool, str]:
    if not args:
        return True, ""
    try:
        proc = subprocess.run(
            [path, *args],
            text=True,
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, (proc.stdout or proc.stderr).strip()
    return False, (proc.stderr or proc.stdout).strip()


def _find_tool(
    key: str,
    names: tuple[str, ...],
    search_paths: tuple[str, ...] | list[str],
) -> dict[str, object]:
    path_env = _path_env(search_paths)
    first_failed: dict[str, object] | None = None
    for name in names:
        found = shutil.which(name, path=path_env)
        if found:
            ok, probe_output = _probe_tool(found, _TOOL_PROBES.get(key, ()))
            row = {
                "name": name,
                "path": found,
                "found": ok,
                "probe_ok": ok,
                "probe_output": probe_output,
            }
            if ok:
                return row
            if first_failed is None:
                first_failed = row
    if first_failed is not None:
        return first_failed
    return {"name": names[0], "path": None, "found": False, "probe_ok": False, "probe_output": ""}


def _default_library_dirs() -> tuple[str, ...]:
    dirs = [
        sysconfig.get_config_var("LIBDIR"),
        "/usr/lib",
        "/usr/local/lib",
        "/opt/homebrew/lib",
    ]
    return tuple(str(Path(item)) for item in dirs if item)


def _find_library(names: tuple[str, ...], library_dirs: tuple[str, ...] | list[str]) -> dict[str, object]:
    dirs = tuple(library_dirs) or _default_library_dirs()
    checked: list[str] = []
    for directory in dirs:
        root = Path(directory).expanduser()
        checked.append(str(root))
        if not root.is_dir():
            continue
        for name in names:
            prefixes = (f"lib{name}", name)
            for child in sorted(root.iterdir()):
                if not child.is_file():
                    continue
                lower = child.name.lower()
                if not lower.endswith(_LIBRARY_SUFFIXES):
                    continue
                if any(lower.startswith(prefix.lower()) for prefix in prefixes):
                    return {"name": name, "path": str(child), "found": True, "checked_dirs": checked}
    return {"name": names[0], "path": None, "found": False, "checked_dirs": checked}


def _version_tuple(text: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _version_less_than(actual: str, minimum: str) -> bool:
    actual_parts = _version_tuple(actual)
    minimum_parts = _version_tuple(minimum)
    if actual_parts is None or minimum_parts is None:
        return False
    length = max(len(actual_parts), len(minimum_parts))
    actual_norm = actual_parts + (0,) * (length - len(actual_parts))
    minimum_norm = minimum_parts + (0,) * (length - len(minimum_parts))
    return actual_norm < minimum_norm


def toolchain_report(
    *,
    search_paths: tuple[str, ...] | list[str] = (),
    library_dirs: tuple[str, ...] | list[str] = (),
    require_fortran: bool = False,
    require_blas: bool = False,
    require_lapack: bool = False,
    require_cython: bool = False,
    require_f2py: bool = False,
    min_cython_version: str | None = None,
) -> dict[str, object]:
    tools = {
        key: _find_tool(key, names, search_paths)
        for key, names in _TOOL_CANDIDATES.items()
    }
    libraries = {
        key: _find_library(names, library_dirs)
        for key, names in _LIBRARY_CANDIDATES.items()
    }
    diagnostics: list[dict[str, str]] = []
    requirements = (
        (require_fortran, "fortran_compiler", tools, "PCC-PKG-MISSING-FORTRAN"),
        (require_cython, "cython", tools, "PCC-PKG-MISSING-CYTHON"),
        (require_f2py, "f2py", tools, "PCC-PKG-MISSING-F2PY"),
        (require_blas, "blas", libraries, "PCC-PKG-MISSING-BLAS"),
        (require_lapack, "lapack", libraries, "PCC-PKG-MISSING-LAPACK"),
    )
    for required, key, mapping, code in requirements:
        if required and not mapping[key]["found"]:
            diagnostics.append(
                {
                    "code": code,
                    "requirement": key,
                    "message": f"required package build toolchain component is missing: {key}",
                }
            )
    if (
        require_cython
        and min_cython_version
        and tools["cython"]["found"]
        and _version_less_than(str(tools["cython"].get("probe_output", "")), min_cython_version)
    ):
        diagnostics.append(
            {
                "code": "PCC-PKG-CYTHON-VERSION-TOO-OLD",
                "requirement": "cython",
                "minimum": min_cython_version,
                "message": f"required Cython version is >= {min_cython_version}",
            }
        )
    return {
        "ok": not diagnostics,
        "tools": tools,
        "libraries": libraries,
        "search_paths": [str(Path(path).expanduser()) for path in search_paths],
        "library_dirs": [str(Path(path).expanduser()) for path in library_dirs],
        "requirements": {
            "fortran": require_fortran,
            "blas": require_blas,
            "lapack": require_lapack,
            "cython": require_cython,
            "f2py": require_f2py,
        },
        "version_requirements": {
            "cython": min_cython_version,
        },
        "diagnostics": diagnostics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcc.package toolchain")
    parser.add_argument("--search-path", action="append", default=[])
    parser.add_argument("--library-dir", action="append", default=[])
    parser.add_argument("--require-fortran", action="store_true")
    parser.add_argument("--require-blas", action="store_true")
    parser.add_argument("--require-lapack", action="store_true")
    parser.add_argument("--require-cython", action="store_true")
    parser.add_argument("--require-f2py", action="store_true")
    parser.add_argument("--min-cython-version", default=None)
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args(argv)
    report = toolchain_report(
        search_paths=ns.search_path,
        library_dirs=ns.library_dir,
        require_fortran=ns.require_fortran,
        require_blas=ns.require_blas,
        require_lapack=ns.require_lapack,
        require_cython=ns.require_cython,
        require_f2py=ns.require_f2py,
        min_cython_version=ns.min_cython_version,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
