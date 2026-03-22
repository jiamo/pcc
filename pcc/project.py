"""C project builder: collect and merge multiple .c files for compilation.

Usage:
    pcc <file.c>      - compile and run a single file
    pcc <directory>    - collect all .c files, merge, compile and run

For a directory project, the builder:
1. Finds all .c files in the directory (non-recursive)
2. Puts the file containing main() last
3. Merges all source files into a single compilation unit
4. #include "xxx.h" is resolved by the preprocessor
"""

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationUnit:
    name: str
    path: str
    source: str


def collect_project(path, sources_from_make=None):
    """Collect C source from a file or directory.

    Args:
        path: path to a .c file or a directory
        sources_from_make: optional make goal used to derive participating .c files

    Returns:
        (merged_source, base_dir)
    """
    path = os.path.abspath(path)

    if os.path.isfile(path):
        if sources_from_make:
            raise ValueError("--sources-from-make requires a directory input")
        base_dir = os.path.dirname(path)
        with open(path, 'r') as f:
            return f.read(), base_dir

    if os.path.isdir(path):
        return _collect_directory(path, sources_from_make=sources_from_make), path

    raise FileNotFoundError(f"Not found: {path}")


def collect_translation_units(path, sources_from_make=None):
    """Collect translation units from a file or directory.

    Returns:
        (list[TranslationUnit], base_dir)
    """
    path = os.path.abspath(path)

    if os.path.isfile(path):
        if sources_from_make:
            raise ValueError("--sources-from-make requires a directory input")
        base_dir = os.path.dirname(path)
        with open(path, "r") as f:
            return [TranslationUnit(os.path.basename(path), path, f.read())], base_dir

    if os.path.isdir(path):
        return (
            _collect_directory_units(path, sources_from_make=sources_from_make),
            path,
        )

    raise FileNotFoundError(f"Not found: {path}")


def _collect_directory(dirpath, sources_from_make=None):
    """Merge all .c files in a directory into one source string."""
    c_files = _project_c_files(dirpath, sources_from_make=sources_from_make)

    if not c_files:
        raise FileNotFoundError(f"No .c files found in {dirpath}")

    # Separate: main file goes last, others go first
    main_file = None
    other_files = []

    for fname in c_files:
        fpath = _resolve_source_path(dirpath, fname)
        with open(fpath, 'r') as f:
            content = f.read()
        if _has_main(content):
            main_file = (fname, content)
        else:
            other_files.append((fname, content))

    if main_file is None:
        raise ValueError(f"No main() function found in {dirpath}/*.c")

    # Merge: other files first, then main file
    parts = []
    for fname, content in other_files:
        parts.append(f"// --- {fname} ---")
        parts.append(content)
    fname, content = main_file
    parts.append(f"// --- {fname} (main) ---")
    parts.append(content)

    return '\n'.join(parts)


def _collect_directory_units(dirpath, sources_from_make=None):
    """Collect a directory as independent translation units."""
    c_files = _project_c_files(dirpath, sources_from_make=sources_from_make)

    if not c_files:
        raise FileNotFoundError(f"No .c files found in {dirpath}")

    main_files = []
    other_units = []
    for fname in c_files:
        fpath = _resolve_source_path(dirpath, fname)
        with open(fpath, "r") as f:
            content = f.read()
        unit = TranslationUnit(fname, fpath, content)
        if _has_main(content):
            main_files.append(unit)
        else:
            other_units.append(unit)

    if not main_files:
        raise ValueError(f"No main() function found in {dirpath}/*.c")
    if len(main_files) > 1:
        names = ", ".join(unit.name for unit in main_files)
        raise ValueError(f"Multiple main() definitions found in {dirpath}: {names}")

    return other_units + main_files


def _project_c_files(dirpath, sources_from_make=None):
    if sources_from_make:
        return _make_goal_c_files(dirpath, sources_from_make)
    return _directory_c_files(dirpath)


def _directory_c_files(dirpath):
    return sorted(f for f in os.listdir(dirpath) if f.endswith(".c"))


def _make_goal_c_files(dirpath, goal):
    make = shutil.which("make")
    if not make:
        raise ValueError("make not found in PATH")

    cmd = [make, "-nB", "-C", dirpath, goal]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(
            f"failed to collect C sources from make goal '{goal}': {detail}"
        )

    sources = []
    seen = set()
    for line in result.stdout.splitlines():
        for source_path in _extract_c_sources_from_make_line(line, dirpath):
            if source_path not in seen:
                seen.add(source_path)
                sources.append(source_path)

    if not sources:
        raise ValueError(
            f"make goal '{goal}' did not yield any C compilation commands in {dirpath}"
        )

    return sources


def _extract_c_sources_from_make_line(line, dirpath):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return []

    stripped = stripped.lstrip("@+-")
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return []

    if "-c" not in tokens:
        return []

    sources = []
    for token in tokens:
        if token.startswith("-") or not token.endswith(".c"):
            continue
        candidate = os.path.abspath(os.path.join(dirpath, token))
        if not os.path.isfile(candidate):
            continue
        sources.append(os.path.relpath(candidate, dirpath))
    return sources


def _resolve_source_path(dirpath, source_path):
    if os.path.isabs(source_path):
        return source_path
    return os.path.join(dirpath, source_path)


def _has_main(source):
    """Check if source contains a main() function definition."""
    return bool(
        re.search(
            r"\b(?:int|void)\s+main\s*\([^;{}]*\)\s*\{",
            source,
            re.MULTILINE | re.DOTALL,
        )
    )
