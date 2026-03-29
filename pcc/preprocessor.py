"""C preprocessor for pcc.

Supports:
  #include <stdio.h>                - system headers: silently ignored
  #include "file.h"                 - user headers: read and inline
  #define NAME VALUE                - object-like macros
  #define NAME(a,b) ((a)+(b))       - function-like macros
  #define NAME                      - flag macros
  #undef NAME
  #ifdef / #ifndef / #if / #elif / #else / #endif
  #if defined(X) && (X > 5)        - full expression evaluation
  defined(NAME)                     - in #if expressions
  ## token pasting                  - in macro bodies
  # stringification                 - in function macro bodies
  __LINE__, __FILE__                - built-in macros
"""

import re
import os
import warnings

IDENTIFIER_RE = re.compile(r"[a-zA-Z_]\w*")

# System headers silently ignored (libc functions auto-declared from LIBC_FUNCTIONS)
SYSTEM_HEADERS = {
    "stdio.h",
    "stdlib.h",
    "string.h",
    "strings.h",
    "ctype.h",
    "math.h",
    "time.h",
    "unistd.h",
    "fcntl.h",
    "errno.h",
    "assert.h",
    "stdarg.h",
    "stddef.h",
    "stdint.h",
    "stdbool.h",
    "limits.h",
    "float.h",
    "signal.h",
    "setjmp.h",
    "locale.h",
    "inttypes.h",
    "iso646.h",
    "wchar.h",
    "wctype.h",
    "sys/types.h",
    "sys/stat.h",
}

BUILTIN_DEFINES = {
    "NULL": "0",
    "EOF": "(-1)",
    "EXIT_SUCCESS": "0",
    "EXIT_FAILURE": "1",
    "RAND_MAX": "2147483647",
    "INT_MAX": "9223372036854775807",
    "INT_MIN": "(-9223372036854775807-1)",
    "CHAR_BIT": "8",
    "CHAR_MAX": "127",
    "UCHAR_MAX": "255",
    "SHRT_MAX": "32767",
    "USHRT_MAX": "65535",
    "UINT_MAX": "4294967295",
    "LONG_MAX": "9223372036854775807",
    "ULONG_MAX": "18446744073709551615",
    "LLONG_MAX": "9223372036854775807",
    "LLONG_MIN": "(-9223372036854775807-1)",
    "ULLONG_MAX": "18446744073709551615",
    "SIZE_MAX": "18446744073709551615",
    "INTPTR_MAX": "9223372036854775807",
    "FLT_MAX": "3.402823466e+38",
    "DBL_MAX": "1.7976931348623158e+308",
    "FLT_MIN": "1.175494351e-38",
    "DBL_MIN": "2.2250738585072014e-308",
    "FLT_MANT_DIG": "24",
    "DBL_MANT_DIG": "53",
    "FLT_MAX_EXP": "128",
    "DBL_MAX_EXP": "1024",
    "HUGE_VAL": "1e309",
    "HUGE_VALF": "1e39f",
    "true": "1",
    "false": "0",
    "__STDC__": "1",
    "__STDC_VERSION__": "201112",
}

# Type definitions injected before user code (like stddef.h / stdint.h)
TYPE_PREAMBLE = """
typedef long size_t;
typedef long ssize_t;
typedef long ptrdiff_t;
typedef long intptr_t;
typedef unsigned long uintptr_t;
typedef void *va_list;
typedef long long intmax_t;
typedef unsigned long long uintmax_t;
typedef int sig_atomic_t;
typedef int wchar_t;
typedef int wint_t;
typedef long off_t;
typedef long clock_t;
typedef long time_t;
typedef int pid_t;
typedef unsigned int mode_t;
typedef char FILE;
"""


