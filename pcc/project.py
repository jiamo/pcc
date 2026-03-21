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


def collect_project(path):
    """Collect C source from a file or directory.

    Args:
        path: path to a .c file or a directory

    Returns:
        (merged_source, base_dir)
    """
    path = os.path.abspath(path)

    if os.path.isfile(path):
        base_dir = os.path.dirname(path)
        with open(path, 'r') as f:
            return f.read(), base_dir

    if os.path.isdir(path):
        return _collect_directory(path), path

    raise FileNotFoundError(f"Not found: {path}")


def _collect_directory(dirpath):
    """Merge all .c files in a directory into one source string."""
    c_files = sorted(f for f in os.listdir(dirpath) if f.endswith('.c'))

    if not c_files:
        raise FileNotFoundError(f"No .c files found in {dirpath}")

    # Separate: main file goes last, others go first
    main_file = None
    other_files = []

    for fname in c_files:
        fpath = os.path.join(dirpath, fname)
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


def _has_main(source):
    """Check if source contains a main() function definition."""
    # Match: int main(, void main(, main( at start of line
    return bool(re.search(r'^\s*(?:int|void)\s+main\s*\(', source, re.MULTILINE))
