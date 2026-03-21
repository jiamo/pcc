"""Simple C preprocessor for pcc.

Supports:
  #include <stdio.h>     - system headers: silently ignored (libc auto-declared)
  #include "file.h"      - user headers: read and inline the file content
  #define NAME VALUE     - simple text replacement macros (no function-like macros)
  #define NAME           - define without value (for #ifdef)
  #ifdef NAME / #ifndef NAME / #else / #endif - conditional compilation
  #undef NAME            - undefine a macro

Does NOT support:
  - Function-like macros: #define MAX(a,b) ...
  - #if expressions
  - Token pasting / stringification
"""

import re
import os

# System headers that are silently ignored (functions auto-declared from LIBC_FUNCTIONS)
SYSTEM_HEADERS = {
    "stdio.h", "stdlib.h", "string.h", "strings.h", "ctype.h",
    "math.h", "time.h", "unistd.h", "fcntl.h", "errno.h",
    "assert.h", "stdarg.h", "stddef.h", "stdint.h", "stdbool.h",
    "limits.h", "float.h", "signal.h", "setjmp.h", "locale.h",
    "inttypes.h", "iso646.h", "wchar.h", "wctype.h",
    "sys/types.h", "sys/stat.h",
}

# Built-in macros provided by common headers
BUILTIN_DEFINES = {
    "NULL": "0",
    "EOF": "(-1)",
    "EXIT_SUCCESS": "0",
    "EXIT_FAILURE": "1",
    "RAND_MAX": "2147483647",
    "INT_MAX": "9223372036854775807",
    "INT_MIN": "(-9223372036854775807-1)",
    "CHAR_BIT": "8",
    "true": "1",
    "false": "0",
}


def preprocess(source, base_dir=None, defines=None):
    """Preprocess C source code.

    Args:
        source: C source code string
        base_dir: directory for resolving relative #include "file.h" paths
        defines: initial dict of macro definitions

    Returns:
        Preprocessed source code string
    """
    if base_dir is None:
        base_dir = "."
    macros = dict(BUILTIN_DEFINES)
    if defines:
        macros.update(defines)

    return _preprocess_lines(source.splitlines(), base_dir, macros, set())


def _preprocess_lines(lines, base_dir, macros, included_files):
    output = []
    i = 0
    skip_stack = []  # stack of (skipping, else_seen)

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check if we're in a skipped block
        skipping = any(s[0] for s in skip_stack)

        # Handle preprocessor directives even in skipped blocks (for nesting)
        if stripped.startswith('#'):
            directive = stripped[1:].strip()

            if directive.startswith('ifdef '):
                name = directive[6:].strip()
                skip_stack.append((name not in macros if not skipping else True, False))
                i += 1
                continue

            if directive.startswith('ifndef '):
                name = directive[7:].strip()
                skip_stack.append((name in macros if not skipping else True, False))
                i += 1
                continue

            if directive.startswith('else'):
                if skip_stack:
                    was_skipping, _ = skip_stack[-1]
                    # Only flip if parent isn't skipping
                    parent_skip = any(s[0] for s in skip_stack[:-1])
                    skip_stack[-1] = (not was_skipping if not parent_skip else True, True)
                i += 1
                continue

            if directive.startswith('endif'):
                if skip_stack:
                    skip_stack.pop()
                i += 1
                continue

            if skipping:
                i += 1
                continue

            # #include <header> or #include "header"
            m = re.match(r'include\s*<(.+?)>', directive)
            if m:
                # System header: silently skip (libc functions auto-declared)
                i += 1
                continue

            m = re.match(r'include\s*"(.+?)"', directive)
            if m:
                filename = m.group(1)
                filepath = os.path.join(base_dir, filename)
                if filepath not in included_files:
                    included_files.add(filepath)
                    try:
                        with open(filepath, 'r') as f:
                            header_lines = f.read().splitlines()
                        header_dir = os.path.dirname(filepath)
                        result = _preprocess_lines(
                            header_lines, header_dir, macros, included_files)
                        output.append(result)
                    except FileNotFoundError:
                        pass  # silently skip missing user headers
                i += 1
                continue

            # #define NAME VALUE
            m = re.match(r'define\s+(\w+)\s*(.*)', directive)
            if m:
                name = m.group(1)
                value = m.group(2).strip()
                macros[name] = value if value else ""
                i += 1
                continue

            # #undef NAME
            m = re.match(r'undef\s+(\w+)', directive)
            if m:
                macros.pop(m.group(1), None)
                i += 1
                continue

            # Unknown directive: pass through (e.g., #pragma, #line)
            i += 1
            continue

        if skipping:
            i += 1
            continue

        # Apply macro substitution on non-directive lines
        processed = _apply_macros(line, macros)
        output.append(processed)
        i += 1

    return '\n'.join(output)


def _apply_macros(line, macros):
    """Replace macro names with their values in a line."""
    if not macros:
        return line
    # Sort by length (longest first) to avoid partial replacements
    for name in sorted(macros, key=len, reverse=True):
        if name in line:
            # Only replace whole words (not substrings of identifiers)
            line = re.sub(r'\b' + re.escape(name) + r'\b', macros[name], line)
    return line