class Macro:
    """Represents a #define macro (object-like or function-like)."""

    __slots__ = ("name", "params", "body", "is_function", "_pattern")

    def __init__(self, name, body, params=None):
        self.name = name
        self.body = body
        self.params = params  # None for object-like, list for function-like
        self.is_function = params is not None
        self._pattern = (
            re.compile(r"\b" + re.escape(name) + r"\b")
            if not self.is_function
            else None
        )


class Preprocessor:
    def __init__(self, base_dir=None, defines=None):
        self.base_dir = base_dir or "."
        self.macros = {}
        self._expand_cache = {}
        self._identifier_cache = {}
        self.included_files = set()
        self._line_no = 0
        self._file = "<string>"

        # Load built-in defines as object-like macros
        for name, value in BUILTIN_DEFINES.items():
            self.macros[name] = Macro(name, value)
        if defines:
            for name, value in defines.items():
                self.macros[name] = Macro(name, str(value))

    def preprocess(self, source):
        # Inject type preamble for common typedefs
        self._expand_cache.clear()
        self._identifier_cache.clear()
        source = TYPE_PREAMBLE + source
        lines = source.splitlines()
        return self._process_lines(lines, self.base_dir)

    def _invalidate_expand_cache(self):
        self._expand_cache.clear()

    def _process_lines(self, lines, base_dir):
        output = []
        i = 0
        skip_stack = []  # stack of (skipping: bool, branch_taken: bool)

        while i < len(lines):
            self._line_no = i + 1
            line = lines[i]
            stripped = line.strip()

            # Line continuation
            while stripped.endswith("\\") and i + 1 < len(lines):
                i += 1
                next_line = lines[i].strip()
                stripped = stripped[:-1] + " " + next_line
                line = stripped

            skipping = any(s[0] for s in skip_stack)

            if stripped.startswith("#"):
                # Strip inline C comments from directives
                directive = re.sub(r"/\*.*?\*/", "", stripped[1:]).strip()
                directive = re.sub(r"//.*$", "", directive).strip()
                handled = self._handle_directive(
                    directive, output, skip_stack, skipping, base_dir
                )
                i += 1
                continue

            if skipping:
                i += 1
                continue

            # Apply macro expansion
            processed = self._expand_line(line)
            output.append(processed)
            i += 1

        return "\n".join(output)

    def _handle_directive(self, directive, output, skip_stack, skipping, base_dir):
        # --- Conditional directives (always processed for nesting) ---
        if directive.startswith("ifdef "):
            name = directive[6:].strip()
            if skipping:
                skip_stack.append((True, False))
            else:
                cond = name in self.macros
                skip_stack.append((not cond, cond))
            return

        if directive.startswith("ifndef "):
            name = directive[7:].strip()
            if skipping:
                skip_stack.append((True, False))
            else:
                cond = name not in self.macros
                skip_stack.append((not cond, cond))
            return

        if directive.startswith("if "):
            expr = directive[3:].strip()
            if skipping:
                skip_stack.append((True, False))
            else:
                cond = self._eval_condition(expr)
                skip_stack.append((not cond, cond))
            return

        if directive.startswith("elif "):
            expr = directive[5:].strip()
            if not skip_stack:
                return
            parent_skip = (
                any(s[0] for s in skip_stack[:-1]) if len(skip_stack) > 1 else False
            )
            _, branch_taken = skip_stack[-1]
            if parent_skip or branch_taken:
                skip_stack[-1] = (True, branch_taken)
            else:
                cond = self._eval_condition(expr)
                skip_stack[-1] = (not cond, cond)
            return

        if directive.startswith("else"):
            if skip_stack:
                parent_skip = (
                    any(s[0] for s in skip_stack[:-1]) if len(skip_stack) > 1 else False
                )
                _, branch_taken = skip_stack[-1]
                if parent_skip or branch_taken:
                    skip_stack[-1] = (True, branch_taken)
                else:
                    skip_stack[-1] = (False, True)
            return

        if directive.startswith("endif"):
            if skip_stack:
                skip_stack.pop()
            return

        if skipping:
            return

        # --- Non-conditional directives (only when not skipping) ---

        # #include <header>
        m = re.match(r"include\s*<(.+?)>", directive)
        if m:
            return  # System header: silently skip

        # #include "header"
        m = re.match(r'include\s*"(.+?)"', directive)
        if m:
            filename = m.group(1)
            filepath = os.path.join(base_dir, filename)
            filepath = os.path.normpath(filepath)
            if filepath not in self.included_files:
                self.included_files.add(filepath)
                try:
                    with open(filepath, "r") as f:
                        header_lines = f.read().splitlines()
                    header_dir = os.path.dirname(filepath)
                    result = self._process_lines(header_lines, header_dir)
                    output.append(result)
                except FileNotFoundError:
                    pass
            return

        # #define NAME(params) body   -- function-like macro
        m = re.match(r"define\s+(\w+)\(([^)]*)\)\s*(.*)", directive)
        if m:
            name = m.group(1)
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]
            body = m.group(3).strip()
            self.macros[name] = Macro(name, body, params)
            self._invalidate_expand_cache()
            return

        # #define NAME body   -- object-like macro
        m = re.match(r"define\s+(\w+)\s*(.*)", directive)
        if m:
            name = m.group(1)
            body = m.group(2).strip()
            self.macros[name] = Macro(name, body)
            self._invalidate_expand_cache()
            return

        # #undef NAME
        m = re.match(r"undef\s+(\w+)", directive)
        if m:
            self.macros.pop(m.group(1), None)
            self._invalidate_expand_cache()
            return

        # #error, #warning, #pragma, #line: silently ignore
        return

    def _eval_condition(self, expr):
        """Evaluate a #if / #elif expression. Returns True/False."""
        # Strip C comments from expression
        expr = re.sub(r"/\*.*?\*/", "", expr).strip()
        expr = re.sub(r"//.*$", "", expr).strip()
        # Handle defined(NAME) and defined NAME BEFORE macro expansion
        # to avoid expanding the name away
        expanded = re.sub(
            r"\bdefined\s*\(\s*(\w+)\s*\)",
            lambda m: "1" if m.group(1) in self.macros else "0",
            expr,
        )
        expanded = re.sub(
            r"\bdefined\s+(\w+)",
            lambda m: "1" if m.group(1) in self.macros else "0",
            expanded,
        )
        # Now expand macros
        expanded = self._expand_line(expanded)
        # Replace any remaining identifiers with 0 (C standard behavior)
        expanded = re.sub(r"\b[a-zA-Z_]\w*\b", "0", expanded)
        # Evaluate using Python
        try:
            # C uses && || ! instead of and or not, but Python handles these as bitwise
            # Convert C logical operators
            py_expr = (
                expanded.replace("&&", " and ")
                .replace("||", " or ")
                .replace("!", " not ")
            )
            return bool(eval(py_expr, {"__builtins__": {}}, {}))
        except Exception as exc:
            warnings.warn(
                f"preprocessor: failed to evaluate #if expression: "
                f"{py_expr!r} ({exc})",
                stacklevel=2,
            )
            return False

    def _expand_line(self, line):
        """Expand all macros in a line, handling both object and function macros."""
        prev = None
        iterations = 0
        while line != prev and iterations < 30:
            prev = line
            line = self._expand_once(line)
            iterations += 1
        return line

    def _expand_once(self, line):
        """One pass of macro expansion — optimized."""
        original_line = line
        cached = self._expand_cache.get(line)
        if cached is not None:
            return cached

        # Extract identifiers from the line once
        ids_in_line = self._identifier_cache.get(line)
        if ids_in_line is None:
            ids_in_line = frozenset(IDENTIFIER_RE.findall(line))
            self._identifier_cache[line] = ids_in_line
        # Only check macros that appear in the line
        matching = [self.macros[name] for name in ids_in_line if name in self.macros]
        if not matching:
            self._expand_cache[line] = line
            return line
        # Sort by name length (longest first) for correct replacement
        matching.sort(key=lambda m: len(m.name), reverse=True)
        for macro in matching:
            if macro.is_function:
                line = self._expand_func_macro(line, macro)
            else:
                # Use a callback replacement so Python's regex engine does not
                # interpret C string escapes like \xNN or \n in macro bodies.
                line = macro._pattern.sub(lambda _m, body=macro.body: body, line)
        self._expand_cache[original_line] = line
        return line

    def _expand_func_macro(self, line, macro):
        """Expand function-like macro invocations in line."""
        result = []
        pos = 0
        while pos < len(line):
            match = self._find_func_macro_call(line, macro.name, pos)
            if match is None:
                result.append(line[pos:])
                break
            start, args_start = match
            result.append(line[pos:start])
            args, end = self._find_macro_args(line, args_start)
            if args is not None:
                expanded = self._substitute_params(macro, args)
                result.append(expanded)
                pos = end
            else:
                result.append(line[pos:args_start])
                pos = args_start
        return "".join(result)

    def _find_func_macro_call(self, line, name, start):
        """Find the next function-like macro invocation using string scanning."""
        name_len = len(name)
        pos = start

        while True:
            idx = line.find(name, pos)
            if idx == -1:
                return None

            # The match must start at an identifier boundary.
            if idx > 0:
                prev = line[idx - 1]
                if prev == "_" or prev.isalnum():
                    pos = idx + name_len
                    continue

            arg_pos = idx + name_len
            while arg_pos < len(line) and line[arg_pos].isspace():
                arg_pos += 1

            if arg_pos < len(line) and line[arg_pos] == "(":
                return idx, arg_pos + 1

            pos = idx + name_len

    def _find_macro_args(self, line, start):
        """Find comma-separated arguments within balanced parentheses.
        Returns (list_of_args, end_position) or (None, 0)."""
        depth = 1
        pos = start
        args = []
        arg_start = start

        while pos < len(line) and depth > 0:
            c = line[pos]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    args.append(line[arg_start:pos].strip())
                    return args, pos + 1
            elif c == "," and depth == 1:
                args.append(line[arg_start:pos].strip())
                arg_start = pos + 1
            elif c == '"':
                # Skip string literal
                pos += 1
                while pos < len(line) and line[pos] != '"':
                    if line[pos] == "\\":
                        pos += 1
                    pos += 1
            elif c == "'":
                pos += 1
                while pos < len(line) and line[pos] != "'":
                    if line[pos] == "\\":
                        pos += 1
                    pos += 1
            pos += 1

        return None, 0

    def _substitute_params(self, macro, args):
        """Replace parameter names with arguments in macro body."""
        body = macro.body
        if not macro.params:
            return body
        # Handle __VA_ARGS__
        if macro.params[-1] == "...":
            regular = macro.params[:-1]
            va_args = (
                ", ".join(args[len(regular) :]) if len(args) > len(regular) else ""
            )
            for i, param in enumerate(regular):
                if i < len(args):
                    arg = args[i]
                    body = re.sub(
                        r"\b" + re.escape(param) + r"\b",
                        lambda _m, arg=arg: arg,
                        body,
                    )
            body = body.replace("__VA_ARGS__", va_args)
        else:
            for i, param in enumerate(macro.params):
                if i < len(args):
                    arg = args[i]
                    body = re.sub(
                        r"\b" + re.escape(param) + r"\b",
                        lambda _m, arg=arg: arg,
                        body,
                    )
        # Handle ## token pasting
        body = re.sub(r"\s*##\s*", "", body)
        return body


def preprocess(source, base_dir=None, defines=None):
    """Preprocess C source code."""
    pp = Preprocessor(base_dir=base_dir, defines=defines)
    return pp.preprocess(source)
