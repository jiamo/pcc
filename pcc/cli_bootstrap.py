import os
import subprocess
import sys

from .py_frontend.pipeline import compile_python as _compile_python
from .py_frontend.pipeline import (
    run_python_multi_codegen_worker as _run_python_multi_codegen_worker,
)

_DEFAULT_EMIT_LL = "__PCC_DEFAULT_LL__"
_VALID_DIAGNOSTIC_FORMATS = ("text", "json", "sarif")

_HELP_TEXT = """Usage: pcc [OPTIONS] PATH

Bootstrap-oriented Python entry for pcc self-hosting.

Python inputs are compiled by this bootstrap binary. C/project inputs are
delegated to the full host pcc CLI; set PCC_HOST_PCC to override the host
entrypoint.

Options:
  -h, --help                Show this help message and exit.
  -m MODULE [ARGS...]       Run a host Python module through pcc's safe module shim.
  --backend BACKEND         Native emission backend: llvm or self.
  --python-libpython MODE   off (default), auto, or on for Python fallback linkage.
  --python-library          Emit a Python library module without @main.
  --ir-scaffold MODE        on (default), off, or auto. Enables Path A
                            closed-world IR-builder lowering (Issue 1).
  --emit-llvm[=PATH]        Emit LLVM IR instead of linking a native binary.
  -o PATH                   Output path for the compiled Python input.
  --pass NAME               Accept optimizer pass selection for host CLI parity.
  --disable-pass NAME       Accept optimizer pass disabling for host CLI parity.
  --diagnostic-format FMT   text (default), json, or sarif for hard errors.
  --profile-json PATH       Write compiler profile JSON.
  --explain-fallback        Include fallback routing details when known.
  --verbose                 Print Python pipeline timing.
  --pytest [ARGS...]        Run `uv run pytest` from this pcc1 process.
"""


def _write_text(text: str, *, err: bool = False, nl: bool = True) -> None:
    if nl:
        if text.endswith("\n"):
            if err:
                sys.stderr.write(text)
            else:
                sys.stdout.write(text)
        else:
            if err:
                sys.stderr.write(text + "\n")
            else:
                sys.stdout.write(text + "\n")
    else:
        if err:
            sys.stderr.write(text)
        else:
            sys.stdout.write(text)


def _normalized_sys_argv():
    argv = []
    i = 1
    while i < len(sys.argv):
        argv.append((sys.argv[i] or "") + "")
        i += 1
    return argv


def _copy_seq(values):
    out = []
    if values is None:
        return out
    i = 0
    while i < len(values):
        out.append(values[i])
        i += 1
    return out


def _is_pytest_request(argv) -> bool:
    if len(argv) == 0:
        return False
    first = argv[0]
    return first == "--pytest" or first == "pytest"


def _run_pytest_from_pcc1(argv) -> int:
    pytest_args = _copy_seq(argv[1:])
    if len(pytest_args) == 0:
        pytest_args.append("tests")

    cmd = [
        "env",
        "-u",
        "LC_ALL",
        "PCC1_BINARY=" + sys.executable,
        "uv",
        "run",
        "pytest",
    ]
    i = 0
    while i < len(pytest_args):
        cmd.append(pytest_args[i])
        i += 1

    try:
        subprocess.run(cmd, check=True)
    except Exception:
        _write_text("Error: pcc1 pytest run failed", err=True)
        return 1
    return 0


def _is_module_request(argv) -> bool:
    return len(argv) > 0 and argv[0] == "-m"


def _json_escape(text: str) -> str:
    out = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            out += "\\\\"
        elif ch == '"':
            out += '\\"'
        elif ch == "\n":
            out += "\\n"
        elif ch == "\r":
            out += "\\r"
        elif ch == "\t":
            out += "\\t"
        else:
            out += ch
        i += 1
    return out


def _json_str(text: str) -> str:
    return '"' + _json_escape(text) + '"'


def _json_str_or_null(text) -> str:
    if text is None:
        return "null"
    return _json_str(str(text))


def _json_str_list(items) -> str:
    out = "["
    i = 0
    while i < len(items):
        if i > 0:
            out += ", "
        out += _json_str(str(items[i]))
        i += 1
    out += "]"
    return out


def _json_int_list(items) -> str:
    out = "["
    i = 0
    while i < len(items):
        if i > 0:
            out += ", "
        out += str(int(items[i]))
        i += 1
    out += "]"
    return out


_PACKAGE_COMPAT_TARGETS = (
    ("pytest", "compat_python", "test runner compatibility target"),
    ("packaging", "compat_python", "pure-Python packaging metadata"),
    ("requests", "nolibpython_python", "pure-Python network stack smoke"),
    (
        "numpy",
        "c_extension_abi",
        "unchanged import via CPython C-API/extension ABI first",
    ),
    ("cffi", "c_extension_abi", "C FFI package target"),
    ("pybind11", "c_extension_abi", "C++ extension ABI target"),
    ("pandas", "c_extension_abi", "depends on NumPy ABI progress"),
)


def _package_compat_row_for_name(name: str):
    i = 0
    while i < len(_PACKAGE_COMPAT_TARGETS):
        row = _PACKAGE_COMPAT_TARGETS[i]
        if name == row[0]:
            return row
        i += 1
    return None


def _package_level_for_name(name: str):
    row = _package_compat_row_for_name(name)
    if row is None:
        return None
    return row[1]


def _package_summary_for_name(name: str):
    row = _package_compat_row_for_name(name)
    if row is None:
        return None
    return row[2]


def _sanitize_tag(text: str) -> str:
    out = ""
    i = 0
    while i < len(text):
        ch = text[i]
        ok = ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch == "_"
        out += ch if ok else "_"
        i += 1
    return out or "unknown"


def _native_current_platform_tag() -> str:
    # pcc1 cannot rely on host sysconfig; use an explicit tag when provided
    # and otherwise stay deterministic.
    env_tag = os.environ.get("PCC_PLATFORM_TAG")
    if env_tag:
        return env_tag
    return "unknown"


def _native_pcc_wheel_tag() -> str:
    return "pcc3-pcc_native-" + _native_current_platform_tag()


def _native_find_from(text: str, needle: str, start: int) -> int:
    if needle == "":
        return start
    i = start
    limit = len(text) - len(needle)
    while i <= limit:
        j = 0
        matched = True
        while j < len(needle):
            if text[i + j] != needle[j]:
                matched = False
                break
            j += 1
        if matched:
            return i
        i += 1
    return -1


def _native_range_endswith(text: str, start: int, end: int, suffix: str) -> bool:
    length = end - start
    if length < len(suffix):
        return False
    offset = end - len(suffix)
    i = 0
    while i < len(suffix):
        if text[offset + i] != suffix[i]:
            return False
        i += 1
    return True


def _native_source_kind(path) -> str:
    if path is None:
        return "unresolved"
    lower = path.lower()
    if os.path.isdir(path):
        return "local_source"
    if lower.endswith(".whl"):
        return "wheel"
    if (
        lower.endswith(".tar.gz")
        or lower.endswith(".tar.bz2")
        or lower.endswith(".tar.xz")
        or lower.endswith(".tgz")
        or lower.endswith(".zip")
    ):
        return "sdist"
    return "artifact"


def _native_compile_commands_json(root) -> str:
    path = None
    entries = 0
    c_entries = 0
    cxx_entries = 0
    fortran_entries = 0
    if root is not None:
        candidate = os.path.join(root, "compile_commands.json")
        if os.path.exists(candidate):
            path = candidate
            try:
                with open(candidate, "r") as fh:
                    text = fh.read()
            except Exception:
                text = ""
            pos = 0
            while True:
                key = _native_find_from(text, '"file"', pos)
                if key < 0:
                    break
                colon = _native_find_from(text, ":", key)
                first = _native_find_from(text, '"', colon + 1)
                second = _native_find_from(text, '"', first + 1)
                if colon >= 0 and first >= 0 and second >= 0:
                    entries += 1
                    if _native_range_endswith(text, first + 1, second, ".c"):
                        c_entries += 1
                    elif (
                        _native_range_endswith(text, first + 1, second, ".cc")
                        or _native_range_endswith(text, first + 1, second, ".cpp")
                        or _native_range_endswith(text, first + 1, second, ".cxx")
                    ):
                        cxx_entries += 1
                    elif (
                        _native_range_endswith(text, first + 1, second, ".f")
                        or _native_range_endswith(text, first + 1, second, ".for")
                        or _native_range_endswith(text, first + 1, second, ".f77")
                        or _native_range_endswith(text, first + 1, second, ".f90")
                        or _native_range_endswith(text, first + 1, second, ".f95")
                        or _native_range_endswith(text, first + 1, second, ".f03")
                        or _native_range_endswith(text, first + 1, second, ".f08")
                    ):
                        fortran_entries += 1
                    pos = second + 1
                else:
                    pos = key + 6
    out = "{"
    out += '"c_entries": ' + str(c_entries)
    out += ', "cxx_entries": ' + str(cxx_entries)
    out += ', "entries": ' + str(entries)
    out += ', "fortran_entries": ' + str(fortran_entries)
    out += ', "path": ' + _json_str_or_null(path)
    out += "}"
    return out


def _native_meson_allows_noblas(root) -> bool:
    if root is None:
        return False
    candidates = [
        os.path.join(root, "meson.options"),
        os.path.join(root, "meson_options.txt"),
    ]
    i = 0
    while i < len(candidates):
        path = candidates[i]
        if os.path.exists(path):
            try:
                with open(path, "r") as fh:
                    text = fh.read().lower()
            except Exception:
                text = ""
            pos = 0
            while True:
                pos = _native_find_from(text, "allow-noblas", pos)
                if pos < 0:
                    break
                end = _native_find_from(text, ")", pos)
                if end < 0:
                    end = pos + 240
                body = text[pos:end]
                if (
                    _native_find_from(body, "type", 0) >= 0
                    and _native_find_from(body, "boolean", 0) >= 0
                ):
                    value_pos = _native_find_from(body, "value", 0)
                    colon = _native_find_from(body, ":", value_pos)
                    value_text = body[colon : colon + 24] if colon >= 0 else ""
                    if (
                        value_pos >= 0
                        and colon >= 0
                        and _native_find_from(value_text, "true", 0) >= 0
                    ):
                        return True
                pos += 1
        i += 1
    return False


def _native_package_metadata_json(pkg_name: str, root) -> str:
    source_kind = _native_source_kind(root)
    pyproject_backend = None
    meson_build = False
    if root is not None and os.path.isdir(root):
        if os.path.exists(os.path.join(root, "meson.build")):
            meson_build = True
        if os.path.exists(os.path.join(root, "pyproject.toml")):
            pyproject_backend = "unknown"
    out = "{"
    out += '"abi_tag": null'
    out += ', "blas_indicators": []'
    out += ', "compile_commands": ' + _native_compile_commands_json(root)
    out += ', "current_platform_tag": ' + _json_str(_native_current_platform_tag())
    out += ', "cython_sources": []'
    out += ', "diagnostics": []'
    out += ', "fortran_sources": []'
    out += ', "generated_c_artifacts": []'
    out += ', "generated_c_policy": "none"'
    out += ', "lapack_indicators": []'
    out += ', "meson_build": ' + ("true" if meson_build else "false")
    out += ', "name": ' + _json_str(pkg_name)
    out += ', "native_extensions": []'
    if _native_meson_allows_noblas(root):
        out += ', "native_library_fallbacks": ["blas", "lapack"]'
    else:
        out += ', "native_library_fallbacks": []'
    out += ', "path": ' + _json_str_or_null(root)
    out += ', "pcc_native_wheel_tag": ' + _json_str(_native_pcc_wheel_tag())
    out += ', "platform_tag": null'
    out += ', "pyproject_build_backend": ' + _json_str_or_null(pyproject_backend)
    out += ', "pyproject_requires": []'
    out += ', "python_tag": null'
    out += ', "requires_cython_regeneration": false'
    out += ', "source_kind": ' + _json_str(source_kind)
    out += "}"
    return out


def _native_package_path(name: str, explicit_path):
    if explicit_path:
        return os.path.abspath(explicit_path)
    if os.path.exists(name):
        return os.path.abspath(name)
    projects = os.path.join(os.getcwd(), "projects")
    if not os.path.isdir(projects):
        return None
    prefix = name + "-"
    best = None
    try:
        names = os.listdir(projects)
    except Exception:
        return None
    i = 0
    while i < len(names):
        candidate = names[i]
        if candidate.startswith(prefix):
            full = os.path.join(projects, candidate)
            if os.path.isdir(full):
                if best is None or candidate > best:
                    best = candidate
        i += 1
    if best is None:
        return None
    return os.path.abspath(os.path.join(projects, best))


def _should_skip_package_dir(name: str) -> bool:
    return (
        name == ".git"
        or name == ".mypy_cache"
        or name == ".pytest_cache"
        or name == "__pycache__"
    )


def _should_skip_package_build_dir(name: str) -> bool:
    if _should_skip_package_dir(name):
        return True
    lowered = name.lower()
    while lowered.startswith("_"):
        lowered = lowered[1:]
    while lowered.endswith("_"):
        lowered = lowered[:-1]
    return (
        lowered == ".github"
        or lowered == ".spin"
        or lowered == "benchmark"
        or lowered == "benchmarks"
        or lowered == "doc"
        or lowered == "docs"
        or lowered == "example"
        or lowered == "examples"
        or lowered == "test"
        or lowered == "tests"
        or lowered == "tools"
        or lowered == "vendored-meson"
        or lowered.startswith(".")
    )


def _count_package_files(root: str, build_relevant: bool = False):
    counts = [0, 0, 0, 0, 0, 0, 0, 0]
    if root is None or not os.path.exists(root):
        return counts
    stack = [root]
    while len(stack) > 0:
        current = stack.pop()
        try:
            entries = os.listdir(current)
        except Exception:
            entries = []
        i = 0
        while i < len(entries):
            name = entries[i]
            full = os.path.join(current, name)
            if os.path.isdir(full):
                skip = (
                    _should_skip_package_build_dir(name)
                    if build_relevant
                    else _should_skip_package_dir(name)
                )
                if not skip:
                    stack.append(full)
            else:
                counts[0] += 1
                lowered = name.lower()
                if lowered.endswith(".py"):
                    counts[1] += 1
                elif lowered.endswith(".c"):
                    counts[2] += 1
                elif (
                    lowered.endswith(".cc")
                    or lowered.endswith(".cpp")
                    or lowered.endswith(".cxx")
                ):
                    counts[3] += 1
                elif lowered.endswith(".pyx"):
                    counts[4] += 1
                elif (
                    lowered.endswith(".f")
                    or lowered.endswith(".for")
                    or lowered.endswith(".f77")
                    or lowered.endswith(".f90")
                    or lowered.endswith(".f95")
                    or lowered.endswith(".f03")
                    or lowered.endswith(".f08")
                ):
                    counts[7] += 1
                elif (
                    lowered.endswith(".so")
                    or lowered.endswith(".pyd")
                    or lowered.endswith(".dylib")
                ):
                    counts[5] += 1
                elif (
                    lowered.endswith(".h")
                    or lowered.endswith(".hpp")
                    or lowered.endswith(".hh")
                ):
                    counts[6] += 1
            i += 1
    return counts


def _package_inspection_json(name: str, explicit_path=None) -> str:
    raw_name = (name or "").strip() or "package"
    root = _native_package_path(raw_name, explicit_path)
    pkg_name = raw_name
    if explicit_path is None and root is not None and os.path.exists(raw_name):
        pkg_name = _native_package_basename(root)
    counts = _count_package_files(root)
    level = _package_level_for_name(pkg_name)
    summary = _package_summary_for_name(pkg_name)
    out = "{"
    out += '"c_files": ' + str(counts[2])
    out += ', "cxx_files": ' + str(counts[3])
    out += ', "files": ' + str(counts[0])
    out += ', "header_files": ' + str(counts[6])
    out += ', "name": ' + _json_str(pkg_name)
    out += ', "package_level": ' + _json_str_or_null(level)
    out += ', "package_summary": ' + _json_str_or_null(summary)
    out += ', "path": ' + _json_str_or_null(root)
    out += ', "python_files": ' + str(counts[1])
    out += ', "pyx_files": ' + str(counts[4])
    out += ', "shared_objects": ' + str(counts[5])
    out += ', "smoke_tests": []'
    out += ', "artifact_metadata": ' + _native_package_metadata_json(pkg_name, root)
    out += "}"
    return out


def _native_build_plan_json(name: str, explicit_path=None) -> str:
    pkg_name = (name or "").strip() or "package"
    root = _native_package_path(pkg_name, explicit_path)
    counts = _count_package_files(root, True)
    compile_commands = _native_compile_commands_json(root)
    actions = []
    diagnostics = []
    if root is not None and os.path.exists(os.path.join(root, "compile_commands.json")):
        actions.append("consume_compile_commands")
    elif root is not None:
        diagnostics.append("compile_commands_missing")
    if counts[4] > 0 and counts[2] == 0:
        diagnostics.append("cython_regeneration_required")
    if counts[4] > 0 and counts[2] > 0:
        actions.append("consume_generated_c")
    if counts[7] > 0:
        actions.append("delegate_fortran_toolchain")
        diagnostics.append("fortran_toolchain_required")
    if root is not None and os.path.exists(os.path.join(root, "meson.build")):
        actions.append("consume_meson_plan")
    if counts[5] > 0:
        diagnostics.append("prebuilt_native_extension_requires_abi_check")

    # pcc1 keeps this native path intentionally conservative: it reports the
    # same schema as the host build-plan command without importing json/shlex.
    out = "{"
    out += '"actions": ' + _json_str_list(actions)
    out += ', "commands": []'
    out += ', "diagnostics": ' + _json_str_list(diagnostics)
    out += ', "metadata": ' + _native_package_metadata_json(pkg_name, root)
    out += ', "name": ' + _json_str(pkg_name)
    out += ', "ok": true'
    out += ', "path": ' + _json_str_or_null(root)
    out += ', "source_kind": ' + _json_str(_native_source_kind(root))
    out += ', "source_summary": {'
    out += '"c": ' + str(counts[2])
    out += ', "cxx": ' + str(counts[3])
    out += ', "fortran": ' + str(counts[7])
    out += ', "unknown": 0'
    out += "}"
    out += ', "compile_commands": ' + compile_commands
    out += "}"
    return out


def _native_known_capi_header(symbol: str):
    if (
        symbol == "Py_Initialize"
        or symbol == "Py_UNUSED"
        or symbol == "PyOS_snprintf"
        or symbol == "PyOS_vsnprintf"
    ):
        return "Python.h"
    if symbol == "Py_REFCNT" or symbol == "Py_SET_REFCNT":
        return "object.h"
    if symbol == "PyMapping_Size" or symbol == "PyMapping_Length":
        return "abstract.h"
    if (
        symbol == "PyMapping_Keys"
        or symbol == "PyMapping_Values"
        or symbol == "PyMapping_Items"
    ):
        return "abstract.h"
    if symbol == "PyObject_LengthHint":
        return "abstract.h"
    if (
        symbol == "PySequence_SetItem"
        or symbol == "PySequence_Concat"
        or symbol == "PySequence_Repeat"
        or symbol == "PySequence_InPlaceConcat"
        or symbol == "PySequence_InPlaceRepeat"
    ):
        return "abstract.h"
    if (
        symbol == "PyMem_Malloc"
        or symbol == "PyMem_Calloc"
        or symbol == "PyMem_Realloc"
        or symbol == "PyMem_Free"
        or symbol == "PyMem_RawMalloc"
        or symbol == "PyMem_RawCalloc"
        or symbol == "PyMem_RawRealloc"
        or symbol == "PyMem_RawFree"
        or symbol == "PyMem_FREE"
    ):
        return "pymem.h"
    if (
        symbol == "PyModule_Create"
        or symbol == "PyModule_Create2"
        or symbol == "PyModule_AddObject"
        or symbol == "PyModule_AddObjectRef"
        or symbol == "PyModule_Add"
        or symbol == "PyModule_AddIntConstant"
        or symbol == "PyModule_AddStringConstant"
        or symbol == "PyModule_GetDict"
    ):
        return "moduleobject.h"
    if (
        symbol == "PyArg_ParseTuple"
        or symbol == "PyArg_ParseTupleAndKeywords"
        or symbol == "Py_BuildValue"
    ):
        return "modsupport.h"
    if symbol == "PyLong_FromDouble" or symbol == "PyLong_AsDouble":
        return "longobject.h"
    if symbol == "PyFloat_AS_DOUBLE":
        return "floatobject.h"
    if (
        symbol == "PyNumber_Check"
        or symbol == "PyNumber_Long"
        or symbol == "PyNumber_Float"
        or symbol == "PyNumber_And"
        or symbol == "PyNumber_Or"
    ):
        return "abstract.h"
    if (
        symbol == "PyNumber_Xor"
        or symbol == "PyNumber_Invert"
        or symbol == "PyNumber_Lshift"
        or symbol == "PyNumber_Rshift"
    ):
        return "abstract.h"
    if (
        symbol == "PySet_New"
        or symbol == "PySet_Add"
        or symbol == "PySet_Contains"
        or symbol == "PySet_Discard"
        or symbol == "PySet_Size"
    ):
        return "setobject.h"
    if (
        symbol == "PySet_GET_SIZE"
        or symbol == "PySet_Check"
        or symbol == "PySet_CheckExact"
        or symbol == "PyAnySet_Check"
        or symbol == "PyAnySet_CheckExact"
    ):
        return "setobject.h"
    if symbol == "PyList_AsTuple":
        return "listobject.h"
    if symbol == "PyDict_Keys" or symbol == "PyDict_Values" or symbol == "PyDict_Items":
        return "dictobject.h"
    if (
        symbol == "PyLong_FromLong"
        or symbol == "PyLong_FromUnsignedLong"
        or symbol == "PyLong_AsLong"
        or symbol == "PyLong_FromLongLong"
        or symbol == "PyLong_FromUnsignedLongLong"
        or symbol == "PyLong_FromInt32"
        or symbol == "PyLong_FromInt64"
        or symbol == "PyLong_FromUInt32"
        or symbol == "PyLong_FromUInt64"
        or symbol == "PyLong_FromVoidPtr"
        or symbol == "PyLong_FromSsize_t"
        or symbol == "PyLong_FromSize_t"
        or symbol == "PyLong_AsLongLong"
        or symbol == "PyLong_AsInt"
        or symbol == "PyLong_AsInt32"
        or symbol == "PyLong_AsInt64"
        or symbol == "PyLong_AsUInt32"
        or symbol == "PyLong_AsUInt64"
        or symbol == "PyLong_AsVoidPtr"
        or symbol == "PyLong_AsLongAndOverflow"
        or symbol == "PyLong_AsUnsignedLong"
        or symbol == "PyLong_AsUnsignedLongLong"
        or symbol == "PyLong_AsUnsignedLongLongMask"
        or symbol == "PyLong_AsSsize_t"
        or symbol == "PyLong_AsSize_t"
        or symbol == "PyLong_Check"
        or symbol == "PyLong_CheckExact"
    ):
        return "longobject.h"
    if symbol == "PyBool_FromLong" or symbol == "PyBool_Check":
        return "boolobject.h"
    if (
        symbol == "PyFloat_FromDouble"
        or symbol == "PyFloat_AsDouble"
        or symbol == "PyFloat_Check"
        or symbol == "PyFloat_CheckExact"
    ):
        return "floatobject.h"
    if (
        symbol == "Py_complex"
        or symbol == "PyComplex_FromDoubles"
        or symbol == "PyComplex_FromCComplex"
        or symbol == "PyComplex_AsCComplex"
        or symbol == "PyComplex_RealAsDouble"
        or symbol == "PyComplex_ImagAsDouble"
        or symbol == "PyComplex_Check"
        or symbol == "PyComplex_CheckExact"
    ):
        return "complexobject.h"
    if (
        symbol == "PyUnicode_AsUTF8String"
        or symbol == "PyUnicode_AsASCIIString"
        or symbol == "PyUnicode_AsEncodedString"
        or symbol == "PyUnicode_FromKindAndData"
        or symbol == "PyUnicode_FromOrdinal"
        or symbol == "PyUnicode_AsUCS4"
        or symbol == "PyUnicode_AsUCS4Copy"
        or symbol == "PyUnicode_Tailmatch"
        or symbol == "PyUnicode_Find"
        or symbol == "PyUnicode_ReadChar"
        or symbol == "PyUnicode_FindChar"
        or symbol == "PyUnicode_Count"
        or symbol == "PyUnicode_Replace"
        or symbol == "PyUnicode_Substring"
        or symbol == "PyUnicode_Contains"
        or symbol == "PyUnicode_Concat"
    ):
        return "unicodeobject.h"
    if (
        symbol == "Py_UCS1"
        or symbol == "Py_UCS2"
        or symbol == "PyUnicode_1BYTE_KIND"
        or symbol == "PyUnicode_2BYTE_KIND"
        or symbol == "PyUnicode_4BYTE_KIND"
    ):
        return "unicodeobject.h"
    if symbol == "PyObject_SelfIter":
        return "object.h"
    if symbol == "PyIter_NextItem":
        return "abstract.h"
    if symbol == "PyErr_Print" or symbol == "PyErr_CheckSignals":
        return "pyerrors.h"
    if (
        symbol == "Py_UCS4"
        or symbol == "PyUnicode_FromString"
        or symbol == "PyUnicode_FromStringAndSize"
        or symbol == "PyUnicode_FromFormat"
        or symbol == "PyUnicode_FromFormatV"
        or symbol == "PyUnicode_InternFromString"
        or symbol == "PyUnicode_FromEncodedObject"
        or symbol == "PyUnicode_AsUTF8"
        or symbol == "PyUnicode_AsUTF8AndSize"
        or symbol == "PyUnicode_Check"
        or symbol == "PyUnicode_CheckExact"
        or symbol == "PyUnicode_GetLength"
        or symbol == "PyUnicode_GET_LENGTH"
        or symbol == "PyUnicode_Compare"
        or symbol == "PyUnicode_CompareWithASCIIString"
        or symbol == "PyUnicode_EqualToUTF8"
        or symbol == "PyUnicode_EqualToUTF8AndSize"
        or symbol == "Py_UNICODE_ISSPACE"
        or symbol == "Py_UNICODE_ISDIGIT"
        or symbol == "Py_UNICODE_ISDECIMAL"
        or symbol == "Py_UNICODE_ISNUMERIC"
        or symbol == "Py_UNICODE_ISLOWER"
        or symbol == "Py_UNICODE_ISUPPER"
        or symbol == "Py_UNICODE_ISTITLE"
        or symbol == "Py_UNICODE_ISALPHA"
        or symbol == "Py_UNICODE_ISALNUM"
    ):
        return "unicodeobject.h"
    if (
        symbol == "PyErr_SetString"
        or symbol == "PyErr_SetNone"
        or symbol == "PyErr_SetObject"
        or symbol == "PyErr_Format"
        or symbol == "PyErr_FormatV"
        or symbol == "PyErr_NoMemory"
        or symbol == "PyErr_SetFromErrno"
        or symbol == "PyErr_SetFromErrnoWithFilenameObject"
        or symbol == "PyErr_NewException"
        or symbol == "PyErr_BadInternalCall"
        or symbol == "PyErr_WarnEx"
        or symbol == "PyErr_WarnFormat"
        or symbol == "PyErr_WriteUnraisable"
        or symbol == "PyErr_Occurred"
        or symbol == "PyErr_Clear"
        or symbol == "PyErr_GivenExceptionMatches"
        or symbol == "PyErr_ExceptionMatches"
        or symbol == "PyErr_Fetch"
        or symbol == "PyErr_Restore"
        or symbol == "PyExc_BaseException"
        or symbol == "PyExc_Exception"
        or symbol == "PyExc_ValueError"
        or symbol == "PyExc_TypeError"
        or symbol == "PyExc_RuntimeError"
        or symbol == "PyExc_KeyError"
        or symbol == "PyExc_IndexError"
        or symbol == "PyExc_AttributeError"
        or symbol == "PyExc_MemoryError"
        or symbol == "PyExc_OverflowError"
        or symbol == "PyExc_SystemError"
        or symbol == "PyExc_NameError"
        or symbol == "PyExc_NotImplementedError"
        or symbol == "PyExc_ArithmeticError"
        or symbol == "PyExc_LookupError"
        or symbol == "PyExc_OSError"
        or symbol == "PyExc_IOError"
        or symbol == "PyExc_AssertionError"
        or symbol == "PyExc_StopIteration"
        or symbol == "PyExc_StopAsyncIteration"
        or symbol == "PyExc_ZeroDivisionError"
        or symbol == "PyExc_ReferenceError"
        or symbol == "PyExc_BufferError"
        or symbol == "PyExc_ImportError"
        or symbol == "PyExc_ImportWarning"
        or symbol == "PyExc_FloatingPointError"
        or symbol == "PyExc_RecursionError"
        or symbol == "PyExc_UnicodeDecodeError"
        or symbol == "PyExc_Warning"
        or symbol == "PyExc_UserWarning"
        or symbol == "PyExc_RuntimeWarning"
        or symbol == "PyExc_DeprecationWarning"
        or symbol == "PyExc_FutureWarning"
    ):
        return "pyerrors.h"
    if (
        symbol == "PyObject_Call"
        or symbol == "PyObject_CallObject"
        or symbol == "PyObject_CallNoArgs"
        or symbol == "PyObject_CallOneArg"
        or symbol == "PyObject_Vectorcall"
        or symbol == "PyObject_VectorcallMethod"
        or symbol == "PyObject_CallFunction"
        or symbol == "PyObject_CallMethod"
        or symbol == "PyObject_CallMethodNoArgs"
        or symbol == "PyObject_CallMethodOneArg"
        or symbol == "PyObject_CallFunctionObjArgs"
        or symbol == "PyObject_GetIter"
        or symbol == "PyIter_Next"
        or symbol == "PyIter_Check"
        or symbol == "PyNumber_Add"
        or symbol == "PyNumber_Subtract"
        or symbol == "PyNumber_Multiply"
        or symbol == "PyNumber_TrueDivide"
        or symbol == "PyNumber_FloorDivide"
        or symbol == "PyNumber_Remainder"
        or symbol == "PyNumber_Power"
        or symbol == "PyNumber_Negative"
        or symbol == "PyNumber_Positive"
        or symbol == "PyNumber_Absolute"
        or symbol == "PyNumber_Index"
        or symbol == "PyNumber_AsSsize_t"
        or symbol == "PyIndex_Check"
        or symbol == "PyObject_GetBuffer"
        or symbol == "PyObject_CheckBuffer"
        or symbol == "PyBuffer_Release"
        or symbol == "PySequence_Check"
        or symbol == "PyMapping_Check"
        or symbol == "PyMapping_GetItemString"
        or symbol == "PyMapping_SetItemString"
        or symbol == "PyMapping_HasKey"
        or symbol == "PyMapping_HasKeyString"
        or symbol == "PyMapping_GetOptionalItem"
        or symbol == "PyMapping_GetOptionalItemString"
        or symbol == "PyMapping_HasKeyWithError"
        or symbol == "PyMapping_HasKeyStringWithError"
        or symbol == "PySequence_Size"
        or symbol == "PySequence_Length"
        or symbol == "PySequence_GetItem"
        or symbol == "PySequence_Contains"
        or symbol == "PySequence_Fast"
        or symbol == "PySequence_Fast_GET_SIZE"
        or symbol == "PySequence_Fast_ITEMS"
        or symbol == "PySequence_Fast_GET_ITEM"
        or symbol == "PySequence_List"
        or symbol == "PySequence_Tuple"
    ):
        return "abstract.h"
    if (
        symbol == "Py_Is"
        or symbol == "Py_IsNone"
        or symbol == "Py_IsTrue"
        or symbol == "Py_IsFalse"
        or symbol == "Py_PRINT_RAW"
        or symbol == "PyObject_Print"
    ):
        return "object.h"
    if (
        symbol == "Py_INCREF"
        or symbol == "Py_DECREF"
        or symbol == "Py_XINCREF"
        or symbol == "Py_XDECREF"
        or symbol == "Py_NewRef"
        or symbol == "Py_XNewRef"
        or symbol == "Py_CLEAR"
        or symbol == "Py_SETREF"
        or symbol == "Py_XSETREF"
        or symbol == "Py_None"
        or symbol == "Py_True"
        or symbol == "Py_False"
        or symbol == "Py_NotImplemented"
        or symbol == "Py_RETURN_NONE"
        or symbol == "Py_RETURN_TRUE"
        or symbol == "Py_RETURN_FALSE"
        or symbol == "Py_RETURN_NOTIMPLEMENTED"
        or symbol == "Py_UNUSED"
        or symbol == "PyOS_snprintf"
        or symbol == "PyOS_vsnprintf"
        or symbol == "PyObject_GetAttrString"
        or symbol == "PyObject_GetAttr"
        or symbol == "PyObject_GetOptionalAttr"
        or symbol == "PyObject_GetOptionalAttrString"
        or symbol == "PyObject_SetAttrString"
        or symbol == "PyObject_SetAttr"
        or symbol == "PyObject_HasAttr"
        or symbol == "PyObject_HasAttrString"
        or symbol == "PyObject_HasAttrWithError"
        or symbol == "PyObject_HasAttrStringWithError"
        or symbol == "PyObject_IsTrue"
        or symbol == "PyObject_Not"
        or symbol == "PyObject_Hash"
        or symbol == "PyCallable_Check"
        or symbol == "PyObject_Str"
        or symbol == "PyObject_Repr"
        or symbol == "PyObject_Bytes"
        or symbol == "PyObject_Format"
        or symbol == "PyObject_Type"
        or symbol == "PyObject_IsInstance"
        or symbol == "PyObject_RichCompare"
        or symbol == "PyObject_RichCompareBool"
        or symbol == "PyObject_GetItem"
        or symbol == "PyObject_SetItem"
        or symbol == "PyObject_DelItem"
        or symbol == "PyObject_Size"
        or symbol == "PyObject_Length"
        or symbol == "PyObject_Malloc"
        or symbol == "PyObject_Calloc"
        or symbol == "PyObject_Realloc"
        or symbol == "PyObject_Free"
        or symbol == "PyObject_MALLOC"
        or symbol == "PyObject_REALLOC"
        or symbol == "PyObject_FREE"
        or symbol == "PyObject_Del"
        or symbol == "PyObject_DEL"
    ):
        return "object.h"
    if (
        symbol == "PyTuple_New"
        or symbol == "PyTuple_SetItem"
        or symbol == "PyTuple_GetItem"
        or symbol == "PyTuple_Size"
        or symbol == "PyTuple_GET_ITEM"
        or symbol == "PyTuple_GET_SIZE"
        or symbol == "PyTuple_SET_ITEM"
        or symbol == "PyTuple_Pack"
        or symbol == "PyTuple_Check"
        or symbol == "PyTuple_CheckExact"
    ):
        return "tupleobject.h"
    if (
        symbol == "PyList_New"
        or symbol == "PyList_SetItem"
        or symbol == "PyList_GetItem"
        or symbol == "PyList_GetItemRef"
        or symbol == "PyList_Size"
        or symbol == "PyList_GET_ITEM"
        or symbol == "PyList_GET_SIZE"
        or symbol == "PyList_SET_ITEM"
        or symbol == "PyList_Append"
        or symbol == "PyList_Check"
        or symbol == "PyList_CheckExact"
    ):
        return "listobject.h"
    if (
        symbol == "PyDict_New"
        or symbol == "PyDict_SetItem"
        or symbol == "PyDict_SetItemString"
        or symbol == "PyDict_GetItem"
        or symbol == "PyDict_GetItemString"
        or symbol == "PyDict_GetItemWithError"
        or symbol == "PyDict_GetItemRef"
        or symbol == "PyDict_GetItemStringRef"
        or symbol == "PyDict_SetDefaultRef"
        or symbol == "PyDict_Pop"
        or symbol == "PyDict_PopString"
        or symbol == "PyDict_DelItem"
        or symbol == "PyDict_DelItemString"
        or symbol == "PyDict_Size"
        or symbol == "PyDict_Contains"
        or symbol == "PyDict_ContainsString"
        or symbol == "PyDict_Next"
        or symbol == "PyDict_Check"
        or symbol == "PyDict_CheckExact"
    ):
        return "dictobject.h"
    if (
        symbol == "PyBytes_FromString"
        or symbol == "PyBytes_FromStringAndSize"
        or symbol == "PyBytes_AsString"
        or symbol == "PyBytes_AsStringAndSize"
        or symbol == "PyBytes_AS_STRING"
        or symbol == "PyBytes_Size"
        or symbol == "PyBytes_GET_SIZE"
        or symbol == "PyBytes_Check"
        or symbol == "PyBytes_CheckExact"
    ):
        return "bytesobject.h"
    if symbol == "PyImport_ImportModule":
        return "import.h"
    if (
        symbol == "PyCapsule_New"
        or symbol == "PyCapsule_GetPointer"
        or symbol == "PyCapsule_GetName"
        or symbol == "PyCapsule_GetContext"
        or symbol == "PyCapsule_IsValid"
        or symbol == "PyCapsule_CheckExact"
        or symbol == "PyCapsule_SetContext"
        or symbol == "PyCapsule_SetName"
        or symbol == "PyCapsule_Import"
    ):
        return "pycapsule.h"
    if (
        symbol == "PyMemoryView_FromObject"
        or symbol == "PyMemoryView_FromMemory"
        or symbol == "PyMemoryView_Check"
        or symbol == "PyMemoryView_GET_BUFFER"
        or symbol == "PyMemoryView_GET_BASE"
    ):
        return "memoryobject.h"
    if symbol == "Py_IsInitialized":
        return "pylifecycle.h"
    if (
        symbol == "PyGILState_Ensure"
        or symbol == "PyGILState_Release"
        or symbol == "PyGILState_Check"
    ):
        return "pystate.h"
    if (
        symbol.startswith("PyArray_")
        or symbol == "PyArray_API"
        or symbol == "PyArray_Type"
        or symbol == "PyArrayDescr_Type"
    ):
        return "numpy/arrayobject.h"
    if symbol.startswith("PyUFunc_"):
        return "numpy/ufuncobject.h"
    return None


def _native_capi_implemented(symbol: str) -> bool:
    if (
        symbol == "Py_Is"
        or symbol == "Py_IsNone"
        or symbol == "Py_IsTrue"
        or symbol == "Py_IsFalse"
        or symbol == "Py_PRINT_RAW"
        or symbol == "PyObject_Print"
    ):
        return True
    if symbol == "PyLong_FromDouble":
        return True
    if symbol == "PyErr_Print" or symbol == "PyErr_CheckSignals":
        return True
    if (
        symbol == "PyUnicode_AsUTF8String"
        or symbol == "PyUnicode_AsASCIIString"
        or symbol == "PyUnicode_AsEncodedString"
        or symbol == "PyUnicode_FromKindAndData"
        or symbol == "PyUnicode_FromOrdinal"
        or symbol == "PyUnicode_AsUCS4"
        or symbol == "PyUnicode_AsUCS4Copy"
        or symbol == "PyUnicode_Tailmatch"
        or symbol == "PyUnicode_Find"
        or symbol == "PyUnicode_ReadChar"
        or symbol == "PyUnicode_FindChar"
        or symbol == "PyUnicode_Count"
        or symbol == "PyUnicode_Replace"
        or symbol == "PyUnicode_Substring"
        or symbol == "PyUnicode_Contains"
        or symbol == "PyUnicode_Concat"
    ):
        return True
    if (
        symbol == "Py_UCS1"
        or symbol == "Py_UCS2"
        or symbol == "PyUnicode_1BYTE_KIND"
        or symbol == "PyUnicode_2BYTE_KIND"
        or symbol == "PyUnicode_4BYTE_KIND"
    ):
        return True
    if symbol == "Py_REFCNT" or symbol == "Py_SET_REFCNT":
        return True
    if symbol == "PyMapping_Size" or symbol == "PyMapping_Length":
        return True
    if (
        symbol == "PyMapping_Keys"
        or symbol == "PyMapping_Values"
        or symbol == "PyMapping_Items"
    ):
        return True
    if symbol == "PyObject_LengthHint":
        return True
    if symbol == "PyObject_SelfIter":
        return True
    if symbol == "PyIter_NextItem":
        return True
    if (
        symbol == "PySequence_SetItem"
        or symbol == "PySequence_Concat"
        or symbol == "PySequence_Repeat"
        or symbol == "PySequence_InPlaceConcat"
        or symbol == "PySequence_InPlaceRepeat"
    ):
        return True
    if (
        symbol == "PyLong_AsDouble"
        or symbol == "PyFloat_AS_DOUBLE"
        or symbol == "PyNumber_Check"
        or symbol == "PyNumber_Long"
        or symbol == "PyNumber_Float"
    ):
        return True
    if (
        symbol == "PyNumber_And"
        or symbol == "PyNumber_Or"
        or symbol == "PyNumber_Xor"
        or symbol == "PyNumber_Invert"
        or symbol == "PyNumber_Lshift"
        or symbol == "PyNumber_Rshift"
    ):
        return True
    if (
        symbol == "PySet_New"
        or symbol == "PySet_Add"
        or symbol == "PySet_Contains"
        or symbol == "PySet_Discard"
        or symbol == "PySet_Size"
    ):
        return True
    if (
        symbol == "PySet_GET_SIZE"
        or symbol == "PySet_Check"
        or symbol == "PySet_CheckExact"
        or symbol == "PyAnySet_Check"
        or symbol == "PyAnySet_CheckExact"
    ):
        return True
    if symbol == "PyList_AsTuple":
        return True
    if symbol == "PyDict_Keys" or symbol == "PyDict_Values" or symbol == "PyDict_Items":
        return True
    return (
        symbol == "Py_Initialize"
        or symbol == "Py_INCREF"
        or symbol == "Py_DECREF"
        or symbol == "Py_XINCREF"
        or symbol == "Py_XDECREF"
        or symbol == "Py_NewRef"
        or symbol == "Py_XNewRef"
        or symbol == "Py_CLEAR"
        or symbol == "Py_SETREF"
        or symbol == "Py_XSETREF"
        or symbol == "Py_None"
        or symbol == "Py_True"
        or symbol == "Py_False"
        or symbol == "Py_NotImplemented"
        or symbol == "Py_RETURN_NONE"
        or symbol == "Py_RETURN_TRUE"
        or symbol == "Py_RETURN_FALSE"
        or symbol == "Py_RETURN_NOTIMPLEMENTED"
        or symbol == "Py_UNUSED"
        or symbol == "PyOS_snprintf"
        or symbol == "PyOS_vsnprintf"
        or symbol == "PyMem_Malloc"
        or symbol == "PyMem_Calloc"
        or symbol == "PyMem_Realloc"
        or symbol == "PyMem_Free"
        or symbol == "PyMem_RawMalloc"
        or symbol == "PyMem_RawCalloc"
        or symbol == "PyMem_RawRealloc"
        or symbol == "PyMem_RawFree"
        or symbol == "PyMem_FREE"
        or symbol == "PyObject_Malloc"
        or symbol == "PyObject_Calloc"
        or symbol == "PyObject_Realloc"
        or symbol == "PyObject_Free"
        or symbol == "PyObject_MALLOC"
        or symbol == "PyObject_REALLOC"
        or symbol == "PyObject_FREE"
        or symbol == "PyObject_Del"
        or symbol == "PyObject_DEL"
        or symbol == "PyModule_Create"
        or symbol == "PyModule_Create2"
        or symbol == "PyModule_AddObject"
        or symbol == "PyModule_AddObjectRef"
        or symbol == "PyModule_Add"
        or symbol == "PyModule_AddIntConstant"
        or symbol == "PyModule_AddStringConstant"
        or symbol == "PyModule_GetDict"
        or symbol == "PyArg_ParseTuple"
        or symbol == "PyArg_ParseTupleAndKeywords"
        or symbol == "Py_BuildValue"
        or symbol == "PyLong_FromLong"
        or symbol == "PyLong_FromUnsignedLong"
        or symbol == "PyLong_AsLong"
        or symbol == "PyLong_FromLongLong"
        or symbol == "PyLong_FromUnsignedLongLong"
        or symbol == "PyLong_FromInt32"
        or symbol == "PyLong_FromInt64"
        or symbol == "PyLong_FromUInt32"
        or symbol == "PyLong_FromUInt64"
        or symbol == "PyLong_FromVoidPtr"
        or symbol == "PyLong_FromSsize_t"
        or symbol == "PyLong_FromSize_t"
        or symbol == "PyLong_AsLongLong"
        or symbol == "PyLong_AsInt"
        or symbol == "PyLong_AsInt32"
        or symbol == "PyLong_AsInt64"
        or symbol == "PyLong_AsUInt32"
        or symbol == "PyLong_AsUInt64"
        or symbol == "PyLong_AsVoidPtr"
        or symbol == "PyLong_AsLongAndOverflow"
        or symbol == "PyLong_AsUnsignedLong"
        or symbol == "PyLong_AsUnsignedLongLong"
        or symbol == "PyLong_AsUnsignedLongLongMask"
        or symbol == "PyLong_AsSsize_t"
        or symbol == "PyLong_AsSize_t"
        or symbol == "PyLong_Check"
        or symbol == "PyLong_CheckExact"
        or symbol == "PyBool_FromLong"
        or symbol == "PyBool_Check"
        or symbol == "PyFloat_FromDouble"
        or symbol == "PyFloat_AsDouble"
        or symbol == "PyFloat_Check"
        or symbol == "PyFloat_CheckExact"
        or symbol == "Py_complex"
        or symbol == "PyComplex_FromDoubles"
        or symbol == "PyComplex_FromCComplex"
        or symbol == "PyComplex_AsCComplex"
        or symbol == "PyComplex_RealAsDouble"
        or symbol == "PyComplex_ImagAsDouble"
        or symbol == "PyComplex_Check"
        or symbol == "PyComplex_CheckExact"
        or symbol == "Py_UCS4"
        or symbol == "PyUnicode_FromString"
        or symbol == "PyUnicode_FromStringAndSize"
        or symbol == "PyUnicode_FromFormat"
        or symbol == "PyUnicode_FromFormatV"
        or symbol == "PyUnicode_InternFromString"
        or symbol == "PyUnicode_FromEncodedObject"
        or symbol == "PyUnicode_AsUTF8"
        or symbol == "PyUnicode_AsUTF8AndSize"
        or symbol == "PyUnicode_Check"
        or symbol == "PyUnicode_CheckExact"
        or symbol == "PyUnicode_GetLength"
        or symbol == "PyUnicode_GET_LENGTH"
        or symbol == "PyUnicode_Compare"
        or symbol == "PyUnicode_CompareWithASCIIString"
        or symbol == "PyUnicode_EqualToUTF8"
        or symbol == "PyUnicode_EqualToUTF8AndSize"
        or symbol == "Py_UNICODE_ISSPACE"
        or symbol == "Py_UNICODE_ISDIGIT"
        or symbol == "Py_UNICODE_ISDECIMAL"
        or symbol == "Py_UNICODE_ISNUMERIC"
        or symbol == "Py_UNICODE_ISLOWER"
        or symbol == "Py_UNICODE_ISUPPER"
        or symbol == "Py_UNICODE_ISTITLE"
        or symbol == "Py_UNICODE_ISALPHA"
        or symbol == "Py_UNICODE_ISALNUM"
        or symbol == "PyErr_SetString"
        or symbol == "PyErr_SetNone"
        or symbol == "PyErr_SetObject"
        or symbol == "PyErr_Format"
        or symbol == "PyErr_FormatV"
        or symbol == "PyErr_NoMemory"
        or symbol == "PyErr_SetFromErrno"
        or symbol == "PyErr_SetFromErrnoWithFilenameObject"
        or symbol == "PyErr_NewException"
        or symbol == "PyErr_BadInternalCall"
        or symbol == "PyErr_WarnEx"
        or symbol == "PyErr_WarnFormat"
        or symbol == "PyErr_WriteUnraisable"
        or symbol == "PyErr_Occurred"
        or symbol == "PyErr_Clear"
        or symbol == "PyErr_GivenExceptionMatches"
        or symbol == "PyErr_ExceptionMatches"
        or symbol == "PyErr_Fetch"
        or symbol == "PyErr_Restore"
        or symbol == "PyExc_BaseException"
        or symbol == "PyExc_Exception"
        or symbol == "PyExc_ValueError"
        or symbol == "PyExc_TypeError"
        or symbol == "PyExc_RuntimeError"
        or symbol == "PyExc_KeyError"
        or symbol == "PyExc_IndexError"
        or symbol == "PyExc_AttributeError"
        or symbol == "PyExc_MemoryError"
        or symbol == "PyExc_OverflowError"
        or symbol == "PyExc_SystemError"
        or symbol == "PyExc_NameError"
        or symbol == "PyExc_NotImplementedError"
        or symbol == "PyExc_ArithmeticError"
        or symbol == "PyExc_LookupError"
        or symbol == "PyExc_OSError"
        or symbol == "PyExc_IOError"
        or symbol == "PyExc_AssertionError"
        or symbol == "PyExc_StopIteration"
        or symbol == "PyExc_StopAsyncIteration"
        or symbol == "PyExc_ZeroDivisionError"
        or symbol == "PyExc_ReferenceError"
        or symbol == "PyExc_BufferError"
        or symbol == "PyExc_ImportError"
        or symbol == "PyExc_ImportWarning"
        or symbol == "PyExc_FloatingPointError"
        or symbol == "PyExc_RecursionError"
        or symbol == "PyExc_UnicodeDecodeError"
        or symbol == "PyExc_Warning"
        or symbol == "PyExc_UserWarning"
        or symbol == "PyExc_RuntimeWarning"
        or symbol == "PyExc_DeprecationWarning"
        or symbol == "PyExc_FutureWarning"
        or symbol == "PyObject_Call"
        or symbol == "PyObject_CallObject"
        or symbol == "PyObject_CallNoArgs"
        or symbol == "PyObject_CallOneArg"
        or symbol == "PyObject_Vectorcall"
        or symbol == "PyObject_VectorcallMethod"
        or symbol == "PyObject_CallFunction"
        or symbol == "PyObject_CallMethod"
        or symbol == "PyObject_CallMethodNoArgs"
        or symbol == "PyObject_CallMethodOneArg"
        or symbol == "PyObject_CallFunctionObjArgs"
        or symbol == "PyObject_GetIter"
        or symbol == "PyIter_Next"
        or symbol == "PyIter_Check"
        or symbol == "PyNumber_Add"
        or symbol == "PyNumber_Subtract"
        or symbol == "PyNumber_Multiply"
        or symbol == "PyNumber_TrueDivide"
        or symbol == "PyNumber_FloorDivide"
        or symbol == "PyNumber_Remainder"
        or symbol == "PyNumber_Power"
        or symbol == "PyNumber_Negative"
        or symbol == "PyNumber_Positive"
        or symbol == "PyNumber_Absolute"
        or symbol == "PyNumber_Index"
        or symbol == "PyNumber_AsSsize_t"
        or symbol == "PyIndex_Check"
        or symbol == "PyObject_GetAttrString"
        or symbol == "PyObject_GetAttr"
        or symbol == "PyObject_GetOptionalAttr"
        or symbol == "PyObject_GetOptionalAttrString"
        or symbol == "PyObject_SetAttrString"
        or symbol == "PyObject_SetAttr"
        or symbol == "PyObject_HasAttr"
        or symbol == "PyObject_HasAttrString"
        or symbol == "PyObject_HasAttrWithError"
        or symbol == "PyObject_HasAttrStringWithError"
        or symbol == "PyObject_IsTrue"
        or symbol == "PyObject_Not"
        or symbol == "PyObject_Hash"
        or symbol == "PyCallable_Check"
        or symbol == "PyObject_Str"
        or symbol == "PyObject_Repr"
        or symbol == "PyObject_Bytes"
        or symbol == "PyObject_Format"
        or symbol == "PyObject_Type"
        or symbol == "PyObject_IsInstance"
        or symbol == "PyObject_RichCompare"
        or symbol == "PyObject_RichCompareBool"
        or symbol == "PyObject_GetItem"
        or symbol == "PyObject_SetItem"
        or symbol == "PyObject_DelItem"
        or symbol == "PyObject_Size"
        or symbol == "PyObject_Length"
        or symbol == "PyTuple_New"
        or symbol == "PyTuple_SetItem"
        or symbol == "PyTuple_GetItem"
        or symbol == "PyTuple_Size"
        or symbol == "PyTuple_GET_ITEM"
        or symbol == "PyTuple_GET_SIZE"
        or symbol == "PyTuple_SET_ITEM"
        or symbol == "PyTuple_Pack"
        or symbol == "PyTuple_Check"
        or symbol == "PyTuple_CheckExact"
        or symbol == "PyList_New"
        or symbol == "PyList_SetItem"
        or symbol == "PyList_GetItem"
        or symbol == "PyList_GetItemRef"
        or symbol == "PyList_Size"
        or symbol == "PyList_GET_ITEM"
        or symbol == "PyList_GET_SIZE"
        or symbol == "PyList_SET_ITEM"
        or symbol == "PyList_Append"
        or symbol == "PyList_Check"
        or symbol == "PyList_CheckExact"
        or symbol == "PyDict_New"
        or symbol == "PyDict_SetItem"
        or symbol == "PyDict_SetItemString"
        or symbol == "PyDict_GetItem"
        or symbol == "PyDict_GetItemString"
        or symbol == "PyDict_GetItemWithError"
        or symbol == "PyDict_GetItemRef"
        or symbol == "PyDict_GetItemStringRef"
        or symbol == "PyDict_SetDefaultRef"
        or symbol == "PyDict_Pop"
        or symbol == "PyDict_PopString"
        or symbol == "PyDict_DelItem"
        or symbol == "PyDict_DelItemString"
        or symbol == "PyDict_Size"
        or symbol == "PyDict_Contains"
        or symbol == "PyDict_ContainsString"
        or symbol == "PyDict_Next"
        or symbol == "PyDict_Check"
        or symbol == "PyDict_CheckExact"
        or symbol == "PyBytes_FromString"
        or symbol == "PyBytes_FromStringAndSize"
        or symbol == "PyBytes_AsString"
        or symbol == "PyBytes_AsStringAndSize"
        or symbol == "PyBytes_AS_STRING"
        or symbol == "PyBytes_Size"
        or symbol == "PyBytes_GET_SIZE"
        or symbol == "PyBytes_Check"
        or symbol == "PyBytes_CheckExact"
        or symbol == "PyCapsule_New"
        or symbol == "PyCapsule_GetPointer"
        or symbol == "PyCapsule_GetName"
        or symbol == "PyCapsule_GetContext"
        or symbol == "PyCapsule_IsValid"
        or symbol == "PyCapsule_CheckExact"
        or symbol == "PyCapsule_SetContext"
        or symbol == "PyCapsule_SetName"
        or symbol == "PyCapsule_Import"
        or symbol == "PyObject_GetBuffer"
        or symbol == "PyObject_CheckBuffer"
        or symbol == "PyBuffer_Release"
        or symbol == "PyMemoryView_FromObject"
        or symbol == "PyMemoryView_FromMemory"
        or symbol == "PyMemoryView_Check"
        or symbol == "PyMemoryView_GET_BUFFER"
        or symbol == "PyMemoryView_GET_BASE"
        or symbol == "Py_IsInitialized"
        or symbol == "PyGILState_Ensure"
        or symbol == "PyGILState_Release"
        or symbol == "PyGILState_Check"
        or symbol == "PyImport_ImportModule"
        or symbol == "PySequence_Check"
        or symbol == "PyMapping_Check"
        or symbol == "PyMapping_GetItemString"
        or symbol == "PyMapping_SetItemString"
        or symbol == "PyMapping_HasKey"
        or symbol == "PyMapping_HasKeyString"
        or symbol == "PyMapping_GetOptionalItem"
        or symbol == "PyMapping_GetOptionalItemString"
        or symbol == "PyMapping_HasKeyWithError"
        or symbol == "PyMapping_HasKeyStringWithError"
        or symbol == "PySequence_Size"
        or symbol == "PySequence_Length"
        or symbol == "PySequence_GetItem"
        or symbol == "PySequence_Contains"
        or symbol == "PySequence_Fast"
        or symbol == "PySequence_Fast_GET_SIZE"
        or symbol == "PySequence_Fast_ITEMS"
        or symbol == "PySequence_Fast_GET_ITEM"
        or symbol == "PySequence_List"
        or symbol == "PySequence_Tuple"
    )


def _native_list_contains(items, value) -> bool:
    i = 0
    while i < len(items):
        if items[i] == value:
            return True
        i += 1
    return False


def _native_numpy_capi_family(symbol: str):
    if symbol == "PyUFunc_API" or symbol.startswith("PyUFunc_"):
        return "ufunc_api"
    if (
        symbol == "PyArray_API"
        or symbol == "PyArray_Type"
        or symbol == "PyArrayDescr_Type"
        or symbol.startswith("PyArray_")
    ):
        return "array_api"
    return None


def _native_numpy_capi_slot(symbol: str):
    if symbol == "PyArray_Type":
        return 0
    if symbol == "PyArrayDescr_Type":
        return 1
    if symbol == "PyArray_DescrFromType":
        return 2
    if symbol == "PyArray_FromAny":
        return 3
    if symbol == "PyArray_SimpleNew":
        return 4
    if symbol == "PyArray_SimpleNewFromData":
        return 5
    if symbol == "PyArray_NDIM":
        return 6
    if symbol == "PyArray_DIMS":
        return 7
    if symbol == "PyArray_STRIDES":
        return 8
    if symbol == "PyArray_DATA":
        return 9
    if symbol == "PyArray_DESCR":
        return 10
    if symbol == "PyArray_GETITEM":
        return 11
    if symbol == "PyArray_SETITEM":
        return 12
    if symbol == "PyArray_SIZE":
        return 13
    if symbol == "PyArray_ITEMSIZE":
        return 14
    if symbol == "PyArray_Check":
        return 15
    if symbol == "PyArray_CheckExact":
        return 16
    if symbol == "PyArray_DIM":
        return 7
    if symbol == "PyArray_BYTES":
        return 9
    if symbol == "PyUFunc_FromFuncAndData":
        return 0
    return None


def _native_numpy_capi_failure_mode(symbol: str) -> str:
    if symbol == "PyArray_API" or symbol == "PyUFunc_API":
        return "missing_capsule_provider"
    if symbol == "PyArray_Type":
        return "missing_array_type_object"
    if symbol == "PyArrayDescr_Type":
        return "missing_dtype_type_object"
    return "unsupported_stub"


def _native_numpy_capi_status_json(symbol: str) -> str:
    family = _native_numpy_capi_family(symbol)
    if family is None:
        return "null"
    table = "_UFUNC_API" if family == "ufunc_api" else "_ARRAY_API"
    slot = _native_numpy_capi_slot(symbol)
    out = "{"
    out += '"capability": "numpy_capi"'
    out += ', "failure_mode": ' + _json_str(_native_numpy_capi_failure_mode(symbol))
    out += ', "implemented": ' + (
        "true" if _native_capi_implemented(symbol) else "false"
    )
    out += ', "provider_shape": ' + _json_str(family)
    out += ', "slot": ' + ("null" if slot is None else str(slot))
    out += ', "symbol": ' + _json_str(symbol)
    out += ', "table": ' + _json_str(table)
    out += "}"
    return out


def _native_extension_abi_json(
    symbols,
    provider: str,
    expected_abi,
    actual_abi,
    abi_mode: str,
    include_dir,
    require_capsule: bool,
    require_buffer: bool,
    require_memoryview: bool,
    require_numpy_capi: bool,
) -> str:
    requested = []

    def add_symbol(name: str) -> None:
        if not _native_list_contains(requested, name):
            requested.append(name)

    i = 0
    while i < len(symbols):
        add_symbol(symbols[i])
        i += 1
    if require_capsule:
        add_symbol("PyCapsule_New")
        add_symbol("PyCapsule_GetPointer")
        add_symbol("PyCapsule_GetName")
        add_symbol("PyCapsule_GetContext")
        add_symbol("PyCapsule_IsValid")
        add_symbol("PyCapsule_CheckExact")
        add_symbol("PyCapsule_SetContext")
        add_symbol("PyCapsule_SetName")
        add_symbol("PyCapsule_Import")
    if require_buffer:
        add_symbol("PyObject_GetBuffer")
        add_symbol("PyObject_CheckBuffer")
        add_symbol("PyBuffer_Release")
    if require_memoryview:
        add_symbol("PyMemoryView_FromObject")
        add_symbol("PyMemoryView_FromMemory")
        add_symbol("PyMemoryView_Check")
        add_symbol("PyMemoryView_GET_BUFFER")
        add_symbol("PyMemoryView_GET_BASE")
    if require_numpy_capi:
        add_symbol("PyArray_API")
        add_symbol("PyArray_Type")
        add_symbol("PyArrayDescr_Type")
        add_symbol("PyArray_DescrFromType")
        add_symbol("PyArray_FromAny")
        add_symbol("PyArray_SimpleNew")
        add_symbol("PyArray_SimpleNewFromData")
        add_symbol("PyArray_NDIM")
        add_symbol("PyArray_DIMS")
        add_symbol("PyArray_STRIDES")
        add_symbol("PyArray_DATA")
        add_symbol("PyArray_DESCR")
        add_symbol("PyArray_GETITEM")
        add_symbol("PyArray_SETITEM")
        add_symbol("PyArray_SIZE")
        add_symbol("PyArray_ITEMSIZE")
        add_symbol("PyArray_Check")
        add_symbol("PyArray_CheckExact")
        add_symbol("PyArray_DIM")
        add_symbol("PyArray_BYTES")
        add_symbol("PyUFunc_API")
        add_symbol("PyUFunc_FromFuncAndData")

    headers = []
    provided_headers = []
    missing_headers = []
    missing = []
    unknown = []
    symbol_rows = "["
    i = 0
    while i < len(requested):
        header = _native_known_capi_header(requested[i])
        if header is None:
            unknown.append(requested[i])
        else:
            if not _native_list_contains(headers, header):
                headers.append(header)
                if include_dir is not None:
                    header_path_check = include_dir + "/" + header
                    if os.path.isfile(header_path_check):
                        provided_headers.append(header)
                    else:
                        missing_headers.append(header)
            if not _native_capi_implemented(requested[i]):
                missing.append(requested[i])
            if symbol_rows != "[":
                symbol_rows += ", "
            header_path = None
            provided_by_package = False
            if include_dir is not None:
                header_path = include_dir + "/" + header
                provided_by_package = os.path.isfile(header_path)
            symbol_rows += "{"
            symbol_rows += '"header": ' + _json_str(header)
            symbol_rows += ', "header_path": ' + _json_str_or_null(header_path)
            symbol_rows += ', "implemented": ' + (
                "true" if _native_capi_implemented(requested[i]) else "false"
            )
            symbol_rows += ', "name": ' + _json_str(requested[i])
            symbol_rows += ', "provided_by_package": ' + (
                "true" if provided_by_package else "false"
            )
            symbol_rows += "}"
        i += 1
    symbol_rows += "]"

    diagnostics = "["
    i = 0
    while i < len(missing):
        if diagnostics != "[":
            diagnostics += ", "
        diagnostics += "{"
        numpy_family = _native_numpy_capi_family(missing[i])
        if numpy_family is not None:
            diagnostics += '"capability": "numpy_capi"'
            diagnostics += ', "code": "PCC-EXT-MISSING-NUMPY-CAPI-SYMBOL"'
            diagnostics += ', "message": ' + _json_str(
                missing[i]
                + " requires a pcc-native NumPy "
                + numpy_family
                + " provider; it is not implemented for "
                + abi_mode
            )
            diagnostics += ', "provider_shape": ' + _json_str(numpy_family)
            diagnostics += ', "table": ' + _json_str(
                "_UFUNC_API" if numpy_family == "ufunc_api" else "_ARRAY_API"
            )
            slot = _native_numpy_capi_slot(missing[i])
            diagnostics += ', "slot": ' + ("null" if slot is None else str(slot))
            diagnostics += ', "failure_mode": ' + _json_str(
                _native_numpy_capi_failure_mode(missing[i])
            )
        else:
            diagnostics += '"code": "PCC-EXT-MISSING-CAPI-SYMBOL"'
            diagnostics += ', "message": ' + _json_str(
                missing[i] + " is not implemented for " + abi_mode
            )
        diagnostics += ', "symbol": ' + _json_str(missing[i])
        diagnostics += "}"
        i += 1
    i = 0
    while i < len(unknown):
        if diagnostics != "[":
            diagnostics += ", "
        diagnostics += "{"
        diagnostics += '"code": "PCC-EXT-UNKNOWN-CAPI-SYMBOL"'
        diagnostics += ', "message": ' + _json_str(
            unknown[i] + " is not in the pcc C-API catalogue"
        )
        diagnostics += ', "symbol": ' + _json_str(unknown[i])
        diagnostics += "}"
        i += 1
    if include_dir is not None:
        i = 0
        while i < len(missing_headers):
            if diagnostics != "[":
                diagnostics += ", "
            diagnostics += "{"
            diagnostics += '"code": "PCC-EXT-MISSING-CAPI-HEADER"'
            diagnostics += ', "header": ' + _json_str(missing_headers[i])
            diagnostics += ', "include_dir": ' + _json_str(include_dir)
            diagnostics += ', "message": ' + _json_str(
                missing_headers[i] + " was not provided by " + include_dir
            )
            diagnostics += "}"
            i += 1
    abi_version = "null"
    if expected_abi is not None and actual_abi is not None:
        abi_ok = expected_abi == actual_abi
        abi_version = "{"
        abi_version += '"abi_mode": ' + _json_str(abi_mode)
        abi_version += ', "actual": ' + str(actual_abi)
        abi_version += ', "code": ' + (
            "null" if abi_ok else '"PCC-EXT-ABI-VERSION-MISMATCH"'
        )
        abi_version += ', "expected": ' + str(expected_abi)
        abi_version += ', "message": '
        if abi_ok:
            abi_version += _json_str("ABI versions match")
        else:
            abi_version += _json_str(
                provider
                + " ABI version mismatch: expected "
                + str(expected_abi)
                + ", got "
                + str(actual_abi)
                + " under "
                + abi_mode
            )
        abi_version += ', "ok": ' + ("true" if abi_ok else "false")
        abi_version += ', "provider": ' + _json_str(provider)
        abi_version += "}"
        if not abi_ok:
            if diagnostics != "[":
                diagnostics += ", "
            diagnostics += abi_version
    diagnostics += "]"

    numpy_capi_status = "["
    i = 0
    while i < len(requested):
        if _native_numpy_capi_family(requested[i]) is not None:
            if numpy_capi_status != "[":
                numpy_capi_status += ", "
            numpy_capi_status += _native_numpy_capi_status_json(requested[i])
        i += 1
    numpy_capi_status += "]"

    ok = len(missing) == 0 and len(unknown) == 0 and len(missing_headers) == 0
    if (
        expected_abi is not None
        and actual_abi is not None
        and expected_abi != actual_abi
    ):
        ok = False

    manifest = "{"
    manifest += '"headers": ' + _json_str_list(headers)
    manifest += ', "include_dir": ' + _json_str_or_null(include_dir)
    manifest += ', "missing_headers": ' + _json_str_list(missing_headers)
    manifest += ', "provided_headers": ' + _json_str_list(provided_headers)
    manifest += ', "symbols": ' + symbol_rows
    manifest += ', "unknown_symbols": ' + _json_str_list(unknown)
    manifest += "}"

    out = "{"
    out += '"abi_mode": ' + _json_str(abi_mode)
    out += ', "abi_version": ' + abi_version
    out += ', "diagnostics": ' + diagnostics
    out += ', "header_manifest": ' + manifest
    out += ', "missing_symbols": ' + _json_str_list(missing)
    out += ', "numpy_capi_status": ' + numpy_capi_status
    out += ', "ok": ' + ("true" if ok else "false")
    out += ', "provider": ' + _json_str(provider)
    out += ', "required_symbols": ' + _json_str_list(requested)
    out += ', "unknown_symbols": ' + _json_str_list(unknown)
    out += "}"
    return out


def _native_extension_abi_ok(
    symbols,
    expected_abi,
    actual_abi,
    require_capsule: bool,
    require_buffer: bool,
    require_memoryview: bool,
    require_numpy_capi: bool,
    include_dir,
) -> bool:
    requested = []

    def add_symbol(name: str) -> None:
        if not _native_list_contains(requested, name):
            requested.append(name)

    i = 0
    while i < len(symbols):
        add_symbol(symbols[i])
        i += 1
    if require_capsule:
        add_symbol("PyCapsule_New")
        add_symbol("PyCapsule_GetPointer")
        add_symbol("PyCapsule_GetName")
        add_symbol("PyCapsule_GetContext")
        add_symbol("PyCapsule_IsValid")
        add_symbol("PyCapsule_CheckExact")
        add_symbol("PyCapsule_SetContext")
        add_symbol("PyCapsule_SetName")
        add_symbol("PyCapsule_Import")
    if require_buffer:
        add_symbol("PyObject_GetBuffer")
        add_symbol("PyObject_CheckBuffer")
        add_symbol("PyBuffer_Release")
    if require_memoryview:
        add_symbol("PyMemoryView_FromObject")
        add_symbol("PyMemoryView_FromMemory")
        add_symbol("PyMemoryView_Check")
        add_symbol("PyMemoryView_GET_BUFFER")
        add_symbol("PyMemoryView_GET_BASE")
    if require_numpy_capi:
        add_symbol("PyArray_API")
        add_symbol("PyArray_Type")
        add_symbol("PyArray_DescrFromType")
        add_symbol("PyArray_FromAny")
        add_symbol("PyArray_NDIM")
        add_symbol("PyArray_DIMS")
        add_symbol("PyArray_STRIDES")
        add_symbol("PyArray_DATA")
        add_symbol("PyArray_DESCR")
        add_symbol("PyArray_SIZE")
        add_symbol("PyArray_ITEMSIZE")
        add_symbol("PyArray_Check")
        add_symbol("PyArray_CheckExact")
        add_symbol("PyArray_DIM")
        add_symbol("PyArray_BYTES")
        add_symbol("PyUFunc_API")
    i = 0
    while i < len(requested):
        if _native_known_capi_header(requested[i]) is None:
            return False
        if include_dir is not None:
            header = _native_known_capi_header(requested[i])
            if header is not None and not os.path.isfile(include_dir + "/" + header):
                return False
        if not _native_capi_implemented(requested[i]):
            return False
        i += 1
    if (
        expected_abi is not None
        and actual_abi is not None
        and expected_abi != actual_abi
    ):
        return False
    return True


def _run_native_package_inspect_from_pcc1(module_args) -> int:
    name = "package"
    path = None
    emit_json = False
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--json":
            emit_json = True
        elif arg == "--path":
            if i + 1 >= len(module_args):
                _write_text("Error: --path requires a value", err=True)
                return 2
            path = module_args[i + 1]
            i += 1
        elif not arg.startswith("-"):
            name = arg
        i += 1
    report = _package_inspection_json(name, path)
    if emit_json:
        _write_text(report)
    else:
        _write_text(report)
    return 0


def _run_native_package_build_plan_from_pcc1(module_args) -> int:
    name = "package"
    path = None
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--json":
            pass
        elif arg == "--path":
            if i + 1 >= len(module_args):
                _write_text("Error: --path requires a value", err=True)
                return 2
            path = module_args[i + 1]
            i += 1
        elif not arg.startswith("-"):
            name = arg
        i += 1
    _write_text(_native_build_plan_json(name, path))
    return 0


def _native_collect_suffix_files(root: str, suffixes, build_relevant: bool = False):
    files = []
    if root is None or not os.path.isdir(root):
        return files
    stack = [root]
    while len(stack) > 0:
        current = stack.pop()
        try:
            names = sorted(os.listdir(current))
        except Exception:
            names = []
        i = 0
        while i < len(names):
            name = names[i]
            path = current + "/" + name
            if os.path.isdir(path):
                skip = (
                    _should_skip_package_build_dir(name)
                    if build_relevant
                    else (name == "__pycache__" or name == ".git" or name == "build")
                )
                if not skip:
                    stack.append(path)
            elif os.path.isfile(path):
                lower = name.lower()
                j = 0
                while j < len(suffixes):
                    if lower.endswith(suffixes[j]):
                        files.append(path)
                        break
                    j += 1
            i += 1
    return files


def _native_split_ws(text: str):
    parts = []
    current = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == " " or ch == "\n" or ch == "\t":
            if current != "":
                parts.append(current)
                current = ""
        else:
            current += ch
        i += 1
    if current != "":
        parts.append(current)
    return parts


def _native_compile_command_rows(root: str):
    rows = []
    path = os.path.join(root, "compile_commands.json")
    if not os.path.exists(path):
        return rows
    try:
        with open(path, "r") as fh:
            text = fh.read()
    except Exception:
        return rows
    pos = 0
    while True:
        key = _native_find_from(text, '"command"', pos)
        if key < 0:
            break
        colon = _native_find_from(text, ":", key)
        first = _native_find_from(text, '"', colon + 1)
        second = _native_find_from(text, '"', first + 1)
        if colon < 0 or first < 0 or second < 0:
            pos = key + 9
            continue
        command = text[first + 1 : second]
        tokens = _native_split_ws(command)
        output = None
        i = 0
        while i < len(tokens):
            if tokens[i] == "-o" and i + 1 < len(tokens):
                output = tokens[i + 1]
            i += 1
        rows.append([command, tokens, output])
        pos = second + 1
    return rows


def _native_source_language(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".c"):
        return "c"
    if (
        lower.endswith(".cc")
        or lower.endswith(".cpp")
        or lower.endswith(".cxx")
        or lower.endswith(".c++")
    ):
        return "cxx"
    if (
        lower.endswith(".f")
        or lower.endswith(".for")
        or lower.endswith(".f77")
        or lower.endswith(".f90")
        or lower.endswith(".f95")
        or lower.endswith(".f03")
        or lower.endswith(".f08")
    ):
        return "fortran"
    return "unknown"


def _native_shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _native_generated_target_suffix_ok(path: str) -> bool:
    lower = path.lower()
    return (
        lower.endswith(".c")
        or lower.endswith(".cc")
        or lower.endswith(".cpp")
        or lower.endswith(".cxx")
        or lower.endswith(".c++")
        or lower.endswith(".h")
        or lower.endswith(".hh")
        or lower.endswith(".hpp")
        or lower.endswith(".inc")
        or lower.endswith(".py")
        or lower.endswith(".pxd")
        or lower.endswith(".pyx")
        or lower.endswith(".json")
        or lower.endswith(".txt")
    )


def _native_is_file_custom_target(target: str) -> bool:
    if target == "" or _native_find_from(target, "meson-internal__", 0) == 0:
        return False
    if (
        target == "all"
        or target == "test"
        or target == "install"
        or target == "benchmark"
        or target == "clean"
        or target == "uninstall"
    ):
        return False
    return _native_generated_target_suffix_ok(target)


def _native_meson_intro_path(root: str):
    candidate1 = root + "/meson-info/intro-targets.json"
    candidate2 = root + "/build/meson-info/intro-targets.json"
    candidate3 = root + "/build/pcc-package/meson-build/meson-info/intro-targets.json"
    if os.path.isfile(candidate1):
        return candidate1
    if os.path.isfile(candidate2):
        return candidate2
    if os.path.isfile(candidate3):
        return candidate3
    return None


def _native_meson_build_dir(root: str) -> str:
    path = _native_meson_intro_path(root)
    if path is None:
        return root + "/build/pcc-package/meson-build"
    marker = "/meson-info/intro-targets.json"
    pos = _native_find_from(path, marker, 0)
    if pos >= 0:
        return path[:pos]
    return root + "/build/pcc-package/meson-build"


def _native_ninja_custom_targets(root: str, search_paths):
    build_dir = _native_meson_build_dir(root)
    ninja = _native_find_tool_path(["ninja"], search_paths) or "ninja"
    output_path = "/tmp/pcc_ninja_targets_" + str(os.getpid())
    command = (
        _native_shell_quote(ninja)
        + " -C "
        + _native_shell_quote(build_dir)
        + " -t targets all > "
        + _native_shell_quote(output_path)
        + " 2>/dev/null"
    )
    try:
        subprocess.run(["/bin/sh", "-c", command], check=True)
    except Exception:
        return []
    try:
        with open(output_path, "r") as fh:
            text = fh.read()
    except Exception:
        text = ""
    try:
        subprocess.run(["rm", "-f", output_path], check=True)
    except Exception:
        pass
    targets = []
    pos = 0
    while pos < len(text):
        end = _native_find_from(text, "\n", pos)
        if end < 0:
            end = len(text)
        line = text[pos:end]
        colon = _native_find_from(line, ":", 0)
        if colon > 0:
            target = line[:colon].strip()
            kind = line[colon + 1 :].strip()
            if (
                _native_find_from(kind, "CUSTOM_COMMAND", 0) == 0
                and _native_is_file_custom_target(target)
                and not _native_list_contains(targets, target)
            ):
                targets.append(target)
        pos = end + 1
    return targets


def _native_meson_command_rows(root: str):
    rows = []
    path = _native_meson_intro_path(root)
    if path is None:
        return rows
    try:
        with open(path, "r") as fh:
            text = fh.read()
    except Exception:
        return rows
    pos = 0
    seen = []
    while True:
        first = _native_find_from(text, '"', pos)
        if first < 0:
            break
        second = _native_find_from(text, '"', first + 1)
        if second < 0:
            break
        value = text[first + 1 : second]
        language = _native_source_language(value)
        if language != "unknown":
            if value.startswith("/"):
                source = value
            else:
                source = root + "/" + value
            if not _native_list_contains(seen, source):
                seen.append(source)
                base = os.path.basename(source).split(".")[0]
                output = (
                    root
                    + "/build/pcc-package/meson-"
                    + base
                    + "-"
                    + str(len(rows))
                    + ".o"
                )
                rows.append([source, language, output])
        pos = second + 1
    return rows


def _native_generated_c_provenance_json(
    root: str, diagnostics, enforce_generated_c: bool
) -> str:
    pyx_files = _native_collect_suffix_files(root, [".pyx"], True)
    out = "["
    i = 0
    while i < len(pyx_files):
        pyx = pyx_files[i]
        generated = pyx[:-4] + ".c"
        exists = os.path.exists(generated)
        status = "missing"
        if exists:
            try:
                if os.path.getmtime(generated) >= os.path.getmtime(pyx):
                    status = "up_to_date"
                else:
                    status = "stale"
            except Exception:
                status = "unknown"
        if i > 0:
            out += ", "
        out += "{"
        out += '"exists": ' + ("true" if exists else "false")
        out += ', "generated_c": ' + _json_str(generated)
        out += ', "pyx": ' + _json_str(pyx)
        out += ', "status": ' + _json_str(status)
        out += "}"
        if enforce_generated_c:
            if status == "missing" and not _native_list_contains(
                diagnostics, "PCC-PKG-GENERATED-C-MISSING"
            ):
                diagnostics.append("PCC-PKG-GENERATED-C-MISSING")
            if status == "stale" and not _native_list_contains(
                diagnostics, "PCC-PKG-GENERATED-C-STALE"
            ):
                diagnostics.append("PCC-PKG-GENERATED-C-STALE")
        i += 1
    out += "]"
    return out


def _native_build_exec_json(
    name: str,
    explicit_path,
    search_paths,
    include_dirs,
    library_dirs,
    execute: bool,
    regenerate_cython: bool,
    run_f2py: bool,
    link_output,
    libraries,
    abi_mode: str,
    from_compile_commands: bool,
    from_meson_introspection: bool,
    configure_meson: bool,
    enforce_generated_c: bool,
) -> str:
    pkg_name = (name or "").strip() or "package"
    root = _native_package_path(pkg_name, explicit_path)
    actions = "["
    diagnostics = []
    object_outputs = []
    vendor_bindings = "[]"
    linkage = "null"
    generated_c_provenance = "[]"
    ok = True

    def add_action(kind: str, source, output, command, status: str, returncode) -> None:
        nonlocal actions, ok
        if actions != "[":
            actions += ", "
        actions += "{"
        actions += '"command": ' + _json_str_list(command)
        actions += ', "kind": ' + _json_str(kind)
        actions += ', "output": ' + _json_str_or_null(output)
        actions += ', "returncode": ' + (
            "null" if returncode is None else str(returncode)
        )
        actions += ', "source": ' + _json_str_or_null(source)
        actions += ', "status": ' + _json_str(status)
        actions += "}"
        if status == "blocked" or status == "failed" or status == "timeout":
            ok = False

    if root is None or not os.path.isdir(root):
        diagnostics.append("PCC-PKG-BUILD-PATH-MISSING")
        ok = False
    else:
        if execute:
            try:
                subprocess.run(["mkdir", "-p", root + "/build/pcc-package"], check=True)
            except Exception:
                diagnostics.append("PCC-PKG-BUILD-DIR-FAILED")
                ok = False

        pyx_files = _native_collect_suffix_files(root, [".pyx"], True)
        c_files = _native_collect_suffix_files(root, [".c"], True)
        fortran_files = _native_collect_suffix_files(
            root,
            [".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08"],
            True,
        )

        if (
            len(pyx_files) > 0
            and not regenerate_cython
            and execute
            and not from_compile_commands
            and not from_meson_introspection
        ):
            diagnostics.append("PCC-PKG-CYTHON-REGENERATION-REQUIRED")
        if regenerate_cython:
            cython = _native_find_tool_path(["cython", "cython3"], search_paths)
            i = 0
            while i < len(pyx_files):
                source = pyx_files[i]
                output = source[:-4] + ".c"
                command = [cython or "cython", source, "-o", output]
                if execute and cython is None:
                    add_action(
                        "cython_regenerate", source, output, command, "blocked", None
                    )
                    diagnostics.append("PCC-PKG-MISSING-CYTHON")
                elif execute:
                    try:
                        subprocess.run(command, check=True)
                        add_action(
                            "cython_regenerate",
                            source,
                            output,
                            command,
                            "passed",
                            0,
                        )
                    except Exception:
                        add_action(
                            "cython_regenerate", source, output, command, "failed", 127
                        )
                else:
                    add_action(
                        "cython_regenerate", source, output, command, "planned", None
                    )
                i += 1

        generated_c_provenance = _native_generated_c_provenance_json(
            root,
            diagnostics,
            enforce_generated_c,
        )

        if run_f2py:
            f2py = _native_find_tool_path(["f2py", "f2py3"], search_paths)
            i = 0
            while i < len(fortran_files):
                source = fortran_files[i]
                command = [f2py or "f2py", "-c", source]
                if execute and f2py is None:
                    add_action("f2py_build", source, None, command, "blocked", None)
                    diagnostics.append("PCC-PKG-MISSING-F2PY")
                elif execute:
                    try:
                        subprocess.run(command, check=True)
                        add_action(
                            "f2py_build",
                            source,
                            None,
                            command,
                            "passed",
                            0,
                        )
                    except Exception:
                        add_action("f2py_build", source, None, command, "failed", 127)
                else:
                    add_action("f2py_build", source, None, command, "planned", None)
                i += 1

        cc = _native_find_tool_path(["cc", "clang", "gcc"], search_paths)
        fortran = _native_find_tool_path(
            ["gfortran", "flang", "ifx", "ifort"], search_paths
        )
        if from_compile_commands:
            rows = _native_compile_command_rows(root)
            i = 0
            while i < len(rows):
                command = rows[i][1]
                if len(command) > 0 and _native_find_from(command[0], "/", 0) < 0:
                    found_tool = _native_find_tool_path([command[0]], search_paths)
                    if found_tool is not None:
                        command[0] = found_tool
                output = rows[i][2]
                if output is not None:
                    object_outputs.append(output)
                if execute:
                    try:
                        subprocess.run(command, check=True)
                        add_action(
                            "compile_command", None, output, command, "passed", 0
                        )
                    except Exception:
                        add_action(
                            "compile_command", None, output, command, "failed", 127
                        )
                else:
                    add_action(
                        "compile_command", None, output, command, "planned", None
                    )
                i += 1
        elif from_meson_introspection:
            rows = _native_meson_command_rows(root)
            if len(rows) == 0 and configure_meson:
                meson = _native_find_tool_path(["meson"], search_paths)
                setup_dir = root + "/build/pcc-package/meson-build"
                command = [meson or "meson", "setup", setup_dir, root]
                if execute and meson is None:
                    add_action("meson_setup", None, setup_dir, command, "blocked", None)
                    if not _native_list_contains(diagnostics, "PCC-PKG-MISSING-MESON"):
                        diagnostics.append("PCC-PKG-MISSING-MESON")
                elif execute:
                    try:
                        subprocess.run(command, check=True)
                        add_action("meson_setup", None, setup_dir, command, "passed", 0)
                        rows = _native_meson_command_rows(root)
                    except Exception:
                        add_action(
                            "meson_setup", None, setup_dir, command, "failed", 127
                        )
                else:
                    add_action("meson_setup", None, setup_dir, command, "planned", None)
            if len(rows) == 0:
                if not configure_meson or execute:
                    diagnostics.append("PCC-PKG-MESON-INTROSPECTION-MISSING")
                    ok = False
            generated_targets = []
            if execute and len(rows) > 0:
                generated_targets = _native_ninja_custom_targets(root, search_paths)
            if len(generated_targets) > 0:
                ninja = _native_find_tool_path(["ninja"], search_paths) or "ninja"
                build_dir = _native_meson_build_dir(root)
                command = [ninja, "-C", build_dir]
                g = 0
                while g < len(generated_targets):
                    command.append(generated_targets[g])
                    g += 1
                try:
                    subprocess.run(command, check=True)
                    add_action(
                        "meson_generated_targets", None, None, command, "passed", 0
                    )
                except Exception:
                    add_action(
                        "meson_generated_targets", None, None, command, "failed", 127
                    )
            i = 0
            while i < len(rows):
                source = rows[i][0]
                language = rows[i][1]
                output = rows[i][2]
                object_outputs.append(output)
                if language == "fortran":
                    command = [fortran or "gfortran", "-c", source, "-o", output]
                    kind = "meson_compile_fortran"
                    tool_missing = fortran is None
                    missing_diag = "PCC-PKG-MISSING-FORTRAN"
                elif language == "cxx":
                    command = [cc or "cc", "-c", source, "-o", output]
                    kind = "meson_compile_cxx"
                    tool_missing = cc is None
                    missing_diag = "PCC-PKG-MISSING-C-COMPILER"
                else:
                    command = [cc or "cc", "-c", source, "-o", output]
                    kind = "meson_compile_c"
                    tool_missing = cc is None
                    missing_diag = "PCC-PKG-MISSING-C-COMPILER"
                if execute and tool_missing:
                    add_action(kind, source, output, command, "blocked", None)
                    if not _native_list_contains(diagnostics, missing_diag):
                        diagnostics.append(missing_diag)
                elif execute:
                    try:
                        subprocess.run(command, check=True)
                        add_action(kind, source, output, command, "passed", 0)
                    except Exception:
                        add_action(kind, source, output, command, "failed", 127)
                else:
                    add_action(kind, source, output, command, "planned", None)
                i += 1
        else:
            i = 0
            while i < len(c_files):
                source = c_files[i]
                base = os.path.basename(source).split(".")[0]
                output = root + "/build/pcc-package/" + base + ".o"
                object_outputs.append(output)
                command = [cc or "cc", "-c"]
                j = 0
                while j < len(include_dirs):
                    command.append("-I" + include_dirs[j])
                    j += 1
                command.append(source)
                command.append("-o")
                command.append(output)
                if execute and cc is None:
                    add_action("c_compile", source, output, command, "blocked", None)
                    diagnostics.append("PCC-PKG-MISSING-C-COMPILER")
                elif execute:
                    try:
                        subprocess.run(command, check=True)
                        add_action(
                            "c_compile",
                            source,
                            output,
                            command,
                            "passed",
                            0,
                        )
                    except Exception:
                        add_action("c_compile", source, output, command, "failed", 127)
                else:
                    add_action("c_compile", source, output, command, "planned", None)
                i += 1

            i = 0
            while i < len(fortran_files):
                source = fortran_files[i]
                base = os.path.basename(source).split(".")[0]
                output = root + "/build/pcc-package/" + base + ".o"
                object_outputs.append(output)
                command = [fortran or "gfortran", "-c", source, "-o", output]
                if execute and fortran is None:
                    add_action(
                        "fortran_compile", source, output, command, "blocked", None
                    )
                    diagnostics.append("PCC-PKG-MISSING-FORTRAN")
                elif execute:
                    try:
                        subprocess.run(command, check=True)
                        add_action(
                            "fortran_compile",
                            source,
                            output,
                            command,
                            "passed",
                            0,
                        )
                    except Exception:
                        add_action(
                            "fortran_compile", source, output, command, "failed", 127
                        )
                else:
                    add_action(
                        "fortran_compile", source, output, command, "planned", None
                    )
                i += 1

        vendor_bindings = _native_vendor_bindings_json(
            libraries, library_dirs, diagnostics
        )
        linkage = "null"
        if link_output is not None:
            if link_output.startswith("/"):
                link_path = link_output
            else:
                link_path = root + "/" + link_output
            command = [cc or "cc", "-shared"]
            i = 0
            while i < len(object_outputs):
                command.append(object_outputs[i])
                i += 1
            command.append("-o")
            command.append(link_path)
            i = 0
            while i < len(library_dirs):
                command.append("-L" + library_dirs[i])
                i += 1
            i = 0
            while i < len(libraries):
                binding = _native_find_library_binding_values(
                    libraries[i], library_dirs
                )
                if binding[0]:
                    command.append("-l" + binding[1])
                i += 1
            if execute and cc is None:
                add_action("native_link", None, link_path, command, "blocked", None)
            elif execute and not ok:
                add_action("native_link", None, link_path, command, "blocked", None)
            elif execute:
                try:
                    subprocess.run(command, check=True)
                    add_action("native_link", None, link_path, command, "passed", 0)
                except Exception:
                    add_action("native_link", None, link_path, command, "failed", 127)
            else:
                add_action("native_link", None, link_path, command, "planned", None)
            artifacts = []
            if os.path.exists(link_path):
                artifacts.append(link_path)
            linkage = _native_linkage_json(artifacts, [], [" ".join(command)], abi_mode)
            if _native_find_from(linkage, '"ok": false', 0) >= 0:
                ok = False
                if not _native_list_contains(diagnostics, "PCC-PKG-003"):
                    diagnostics.append("PCC-PKG-003")
    if (
        _native_list_contains(diagnostics, "PCC-PKG-GENERATED-C-MISSING")
        or _native_list_contains(diagnostics, "PCC-PKG-GENERATED-C-STALE")
        or _native_list_contains(diagnostics, "PCC-PKG-MISSING-LIBRARY")
    ):
        ok = False
    if not ok and not _native_list_contains(diagnostics, "PCC-PKG-BUILD-ACTION-FAILED"):
        diagnostics.append("PCC-PKG-BUILD-ACTION-FAILED")
    actions += "]"
    out = "{"
    out += '"actions": ' + actions
    out += ', "build_plan": ' + _native_build_plan_json(pkg_name, root)
    out += ', "diagnostics": ' + _json_str_list(diagnostics)
    out += ', "execute": ' + ("true" if execute else "false")
    out += ', "include_dirs": ' + _json_str_list(include_dirs)
    out += ', "from_compile_commands": ' + (
        "true" if from_compile_commands else "false"
    )
    out += ', "from_meson_introspection": ' + (
        "true" if from_meson_introspection else "false"
    )
    out += ', "configure_meson": ' + ("true" if configure_meson else "false")
    out += ', "generated_c_provenance": ' + generated_c_provenance
    out += ', "linkage": ' + linkage
    out += ', "name": ' + _json_str(pkg_name)
    out += ', "ok": ' + ("true" if ok else "false")
    out += ', "path": ' + _json_str_or_null(root)
    out += ', "vendor_bindings": ' + vendor_bindings
    out += "}"
    return out


def _run_native_package_build_exec_from_pcc1(module_args) -> int:
    name = "package"
    path = None
    search_paths = []
    include_dirs = []
    library_dirs = []
    libraries = []
    link_output = None
    abi_mode = "pcc-native"
    execute = False
    regenerate_cython = False
    run_f2py = False
    from_compile_commands = False
    from_meson_introspection = False
    configure_meson = False
    enforce_generated_c = False
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--json":
            pass
        elif arg == "--path":
            if i + 1 >= len(module_args):
                _write_text("Error: --path requires a value", err=True)
                return 2
            path = module_args[i + 1]
            i += 1
        elif arg == "--search-path":
            if i + 1 >= len(module_args):
                _write_text("Error: --search-path requires a value", err=True)
                return 2
            search_paths.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--search-path="):
            search_paths.append(arg.split("=", 1)[1])
        elif arg == "--include-dir":
            if i + 1 >= len(module_args):
                _write_text("Error: --include-dir requires a value", err=True)
                return 2
            include_dirs.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--include-dir="):
            include_dirs.append(arg.split("=", 1)[1])
        elif arg == "--library-dir":
            if i + 1 >= len(module_args):
                _write_text("Error: --library-dir requires a value", err=True)
                return 2
            library_dirs.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--library-dir="):
            library_dirs.append(arg.split("=", 1)[1])
        elif arg == "--library":
            if i + 1 >= len(module_args):
                _write_text("Error: --library requires a value", err=True)
                return 2
            libraries.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--library="):
            libraries.append(arg.split("=", 1)[1])
        elif arg == "--link-output":
            if i + 1 >= len(module_args):
                _write_text("Error: --link-output requires a value", err=True)
                return 2
            link_output = module_args[i + 1]
            i += 1
        elif arg.startswith("--link-output="):
            link_output = arg.split("=", 1)[1]
        elif arg == "--abi":
            if i + 1 >= len(module_args):
                _write_text("Error: --abi requires a value", err=True)
                return 2
            abi_mode = module_args[i + 1]
            i += 1
        elif arg.startswith("--abi="):
            abi_mode = arg.split("=", 1)[1]
        elif arg == "--execute":
            execute = True
        elif arg == "--regenerate-cython":
            regenerate_cython = True
        elif arg == "--run-f2py":
            run_f2py = True
        elif arg == "--from-compile-commands":
            from_compile_commands = True
        elif arg == "--from-meson-introspection":
            from_meson_introspection = True
        elif arg == "--configure-meson":
            configure_meson = True
        elif arg == "--enforce-generated-c":
            enforce_generated_c = True
        elif not arg.startswith("-"):
            name = arg
        i += 1
    report = _native_build_exec_json(
        name,
        path,
        search_paths,
        include_dirs,
        library_dirs,
        execute,
        regenerate_cython,
        run_f2py,
        link_output,
        libraries,
        abi_mode,
        from_compile_commands,
        from_meson_introspection,
        configure_meson,
        enforce_generated_c,
    )
    _write_text(report)
    return 2 if _native_find_from(report, '"ok": false', 0) >= 0 else 0


def _run_native_package_ext_abi_from_pcc1(module_args) -> int:
    symbols = []
    provider = "extension"
    expected_abi = None
    actual_abi = None
    abi_mode = "pcc-native"
    include_dir = None
    require_capsule = False
    require_buffer = False
    require_memoryview = False
    require_numpy_capi = False
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--symbol":
            if i + 1 >= len(module_args):
                _write_text("Error: --symbol requires a value", err=True)
                return 2
            symbols.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--symbol="):
            symbols.append(arg.split("=", 1)[1])
        elif arg == "--provider":
            if i + 1 >= len(module_args):
                _write_text("Error: --provider requires a value", err=True)
                return 2
            provider = module_args[i + 1]
            i += 1
        elif arg.startswith("--provider="):
            provider = arg.split("=", 1)[1]
        elif arg == "--expected-abi":
            if i + 1 >= len(module_args):
                _write_text("Error: --expected-abi requires a value", err=True)
                return 2
            expected_abi = int(module_args[i + 1])
            i += 1
        elif arg.startswith("--expected-abi="):
            expected_abi = int(arg.split("=", 1)[1])
        elif arg == "--actual-abi":
            if i + 1 >= len(module_args):
                _write_text("Error: --actual-abi requires a value", err=True)
                return 2
            actual_abi = int(module_args[i + 1])
            i += 1
        elif arg.startswith("--actual-abi="):
            actual_abi = int(arg.split("=", 1)[1])
        elif arg == "--abi":
            if i + 1 >= len(module_args):
                _write_text("Error: --abi requires a value", err=True)
                return 2
            abi_mode = module_args[i + 1]
            i += 1
        elif arg.startswith("--abi="):
            abi_mode = arg.split("=", 1)[1]
        elif arg == "--include-dir":
            if i + 1 >= len(module_args):
                _write_text("Error: --include-dir requires a value", err=True)
                return 2
            include_dir = module_args[i + 1]
            i += 1
        elif arg.startswith("--include-dir="):
            include_dir = arg.split("=", 1)[1]
        elif arg == "--require-capsule":
            require_capsule = True
        elif arg == "--require-buffer":
            require_buffer = True
        elif arg == "--require-memoryview":
            require_memoryview = True
        elif arg == "--require-numpy-capi":
            require_numpy_capi = True
        elif arg == "--json":
            pass
        i += 1
    _write_text(
        _native_extension_abi_json(
            symbols,
            provider,
            expected_abi,
            actual_abi,
            abi_mode,
            include_dir,
            require_capsule,
            require_buffer,
            require_memoryview,
            require_numpy_capi,
        )
    )
    if _native_extension_abi_ok(
        symbols,
        expected_abi,
        actual_abi,
        require_capsule,
        require_buffer,
        require_memoryview,
        require_numpy_capi,
        include_dir,
    ):
        return 0
    return 2


def _native_campaign_pattern_match(name: str, pattern: str) -> bool:
    if pattern == "test_*.py":
        return name.startswith("test_") and name.endswith(".py")
    star = _native_find_from(pattern, "*", 0)
    if star < 0:
        return name == pattern
    prefix = pattern[:star]
    suffix = pattern[star + 1 :]
    return name.startswith(prefix) and name.endswith(suffix)


def _native_campaign_filter_match(path: str, includes, excludes) -> bool:
    if len(includes) > 0:
        matched = False
        i = 0
        while i < len(includes):
            if _native_find_from(path, includes[i], 0) >= 0:
                matched = True
            i += 1
        if not matched:
            return False
    i = 0
    while i < len(excludes):
        if _native_find_from(path, excludes[i], 0) >= 0:
            return False
        i += 1
    return True


def _native_campaign_xfail_reason(path: str, xfails):
    i = 0
    while i < len(xfails):
        rule = xfails[i]
        eq = _native_find_from(rule, "=", 0)
        colon = _native_find_from(rule, ":", 0)
        split = eq
        if split < 0 or (colon >= 0 and colon < split):
            split = colon
        if split >= 0:
            token = rule[:split]
            reason = rule[split + 1 :]
        else:
            token = rule
            reason = "unspecified"
        if token != "" and _native_find_from(path, token, 0) >= 0:
            return reason or "unspecified"
        i += 1
    return None


def _native_campaign_profile_root(root: str, profile: str) -> str:
    if profile == "numpy-core-l6":
        nested = root + "/numpy/_core/tests"
        if os.path.isdir(nested):
            return nested
    return root


def _native_campaign_profile_task(path: str, profile: str) -> str:
    if profile != "numpy-core-l6":
        return ""
    name = _native_basename(path)
    if (
        name == "test_multiarray.py"
        or name == "test_numeric.py"
        or name == "test_shape_base.py"
        or name == "test_dtype.py"
    ):
        return "L6.2"
    if name == "test_array_coercion.py" or name == "test_scalarmath.py":
        return "L6.3"
    if name == "test_indexing.py" or name == "test_stride_tricks.py":
        return "L6.4"
    if name == "test_umath.py" or name == "test_ufunc.py":
        return "L6.5"
    if name == "test_arrayprint.py":
        return "L6.6"
    return ""


def _native_campaign_profile_feature(path: str, profile: str) -> str:
    task = _native_campaign_profile_task(path, profile)
    name = _native_basename(path)
    if task == "L6.2":
        return "shape-strides-dtype"
    if name == "test_array_coercion.py":
        return "scalar-coercion"
    if task == "L6.3":
        return "scalar-types"
    if task == "L6.4":
        return "indexing-slicing-broadcast"
    if task == "L6.5":
        return "ufunc-add-sub-mul-div"
    if task == "L6.6":
        return "array-repr-print"
    return ""


def _native_campaign_profile_selected(path: str, profile: str) -> bool:
    if profile == "":
        return True
    return _native_campaign_profile_task(path, profile) != ""


def _native_campaign_json(
    root: str, pattern: str, area: str, includes, excludes, xfails, profile: str
) -> str:
    scan_root = _native_campaign_profile_root(root, profile)
    effective_area = area
    if profile == "numpy-core-l6" and area == "core":
        effective_area = "numpy-core"
    selected = []
    stack = [scan_root]
    while len(stack) > 0:
        current = stack.pop()
        try:
            names = sorted(os.listdir(current))
        except Exception:
            names = []
        i = 0
        while i < len(names):
            name = names[i]
            path = current + "/" + name
            if os.path.isdir(path):
                stack.append(path)
            elif os.path.isfile(path) and _native_campaign_pattern_match(name, pattern):
                if _native_campaign_profile_selected(
                    path, profile
                ) and _native_campaign_filter_match(path, includes, excludes):
                    selected.append(path)
            i += 1
    selected = sorted(selected)

    selected_count = 0
    xfail_count = 0
    xfail_reasons = []
    task_names = []
    records = "["
    i = 0
    while i < len(selected):
        reason = _native_campaign_xfail_reason(selected[i], xfails)
        status = "selected"
        task = _native_campaign_profile_task(selected[i], profile)
        feature = _native_campaign_profile_feature(selected[i], profile)
        if task != "" and not _native_list_contains(task_names, task):
            task_names.append(task)
        if reason is not None:
            status = "xfail"
            xfail_count += 1
            if not _native_list_contains(xfail_reasons, reason):
                xfail_reasons.append(reason)
        else:
            selected_count += 1
            reason = ""
        if records != "[":
            records += ", "
        records += "{"
        records += '"area": ' + _json_str(effective_area)
        if feature != "":
            records += ', "feature": ' + _json_str(feature)
        records += ', "path": ' + _json_str(selected[i])
        if profile != "":
            records += ', "profile": ' + _json_str(profile)
        records += ', "reason": ' + _json_str(reason)
        records += ', "status": ' + _json_str(status)
        if task != "":
            records += ', "task": ' + _json_str(task)
        records += "}"
        i += 1
    records += "]"

    xfail_taxonomy = "{"
    i = 0
    while i < len(xfail_reasons):
        if i > 0:
            xfail_taxonomy += ", "
        reason = xfail_reasons[i]
        count = 0
        j = 0
        while j < len(selected):
            if _native_campaign_xfail_reason(selected[j], xfails) == reason:
                count += 1
            j += 1
        xfail_taxonomy += _json_str(reason) + ": " + str(count)
        i += 1
    xfail_taxonomy += "}"

    task_counts = "{"
    task_names = sorted(task_names)
    i = 0
    while i < len(task_names):
        if i > 0:
            task_counts += ", "
        task = task_names[i]
        count = 0
        j = 0
        while j < len(selected):
            if _native_campaign_profile_task(selected[j], profile) == task:
                count += 1
            j += 1
        task_counts += _json_str(task) + ": " + str(count)
        i += 1
    task_counts += "}"

    dashboard = "{"
    dashboard += '"by_area": {'
    dashboard += _json_str(effective_area) + ": {"
    dashboard += '"fail": 0, "pass": 0, "selected": ' + str(selected_count)
    dashboard += ', "skip": 0, "xfail": ' + str(xfail_count)
    dashboard += "}}"
    dashboard += ', "by_status": {'
    dashboard += '"fail": 0, "pass": 0, "selected": ' + str(selected_count)
    dashboard += ', "skip": 0, "xfail": ' + str(xfail_count)
    dashboard += "}"
    dashboard += ', "total": ' + str(len(selected))
    dashboard += ', "xfail_taxonomy": ' + xfail_taxonomy
    dashboard += "}"

    out = "{"
    out += '"area": ' + _json_str(effective_area)
    out += ', "dashboard": ' + dashboard
    out += ', "exclude": ' + _json_str_list(excludes)
    out += ', "include": ' + _json_str_list(includes)
    out += ', "pattern": ' + _json_str(pattern)
    out += ', "profile": ' + _json_str(profile)
    if profile == "numpy-core-l6":
        out += ', "profile_description": ' + _json_str(
            "NumPy L6 useful core-test subset profile. It selects stable numpy/_core/tests files that map to L6.2-L6.6 feature domains; it does not mark those tests passing."
        )
        out += ', "selection_rule": ' + _json_str(
            "fixed NumPy L6 core-test filename profile under numpy/_core/tests"
        )
    else:
        out += ', "profile_description": ""'
        out += ', "selection_rule": ' + _json_str("pattern/include/exclude")
    out += ', "records": ' + records
    out += ', "root": ' + _json_str(root)
    out += ', "scan_root": ' + _json_str(scan_root)
    out += ', "selected": ' + _json_str_list(selected)
    out += ', "task_counts": ' + task_counts
    out += ', "xfail_rules": ' + _json_str_list(xfails)
    out += "}"
    return out


def _run_native_package_campaign_from_pcc1(module_args) -> int:
    root = None
    pattern = "test_*.py"
    area = "core"
    includes = []
    excludes = []
    xfails = []
    profile = ""
    out_path = None
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--root":
            if i + 1 >= len(module_args):
                _write_text("Error: --root requires a value", err=True)
                return 2
            root = module_args[i + 1]
            i += 1
        elif arg.startswith("--root="):
            root = arg.split("=", 1)[1]
        elif arg == "--pattern":
            if i + 1 >= len(module_args):
                _write_text("Error: --pattern requires a value", err=True)
                return 2
            pattern = module_args[i + 1]
            i += 1
        elif arg.startswith("--pattern="):
            pattern = arg.split("=", 1)[1]
        elif arg == "--area":
            if i + 1 >= len(module_args):
                _write_text("Error: --area requires a value", err=True)
                return 2
            area = module_args[i + 1]
            i += 1
        elif arg.startswith("--area="):
            area = arg.split("=", 1)[1]
        elif arg == "--include":
            if i + 1 >= len(module_args):
                _write_text("Error: --include requires a value", err=True)
                return 2
            includes.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--include="):
            includes.append(arg.split("=", 1)[1])
        elif arg == "--exclude":
            if i + 1 >= len(module_args):
                _write_text("Error: --exclude requires a value", err=True)
                return 2
            excludes.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--exclude="):
            excludes.append(arg.split("=", 1)[1])
        elif arg == "--xfail":
            if i + 1 >= len(module_args):
                _write_text("Error: --xfail requires a value", err=True)
                return 2
            xfails.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--xfail="):
            xfails.append(arg.split("=", 1)[1])
        elif arg == "--profile":
            if i + 1 >= len(module_args):
                _write_text("Error: --profile requires a value", err=True)
                return 2
            profile = module_args[i + 1]
            i += 1
        elif arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
        elif arg == "--out":
            if i + 1 >= len(module_args):
                _write_text("Error: --out requires a value", err=True)
                return 2
            out_path = module_args[i + 1]
            i += 1
        elif arg.startswith("--out="):
            out_path = arg.split("=", 1)[1]
        elif arg == "--json":
            pass
        i += 1
    if root is None:
        _write_text('{"error": "missing --root", "ok": false}')
        return 2
    if profile != "" and profile != "numpy-core-l6":
        _write_text('{"error": "unknown campaign profile", "ok": false}')
        return 2
    report = _native_campaign_json(
        root, pattern, area, includes, excludes, xfails, profile
    )
    if out_path is not None:
        try:
            with open(out_path, "w") as fh:
                fh.write(report)
        except Exception:
            _write_text('{"error": "failed to write campaign report", "ok": false}')
            return 2
    _write_text(report)
    return 0


def _native_array_dtype_itemsize(dtype: str) -> int:
    if dtype == "bool" or dtype == "int8" or dtype == "uint8":
        return 1
    if dtype == "int16" or dtype == "uint16":
        return 2
    if dtype == "int32" or dtype == "uint32" or dtype == "float32":
        return 4
    if dtype == "int64" or dtype == "uint64" or dtype == "float64" or dtype == "object":
        return 8
    return 8


def _native_array_dtype_format(dtype: str) -> str:
    if dtype == "bool":
        return "?"
    if dtype == "int8":
        return "b"
    if dtype == "int16":
        return "h"
    if dtype == "int32":
        return "i"
    if dtype == "int64":
        return "q"
    if dtype == "uint8":
        return "B"
    if dtype == "uint16":
        return "H"
    if dtype == "uint32":
        return "I"
    if dtype == "uint64":
        return "Q"
    if dtype == "float32":
        return "f"
    if dtype == "float64":
        return "d"
    return "O"


def _native_array_normalize_dtype(dtype: str) -> str:
    d = (dtype or "auto").lower()
    if d == "auto" or d == "":
        return "object"
    if d == "bool" or d == "bool_" or d == "boolean":
        return "bool"
    if d == "int8" or d == "byte":
        return "int8"
    if d == "int16" or d == "short":
        return "int16"
    if d == "int32" or d == "intc":
        return "int32"
    if d == "int" or d == "int_" or d == "long" or d == "longlong" or d == "int64":
        return "int64"
    if d == "uint8":
        return "uint8"
    if d == "uint16":
        return "uint16"
    if d == "uint32":
        return "uint32"
    if d == "uint" or d == "uint_" or d == "ulong" or d == "uint64":
        return "uint64"
    if d == "float32" or d == "single":
        return "float32"
    if d == "float" or d == "float_" or d == "double" or d == "float64":
        return "float64"
    if d == "object" or d == "object_" or d == "pyobject":
        return "object"
    return "object"


def _native_array_is_integer_dtype(dtype: str) -> bool:
    return (
        dtype == "int8"
        or dtype == "int16"
        or dtype == "int32"
        or dtype == "int64"
        or dtype == "uint8"
        or dtype == "uint16"
        or dtype == "uint32"
        or dtype == "uint64"
    )


def _native_array_integer_bits(dtype: str) -> int:
    if dtype == "int8" or dtype == "uint8":
        return 8
    if dtype == "int16" or dtype == "uint16":
        return 16
    if dtype == "int32" or dtype == "uint32":
        return 32
    return 64


def _native_array_integer_signed(dtype: str) -> bool:
    return dtype == "int8" or dtype == "int16" or dtype == "int32" or dtype == "int64"


def _native_array_int_pow2(bits: int) -> int:
    value = 1
    i = 0
    while i < bits:
        value *= 2
        i += 1
    return value


def _native_array_wrap_integer(value: int, dtype: str) -> int:
    bits = _native_array_integer_bits(dtype)
    modulo = _native_array_int_pow2(bits)
    wrapped = value % modulo
    if _native_array_integer_signed(dtype):
        sign = _native_array_int_pow2(bits - 1)
        if wrapped >= sign:
            wrapped -= modulo
    return wrapped


def _native_array_dtype_range_json(dtype: str) -> str:
    if dtype == "bool":
        return "[0, 1]"
    if not _native_array_is_integer_dtype(dtype):
        return "null"
    bits = _native_array_integer_bits(dtype)
    if _native_array_integer_signed(dtype):
        low = -_native_array_int_pow2(bits - 1)
        high = _native_array_int_pow2(bits - 1) - 1
        return "[" + str(low) + ", " + str(high) + "]"
    high = _native_array_int_pow2(bits) - 1
    return "[0, " + str(high) + "]"


def _native_array_parse_shape(text: str):
    dims = []
    token = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == ",":
            if token.strip() != "":
                try:
                    dims.append(int(token.strip()))
                except Exception:
                    dims.append(-1)
            token = ""
        else:
            token += ch
        i += 1
    if token.strip() != "":
        try:
            dims.append(int(token.strip()))
        except Exception:
            dims.append(-1)
    return dims


def _native_array_split_commas(text: str):
    parts = []
    token = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == ",":
            if token.strip() != "":
                parts.append(token.strip())
            token = ""
        else:
            token += ch
        i += 1
    if token.strip() != "":
        parts.append(token.strip())
    return parts


def _native_array_size(shape) -> int:
    if len(shape) == 0:
        return 1
    total = 1
    i = 0
    while i < len(shape):
        dim = shape[i]
        if dim == 0:
            return 0
        total *= dim
        i += 1
    return total


def _native_array_strides(shape, itemsize: int):
    strides = []
    i = 0
    while i < len(shape):
        strides.append(0)
        i += 1
    stride = itemsize
    i = len(shape) - 1
    while i >= 0:
        strides[i] = stride
        dim = shape[i]
        if dim > 0:
            stride *= dim
        i -= 1
    return strides


def _native_array_literal_dtype(text: str) -> str:
    has_quote = False
    has_float = False
    has_int = False
    has_bool = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"' or ch == "'":
            has_quote = True
        if ch == ".":
            has_float = True
        if "0" <= ch <= "9":
            has_int = True
        i += 1
    if (
        _native_find_from(text, "True", 0) >= 0
        or _native_find_from(text, "False", 0) >= 0
    ):
        has_bool = True
    if has_quote:
        return "object"
    if has_float:
        return "float64"
    if has_int:
        return "int64"
    if has_bool:
        return "bool"
    return "object"


def _native_array_literal_shape_and_diagnostics(text: str):
    stripped = text.strip()
    diagnostics = []
    if stripped == "" or stripped == "[]":
        diagnostics.append("PCC-ARRAY-EMPTY-DTYPE")
        return [[0], diagnostics]
    if not stripped.startswith("["):
        return [[], diagnostics]
    is_2d = stripped.startswith("[[")
    if is_2d:
        row_counts = []
        depth = 0
        cols = 0
        token = False
        i = 0
        while i < len(stripped):
            ch = stripped[i]
            if ch == "[":
                if depth == 1:
                    cols = 0
                    token = False
                depth += 1
            elif ch == "]":
                if depth == 2:
                    if token:
                        cols += 1
                    row_counts.append(cols)
                    token = False
                depth -= 1
            elif ch == ",":
                if depth == 2 and token:
                    cols += 1
                    token = False
            elif depth == 2 and ch != " " and ch != "\n" and ch != "\t":
                token = True
            i += 1
        if len(row_counts) == 0:
            diagnostics.append("PCC-ARRAY-LITERAL-PARSE-FAILED")
            return [[], diagnostics]
        first = row_counts[0]
        j = 0
        while j < len(row_counts):
            if row_counts[j] != first:
                diagnostics.append("PCC-ARRAY-RAGGED")
                return [[len(row_counts)], diagnostics]
            j += 1
        return [[len(row_counts), first], diagnostics]
    count = 0
    depth = 0
    token = False
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            if depth == 1 and token:
                count += 1
                token = False
            depth -= 1
        elif ch == ",":
            if depth == 1 and token:
                count += 1
                token = False
        elif depth == 1 and ch != " " and ch != "\n" and ch != "\t":
            token = True
        i += 1
    return [[count], diagnostics]


def _native_array_literal_values(text: str):
    stripped = text.strip()
    if stripped == "":
        return []
    if not stripped.startswith("["):
        return [stripped]
    values = []
    token = ""
    quote = ""
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if quote != "":
            token += ch
            if ch == quote:
                values.append(token.strip())
                token = ""
                quote = ""
        elif ch == '"' or ch == "'":
            quote = ch
            token = ch
        elif (
            ch == "[" or ch == "]" or ch == "," or ch == " " or ch == "\n" or ch == "\t"
        ):
            if token.strip() != "":
                values.append(token.strip())
                token = ""
        else:
            token += ch
        i += 1
    if token.strip() != "":
        values.append(token.strip())
    return values


def _native_array_token_json(token: str) -> str:
    stripped = token.strip()
    if stripped == "True":
        return "true"
    if stripped == "False":
        return "false"
    if stripped.startswith('"') or stripped.startswith("'"):
        inner = stripped[1:]
        if len(inner) > 0 and (inner.endswith('"') or inner.endswith("'")):
            inner = inner[:-1]
        return _json_str(inner)
    return stripped


def _native_array_values_json(values) -> str:
    out = "["
    i = 0
    while i < len(values):
        if i > 0:
            out += ", "
        out += _native_array_token_json(values[i])
        i += 1
    out += "]"
    return out


def _native_array_data_json(shape, values) -> str:
    if len(shape) > 0 and len(values) != _native_array_size(shape):
        return _native_array_values_json(values)
    if len(shape) == 0:
        if len(values) == 0:
            return "null"
        return _native_array_token_json(values[0])
    if len(shape) == 1:
        return _native_array_values_json(values)
    if len(shape) == 2:
        rows = shape[0]
        cols = shape[1]
        out = "["
        r = 0
        while r < rows:
            if r > 0:
                out += ", "
            row = []
            c = 0
            while c < cols:
                pos = r * cols + c
                if pos < len(values):
                    row.append(values[pos])
                c += 1
            out += _native_array_values_json(row)
            r += 1
        out += "]"
        return out
    return _native_array_values_json(values)


def _native_array_repr(shape, values) -> str:
    return "array(" + _native_array_data_json(shape, values) + ")"


def _native_array_is_float_token(token: str) -> bool:
    return (
        _native_find_from(token, ".", 0) >= 0
        or _native_find_from(token, "e", 0) >= 0
        or _native_find_from(token, "E", 0) >= 0
    )


def _native_array_op_dtype(op: str, left_dtype: str, right_dtype: str) -> str:
    if left_dtype == "object" or right_dtype == "object":
        return "object"
    if op == "div":
        return "float64"
    if left_dtype == right_dtype:
        return left_dtype
    if left_dtype == "float64" or right_dtype == "float64":
        return "float64"
    if left_dtype == "float32" or right_dtype == "float32":
        return "float32"
    if left_dtype != "bool" or right_dtype != "bool":
        return "int64"
    return "bool"


def _native_array_token_to_scaled(token: str) -> int:
    if token == "True":
        return 1000000
    if token == "False":
        return 0
    stripped = token.strip()
    negative = False
    if stripped.startswith("-"):
        negative = True
        stripped = stripped[1:]
    whole = stripped
    frac = ""
    dot = _native_find_from(stripped, ".", 0)
    if dot >= 0:
        whole = stripped[:dot]
        frac = stripped[dot + 1 :]
    if whole == "":
        whole_value = 0
    else:
        whole_value = int(whole)
    value = whole_value * 1000000
    scale = 100000
    i = 0
    while i < len(frac) and i < 6:
        ch = frac[i]
        if "0" <= ch <= "9":
            value += int(ch) * scale
        scale //= 10
        i += 1
    if negative:
        return -value
    return value


def _native_array_scaled_to_token(value: int, dtype: str) -> str:
    if dtype == "float32" or dtype == "float64":
        negative = value < 0
        if negative:
            value = -value
        whole = value // 1000000
        frac = value % 1000000
        text = str(whole) + "."
        scale = 100000
        while scale > 0:
            digit = frac // scale
            text += str(digit)
            frac = frac - digit * scale
            scale //= 10
        while len(text) > 0 and text.endswith("0"):
            text = text[:-1]
        if text.endswith("."):
            text += "0"
        if negative:
            text = "-" + text
        return text
    if dtype == "bool":
        return "True" if value != 0 else "False"
    if value < 0:
        integer = -((-value) // 1000000)
    else:
        integer = value // 1000000
    if _native_array_is_integer_dtype(dtype):
        integer = _native_array_wrap_integer(integer, dtype)
    return str(integer)


def _native_array_token_is_scaled_number(token: str) -> bool:
    stripped = token.strip()
    if stripped == "":
        return False
    i = 0
    if stripped[0] == "-" or stripped[0] == "+":
        i = 1
    if i >= len(stripped):
        return False
    saw_digit = False
    saw_dot = False
    while i < len(stripped):
        ch = stripped[i]
        if "0" <= ch <= "9":
            saw_digit = True
        elif ch == "." and not saw_dot:
            saw_dot = True
        else:
            return False
        i += 1
    return saw_digit


def _native_array_arange_uses_float(arange_text: str) -> bool:
    parts = _native_array_split_commas(arange_text)
    i = 0
    while i < len(parts):
        if _native_find_from(parts[i], ".", 0) >= 0:
            return True
        i += 1
    return False


def _native_array_cast_values(values, dtype: str):
    out = []
    i = 0
    while i < len(values):
        if dtype == "object":
            out.append(values[i])
        else:
            out.append(
                _native_array_scaled_to_token(
                    _native_array_token_to_scaled(values[i]), dtype
                )
            )
        i += 1
    return out


def _native_array_apply_op(left: str, right: str, op: str, dtype: str) -> str:
    lv = _native_array_token_to_scaled(left)
    rv = _native_array_token_to_scaled(right)
    if op == "add":
        result = lv + rv
    elif op == "sub":
        result = lv - rv
    elif op == "mul":
        result = (lv * rv) // 1000000
    else:
        result = (lv * 1000000) // rv
    return _native_array_scaled_to_token(result, dtype)


def _native_array_unary_op_name(op: str) -> str:
    if op == "negative":
        return "neg"
    if op == "absolute":
        return "abs"
    if op == "not":
        return "logical_not"
    return op


def _native_array_unary_op(shape, values, dtype: str, op: str, diagnostics):
    op_name = _native_array_unary_op_name(op)
    if not (op_name == "neg" or op_name == "abs" or op_name == "logical_not"):
        diagnostics.append("PCC-ARRAY-UNARY-UNSUPPORTED")
        return [shape, [], dtype]
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-OBJECT-UNARY-UNSUPPORTED")
        return [shape, [], dtype]
    if dtype == "bool" and op_name == "neg":
        diagnostics.append("PCC-ARRAY-UNARY-DTYPE-UNSUPPORTED")
        return [shape, [], dtype]
    out_dtype = dtype
    if op_name == "logical_not":
        out_dtype = "bool"
    out = []
    i = 0
    while i < len(values):
        scaled = _native_array_token_to_scaled(values[i])
        if op_name == "neg":
            result = -scaled
        elif op_name == "abs":
            if scaled < 0:
                result = -scaled
            else:
                result = scaled
        else:
            result = 0
            if scaled == 0:
                result = 1000000
        out.append(_native_array_scaled_to_token(result, out_dtype))
        i += 1
    return [shape, out, out_dtype]


def _native_array_clip(shape, values, dtype: str, clip_text: str, diagnostics):
    comma = _native_find_from(clip_text, ",", 0)
    if comma < 0:
        diagnostics.append("PCC-ARRAY-CLIP-PARSE-FAILED")
        return [shape, [], dtype]
    lower_text = clip_text[:comma].strip()
    upper_text = clip_text[comma + 1 :].strip()
    if lower_text == "" or upper_text == "":
        diagnostics.append("PCC-ARRAY-CLIP-PARSE-FAILED")
        return [shape, [], dtype]
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-OBJECT-CLIP-UNSUPPORTED")
        return [shape, [], dtype]
    lower = _native_array_token_to_scaled(lower_text)
    upper = _native_array_token_to_scaled(upper_text)
    out_dtype = dtype
    if _native_array_is_float_token(lower_text) or _native_array_is_float_token(
        upper_text
    ):
        out_dtype = "float64"
    out = []
    i = 0
    while i < len(values):
        current = _native_array_token_to_scaled(values[i])
        if current < lower:
            current = lower
        if current > upper:
            current = upper
        out.append(_native_array_scaled_to_token(current, out_dtype))
        i += 1
    return [shape, out, out_dtype]


def _native_array_broadcast_shape(left_shape, right_shape, diagnostics):
    reversed_out = []
    max_rank = len(left_shape)
    if len(right_shape) > max_rank:
        max_rank = len(right_shape)
    offset = 1
    while offset <= max_rank:
        ldim = 1
        rdim = 1
        if offset <= len(left_shape):
            ldim = left_shape[len(left_shape) - offset]
        if offset <= len(right_shape):
            rdim = right_shape[len(right_shape) - offset]
        if ldim == rdim:
            reversed_out.append(ldim)
        elif ldim == 1:
            reversed_out.append(rdim)
        elif rdim == 1:
            reversed_out.append(ldim)
        else:
            diagnostics.append("PCC-ARRAY-BROADCAST-INCOMPATIBLE")
            return []
        offset += 1
    out = []
    i = len(reversed_out) - 1
    while i >= 0:
        out.append(reversed_out[i])
        i -= 1
    return out


def _native_array_flat_index(shape, indices) -> int:
    if len(shape) == 0:
        return 0
    flat = 0
    stride = 1
    axis = len(shape) - 1
    while axis >= 0:
        flat += indices[axis] * stride
        stride *= shape[axis]
        axis -= 1
    return flat


def _native_array_broadcast_flat_index(shape, out_index) -> int:
    if len(shape) == 0:
        return 0
    source = []
    offset = len(out_index) - len(shape)
    i = 0
    while i < len(shape):
        value = out_index[offset + i]
        if shape[i] == 1:
            source.append(0)
        else:
            source.append(value)
        i += 1
    return _native_array_flat_index(shape, source)


def _native_array_broadcast_to_strides(shape, dtype: str, target_shape):
    if len(shape) > len(target_shape):
        return []
    source_strides = _native_array_strides(shape, _native_array_dtype_itemsize(dtype))
    offset = len(target_shape) - len(shape)
    out = []
    axis = 0
    while axis < len(target_shape):
        if axis < offset:
            out.append(0)
        else:
            source_axis = axis - offset
            source_dim = shape[source_axis]
            target_dim = target_shape[axis]
            if source_dim == target_dim:
                out.append(source_strides[source_axis])
            elif source_dim == 1:
                out.append(0)
            else:
                return []
        axis += 1
    return out


def _native_array_broadcast_to(
    shape, values, dtype: str, target_text: str, diagnostics
):
    target_shape = _native_array_parse_shape(target_text)
    local_diags = []
    out_shape = _native_array_broadcast_shape(shape, target_shape, local_diags)
    strides = _native_array_broadcast_to_strides(shape, dtype, target_shape)
    if (
        len(local_diags) > 0
        or out_shape != target_shape
        or len(strides) != len(target_shape)
    ):
        diagnostics.append("PCC-ARRAY-BROADCAST-TO-SHAPE-MISMATCH")
        return [
            target_shape,
            [],
            dtype,
            _native_array_strides(target_shape, _native_array_dtype_itemsize(dtype)),
            True,
        ]
    out = []
    if len(target_shape) == 0:
        out.append(values[0])
    elif len(target_shape) == 1:
        i = 0
        while i < target_shape[0]:
            out_index = [i]
            out.append(values[_native_array_broadcast_flat_index(shape, out_index)])
            i += 1
    elif len(target_shape) == 2:
        r = 0
        while r < target_shape[0]:
            c = 0
            while c < target_shape[1]:
                out_index = [r, c]
                out.append(values[_native_array_broadcast_flat_index(shape, out_index)])
                c += 1
            r += 1
    else:
        diagnostics.append("PCC-ARRAY-RANK-UNSUPPORTED")
        return [
            target_shape,
            [],
            dtype,
            _native_array_strides(target_shape, _native_array_dtype_itemsize(dtype)),
            False,
        ]
    c_contiguous = shape == target_shape
    return [target_shape, out, dtype, strides, c_contiguous]


def _native_array_repeat(
    shape, values, dtype: str, repeats_text: str, axis_text: str, diagnostics
):
    repeats = int(repeats_text)
    if repeats < 0:
        diagnostics.append("PCC-ARRAY-REPEAT-NEGATIVE")
        return [[], [], dtype]
    out = []
    if axis_text == "":
        i = 0
        while i < len(values):
            j = 0
            while j < repeats:
                out.append(values[i])
                j += 1
            i += 1
        return [[len(out)], out, dtype]
    axis = int(axis_text)
    if axis < 0:
        axis += len(shape)
    if axis < 0 or axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    if len(shape) == 1:
        return _native_array_repeat(shape, values, dtype, repeats_text, "", diagnostics)
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-REPEAT-RANK-UNSUPPORTED")
        return [[], [], dtype]
    rows = shape[0]
    cols = shape[1]
    if axis == 0:
        r = 0
        while r < rows:
            j = 0
            while j < repeats:
                c = 0
                while c < cols:
                    out.append(values[r * cols + c])
                    c += 1
                j += 1
            r += 1
        return [[rows * repeats, cols], out, dtype]
    r = 0
    while r < rows:
        c = 0
        while c < cols:
            j = 0
            while j < repeats:
                out.append(values[r * cols + c])
                j += 1
            c += 1
        r += 1
    return [[rows, cols * repeats], out, dtype]


def _native_array_tile_reps(shape, values, dtype: str, reps, diagnostics):
    if len(reps) == 0:
        diagnostics.append("PCC-ARRAY-TILE-REPS-EMPTY")
        return [[], [], dtype]
    i = 0
    while i < len(reps):
        if reps[i] < 0:
            diagnostics.append("PCC-ARRAY-TILE-REPS-NEGATIVE")
            return [[], [], dtype]
        i += 1
    out = []
    if len(shape) == 0:
        size = _native_array_size(reps)
        i = 0
        while i < size:
            out.append(values[0])
            i += 1
        return [reps, out, dtype]
    if len(shape) == 1:
        if len(reps) == 1:
            r = 0
            while r < reps[0]:
                i = 0
                while i < len(values):
                    out.append(values[i])
                    i += 1
                r += 1
            return [[len(out)], out, dtype]
        if len(reps) == 2:
            row_reps = reps[0]
            col_reps = reps[1]
            r = 0
            while r < row_reps:
                c = 0
                while c < col_reps:
                    i = 0
                    while i < len(values):
                        out.append(values[i])
                        i += 1
                    c += 1
                r += 1
            return [[row_reps, shape[0] * col_reps], out, dtype]
    if len(shape) == 2 and (len(reps) == 1 or len(reps) == 2):
        row_reps = 1
        col_reps = reps[0]
        if len(reps) == 2:
            row_reps = reps[0]
            col_reps = reps[1]
        rows = shape[0]
        cols = shape[1]
        rr = 0
        while rr < row_reps:
            r = 0
            while r < rows:
                cc = 0
                while cc < col_reps:
                    c = 0
                    while c < cols:
                        out.append(values[r * cols + c])
                        c += 1
                    cc += 1
                r += 1
            rr += 1
        return [[rows * row_reps, cols * col_reps], out, dtype]
    diagnostics.append("PCC-ARRAY-TILE-RANK-UNSUPPORTED")
    return [[], [], dtype]


def _native_array_tile(shape, values, dtype: str, tile_text: str, diagnostics):
    return _native_array_tile_reps(
        shape,
        values,
        dtype,
        _native_array_parse_shape(tile_text),
        diagnostics,
    )


def _native_array_roll(
    shape, values, dtype: str, shift_text: str, axis_text: str, diagnostics
):
    shift = int(shift_text)
    if _native_array_size(shape) == 0:
        return [shape, [], dtype]
    out = []
    axis = _native_array_axis_value(axis_text)
    if axis == -999999:
        offset = shift % len(values)
        i = 0
        while i < len(values):
            source = (i - offset) % len(values)
            out.append(values[source])
            i += 1
        return [shape, out, dtype]
    normalized_axis = _native_array_axis_normalize(axis, len(shape))
    if normalized_axis < 0 or normalized_axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [shape, [], dtype]
    if len(shape) == 0:
        return [shape, _native_array_copy_values(values), dtype]
    if len(shape) == 1:
        return _native_array_roll(shape, values, dtype, shift_text, "", diagnostics)
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-ROLL-RANK-UNSUPPORTED")
        return [shape, [], dtype]
    rows = shape[0]
    cols = shape[1]
    if normalized_axis == 0:
        offset = shift % rows if rows > 0 else 0
        r = 0
        while r < rows:
            source_row = (r - offset) % rows
            c = 0
            while c < cols:
                out.append(values[source_row * cols + c])
                c += 1
            r += 1
    else:
        offset = shift % cols if cols > 0 else 0
        r = 0
        while r < rows:
            c = 0
            while c < cols:
                source_col = (c - offset) % cols
                out.append(values[r * cols + source_col])
                c += 1
            r += 1
    return [shape, out, dtype]


def _native_array_binary_op(
    left_shape, left_values, left_dtype: str, right_text: str, op: str, diagnostics
):
    right_shape = []
    if right_text.strip().startswith("["):
        parsed = _native_array_literal_shape_and_diagnostics(right_text)
        right_shape = parsed[0]
        right_diags = parsed[1]
        i = 0
        while i < len(right_diags):
            diagnostics.append(right_diags[i])
            i += 1
    right_values = _native_array_literal_values(right_text)
    right_dtype = _native_array_literal_dtype(right_text)
    op_name = op
    if op_name == "subtract":
        op_name = "sub"
    elif op_name == "multiply":
        op_name = "mul"
    elif op_name == "divide":
        op_name = "div"
    if not (
        op_name == "add" or op_name == "sub" or op_name == "mul" or op_name == "div"
    ):
        diagnostics.append("PCC-ARRAY-UFUNC-UNSUPPORTED")
        return [[], [], "object"]
    out_shape = _native_array_broadcast_shape(left_shape, right_shape, diagnostics)
    dtype = _native_array_op_dtype(op_name, left_dtype, right_dtype)
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-OBJECT-UFUNC-UNSUPPORTED")
    if len(diagnostics) > 0:
        return [out_shape, [], dtype]
    out_values = []
    if len(out_shape) == 0:
        out_values.append(
            _native_array_apply_op(left_values[0], right_values[0], op_name, dtype)
        )
    elif len(out_shape) == 1:
        i = 0
        while i < out_shape[0]:
            out_index = [i]
            lv = left_values[_native_array_broadcast_flat_index(left_shape, out_index)]
            rv = right_values[
                _native_array_broadcast_flat_index(right_shape, out_index)
            ]
            out_values.append(_native_array_apply_op(lv, rv, op_name, dtype))
            i += 1
    elif len(out_shape) == 2:
        r = 0
        while r < out_shape[0]:
            c = 0
            while c < out_shape[1]:
                out_index = [r, c]
                lv = left_values[
                    _native_array_broadcast_flat_index(left_shape, out_index)
                ]
                rv = right_values[
                    _native_array_broadcast_flat_index(right_shape, out_index)
                ]
                out_values.append(_native_array_apply_op(lv, rv, op_name, dtype))
                c += 1
            r += 1
    else:
        diagnostics.append("PCC-ARRAY-RANK-UNSUPPORTED")
        return [out_shape, [], dtype]
    return [out_shape, out_values, dtype]


def _native_array_matmul_dtype(left_dtype: str, right_dtype: str) -> str:
    dtype = _native_array_op_dtype("mul", left_dtype, right_dtype)
    if dtype == "bool":
        return "int64"
    return dtype


def _native_array_matmul_token_sum(total: int, left: str, right: str) -> int:
    return (
        total
        + (_native_array_token_to_scaled(left) * _native_array_token_to_scaled(right))
        // 1000000
    )


def _native_array_matmul(
    left_shape, left_values, left_dtype: str, right_text: str, diagnostics
):
    right_shape = []
    if right_text.strip().startswith("["):
        parsed = _native_array_literal_shape_and_diagnostics(right_text)
        right_shape = parsed[0]
        right_diags = parsed[1]
        i = 0
        while i < len(right_diags):
            diagnostics.append(right_diags[i])
            i += 1
    right_values = _native_array_literal_values(right_text)
    right_dtype = _native_array_literal_dtype(right_text)
    dtype = _native_array_matmul_dtype(left_dtype, right_dtype)
    if left_dtype == "object" or right_dtype == "object":
        diagnostics.append("PCC-ARRAY-MATMUL-OBJECT-UNSUPPORTED")
    out_shape = []
    left_rank = len(left_shape)
    right_rank = len(right_shape)
    if not (
        (left_rank == 1 or left_rank == 2) and (right_rank == 1 or right_rank == 2)
    ):
        diagnostics.append("PCC-ARRAY-MATMUL-RANK-UNSUPPORTED")
    elif left_rank == 1 and right_rank == 1:
        if left_shape[0] != right_shape[0]:
            diagnostics.append("PCC-ARRAY-MATMUL-SHAPE-MISMATCH")
    elif left_rank == 2 and right_rank == 1:
        if left_shape[1] != right_shape[0]:
            diagnostics.append("PCC-ARRAY-MATMUL-SHAPE-MISMATCH")
        out_shape = [left_shape[0]]
    elif left_rank == 1 and right_rank == 2:
        if left_shape[0] != right_shape[0]:
            diagnostics.append("PCC-ARRAY-MATMUL-SHAPE-MISMATCH")
        out_shape = [right_shape[1]]
    else:
        if left_shape[1] != right_shape[0]:
            diagnostics.append("PCC-ARRAY-MATMUL-SHAPE-MISMATCH")
        out_shape = [left_shape[0], right_shape[1]]
    if len(diagnostics) > 0:
        return [out_shape, [], dtype]
    out = []
    if left_rank == 1 and right_rank == 1:
        total = 0
        i = 0
        while i < left_shape[0]:
            total = _native_array_matmul_token_sum(
                total, left_values[i], right_values[i]
            )
            i += 1
        out.append(_native_array_scaled_to_token(total, dtype))
        return [[], out, dtype]
    if left_rank == 2 and right_rank == 1:
        rows = left_shape[0]
        inner = left_shape[1]
        r = 0
        while r < rows:
            total = 0
            i = 0
            while i < inner:
                total = _native_array_matmul_token_sum(
                    total, left_values[r * inner + i], right_values[i]
                )
                i += 1
            out.append(_native_array_scaled_to_token(total, dtype))
            r += 1
        return [out_shape, out, dtype]
    if left_rank == 1 and right_rank == 2:
        inner = right_shape[0]
        cols = right_shape[1]
        c = 0
        while c < cols:
            total = 0
            i = 0
            while i < inner:
                total = _native_array_matmul_token_sum(
                    total, left_values[i], right_values[i * cols + c]
                )
                i += 1
            out.append(_native_array_scaled_to_token(total, dtype))
            c += 1
        return [out_shape, out, dtype]
    rows = left_shape[0]
    inner = left_shape[1]
    cols = right_shape[1]
    r = 0
    while r < rows:
        c = 0
        while c < cols:
            total = 0
            i = 0
            while i < inner:
                total = _native_array_matmul_token_sum(
                    total, left_values[r * inner + i], right_values[i * cols + c]
                )
                i += 1
            out.append(_native_array_scaled_to_token(total, dtype))
            c += 1
        r += 1
    return [out_shape, out, dtype]


def _native_array_concat(
    left_shape,
    left_values,
    left_dtype: str,
    right_text: str,
    axis_text: str,
    diagnostics,
):
    right_shape = []
    if right_text.strip().startswith("["):
        parsed = _native_array_literal_shape_and_diagnostics(right_text)
        right_shape = parsed[0]
        right_diags = parsed[1]
        i = 0
        while i < len(right_diags):
            diagnostics.append(right_diags[i])
            i += 1
    right_values = _native_array_literal_values(right_text)
    right_dtype = _native_array_literal_dtype(right_text)
    dtype = _native_array_op_dtype("add", left_dtype, right_dtype)
    if len(left_shape) != len(right_shape):
        diagnostics.append("PCC-ARRAY-CONCAT-RANK-MISMATCH")
        return [[], [], dtype]
    axis = 0
    if axis_text != "":
        axis = int(axis_text)
    if axis < 0:
        axis += len(left_shape)
    if axis < 0 or axis >= len(left_shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    out = []
    if len(left_shape) == 1:
        if axis != 0:
            diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
            return [[], [], dtype]
        i = 0
        while i < len(left_values):
            out.append(left_values[i])
            i += 1
        i = 0
        while i < len(right_values):
            out.append(right_values[i])
            i += 1
        return [
            [left_shape[0] + right_shape[0]],
            _native_array_cast_values(out, dtype),
            dtype,
        ]
    if len(left_shape) != 2:
        diagnostics.append("PCC-ARRAY-CONCAT-RANK-UNSUPPORTED")
        return [[], [], dtype]
    left_rows = left_shape[0]
    left_cols = left_shape[1]
    right_rows = right_shape[0]
    right_cols = right_shape[1]
    if axis == 0:
        if left_cols != right_cols:
            diagnostics.append("PCC-ARRAY-CONCAT-SHAPE-MISMATCH")
            return [[left_rows + right_rows, left_cols], [], dtype]
        i = 0
        while i < len(left_values):
            out.append(left_values[i])
            i += 1
        i = 0
        while i < len(right_values):
            out.append(right_values[i])
            i += 1
        return [
            [left_rows + right_rows, left_cols],
            _native_array_cast_values(out, dtype),
            dtype,
        ]
    if left_rows != right_rows:
        diagnostics.append("PCC-ARRAY-CONCAT-SHAPE-MISMATCH")
        return [[left_rows, left_cols + right_cols], [], dtype]
    r = 0
    while r < left_rows:
        c = 0
        while c < left_cols:
            out.append(left_values[r * left_cols + c])
            c += 1
        c = 0
        while c < right_cols:
            out.append(right_values[r * right_cols + c])
            c += 1
        r += 1
    return [
        [left_rows, left_cols + right_cols],
        _native_array_cast_values(out, dtype),
        dtype,
    ]


def _native_array_stack(
    left_shape,
    left_values,
    left_dtype: str,
    right_text: str,
    axis_text: str,
    diagnostics,
):
    right_shape = []
    if right_text.strip().startswith("["):
        parsed = _native_array_literal_shape_and_diagnostics(right_text)
        right_shape = parsed[0]
        right_diags = parsed[1]
        i = 0
        while i < len(right_diags):
            diagnostics.append(right_diags[i])
            i += 1
    right_values = _native_array_literal_values(right_text)
    right_dtype = _native_array_literal_dtype(right_text)
    dtype = _native_array_op_dtype("add", left_dtype, right_dtype)
    if len(left_shape) != 1 or len(right_shape) != 1:
        diagnostics.append("PCC-ARRAY-STACK-RANK-UNSUPPORTED")
        return [[], [], dtype]
    if left_shape[0] != right_shape[0]:
        diagnostics.append("PCC-ARRAY-STACK-SHAPE-MISMATCH")
        return [[], [], dtype]
    axis = 0
    if axis_text != "":
        axis = int(axis_text)
    if axis < 0:
        axis += 2
    out = []
    if axis == 0:
        i = 0
        while i < len(left_values):
            out.append(left_values[i])
            i += 1
        i = 0
        while i < len(right_values):
            out.append(right_values[i])
            i += 1
        return [[2, left_shape[0]], _native_array_cast_values(out, dtype), dtype]
    if axis == 1:
        i = 0
        while i < left_shape[0]:
            out.append(left_values[i])
            out.append(right_values[i])
            i += 1
        return [[left_shape[0], 2], _native_array_cast_values(out, dtype), dtype]
    diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
    return [[], [], dtype]


def _native_array_compare_token(
    left: str, right: str, op: str, left_dtype: str, right_dtype: str
) -> str:
    if left_dtype == "object" or right_dtype == "object":
        if op == "eq":
            return "True" if left == right else "False"
        if op == "ne":
            return "True" if left != right else "False"
        return "False"
    lv = _native_array_token_to_scaled(left)
    rv = _native_array_token_to_scaled(right)
    if op == "eq":
        ok = lv == rv
    elif op == "ne":
        ok = lv != rv
    elif op == "lt":
        ok = lv < rv
    elif op == "le":
        ok = lv <= rv
    elif op == "gt":
        ok = lv > rv
    else:
        ok = lv >= rv
    return "True" if ok else "False"


def _native_array_compare(
    left_shape, left_values, left_dtype: str, right_text: str, op: str, diagnostics
):
    op_name = op
    if op_name == "==":
        op_name = "eq"
    elif op_name == "!=":
        op_name = "ne"
    elif op_name == "<":
        op_name = "lt"
    elif op_name == "<=":
        op_name = "le"
    elif op_name == ">":
        op_name = "gt"
    elif op_name == ">=":
        op_name = "ge"
    if not (
        op_name == "eq"
        or op_name == "ne"
        or op_name == "lt"
        or op_name == "le"
        or op_name == "gt"
        or op_name == "ge"
    ):
        diagnostics.append("PCC-ARRAY-COMPARE-UNSUPPORTED")
        return [[], [], "bool"]
    right_shape = []
    if right_text.strip().startswith("["):
        parsed = _native_array_literal_shape_and_diagnostics(right_text)
        right_shape = parsed[0]
        right_diags = parsed[1]
        i = 0
        while i < len(right_diags):
            diagnostics.append(right_diags[i])
            i += 1
    right_values = _native_array_literal_values(right_text)
    right_dtype = _native_array_literal_dtype(right_text)
    if (left_dtype == "object" or right_dtype == "object") and not (
        op_name == "eq" or op_name == "ne"
    ):
        diagnostics.append("PCC-ARRAY-OBJECT-COMPARE-UNSUPPORTED")
    out_shape = _native_array_broadcast_shape(left_shape, right_shape, diagnostics)
    if len(diagnostics) > 0:
        return [out_shape, [], "bool"]
    out_values = []
    if len(out_shape) == 0:
        out_values.append(
            _native_array_compare_token(
                left_values[0], right_values[0], op_name, left_dtype, right_dtype
            )
        )
    elif len(out_shape) == 1:
        i = 0
        while i < out_shape[0]:
            out_index = [i]
            lv = left_values[_native_array_broadcast_flat_index(left_shape, out_index)]
            rv = right_values[
                _native_array_broadcast_flat_index(right_shape, out_index)
            ]
            out_values.append(
                _native_array_compare_token(lv, rv, op_name, left_dtype, right_dtype)
            )
            i += 1
    elif len(out_shape) == 2:
        r = 0
        while r < out_shape[0]:
            c = 0
            while c < out_shape[1]:
                out_index = [r, c]
                lv = left_values[
                    _native_array_broadcast_flat_index(left_shape, out_index)
                ]
                rv = right_values[
                    _native_array_broadcast_flat_index(right_shape, out_index)
                ]
                out_values.append(
                    _native_array_compare_token(
                        lv, rv, op_name, left_dtype, right_dtype
                    )
                )
                c += 1
            r += 1
    else:
        diagnostics.append("PCC-ARRAY-RANK-UNSUPPORTED")
        return [out_shape, [], "bool"]
    return [out_shape, out_values, "bool"]


def _native_array_parse_slice_indices(text: str, dim: int, diagnostics):
    token = text.strip()
    parts = []
    current = ""
    i = 0
    while i < len(token):
        ch = token[i]
        if ch == ":":
            parts.append(current.strip())
            current = ""
        else:
            current += ch
        i += 1
    parts.append(current.strip())
    if len(parts) == 1:
        if parts[0] == "":
            diagnostics.append("PCC-ARRAY-INDEX-PARSE-FAILED")
            return [[], True]
        index = int(token)
        if index < 0:
            index += dim
        if index < 0 or index >= dim:
            diagnostics.append("PCC-ARRAY-INDEX-OUT-OF-BOUNDS")
            return [[], True]
        return [[index], True]
    if len(parts) > 3:
        diagnostics.append("PCC-ARRAY-INDEX-PARSE-FAILED")
        return [[], False]
    step = 1
    if len(parts) == 3 and parts[2] != "":
        step = int(parts[2])
    if step == 0:
        diagnostics.append("PCC-ARRAY-INDEX-PARSE-FAILED")
        return [[], False]
    out = []
    if step > 0:
        start = 0 if parts[0] == "" else int(parts[0])
        stop = dim if len(parts) < 2 or parts[1] == "" else int(parts[1])
        if start < 0:
            start += dim
        if stop < 0:
            stop += dim
        if start < 0:
            start = 0
        if start > dim:
            start = dim
        if stop < 0:
            stop = 0
        if stop > dim:
            stop = dim
        i = start
        while i < stop:
            out.append(i)
            i += step
    else:
        start = dim - 1 if parts[0] == "" else int(parts[0])
        stop = -1
        if len(parts) >= 2 and parts[1] != "":
            stop = int(parts[1])
            if stop < 0:
                stop += dim
        if start < 0:
            start += dim
        if start >= dim:
            start = dim - 1
        if start < -1:
            start = -1
        if stop >= dim:
            stop = dim - 1
        if stop < -1:
            stop = -1
        i = start
        while i > stop:
            out.append(i)
            i += step
    return [out, False]


def _native_array_is_newaxis_token(text: str) -> bool:
    token = text.strip()
    return token == "None" or token == "none" or token == "newaxis"


def _native_array_index(shape, values, dtype: str, index_spec: str, diagnostics):
    parts = []
    token = ""
    i = 0
    while i < len(index_spec):
        ch = index_spec[i]
        if ch == ",":
            parts.append(token.strip())
            token = ""
        else:
            token += ch
        i += 1
    if token.strip() != "":
        parts.append(token.strip())
    ellipsis_count = 0
    consumed = 0
    i = 0
    while i < len(parts):
        part = parts[i]
        if part == "...":
            ellipsis_count += 1
        elif _native_array_is_newaxis_token(part):
            pass
        else:
            consumed += 1
        i += 1
    if ellipsis_count > 1:
        diagnostics.append("PCC-ARRAY-INDEX-PARSE-FAILED")
        return [[], [], dtype]
    if ellipsis_count == 0:
        if consumed != len(shape):
            diagnostics.append("PCC-ARRAY-INDEX-RANK-MISMATCH")
            return [[], [], dtype]
    else:
        fill = len(shape) - consumed
        if fill < 0:
            diagnostics.append("PCC-ARRAY-INDEX-RANK-MISMATCH")
            return [[], [], dtype]
        expanded = []
        i = 0
        while i < len(parts):
            part = parts[i]
            if part == "...":
                j = 0
                while j < fill:
                    expanded.append(":")
                    j += 1
            else:
                expanded.append(part)
            i += 1
        parts = expanded
    if len(shape) > 2:
        diagnostics.append("PCC-ARRAY-RANK-UNSUPPORTED")
        return [[], [], dtype]
    source_axes = []
    output_shape = []
    source_axis = 0
    i = 0
    while i < len(parts):
        part = parts[i]
        if _native_array_is_newaxis_token(part):
            output_shape.append(1)
        else:
            if source_axis >= len(shape):
                diagnostics.append("PCC-ARRAY-INDEX-RANK-MISMATCH")
                return [[], [], dtype]
            parsed = _native_array_parse_slice_indices(
                part, shape[source_axis], diagnostics
            )
            if len(diagnostics) > 0:
                return [output_shape, [], dtype]
            indices = parsed[0]
            scalar = parsed[1]
            source_axes.append(indices)
            if not scalar:
                output_shape.append(len(indices))
            source_axis += 1
        i += 1
    if source_axis != len(shape):
        diagnostics.append("PCC-ARRAY-INDEX-RANK-MISMATCH")
        return [output_shape, [], dtype]
    out_values = []
    if len(shape) == 0:
        out_values = _native_array_copy_values(values)
    elif len(shape) == 1:
        i = 0
        while i < len(source_axes[0]):
            out_values.append(values[source_axes[0][i]])
            i += 1
    else:
        ri = 0
        while ri < len(source_axes[0]):
            ci = 0
            while ci < len(source_axes[1]):
                out_values.append(
                    values[source_axes[0][ri] * shape[1] + source_axes[1][ci]]
                )
                ci += 1
            ri += 1
    return [output_shape, out_values, dtype]


def _native_array_diagonal(shape, values, dtype: str, diagonal_text: str, diagnostics):
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-DIAGONAL-RANK-UNSUPPORTED")
        return [[], [], dtype]
    offset = 0
    if diagonal_text != "":
        offset = int(diagonal_text)
    rows = shape[0]
    cols = shape[1]
    start_row = 0
    start_col = 0
    if offset >= 0:
        start_col = offset
    else:
        start_row = -offset
    length = rows - start_row
    col_length = cols - start_col
    if col_length < length:
        length = col_length
    if length < 0:
        length = 0
    out = []
    i = 0
    while i < length:
        out.append(values[(start_row + i) * cols + start_col + i])
        i += 1
    return [[length], out, dtype]


def _native_array_copy_values(values):
    out = []
    i = 0
    while i < len(values):
        out.append(values[i])
        i += 1
    return out


def _native_array_reshape(shape, values, dtype: str, reshape_text: str, diagnostics):
    target = _native_array_parse_shape(reshape_text)
    if _native_array_size(target) != _native_array_size(shape):
        diagnostics.append("PCC-ARRAY-RESHAPE-SIZE-MISMATCH")
        return [target, [], dtype]
    return [target, _native_array_copy_values(values), dtype]


def _native_array_ravel(shape, values, dtype: str, diagnostics):
    return [[_native_array_size(shape)], _native_array_copy_values(values), dtype]


def _native_array_transpose(shape, values, dtype: str, diagnostics):
    if len(shape) == 1:
        return [shape, _native_array_copy_values(values), dtype]
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-TRANSPOSE-RANK-UNSUPPORTED")
        return [shape, [], dtype]
    rows = shape[0]
    cols = shape[1]
    out = []
    c = 0
    while c < cols:
        r = 0
        while r < rows:
            out.append(values[r * cols + c])
            r += 1
        c += 1
    return [[cols, rows], out, dtype]


def _native_array_swapaxes(shape, values, dtype: str, axes_text: str, diagnostics):
    axes = _native_array_parse_shape(axes_text)
    if len(axes) != 2:
        diagnostics.append("PCC-ARRAY-SWAPAXES-AXES-INVALID")
        return [shape, [], dtype]
    axis0 = _native_array_axis_normalize(axes[0], len(shape))
    axis1 = _native_array_axis_normalize(axes[1], len(shape))
    if axis0 < 0 or axis0 >= len(shape) or axis1 < 0 or axis1 >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [shape, [], dtype]
    if len(shape) > 2:
        diagnostics.append("PCC-ARRAY-SWAPAXES-RANK-UNSUPPORTED")
        return [shape, [], dtype]
    if axis0 == axis1 or len(shape) == 1:
        return [shape, _native_array_copy_values(values), dtype]
    return _native_array_transpose(shape, values, dtype, diagnostics)


def _native_array_moveaxis(shape, values, dtype: str, axes_text: str, diagnostics):
    axes = _native_array_parse_shape(axes_text)
    if len(axes) != 2:
        diagnostics.append("PCC-ARRAY-MOVEAXIS-AXES-INVALID")
        return [shape, [], dtype]
    source = _native_array_axis_normalize(axes[0], len(shape))
    destination = _native_array_axis_normalize(axes[1], len(shape))
    if (
        source < 0
        or source >= len(shape)
        or destination < 0
        or destination >= len(shape)
    ):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [shape, [], dtype]
    if len(shape) > 2:
        diagnostics.append("PCC-ARRAY-MOVEAXIS-RANK-UNSUPPORTED")
        return [shape, [], dtype]
    if source == destination or len(shape) == 1:
        return [shape, _native_array_copy_values(values), dtype]
    return _native_array_transpose(shape, values, dtype, diagnostics)


def _native_array_rot90(shape, values, dtype: str, k_text: str, diagnostics):
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-ROT90-RANK-UNSUPPORTED")
        return [shape, [], dtype]
    turns = int(k_text) % 4
    if turns == 0:
        return [shape, _native_array_copy_values(values), dtype]
    rows = shape[0]
    cols = shape[1]
    out = []
    if turns == 1:
        c = cols - 1
        while c >= 0:
            r = 0
            while r < rows:
                out.append(values[r * cols + c])
                r += 1
            c -= 1
        return [[cols, rows], out, dtype]
    if turns == 2:
        i = len(values) - 1
        while i >= 0:
            out.append(values[i])
            i -= 1
        return [shape, out, dtype]
    c = 0
    while c < cols:
        r = rows - 1
        while r >= 0:
            out.append(values[r * cols + c])
            r -= 1
        c += 1
    return [[cols, rows], out, dtype]


def _native_array_flip(shape, values, dtype: str, axis_text: str, diagnostics):
    if len(shape) == 0:
        return [shape, _native_array_copy_values(values), dtype]
    if len(shape) > 2:
        diagnostics.append("PCC-ARRAY-FLIP-RANK-UNSUPPORTED")
        return [shape, [], dtype]
    axis = _native_array_axis_value(axis_text)
    flip_axis0 = False
    flip_axis1 = False
    if axis == -999999:
        flip_axis0 = True
        if len(shape) == 2:
            flip_axis1 = True
    else:
        normalized_axis = _native_array_axis_normalize(axis, len(shape))
        if normalized_axis < 0 or normalized_axis >= len(shape):
            diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
            return [shape, [], dtype]
        if normalized_axis == 0:
            flip_axis0 = True
        else:
            flip_axis1 = True
    out = []
    if len(shape) == 1:
        i = shape[0] - 1
        while i >= 0:
            out.append(values[i])
            i -= 1
        return [shape, out, dtype]
    rows = shape[0]
    cols = shape[1]
    r = rows - 1 if flip_axis0 else 0
    while r >= 0 if flip_axis0 else r < rows:
        c = cols - 1 if flip_axis1 else 0
        while c >= 0 if flip_axis1 else c < cols:
            out.append(values[r * cols + c])
            if flip_axis1:
                c -= 1
            else:
                c += 1
        if flip_axis0:
            r -= 1
        else:
            r += 1
    return [shape, out, dtype]


def _native_array_squeeze(shape, values, dtype: str, axis_text: str, diagnostics):
    out_shape = []
    if axis_text == "":
        i = 0
        while i < len(shape):
            if shape[i] != 1:
                out_shape.append(shape[i])
            i += 1
        return [out_shape, _native_array_copy_values(values), dtype]
    axis = int(axis_text)
    if axis < 0:
        axis += len(shape)
    if axis < 0 or axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    if shape[axis] != 1:
        diagnostics.append("PCC-ARRAY-SQUEEZE-AXIS-NOT-ONE")
        return [shape, [], dtype]
    i = 0
    while i < len(shape):
        if i != axis:
            out_shape.append(shape[i])
        i += 1
    return [out_shape, _native_array_copy_values(values), dtype]


def _native_array_expand_dims(shape, values, dtype: str, axis_text: str, diagnostics):
    axis = int(axis_text)
    if axis < 0:
        axis += len(shape) + 1
    if axis < 0 or axis > len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    out_shape = []
    i = 0
    while i < axis:
        out_shape.append(shape[i])
        i += 1
    out_shape.append(1)
    while i < len(shape):
        out_shape.append(shape[i])
        i += 1
    return [out_shape, _native_array_copy_values(values), dtype]


def _native_array_sort_values(values):
    out = _native_array_copy_values(values)
    i = 1
    while i < len(out):
        current = out[i]
        current_scaled = _native_array_token_to_scaled(current)
        j = i - 1
        while j >= 0 and _native_array_token_to_scaled(out[j]) > current_scaled:
            out[j + 1] = out[j]
            j -= 1
        out[j + 1] = current
        i += 1
    return out


def _native_array_argsort_values(values):
    indices = []
    i = 0
    while i < len(values):
        indices.append(i)
        i += 1
    i = 1
    while i < len(indices):
        current_index = indices[i]
        current_scaled = _native_array_token_to_scaled(values[current_index])
        j = i - 1
        while (
            j >= 0
            and _native_array_token_to_scaled(values[indices[j]]) > current_scaled
        ):
            indices[j + 1] = indices[j]
            j -= 1
        indices[j + 1] = current_index
        i += 1
    out = []
    i = 0
    while i < len(indices):
        out.append(str(indices[i]))
        i += 1
    return out


def _native_array_sort(shape, values, dtype: str, axis_text: str, diagnostics):
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-SORT-UNSUPPORTED")
        return [shape, [], dtype]
    if len(shape) == 0:
        return [shape, _native_array_copy_values(values), dtype]
    axis = -1
    if axis_text != "":
        axis = int(axis_text)
    if axis < 0:
        axis += len(shape)
    if axis < 0 or axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    if len(shape) == 1:
        return [shape, _native_array_sort_values(values), dtype]
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-SORT-RANK-UNSUPPORTED")
        return [shape, [], dtype]
    rows = shape[0]
    cols = shape[1]
    out = _native_array_copy_values(values)
    if axis == 0:
        c = 0
        while c < cols:
            col_values = []
            r = 0
            while r < rows:
                col_values.append(values[r * cols + c])
                r += 1
            col_values = _native_array_sort_values(col_values)
            r = 0
            while r < rows:
                out[r * cols + c] = col_values[r]
                r += 1
            c += 1
    else:
        r = 0
        while r < rows:
            row_values = []
            c = 0
            while c < cols:
                row_values.append(values[r * cols + c])
                c += 1
            row_values = _native_array_sort_values(row_values)
            c = 0
            while c < cols:
                out[r * cols + c] = row_values[c]
                c += 1
            r += 1
    return [shape, out, dtype]


def _native_array_argsort(shape, values, dtype: str, axis_text: str, diagnostics):
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-ARGSORT-UNSUPPORTED")
        return [shape, [], "int64"]
    if len(shape) == 0:
        return [shape, ["0"], "int64"]
    axis = -1
    if axis_text != "":
        axis = int(axis_text)
    if axis < 0:
        axis += len(shape)
    if axis < 0 or axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], "int64"]
    if len(shape) == 1:
        return [shape, _native_array_argsort_values(values), "int64"]
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-ARGSORT-RANK-UNSUPPORTED")
        return [shape, [], "int64"]
    rows = shape[0]
    cols = shape[1]
    out = []
    i = 0
    while i < len(values):
        out.append("0")
        i += 1
    if axis == 0:
        c = 0
        while c < cols:
            col_values = []
            r = 0
            while r < rows:
                col_values.append(values[r * cols + c])
                r += 1
            col_indices = _native_array_argsort_values(col_values)
            r = 0
            while r < rows:
                out[r * cols + c] = col_indices[r]
                r += 1
            c += 1
    else:
        r = 0
        while r < rows:
            row_values = []
            c = 0
            while c < cols:
                row_values.append(values[r * cols + c])
                c += 1
            row_indices = _native_array_argsort_values(row_values)
            c = 0
            while c < cols:
                out[r * cols + c] = row_indices[c]
                c += 1
            r += 1
    return [shape, out, "int64"]


def _native_array_searchsorted(
    shape, values, dtype: str, query_text: str, side: str, diagnostics
):
    if side != "left" and side != "right":
        diagnostics.append("PCC-ARRAY-SEARCHSORTED-SIDE-UNSUPPORTED")
        return [[], [], "int64"]
    query_parsed = _native_array_literal_shape_and_diagnostics(query_text)
    query_shape = query_parsed[0]
    query_diagnostics = query_parsed[1]
    i = 0
    while i < len(query_diagnostics):
        diagnostics.append(query_diagnostics[i])
        i += 1
    query_dtype = _native_array_literal_dtype(query_text)
    if dtype == "object" or query_dtype == "object":
        diagnostics.append("PCC-ARRAY-SEARCHSORTED-UNSUPPORTED")
        return [query_shape, [], "int64"]
    if len(shape) != 1:
        diagnostics.append("PCC-ARRAY-SEARCHSORTED-RANK-UNSUPPORTED")
        return [query_shape, [], "int64"]
    query_values = _native_array_literal_values(query_text)
    out = []
    q = 0
    while q < len(query_values):
        query_scaled = _native_array_token_to_scaled(query_values[q])
        pos = 0
        while pos < len(values):
            current_scaled = _native_array_token_to_scaled(values[pos])
            if side == "left":
                if current_scaled >= query_scaled:
                    break
            elif current_scaled > query_scaled:
                break
            pos += 1
        out.append(str(pos))
        q += 1
    return [query_shape, out, "int64"]


def _native_array_normalize_kth(kth_text: str, axis_len: int):
    kth = int(kth_text)
    if kth < 0:
        kth += axis_len
    return kth


def _native_array_partition(
    shape, values, dtype: str, kth_text: str, axis_text: str, diagnostics
):
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-PARTITION-UNSUPPORTED")
        return [shape, [], dtype]
    if len(shape) == 0:
        kth = _native_array_normalize_kth(kth_text, 1)
        if kth != 0:
            diagnostics.append("PCC-ARRAY-PARTITION-KTH-OUT-OF-BOUNDS")
            return [shape, [], dtype]
        return [shape, _native_array_copy_values(values), dtype]
    axis = -1
    if axis_text != "":
        axis = int(axis_text)
    if axis < 0:
        axis += len(shape)
    if axis < 0 or axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    kth = _native_array_normalize_kth(kth_text, shape[axis])
    if kth < 0 or kth >= shape[axis]:
        diagnostics.append("PCC-ARRAY-PARTITION-KTH-OUT-OF-BOUNDS")
        return [shape, [], dtype]
    if len(shape) == 1 or len(shape) == 2:
        return _native_array_sort(shape, values, dtype, axis_text, diagnostics)
    diagnostics.append("PCC-ARRAY-PARTITION-RANK-UNSUPPORTED")
    return [shape, [], dtype]


def _native_array_argpartition(
    shape, values, dtype: str, kth_text: str, axis_text: str, diagnostics
):
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-ARGPARTITION-UNSUPPORTED")
        return [shape, [], "int64"]
    if len(shape) == 0:
        kth = _native_array_normalize_kth(kth_text, 1)
        if kth != 0:
            diagnostics.append("PCC-ARRAY-ARGPARTITION-KTH-OUT-OF-BOUNDS")
            return [shape, [], "int64"]
        return [shape, ["0"], "int64"]
    axis = -1
    if axis_text != "":
        axis = int(axis_text)
    if axis < 0:
        axis += len(shape)
    if axis < 0 or axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], "int64"]
    kth = _native_array_normalize_kth(kth_text, shape[axis])
    if kth < 0 or kth >= shape[axis]:
        diagnostics.append("PCC-ARRAY-ARGPARTITION-KTH-OUT-OF-BOUNDS")
        return [shape, [], "int64"]
    if len(shape) == 1 or len(shape) == 2:
        return _native_array_argsort(shape, values, dtype, axis_text, diagnostics)
    diagnostics.append("PCC-ARRAY-ARGPARTITION-RANK-UNSUPPORTED")
    return [shape, [], "int64"]


def _native_array_full(shape, fill_text: str, dtype: str, diagnostics):
    values = []
    token = _native_array_cast_values([fill_text], dtype)[0]
    size = _native_array_size(shape)
    i = 0
    while i < size:
        values.append(token)
        i += 1
    return [shape, values, dtype]


def _native_array_arange(arange_text: str, dtype: str, diagnostics):
    parts = _native_array_split_commas(arange_text)
    if len(parts) == 1:
        start = "0"
        stop = parts[0]
        step = "1"
    elif len(parts) == 2:
        start = parts[0]
        stop = parts[1]
        step = "1"
    elif len(parts) == 3:
        start = parts[0]
        stop = parts[1]
        step = parts[2]
    else:
        diagnostics.append("PCC-ARRAY-ARANGE-PARSE-FAILED")
        return [[], [], dtype]
    if (
        not _native_array_token_is_scaled_number(start)
        or not _native_array_token_is_scaled_number(stop)
        or not _native_array_token_is_scaled_number(step)
    ):
        diagnostics.append("PCC-ARRAY-ARANGE-PARSE-FAILED")
        return [[], [], dtype]
    start_value = _native_array_token_to_scaled(start)
    stop_value = _native_array_token_to_scaled(stop)
    step_value = _native_array_token_to_scaled(step)
    if step_value == 0:
        diagnostics.append("PCC-ARRAY-ARANGE-PARSE-FAILED")
        return [[], [], dtype]
    values = []
    current = start_value
    if step_value > 0:
        while current < stop_value:
            values.append(_native_array_scaled_to_token(current, dtype))
            current += step_value
    else:
        while current > stop_value:
            values.append(_native_array_scaled_to_token(current, dtype))
            current += step_value
    return [[len(values)], values, dtype]


def _native_array_eye(eye_text: str, dtype: str, diagnostics):
    parts = _native_array_parse_shape(eye_text)
    if len(parts) == 1:
        rows = parts[0]
        cols = parts[0]
        diagonal = 0
    elif len(parts) == 2:
        rows = parts[0]
        cols = parts[1]
        diagonal = 0
    elif len(parts) == 3:
        rows = parts[0]
        cols = parts[1]
        diagonal = parts[2]
    else:
        diagnostics.append("PCC-ARRAY-EYE-PARSE-FAILED")
        return [[], [], dtype]
    shape = [rows, cols]
    values = []
    r = 0
    while r < rows:
        c = 0
        while c < cols:
            if c - r == diagonal:
                values.append(_native_array_scaled_to_token(1000000, dtype))
            else:
                values.append(_native_array_scaled_to_token(0, dtype))
            c += 1
        r += 1
    return [shape, values, dtype]


def _native_array_linspace(linspace_text: str, dtype: str, diagnostics):
    parts = _native_array_split_commas(linspace_text)
    if len(parts) == 2:
        start = _native_array_token_to_scaled(parts[0])
        stop = _native_array_token_to_scaled(parts[1])
        count = 50
    elif len(parts) == 3:
        start = _native_array_token_to_scaled(parts[0])
        stop = _native_array_token_to_scaled(parts[1])
        count = int(parts[2])
    else:
        diagnostics.append("PCC-ARRAY-LINSPACE-PARSE-FAILED")
        return [[], [], dtype]
    if count < 0:
        diagnostics.append("PCC-ARRAY-LINSPACE-PARSE-FAILED")
        return [[], [], dtype]
    values = []
    if count == 0:
        return [[0], values, dtype]
    if count == 1:
        values.append(_native_array_scaled_to_token(start, dtype))
        return [[1], values, dtype]
    step = (stop - start) // (count - 1)
    i = 0
    while i < count:
        values.append(_native_array_scaled_to_token(start + step * i, dtype))
        i += 1
    return [[count], values, dtype]


def _native_array_axis_value(axis_text: str) -> int:
    if axis_text == "":
        return -999999
    return int(axis_text)


def _native_array_axis_normalize(axis: int, ndim: int) -> int:
    if axis < 0:
        return axis + ndim
    return axis


def _native_array_reduce_scaled(values, kind: str) -> int:
    result = _native_array_token_to_scaled(values[0])
    if kind == "sum":
        result = 0
        i = 0
        while i < len(values):
            result += _native_array_token_to_scaled(values[i])
            i += 1
        return result
    if kind == "prod":
        result = 1000000
        i = 0
        while i < len(values):
            result = (result * _native_array_token_to_scaled(values[i])) // 1000000
            i += 1
        return result
    if kind == "any":
        i = 0
        while i < len(values):
            if _native_array_token_to_scaled(values[i]) != 0:
                return 1000000
            i += 1
        return 0
    if kind == "all":
        i = 0
        while i < len(values):
            if _native_array_token_to_scaled(values[i]) == 0:
                return 0
            i += 1
        return 1000000
    if kind == "mean":
        result = 0
        i = 0
        while i < len(values):
            result += _native_array_token_to_scaled(values[i])
            i += 1
        return result // len(values)
    i = 1
    while i < len(values):
        current = _native_array_token_to_scaled(values[i])
        if kind == "min" and current < result:
            result = current
        if kind == "max" and current > result:
            result = current
        i += 1
    return result


def _native_array_reduce(
    shape, values, dtype: str, kind: str, axis_text: str, keepdims: bool, diagnostics
):
    if not (
        kind == "sum"
        or kind == "prod"
        or kind == "min"
        or kind == "max"
        or kind == "mean"
        or kind == "any"
        or kind == "all"
    ):
        diagnostics.append("PCC-ARRAY-REDUCE-UNSUPPORTED")
        return [[], [], dtype]
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-REDUCE-UNSUPPORTED")
        return [[], [], dtype]
    if len(values) == 0:
        diagnostics.append("PCC-ARRAY-REDUCE-EMPTY")
        return [[], [], dtype]
    if kind == "any" or kind == "all":
        dtype = "bool"
    elif kind == "mean":
        dtype = "float64"
    elif (
        (kind == "sum" or kind == "prod") and dtype != "float32" and dtype != "float64"
    ):
        dtype = "int64"
    axis = _native_array_axis_value(axis_text)
    if axis == -999999:
        result = _native_array_reduce_scaled(values, kind)
        out_shape = []
        if keepdims:
            i = 0
            while i < len(shape):
                out_shape.append(1)
                i += 1
        return [out_shape, [_native_array_scaled_to_token(result, dtype)], dtype]
    normalized_axis = _native_array_axis_normalize(axis, len(shape))
    if normalized_axis < 0 or normalized_axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    if len(shape) == 1:
        result = _native_array_reduce_scaled(values, kind)
        if keepdims:
            return [[1], [_native_array_scaled_to_token(result, dtype)], dtype]
        return [[], [_native_array_scaled_to_token(result, dtype)], dtype]
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-AXIS-REDUCE-RANK-UNSUPPORTED")
        return [[], [], dtype]
    rows = shape[0]
    cols = shape[1]
    out = []
    if normalized_axis == 0:
        c = 0
        while c < cols:
            slice_values = []
            r = 0
            while r < rows:
                slice_values.append(values[r * cols + c])
                r += 1
            out.append(
                _native_array_scaled_to_token(
                    _native_array_reduce_scaled(slice_values, kind), dtype
                )
            )
            c += 1
        if keepdims:
            return [[1, cols], out, dtype]
        return [[cols], out, dtype]
    r = 0
    while r < rows:
        slice_values = []
        c = 0
        while c < cols:
            slice_values.append(values[r * cols + c])
            c += 1
        out.append(
            _native_array_scaled_to_token(
                _native_array_reduce_scaled(slice_values, kind), dtype
            )
        )
        r += 1
    if keepdims:
        return [[rows, 1], out, dtype]
    return [[rows], out, dtype]


def _native_array_arg_reduce_scaled(values, kind: str) -> int:
    best_index = 0
    best_value = _native_array_token_to_scaled(values[0])
    i = 1
    while i < len(values):
        current = _native_array_token_to_scaled(values[i])
        if kind == "argmin" and current < best_value:
            best_index = i
            best_value = current
        if kind == "argmax" and current > best_value:
            best_index = i
            best_value = current
        i += 1
    return best_index


def _native_array_arg_reduce(
    shape, values, dtype: str, kind: str, axis_text: str, keepdims: bool, diagnostics
):
    if not (kind == "argmin" or kind == "argmax"):
        diagnostics.append("PCC-ARRAY-ARGREDUCE-UNSUPPORTED")
        return [[], [], "int64"]
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-ARGREDUCE-UNSUPPORTED")
        return [[], [], "int64"]
    if len(values) == 0:
        diagnostics.append("PCC-ARRAY-ARGREDUCE-EMPTY")
        return [[], [], "int64"]
    axis = _native_array_axis_value(axis_text)
    if axis == -999999:
        result = _native_array_arg_reduce_scaled(values, kind)
        out_shape = []
        if keepdims:
            i = 0
            while i < len(shape):
                out_shape.append(1)
                i += 1
        return [out_shape, [str(result)], "int64"]
    normalized_axis = _native_array_axis_normalize(axis, len(shape))
    if normalized_axis < 0 or normalized_axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], "int64"]
    if len(shape) == 1:
        result = _native_array_arg_reduce_scaled(values, kind)
        if keepdims:
            return [[1], [str(result)], "int64"]
        return [[], [str(result)], "int64"]
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-AXIS-ARGREDUCE-RANK-UNSUPPORTED")
        return [[], [], "int64"]
    rows = shape[0]
    cols = shape[1]
    out = []
    if normalized_axis == 0:
        c = 0
        while c < cols:
            slice_values = []
            r = 0
            while r < rows:
                slice_values.append(values[r * cols + c])
                r += 1
            out.append(str(_native_array_arg_reduce_scaled(slice_values, kind)))
            c += 1
        if keepdims:
            return [[1, cols], out, "int64"]
        return [[cols], out, "int64"]
    r = 0
    while r < rows:
        slice_values = []
        c = 0
        while c < cols:
            slice_values.append(values[r * cols + c])
            c += 1
        out.append(str(_native_array_arg_reduce_scaled(slice_values, kind)))
        r += 1
    if keepdims:
        return [[rows, 1], out, "int64"]
    return [[rows], out, "int64"]


def _native_array_count_nonzero_values(values) -> int:
    count = 0
    i = 0
    while i < len(values):
        if _native_array_token_to_scaled(values[i]) != 0:
            count += 1
        i += 1
    return count


def _native_array_count_nonzero(
    shape, values, dtype: str, axis_text: str, keepdims: bool, diagnostics
):
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-COUNT-NONZERO-UNSUPPORTED")
        return [[], [], "int64"]
    axis = _native_array_axis_value(axis_text)
    if axis == -999999:
        result = _native_array_count_nonzero_values(values)
        out_shape = []
        if keepdims:
            i = 0
            while i < len(shape):
                out_shape.append(1)
                i += 1
        return [out_shape, [str(result)], "int64"]
    normalized_axis = _native_array_axis_normalize(axis, len(shape))
    if normalized_axis < 0 or normalized_axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], "int64"]
    if len(shape) == 1:
        result = _native_array_count_nonzero_values(values)
        if keepdims:
            return [[1], [str(result)], "int64"]
        return [[], [str(result)], "int64"]
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-AXIS-COUNT-NONZERO-RANK-UNSUPPORTED")
        return [[], [], "int64"]
    rows = shape[0]
    cols = shape[1]
    out = []
    if normalized_axis == 0:
        c = 0
        while c < cols:
            slice_values = []
            r = 0
            while r < rows:
                slice_values.append(values[r * cols + c])
                r += 1
            out.append(str(_native_array_count_nonzero_values(slice_values)))
            c += 1
        if keepdims:
            return [[1, cols], out, "int64"]
        return [[cols], out, "int64"]
    r = 0
    while r < rows:
        slice_values = []
        c = 0
        while c < cols:
            slice_values.append(values[r * cols + c])
            c += 1
        out.append(str(_native_array_count_nonzero_values(slice_values)))
        r += 1
    if keepdims:
        return [[rows, 1], out, "int64"]
    return [[rows], out, "int64"]


def _native_array_nonzero(shape, values, dtype: str, diagnostics):
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-NONZERO-UNSUPPORTED")
        return [[len(shape), 0], [], "int64"]
    if len(shape) == 0:
        if len(values) > 0 and _native_array_token_to_scaled(values[0]) != 0:
            return [[1, 1], ["0"], "int64"]
        return [[1, 0], [], "int64"]
    if len(shape) == 1:
        out = []
        i = 0
        while i < len(values):
            if _native_array_token_to_scaled(values[i]) != 0:
                out.append(str(i))
            i += 1
        return [[1, len(out)], out, "int64"]
    if len(shape) == 2:
        rows = shape[0]
        cols = shape[1]
        row_indices = []
        col_indices = []
        r = 0
        while r < rows:
            c = 0
            while c < cols:
                if _native_array_token_to_scaled(values[r * cols + c]) != 0:
                    row_indices.append(str(r))
                    col_indices.append(str(c))
                c += 1
            r += 1
        out = []
        i = 0
        while i < len(row_indices):
            out.append(row_indices[i])
            i += 1
        i = 0
        while i < len(col_indices):
            out.append(col_indices[i])
            i += 1
        return [[2, len(row_indices)], out, "int64"]
    diagnostics.append("PCC-ARRAY-NONZERO-RANK-UNSUPPORTED")
    return [[len(shape), 0], [], "int64"]


def _native_array_argwhere(shape, values, dtype: str, diagnostics):
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-ARGWHERE-UNSUPPORTED")
        return [[0, len(shape)], [], "int64"]
    if len(shape) == 0:
        if len(values) > 0 and _native_array_token_to_scaled(values[0]) != 0:
            return [[1, 0], [], "int64"]
        return [[0, 0], [], "int64"]
    if len(shape) == 1:
        out = []
        i = 0
        while i < len(values):
            if _native_array_token_to_scaled(values[i]) != 0:
                out.append(str(i))
            i += 1
        return [[len(out), 1], out, "int64"]
    if len(shape) == 2:
        rows = shape[0]
        cols = shape[1]
        out = []
        count = 0
        r = 0
        while r < rows:
            c = 0
            while c < cols:
                if _native_array_token_to_scaled(values[r * cols + c]) != 0:
                    out.append(str(r))
                    out.append(str(c))
                    count += 1
                c += 1
            r += 1
        return [[count, 2], out, "int64"]
    diagnostics.append("PCC-ARRAY-ARGWHERE-RANK-UNSUPPORTED")
    return [[0, len(shape)], [], "int64"]


def _native_array_flatnonzero(shape, values, dtype: str, diagnostics):
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-FLATNONZERO-UNSUPPORTED")
        return [[0], [], "int64"]
    out = []
    i = 0
    while i < len(values):
        if _native_array_token_to_scaled(values[i]) != 0:
            out.append(str(i))
        i += 1
    return [[len(out)], out, "int64"]


def _native_array_cumulative_dtype(dtype: str) -> str:
    if dtype == "float32" or dtype == "float64":
        return dtype
    return "int64"


def _native_array_cumulative_values(values, kind: str, dtype: str):
    out = []
    if kind == "cumsum":
        total = 0
        i = 0
        while i < len(values):
            total += _native_array_token_to_scaled(values[i])
            out.append(_native_array_scaled_to_token(total, dtype))
            i += 1
        return out
    total = 1000000
    i = 0
    while i < len(values):
        total = (total * _native_array_token_to_scaled(values[i])) // 1000000
        out.append(_native_array_scaled_to_token(total, dtype))
        i += 1
    return out


def _native_array_cumulative(
    shape, values, dtype: str, kind: str, axis_text: str, diagnostics
):
    if not (kind == "cumsum" or kind == "cumprod"):
        diagnostics.append("PCC-ARRAY-CUMULATIVE-UNSUPPORTED")
        return [[], [], dtype]
    if dtype == "object":
        diagnostics.append("PCC-ARRAY-CUMULATIVE-UNSUPPORTED")
        return [[], [], dtype]
    dtype = _native_array_cumulative_dtype(dtype)
    axis = _native_array_axis_value(axis_text)
    if axis == -999999:
        out = _native_array_cumulative_values(values, kind, dtype)
        return [[len(out)], out, dtype]
    normalized_axis = _native_array_axis_normalize(axis, len(shape))
    if normalized_axis < 0 or normalized_axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    if len(shape) == 1:
        return [shape, _native_array_cumulative_values(values, kind, dtype), dtype]
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-AXIS-CUMULATIVE-RANK-UNSUPPORTED")
        return [[], [], dtype]
    rows = shape[0]
    cols = shape[1]
    out = []
    i = 0
    while i < len(values):
        out.append("0")
        i += 1
    if normalized_axis == 0:
        c = 0
        while c < cols:
            slice_values = []
            r = 0
            while r < rows:
                slice_values.append(values[r * cols + c])
                r += 1
            slice_out = _native_array_cumulative_values(slice_values, kind, dtype)
            r = 0
            while r < rows:
                out[r * cols + c] = slice_out[r]
                r += 1
            c += 1
    else:
        r = 0
        while r < rows:
            slice_values = []
            c = 0
            while c < cols:
                slice_values.append(values[r * cols + c])
                c += 1
            slice_out = _native_array_cumulative_values(slice_values, kind, dtype)
            c = 0
            while c < cols:
                out[r * cols + c] = slice_out[c]
                c += 1
            r += 1
    return [shape, out, dtype]


def _native_array_take(
    shape, values, dtype: str, take_text: str, axis_text: str, diagnostics
):
    indices = _native_array_parse_shape(take_text)
    axis = _native_array_axis_value(axis_text)
    out = []
    if axis == -999999:
        i = 0
        while i < len(indices):
            actual = indices[i]
            if actual < 0:
                actual += _native_array_size(shape)
            if actual < 0 or actual >= _native_array_size(shape):
                diagnostics.append("PCC-ARRAY-INDEX-OUT-OF-BOUNDS")
                return [[len(indices)], [], dtype]
            out.append(values[actual])
            i += 1
        return [[len(indices)], out, dtype]
    normalized_axis = _native_array_axis_normalize(axis, len(shape))
    if normalized_axis < 0 or normalized_axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    if len(shape) == 1:
        return _native_array_take(shape, values, dtype, take_text, "", diagnostics)
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-TAKE-RANK-UNSUPPORTED")
        return [[], [], dtype]
    rows = shape[0]
    cols = shape[1]
    if normalized_axis == 0:
        i = 0
        while i < len(indices):
            actual = indices[i]
            if actual < 0:
                actual += rows
            if actual < 0 or actual >= rows:
                diagnostics.append("PCC-ARRAY-INDEX-OUT-OF-BOUNDS")
                return [[len(indices), cols], [], dtype]
            c = 0
            while c < cols:
                out.append(values[actual * cols + c])
                c += 1
            i += 1
        return [[len(indices), cols], out, dtype]
    r = 0
    while r < rows:
        i = 0
        while i < len(indices):
            actual = indices[i]
            if actual < 0:
                actual += cols
            if actual < 0 or actual >= cols:
                diagnostics.append("PCC-ARRAY-INDEX-OUT-OF-BOUNDS")
                return [[rows, len(indices)], [], dtype]
            out.append(values[r * cols + actual])
            i += 1
        r += 1
    return [[rows, len(indices)], out, dtype]


def _native_array_put(
    shape, values, dtype: str, put_text: str, put_values_text: str, diagnostics
):
    indices = _native_array_parse_shape(put_text)
    replacement_text = put_values_text
    if replacement_text == "":
        replacement_text = "0"
    replacement_values = _native_array_literal_values(replacement_text)
    if len(replacement_values) == 0:
        diagnostics.append("PCC-ARRAY-PUT-VALUES-EMPTY")
        return [shape, [], dtype]
    replacement_values = _native_array_cast_values(replacement_values, dtype)
    out = _native_array_copy_values(values)
    size = _native_array_size(shape)
    i = 0
    while i < len(indices):
        actual = indices[i]
        if actual < 0:
            actual += size
        if actual < 0 or actual >= size:
            diagnostics.append("PCC-ARRAY-INDEX-OUT-OF-BOUNDS")
            return [shape, [], dtype]
        out[actual] = replacement_values[i % len(replacement_values)]
        i += 1
    return [shape, out, dtype]


def _native_array_putmask(
    shape, values, dtype: str, mask_text: str, putmask_values_text: str, diagnostics
):
    parsed = _native_array_literal_shape_and_diagnostics(mask_text)
    mask_shape = parsed[0]
    mask_diagnostics = parsed[1]
    i = 0
    while i < len(mask_diagnostics):
        diagnostics.append(mask_diagnostics[i])
        i += 1
    mask_dtype = _native_array_literal_dtype(mask_text)
    if mask_dtype != "bool":
        diagnostics.append("PCC-ARRAY-PUTMASK-MASK-DTYPE-UNSUPPORTED")
        return [shape, [], dtype]
    if len(mask_shape) != len(shape):
        diagnostics.append("PCC-ARRAY-PUTMASK-SHAPE-MISMATCH")
        return [shape, [], dtype]
    i = 0
    while i < len(shape):
        if shape[i] != mask_shape[i]:
            diagnostics.append("PCC-ARRAY-PUTMASK-SHAPE-MISMATCH")
            return [shape, [], dtype]
        i += 1
    replacement_text = putmask_values_text
    if replacement_text == "":
        replacement_text = "0"
    replacement_values = _native_array_literal_values(replacement_text)
    if len(replacement_values) == 0:
        diagnostics.append("PCC-ARRAY-PUTMASK-VALUES-EMPTY")
        return [shape, [], dtype]
    replacement_values = _native_array_cast_values(replacement_values, dtype)
    mask_values = _native_array_literal_values(mask_text)
    out = _native_array_copy_values(values)
    selected = 0
    i = 0
    while i < len(out) and i < len(mask_values):
        if mask_values[i] == "True":
            out[i] = replacement_values[selected % len(replacement_values)]
            selected += 1
        i += 1
    return [shape, out, dtype]


def _native_array_mask(shape, values, dtype: str, mask_text: str, diagnostics):
    parsed = _native_array_literal_shape_and_diagnostics(mask_text)
    mask_shape = parsed[0]
    mask_diagnostics = parsed[1]
    i = 0
    while i < len(mask_diagnostics):
        diagnostics.append(mask_diagnostics[i])
        i += 1
    mask_dtype = _native_array_literal_dtype(mask_text)
    if mask_dtype != "bool":
        diagnostics.append("PCC-ARRAY-MASK-DTYPE-UNSUPPORTED")
        return [[], [], dtype]
    mask_values = _native_array_literal_values(mask_text)
    out = []
    if len(mask_shape) == len(shape):
        same = True
        i = 0
        while i < len(shape):
            if shape[i] != mask_shape[i]:
                same = False
            i += 1
        if same:
            i = 0
            while i < len(values) and i < len(mask_values):
                if mask_values[i] == "True":
                    out.append(values[i])
                i += 1
            return [[len(out)], out, dtype]
    if len(shape) == 2 and len(mask_shape) == 1 and mask_shape[0] == shape[0]:
        rows = shape[0]
        cols = shape[1]
        selected_rows = 0
        r = 0
        while r < rows:
            if mask_values[r] == "True":
                selected_rows += 1
                c = 0
                while c < cols:
                    out.append(values[r * cols + c])
                    c += 1
            r += 1
        return [[selected_rows, cols], out, dtype]
    diagnostics.append("PCC-ARRAY-MASK-SHAPE-MISMATCH")
    return [[], [], dtype]


def _native_array_compress(
    shape, values, dtype: str, condition_text: str, axis_text: str, diagnostics
):
    parsed = _native_array_literal_shape_and_diagnostics(condition_text)
    condition_shape = parsed[0]
    condition_diagnostics = parsed[1]
    i = 0
    while i < len(condition_diagnostics):
        diagnostics.append(condition_diagnostics[i])
        i += 1
    condition_dtype = _native_array_literal_dtype(condition_text)
    if condition_dtype != "bool":
        diagnostics.append("PCC-ARRAY-COMPRESS-MASK-DTYPE-UNSUPPORTED")
        return [[], [], dtype]
    if len(condition_shape) != 1:
        diagnostics.append("PCC-ARRAY-COMPRESS-SHAPE-MISMATCH")
        return [[], [], dtype]
    condition_values = _native_array_literal_values(condition_text)
    axis = _native_array_axis_value(axis_text)
    out = []
    if axis == -999999:
        if condition_shape[0] != len(values):
            diagnostics.append("PCC-ARRAY-COMPRESS-SHAPE-MISMATCH")
            return [[], [], dtype]
        i = 0
        while i < len(values):
            if condition_values[i] == "True":
                out.append(values[i])
            i += 1
        return [[len(out)], out, dtype]
    normalized_axis = _native_array_axis_normalize(axis, len(shape))
    if normalized_axis < 0 or normalized_axis >= len(shape):
        diagnostics.append("PCC-ARRAY-AXIS-OUT-OF-BOUNDS")
        return [[], [], dtype]
    if condition_shape[0] != shape[normalized_axis]:
        diagnostics.append("PCC-ARRAY-COMPRESS-SHAPE-MISMATCH")
        return [shape, [], dtype]
    if len(shape) == 1:
        i = 0
        while i < len(values):
            if condition_values[i] == "True":
                out.append(values[i])
            i += 1
        return [[len(out)], out, dtype]
    if len(shape) != 2:
        diagnostics.append("PCC-ARRAY-COMPRESS-RANK-UNSUPPORTED")
        return [shape, [], dtype]
    rows = shape[0]
    cols = shape[1]
    if normalized_axis == 0:
        selected_rows = 0
        r = 0
        while r < rows:
            if condition_values[r] == "True":
                selected_rows += 1
                c = 0
                while c < cols:
                    out.append(values[r * cols + c])
                    c += 1
            r += 1
        return [[selected_rows, cols], out, dtype]
    selected_cols = 0
    c = 0
    while c < cols:
        if condition_values[c] == "True":
            selected_cols += 1
        c += 1
    r = 0
    while r < rows:
        c = 0
        while c < cols:
            if condition_values[c] == "True":
                out.append(values[r * cols + c])
            c += 1
        r += 1
    return [[rows, selected_cols], out, dtype]


def _native_array_where(
    mask_text: str,
    true_shape,
    true_values,
    true_dtype: str,
    false_text: str,
    diagnostics,
):
    parsed = _native_array_literal_shape_and_diagnostics(mask_text)
    mask_shape = parsed[0]
    mask_diagnostics = parsed[1]
    i = 0
    while i < len(mask_diagnostics):
        diagnostics.append(mask_diagnostics[i])
        i += 1
    mask_dtype = _native_array_literal_dtype(mask_text)
    if mask_dtype != "bool":
        diagnostics.append("PCC-ARRAY-WHERE-MASK-DTYPE-UNSUPPORTED")
    mask_values = _native_array_literal_values(mask_text)
    false_shape = []
    if false_text.strip().startswith("["):
        parsed_false = _native_array_literal_shape_and_diagnostics(false_text)
        false_shape = parsed_false[0]
        false_diagnostics = parsed_false[1]
        i = 0
        while i < len(false_diagnostics):
            diagnostics.append(false_diagnostics[i])
            i += 1
    false_values = _native_array_literal_values(false_text)
    false_dtype = _native_array_literal_dtype(false_text)
    value_shape = _native_array_broadcast_shape(true_shape, false_shape, diagnostics)
    out_shape = _native_array_broadcast_shape(value_shape, mask_shape, diagnostics)
    dtype = _native_array_op_dtype("add", true_dtype, false_dtype)
    if true_dtype == "object" or false_dtype == "object":
        dtype = "object"
    if len(diagnostics) > 0:
        return [out_shape, [], dtype]
    out = []
    if len(out_shape) == 0:
        chosen = true_values[0] if mask_values[0] == "True" else false_values[0]
        out.append(chosen)
    elif len(out_shape) == 1:
        i = 0
        while i < out_shape[0]:
            out_index = [i]
            mv = mask_values[_native_array_broadcast_flat_index(mask_shape, out_index)]
            if mv == "True":
                out.append(
                    true_values[
                        _native_array_broadcast_flat_index(true_shape, out_index)
                    ]
                )
            else:
                out.append(
                    false_values[
                        _native_array_broadcast_flat_index(false_shape, out_index)
                    ]
                )
            i += 1
    elif len(out_shape) == 2:
        r = 0
        while r < out_shape[0]:
            c = 0
            while c < out_shape[1]:
                out_index = [r, c]
                mv = mask_values[
                    _native_array_broadcast_flat_index(mask_shape, out_index)
                ]
                if mv == "True":
                    out.append(
                        true_values[
                            _native_array_broadcast_flat_index(true_shape, out_index)
                        ]
                    )
                else:
                    out.append(
                        false_values[
                            _native_array_broadcast_flat_index(false_shape, out_index)
                        ]
                    )
                c += 1
            r += 1
    else:
        diagnostics.append("PCC-ARRAY-RANK-UNSUPPORTED")
        return [out_shape, [], dtype]
    return [out_shape, out, dtype]


def _native_array_astype(shape, values, dtype: str, target_dtype: str, diagnostics):
    dtype = _native_array_normalize_dtype(target_dtype)
    out = []
    i = 0
    while i < len(values):
        if dtype == "object":
            out.append(values[i])
        else:
            out.append(
                _native_array_scaled_to_token(
                    _native_array_token_to_scaled(values[i]), dtype
                )
            )
        i += 1
    return [shape, out, dtype]


def _native_array_diagnostics_json(codes) -> str:
    out = "["
    i = 0
    while i < len(codes):
        if i > 0:
            out += ", "
        code = codes[i]
        out += "{"
        out += '"code": ' + _json_str(code)
        if code == "PCC-ARRAY-RAGGED":
            out += ', "message": ' + _json_str(
                "array literal is ragged; pcc cannot claim rectangular ndarray layout"
            )
        elif code == "PCC-ARRAY-EMPTY-DTYPE":
            out += ', "message": ' + _json_str(
                "empty array literal needs an explicit dtype for a precise layout"
            )
        elif code == "PCC-ARRAY-NEGATIVE-DIMENSION":
            out += ', "message": ' + _json_str(
                "array shape dimensions must be non-negative"
            )
        elif code == "PCC-ARRAY-ARANGE-PARSE-FAILED":
            out += ', "message": ' + _json_str(
                "arange expects stop, start,stop, or start,stop,step with a nonzero step"
            )
        elif code == "PCC-ARRAY-EYE-PARSE-FAILED":
            out += ', "message": ' + _json_str("eye expects n, n,m, or n,m,k")
        elif code == "PCC-ARRAY-LINSPACE-PARSE-FAILED":
            out += ', "message": ' + _json_str(
                "linspace expects start,stop or start,stop,num with non-negative num"
            )
        elif code == "PCC-ARRAY-REQUIRES-RECTANGULAR":
            out += ', "message": ' + _json_str(
                "rectangular layout was required but the input is ragged"
            )
        elif code == "PCC-ARRAY-BROADCAST-INCOMPATIBLE":
            out += ', "message": ' + _json_str(
                "array operands cannot be broadcast together"
            )
        elif code == "PCC-ARRAY-BROADCAST-TO-SHAPE-MISMATCH":
            out += ', "message": ' + _json_str(
                "array cannot be broadcast to the requested shape"
            )
        elif code == "PCC-ARRAY-REPEAT-NEGATIVE":
            out += ', "message": ' + _json_str("repeat count must be non-negative")
        elif code == "PCC-ARRAY-REPEAT-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "repeat currently supports 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-TILE-REPS-EMPTY":
            out += ', "message": ' + _json_str(
                "tile requires at least one repeat dimension"
            )
        elif code == "PCC-ARRAY-TILE-REPS-NEGATIVE":
            out += ', "message": ' + _json_str(
                "tile repeat dimensions must be non-negative"
            )
        elif code == "PCC-ARRAY-TILE-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "tile currently supports scalar/1D/2D arrays with one or two repeat dimensions"
            )
        elif code == "PCC-ARRAY-ROLL-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "roll currently supports scalar/1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-UFUNC-UNSUPPORTED":
            out += ', "message": ' + _json_str("unsupported array-core ufunc")
        elif code == "PCC-ARRAY-OBJECT-UFUNC-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array numeric ufuncs are not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-UNARY-UNSUPPORTED":
            out += ', "message": ' + _json_str("unsupported array-core unary ufunc")
        elif code == "PCC-ARRAY-OBJECT-UNARY-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array unary ufuncs are not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-UNARY-DTYPE-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "unary ufunc is not supported for this dtype by the current array-core subset"
            )
        elif code == "PCC-ARRAY-UNARY-FAILED":
            out += ', "message": ' + _json_str("array-core unary ufunc failed")
        elif code == "PCC-ARRAY-CLIP-PARSE-FAILED":
            out += ', "message": ' + _json_str("clip expects min,max scalar bounds")
        elif code == "PCC-ARRAY-OBJECT-CLIP-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array clip is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-CLIP-FAILED":
            out += ', "message": ' + _json_str("array-core clip failed")
        elif code == "PCC-ARRAY-MATMUL-OBJECT-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array matrix multiplication is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-MATMUL-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "array-core matmul supports 1D/2D operands only"
            )
        elif code == "PCC-ARRAY-MATMUL-SHAPE-MISMATCH":
            out += ', "message": ' + _json_str(
                "array-core matmul operands have incompatible shapes"
            )
        elif code == "PCC-ARRAY-CONCAT-RANK-MISMATCH":
            out += ', "message": ' + _json_str(
                "concatenate operands must have the same rank"
            )
        elif code == "PCC-ARRAY-CONCAT-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "the current concatenate subset supports 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-CONCAT-SHAPE-MISMATCH":
            out += ', "message": ' + _json_str(
                "concatenate operands have incompatible shapes"
            )
        elif code == "PCC-ARRAY-STACK-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "the current stack subset supports 1D arrays only"
            )
        elif code == "PCC-ARRAY-STACK-SHAPE-MISMATCH":
            out += ', "message": ' + _json_str(
                "stack operands must have identical shapes"
            )
        elif code == "PCC-ARRAY-COMPARE-UNSUPPORTED":
            out += ', "message": ' + _json_str("unsupported array-core comparison")
        elif code == "PCC-ARRAY-COMPARE-FAILED":
            out += ', "message": ' + _json_str("array-core comparison failed")
        elif code == "PCC-ARRAY-OBJECT-COMPARE-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array ordered comparisons are not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-INDEX-PARSE-FAILED":
            out += ', "message": ' + _json_str("array index parse failed")
        elif code == "PCC-ARRAY-INDEX-RANK-MISMATCH":
            out += ', "message": ' + _json_str(
                "index rank must match array rank for the current array-core subset"
            )
        elif code == "PCC-ARRAY-INDEX-OUT-OF-BOUNDS":
            out += ', "message": ' + _json_str("array index is out of bounds")
        elif code == "PCC-ARRAY-DIAGONAL-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "diagonal currently supports 2D arrays only"
            )
        elif code == "PCC-ARRAY-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "array rank is unsupported by the native bootstrap array-core subset"
            )
        elif code == "PCC-ARRAY-RESHAPE-SIZE-MISMATCH":
            out += ', "message": ' + _json_str(
                "reshape target must have the same number of elements"
            )
        elif code == "PCC-ARRAY-TRANSPOSE-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "the current array-core transpose subset supports 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-SWAPAXES-AXES-INVALID":
            out += ', "message": ' + _json_str("swapaxes expects exactly two axes")
        elif code == "PCC-ARRAY-SWAPAXES-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "swapaxes currently supports 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-MOVEAXIS-AXES-INVALID":
            out += ', "message": ' + _json_str(
                "moveaxis expects source,destination axes"
            )
        elif code == "PCC-ARRAY-MOVEAXIS-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "moveaxis currently supports 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-ROT90-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "rot90 currently supports 2D arrays only"
            )
        elif code == "PCC-ARRAY-FLIP-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "flip currently supports scalar/1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-SQUEEZE-AXIS-NOT-ONE":
            out += ', "message": ' + _json_str("squeeze axis must have length one")
        elif code == "PCC-ARRAY-REDUCE-UNSUPPORTED":
            out += ', "message": ' + _json_str("unsupported array-core reduction")
        elif code == "PCC-ARRAY-REDUCE-EMPTY":
            out += ', "message": ' + _json_str(
                "cannot reduce an empty array in the current array-core subset"
            )
        elif code == "PCC-ARRAY-AXIS-OUT-OF-BOUNDS":
            out += ', "message": ' + _json_str("axis is out of bounds for array")
        elif code == "PCC-ARRAY-AXIS-REDUCE-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "axis reductions currently support 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-ARGREDUCE-UNSUPPORTED":
            out += ', "message": ' + _json_str("unsupported array-core arg reduction")
        elif code == "PCC-ARRAY-ARGREDUCE-EMPTY":
            out += ', "message": ' + _json_str(
                "cannot arg-reduce an empty array in the current array-core subset"
            )
        elif code == "PCC-ARRAY-AXIS-ARGREDUCE-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "axis arg reductions currently support 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-COUNT-NONZERO-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array count_nonzero is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-AXIS-COUNT-NONZERO-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "axis count_nonzero currently supports 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-NONZERO-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array nonzero is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-NONZERO-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "nonzero currently supports scalar/1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-ARGWHERE-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array argwhere is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-ARGWHERE-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "argwhere currently supports scalar/1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-FLATNONZERO-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array flatnonzero is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-CUMULATIVE-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "unsupported array-core cumulative operation"
            )
        elif code == "PCC-ARRAY-AXIS-CUMULATIVE-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "axis cumulative operations currently support 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-TAKE-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "take currently supports 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-PUT-VALUES-EMPTY":
            out += ', "message": ' + _json_str(
                "put requires at least one replacement value"
            )
        elif code == "PCC-ARRAY-PUT-FAILED":
            out += ', "message": ' + _json_str("array-core put failed")
        elif code == "PCC-ARRAY-PUTMASK-MASK-DTYPE-UNSUPPORTED":
            out += ', "message": ' + _json_str("putmask requires a bool mask")
        elif code == "PCC-ARRAY-PUTMASK-SHAPE-MISMATCH":
            out += ', "message": ' + _json_str(
                "putmask mask shape must match the array shape"
            )
        elif code == "PCC-ARRAY-PUTMASK-VALUES-EMPTY":
            out += ', "message": ' + _json_str(
                "putmask requires at least one replacement value"
            )
        elif code == "PCC-ARRAY-PUTMASK-FAILED":
            out += ', "message": ' + _json_str("array-core putmask failed")
        elif code == "PCC-ARRAY-MASK-DTYPE-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "boolean mask selection requires a bool mask"
            )
        elif code == "PCC-ARRAY-MASK-SHAPE-MISMATCH":
            out += ', "message": ' + _json_str(
                "boolean mask shape must match the array shape or the leading axis for 2D arrays"
            )
        elif code == "PCC-ARRAY-COMPRESS-MASK-DTYPE-UNSUPPORTED":
            out += ', "message": ' + _json_str("compress requires a bool condition")
        elif code == "PCC-ARRAY-COMPRESS-SHAPE-MISMATCH":
            out += ', "message": ' + _json_str(
                "compress condition length must match the selected array extent"
            )
        elif code == "PCC-ARRAY-COMPRESS-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "compress currently supports 1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-WHERE-MASK-DTYPE-UNSUPPORTED":
            out += ', "message": ' + _json_str("where selection requires a bool mask")
        elif code == "PCC-ARRAY-SORT-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array sort is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-SORT-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "sort currently supports scalar/1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-ARGSORT-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array argsort is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-ARGSORT-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "argsort currently supports scalar/1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-SEARCHSORTED-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array searchsorted is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-SEARCHSORTED-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "searchsorted currently supports 1D sorted arrays only"
            )
        elif code == "PCC-ARRAY-SEARCHSORTED-SIDE-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "searchsorted side must be left or right"
            )
        elif code == "PCC-ARRAY-PARTITION-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array partition is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-PARTITION-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "partition currently supports scalar/1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-PARTITION-KTH-OUT-OF-BOUNDS":
            out += ', "message": ' + _json_str(
                "partition kth is out of bounds for the selected axis"
            )
        elif code == "PCC-ARRAY-ARGPARTITION-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "object-array argpartition is not supported by the current array-core subset"
            )
        elif code == "PCC-ARRAY-ARGPARTITION-RANK-UNSUPPORTED":
            out += ', "message": ' + _json_str(
                "argpartition currently supports scalar/1D/2D arrays only"
            )
        elif code == "PCC-ARRAY-ARGPARTITION-KTH-OUT-OF-BOUNDS":
            out += ', "message": ' + _json_str(
                "argpartition kth is out of bounds for the selected axis"
            )
        else:
            out += ', "message": ' + _json_str("array-core layout diagnostic")
        out += "}"
        i += 1
    out += "]"
    return out


def _native_array_core_json(
    shape,
    dtype: str,
    source: str,
    diagnostics,
    values,
    fill,
    arange: str,
    zeros: str,
    ones: str,
    zeros_like: bool,
    ones_like: bool,
    full_like,
    eye: str,
    linspace: str,
    op: str,
    rhs,
    matmul,
    concat,
    stack,
    unary: str,
    clip: str,
    broadcast_to: str,
    repeat,
    tile,
    roll: str,
    rot90: str,
    compare: str,
    index: str,
    diagonal: str,
    reshape: str,
    ravel: bool,
    flatten: bool,
    flip: bool,
    transpose: bool,
    swapaxes: str,
    moveaxis: str,
    squeeze: bool,
    squeeze_axis_text: str,
    expand_dims_text: str,
    reduce_name: str,
    argreduce_name: str,
    count_nonzero: bool,
    nonzero: bool,
    argwhere: bool,
    flatnonzero: bool,
    cumulative: str,
    sort_value: bool,
    argsort_value: bool,
    searchsorted: str,
    search_side: str,
    partition: str,
    argpartition: str,
    axis_text: str,
    keepdims: bool,
    take: str,
    put: str,
    put_values: str,
    putmask: str,
    putmask_values: str,
    mask: str,
    compress: str,
    where: str,
    otherwise,
    astype: str,
    copy_value: bool,
    view: bool,
    owns_data: bool,
    base_shape,
    strides_override,
    c_contiguous: bool,
) -> str:
    i = 0
    while i < len(shape):
        if shape[i] < 0 and not _native_list_contains(
            diagnostics, "PCC-ARRAY-NEGATIVE-DIMENSION"
        ):
            diagnostics.append("PCC-ARRAY-NEGATIVE-DIMENSION")
        i += 1
    itemsize = _native_array_dtype_itemsize(dtype)
    strides = _native_array_strides(shape, itemsize)
    if strides_override is not None:
        strides = strides_override
    size = _native_array_size(shape)
    out = "{"
    out += '"c_contiguous": ' + ("true" if c_contiguous else "false")
    out += ', "diagnostics": ' + _native_array_diagnostics_json(diagnostics)
    if values is not None:
        out += ', "data": ' + _native_array_data_json(shape, values)
    out += ', "dtype": ' + _json_str(dtype)
    out += ', "dtype_format": ' + _json_str(_native_array_dtype_format(dtype))
    if dtype == "bool" or _native_array_is_integer_dtype(dtype):
        out += ', "dtype_range": ' + _native_array_dtype_range_json(dtype)
        out += ', "dtype_signed": ' + (
            "true" if _native_array_integer_signed(dtype) else "false"
        )
    if values is not None:
        out += ', "flat_data": ' + _native_array_values_json(values)
    if fill is not None:
        out += ', "fill": ' + _json_str(fill)
    if arange != "":
        out += ', "arange": ' + _json_str(arange)
    if zeros != "":
        out += ', "zeros": ' + _json_str(zeros)
    if ones != "":
        out += ', "ones": ' + _json_str(ones)
    if zeros_like:
        out += ', "zeros_like": true'
    if ones_like:
        out += ', "ones_like": true'
    if full_like != "":
        out += ', "full_like": ' + _json_str(full_like)
    if eye != "":
        out += ', "eye": ' + _json_str(eye)
    if linspace != "":
        out += ', "linspace": ' + _json_str(linspace)
    out += ', "itemsize": ' + str(itemsize)
    out += ', "nbytes": ' + str(size * itemsize)
    out += ', "ndim": ' + str(len(shape))
    out += ', "ok": ' + ("true" if len(diagnostics) == 0 else "false")
    if op != "":
        out += ', "op": ' + _json_str(op)
    if matmul != "":
        out += ', "matmul": ' + _json_str(matmul)
    if concat != "":
        out += ', "concat": ' + _json_str(concat)
    if stack != "":
        out += ', "stack": ' + _json_str(stack)
    if unary != "":
        out += ', "unary": ' + _json_str(unary)
    if clip != "":
        out += ', "clip": ' + _json_str(clip)
    if broadcast_to != "":
        out += ', "broadcast_to": ' + _json_str(broadcast_to)
    if repeat != "":
        out += ', "repeat": ' + str(repeat)
    if tile != "":
        out += ', "tile": ' + _json_str(tile)
    if roll != "":
        out += ', "roll": ' + roll
    if rot90 != "":
        out += ', "rot90": ' + rot90
    if index != "":
        out += ', "index": ' + _json_str(index)
    if diagonal != "":
        out += ', "diagonal": ' + diagonal
    if compare != "":
        out += ', "compare": ' + _json_str(compare)
    out += ', "owns_data": ' + ("true" if owns_data else "false")
    if dtype == "object":
        out += ', "object_policy": {"allowed": ["storage", "index", "take", "put", "putmask", "compress", "roll", "flip", "transpose", "swapaxes", "moveaxis", "rot90", "reshape", "ravel", "flatten", "copy", "repr"], "unsupported": ["numeric_ufunc", "numeric_reduce", "typed_memoryview"]}'
    if values is not None:
        out += ', "repr": ' + _json_str(_native_array_repr(shape, values))
    if rhs != "":
        out += ', "rhs": ' + _json_str(rhs)
    if reshape != "":
        out += ', "reshape": ' + _json_str(reshape)
    if ravel:
        out += ', "ravel": true'
    if flatten:
        out += ', "flatten": true'
    if flip:
        out += ', "flip": true'
    if transpose:
        out += ', "transpose": true'
    if swapaxes != "":
        out += ', "swapaxes": ' + _json_str(swapaxes)
    if moveaxis != "":
        out += ', "moveaxis": ' + _json_str(moveaxis)
    if squeeze:
        out += ', "squeeze": true'
    if squeeze_axis_text != "":
        out += ', "squeeze_axis": ' + squeeze_axis_text
    if expand_dims_text != "":
        out += ', "expand_dims": ' + expand_dims_text
    if reduce_name != "":
        out += ', "reduce": ' + _json_str(reduce_name)
    if argreduce_name != "":
        out += ', "argreduce": ' + _json_str(argreduce_name)
    if count_nonzero:
        out += ', "count_nonzero": true'
    if nonzero:
        out += ', "nonzero": true'
    if argwhere:
        out += ', "argwhere": true'
    if flatnonzero:
        out += ', "flatnonzero": true'
    if cumulative != "":
        out += ', "cumulative": ' + _json_str(cumulative)
    if sort_value:
        out += ', "sort": true'
    if argsort_value:
        out += ', "argsort": true'
    if searchsorted != "":
        out += ', "searchsorted": ' + _json_str(searchsorted)
        out += ', "side": ' + _json_str(search_side)
    if partition != "":
        out += ', "partition": ' + partition
    if argpartition != "":
        out += ', "argpartition": ' + argpartition
    if axis_text != "":
        out += ', "axis": ' + axis_text
    if keepdims:
        out += ', "keepdims": true'
    if take != "":
        out += ', "take": ' + _json_str(take)
    if put != "":
        out += ', "put": ' + _json_str(put)
        out += ', "put_values": ' + _json_str(put_values)
    if putmask != "":
        out += ', "putmask": ' + _json_str(putmask)
        out += ', "putmask_values": ' + _json_str(putmask_values)
    if mask != "":
        out += ', "mask": ' + _json_str(mask)
    if compress != "":
        out += ', "compress": ' + _json_str(compress)
    if where != "":
        out += ', "where": ' + _json_str(where)
    if otherwise != "":
        out += ', "otherwise": ' + _json_str(otherwise)
    if astype != "":
        out += ', "astype": ' + _json_str(astype)
    if copy_value:
        out += ', "copy": true'
    out += ', "view": ' + ("true" if view else "false")
    if base_shape is not None:
        out += ', "base_shape": ' + _json_int_list(base_shape)
    out += ', "schema": "pcc.array-core.v1"'
    out += ', "shape": ' + _json_int_list(shape)
    out += ', "size": ' + str(size)
    out += ', "source": ' + _json_str(source)
    out += ', "strides": ' + _json_int_list(strides)
    out += "}"
    return out


def _run_native_package_array_core_from_pcc1(module_args) -> int:
    shape_text = ""
    literal = ""
    dtype = "auto"
    require_rectangular = False
    fill = None
    arange = ""
    zeros = ""
    ones = ""
    zeros_like = False
    ones_like = False
    full_like = ""
    eye = ""
    linspace = ""
    op = ""
    rhs = ""
    matmul = ""
    concat = ""
    stack = ""
    unary = ""
    clip = ""
    broadcast_to = ""
    repeat = ""
    tile = ""
    roll = ""
    rot90 = ""
    compare = ""
    index = ""
    diagonal = ""
    reshape = ""
    ravel = False
    flatten = False
    flip = False
    transpose = False
    swapaxes = ""
    moveaxis = ""
    squeeze = False
    squeeze_axis_text = ""
    expand_dims_text = ""
    reduce_name = ""
    argreduce_name = ""
    count_nonzero = False
    nonzero = False
    argwhere = False
    flatnonzero = False
    cumulative = ""
    sort_value = False
    argsort_value = False
    searchsorted = ""
    search_side = "left"
    partition = ""
    argpartition = ""
    axis_text = ""
    keepdims = False
    take = ""
    put = ""
    put_values = ""
    putmask = ""
    putmask_values = ""
    mask = ""
    compress = ""
    where = ""
    otherwise = ""
    astype = ""
    copy_value = False
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--shape":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--shape requires a value", "ok": false}')
                return 2
            shape_text = module_args[i + 1]
            i += 1
        elif arg.startswith("--shape="):
            shape_text = arg.split("=", 1)[1]
        elif arg == "--literal":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--literal requires a value", "ok": false}')
                return 2
            literal = module_args[i + 1]
            i += 1
        elif arg.startswith("--literal="):
            literal = arg.split("=", 1)[1]
        elif arg == "--dtype":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--dtype requires a value", "ok": false}')
                return 2
            dtype = module_args[i + 1]
            i += 1
        elif arg.startswith("--dtype="):
            dtype = arg.split("=", 1)[1]
        elif arg == "--require-rectangular":
            require_rectangular = True
        elif arg == "--fill":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--fill requires a value", "ok": false}')
                return 2
            fill = module_args[i + 1]
            i += 1
        elif arg.startswith("--fill="):
            fill = arg.split("=", 1)[1]
        elif arg == "--arange":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--arange requires a value", "ok": false}')
                return 2
            arange = module_args[i + 1]
            i += 1
        elif arg.startswith("--arange="):
            arange = arg.split("=", 1)[1]
        elif arg == "--zeros":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--zeros requires a value", "ok": false}')
                return 2
            zeros = module_args[i + 1]
            i += 1
        elif arg.startswith("--zeros="):
            zeros = arg.split("=", 1)[1]
        elif arg == "--ones":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--ones requires a value", "ok": false}')
                return 2
            ones = module_args[i + 1]
            i += 1
        elif arg.startswith("--ones="):
            ones = arg.split("=", 1)[1]
        elif arg == "--zeros-like":
            zeros_like = True
        elif arg == "--ones-like":
            ones_like = True
        elif arg == "--full-like":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--full-like requires a value", "ok": false}')
                return 2
            full_like = module_args[i + 1]
            i += 1
        elif arg.startswith("--full-like="):
            full_like = arg.split("=", 1)[1]
        elif arg == "--eye":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--eye requires a value", "ok": false}')
                return 2
            eye = module_args[i + 1]
            i += 1
        elif arg.startswith("--eye="):
            eye = arg.split("=", 1)[1]
        elif arg == "--linspace":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--linspace requires a value", "ok": false}')
                return 2
            linspace = module_args[i + 1]
            i += 1
        elif arg.startswith("--linspace="):
            linspace = arg.split("=", 1)[1]
        elif arg == "--op":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--op requires a value", "ok": false}')
                return 2
            op = module_args[i + 1]
            i += 1
        elif arg.startswith("--op="):
            op = arg.split("=", 1)[1]
        elif arg == "--rhs":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--rhs requires a value", "ok": false}')
                return 2
            rhs = module_args[i + 1]
            i += 1
        elif arg.startswith("--rhs="):
            rhs = arg.split("=", 1)[1]
        elif arg == "--matmul":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--matmul requires a value", "ok": false}')
                return 2
            matmul = module_args[i + 1]
            i += 1
        elif arg.startswith("--matmul="):
            matmul = arg.split("=", 1)[1]
        elif arg == "--concat":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--concat requires a value", "ok": false}')
                return 2
            concat = module_args[i + 1]
            i += 1
        elif arg.startswith("--concat="):
            concat = arg.split("=", 1)[1]
        elif arg == "--stack":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--stack requires a value", "ok": false}')
                return 2
            stack = module_args[i + 1]
            i += 1
        elif arg.startswith("--stack="):
            stack = arg.split("=", 1)[1]
        elif arg == "--unary":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--unary requires a value", "ok": false}')
                return 2
            unary = module_args[i + 1]
            i += 1
        elif arg.startswith("--unary="):
            unary = arg.split("=", 1)[1]
        elif arg == "--clip":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--clip requires a value", "ok": false}')
                return 2
            clip = module_args[i + 1]
            i += 1
        elif arg.startswith("--clip="):
            clip = arg.split("=", 1)[1]
        elif arg == "--broadcast-to":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--broadcast-to requires a value", "ok": false}')
                return 2
            broadcast_to = module_args[i + 1]
            i += 1
        elif arg.startswith("--broadcast-to="):
            broadcast_to = arg.split("=", 1)[1]
        elif arg == "--repeat":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--repeat requires a value", "ok": false}')
                return 2
            repeat = module_args[i + 1]
            i += 1
        elif arg.startswith("--repeat="):
            repeat = arg.split("=", 1)[1]
        elif arg == "--tile":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--tile requires a value", "ok": false}')
                return 2
            tile = module_args[i + 1]
            i += 1
        elif arg.startswith("--tile="):
            tile = arg.split("=", 1)[1]
        elif arg == "--roll":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--roll requires a value", "ok": false}')
                return 2
            roll = module_args[i + 1]
            i += 1
        elif arg.startswith("--roll="):
            roll = arg.split("=", 1)[1]
        elif arg == "--rot90":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--rot90 requires a value", "ok": false}')
                return 2
            rot90 = module_args[i + 1]
            i += 1
        elif arg.startswith("--rot90="):
            rot90 = arg.split("=", 1)[1]
        elif arg == "--compare":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--compare requires a value", "ok": false}')
                return 2
            compare = module_args[i + 1]
            i += 1
        elif arg.startswith("--compare="):
            compare = arg.split("=", 1)[1]
        elif arg == "--index":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--index requires a value", "ok": false}')
                return 2
            index = module_args[i + 1]
            i += 1
        elif arg.startswith("--index="):
            index = arg.split("=", 1)[1]
        elif arg == "--diagonal":
            diagonal = "0"
            if i + 1 < len(module_args) and not module_args[i + 1].startswith("--"):
                diagonal = module_args[i + 1]
                i += 1
        elif arg.startswith("--diagonal="):
            diagonal = arg.split("=", 1)[1]
        elif arg == "--reshape":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--reshape requires a value", "ok": false}')
                return 2
            reshape = module_args[i + 1]
            i += 1
        elif arg.startswith("--reshape="):
            reshape = arg.split("=", 1)[1]
        elif arg == "--ravel":
            ravel = True
        elif arg == "--flatten":
            flatten = True
        elif arg == "--flip":
            flip = True
        elif arg == "--transpose":
            transpose = True
        elif arg == "--swapaxes":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--swapaxes requires a value", "ok": false}')
                return 2
            swapaxes = module_args[i + 1]
            i += 1
        elif arg.startswith("--swapaxes="):
            swapaxes = arg.split("=", 1)[1]
        elif arg == "--moveaxis":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--moveaxis requires a value", "ok": false}')
                return 2
            moveaxis = module_args[i + 1]
            i += 1
        elif arg.startswith("--moveaxis="):
            moveaxis = arg.split("=", 1)[1]
        elif arg == "--squeeze":
            squeeze = True
        elif arg == "--squeeze-axis":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--squeeze-axis requires a value", "ok": false}')
                return 2
            squeeze_axis_text = module_args[i + 1]
            i += 1
        elif arg.startswith("--squeeze-axis="):
            squeeze_axis_text = arg.split("=", 1)[1]
        elif arg == "--expand-dims":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--expand-dims requires a value", "ok": false}')
                return 2
            expand_dims_text = module_args[i + 1]
            i += 1
        elif arg.startswith("--expand-dims="):
            expand_dims_text = arg.split("=", 1)[1]
        elif arg == "--reduce":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--reduce requires a value", "ok": false}')
                return 2
            reduce_name = module_args[i + 1]
            i += 1
        elif arg.startswith("--reduce="):
            reduce_name = arg.split("=", 1)[1]
        elif arg == "--argreduce":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--argreduce requires a value", "ok": false}')
                return 2
            argreduce_name = module_args[i + 1]
            i += 1
        elif arg.startswith("--argreduce="):
            argreduce_name = arg.split("=", 1)[1]
        elif arg == "--count-nonzero":
            count_nonzero = True
        elif arg == "--nonzero":
            nonzero = True
        elif arg == "--argwhere":
            argwhere = True
        elif arg == "--flatnonzero":
            flatnonzero = True
        elif arg == "--cumulative":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--cumulative requires a value", "ok": false}')
                return 2
            cumulative = module_args[i + 1]
            i += 1
        elif arg.startswith("--cumulative="):
            cumulative = arg.split("=", 1)[1]
        elif arg == "--sort":
            sort_value = True
        elif arg == "--argsort":
            argsort_value = True
        elif arg == "--searchsorted":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--searchsorted requires a value", "ok": false}')
                return 2
            searchsorted = module_args[i + 1]
            i += 1
        elif arg.startswith("--searchsorted="):
            searchsorted = arg.split("=", 1)[1]
        elif arg == "--side":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--side requires a value", "ok": false}')
                return 2
            search_side = module_args[i + 1]
            i += 1
        elif arg.startswith("--side="):
            search_side = arg.split("=", 1)[1]
        elif arg == "--partition":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--partition requires a value", "ok": false}')
                return 2
            partition = module_args[i + 1]
            i += 1
        elif arg.startswith("--partition="):
            partition = arg.split("=", 1)[1]
        elif arg == "--argpartition":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--argpartition requires a value", "ok": false}')
                return 2
            argpartition = module_args[i + 1]
            i += 1
        elif arg.startswith("--argpartition="):
            argpartition = arg.split("=", 1)[1]
        elif arg == "--axis":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--axis requires a value", "ok": false}')
                return 2
            axis_text = module_args[i + 1]
            i += 1
        elif arg.startswith("--axis="):
            axis_text = arg.split("=", 1)[1]
        elif arg == "--keepdims":
            keepdims = True
        elif arg == "--take":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--take requires a value", "ok": false}')
                return 2
            take = module_args[i + 1]
            i += 1
        elif arg.startswith("--take="):
            take = arg.split("=", 1)[1]
        elif arg == "--put":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--put requires a value", "ok": false}')
                return 2
            put = module_args[i + 1]
            i += 1
        elif arg.startswith("--put="):
            put = arg.split("=", 1)[1]
        elif arg == "--put-values":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--put-values requires a value", "ok": false}')
                return 2
            put_values = module_args[i + 1]
            i += 1
        elif arg.startswith("--put-values="):
            put_values = arg.split("=", 1)[1]
        elif arg == "--putmask":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--putmask requires a value", "ok": false}')
                return 2
            putmask = module_args[i + 1]
            i += 1
        elif arg.startswith("--putmask="):
            putmask = arg.split("=", 1)[1]
        elif arg == "--putmask-values":
            if i + 1 >= len(module_args):
                _write_text(
                    '{"error": "--putmask-values requires a value", "ok": false}'
                )
                return 2
            putmask_values = module_args[i + 1]
            i += 1
        elif arg.startswith("--putmask-values="):
            putmask_values = arg.split("=", 1)[1]
        elif arg == "--mask":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--mask requires a value", "ok": false}')
                return 2
            mask = module_args[i + 1]
            i += 1
        elif arg.startswith("--mask="):
            mask = arg.split("=", 1)[1]
        elif arg == "--compress":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--compress requires a value", "ok": false}')
                return 2
            compress = module_args[i + 1]
            i += 1
        elif arg.startswith("--compress="):
            compress = arg.split("=", 1)[1]
        elif arg == "--where":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--where requires a value", "ok": false}')
                return 2
            where = module_args[i + 1]
            i += 1
        elif arg.startswith("--where="):
            where = arg.split("=", 1)[1]
        elif arg == "--otherwise":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--otherwise requires a value", "ok": false}')
                return 2
            otherwise = module_args[i + 1]
            i += 1
        elif arg.startswith("--otherwise="):
            otherwise = arg.split("=", 1)[1]
        elif arg == "--astype":
            if i + 1 >= len(module_args):
                _write_text('{"error": "--astype requires a value", "ok": false}')
                return 2
            astype = module_args[i + 1]
            i += 1
        elif arg.startswith("--astype="):
            astype = arg.split("=", 1)[1]
        elif arg == "--copy":
            copy_value = True
        elif arg == "--json":
            pass
        i += 1
    diagnostics = []
    source = "shape"
    values = None
    view = False
    owns_data = True
    base_shape = None
    strides_override = None
    c_contiguous = True
    if linspace != "":
        source = "linspace"
        if dtype == "auto" or dtype == "":
            dtype = "float64"
        dtype = _native_array_normalize_dtype(dtype)
        result = _native_array_linspace(linspace, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    elif eye != "":
        source = "eye"
        if dtype == "auto" or dtype == "":
            dtype = "float64"
        dtype = _native_array_normalize_dtype(dtype)
        result = _native_array_eye(eye, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    elif zeros != "":
        source = "zeros"
        if dtype == "auto" or dtype == "":
            dtype = "float64"
        dtype = _native_array_normalize_dtype(dtype)
        shape = _native_array_parse_shape(zeros)
        result = _native_array_full(shape, "0", dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    elif ones != "":
        source = "ones"
        if dtype == "auto" or dtype == "":
            dtype = "float64"
        dtype = _native_array_normalize_dtype(dtype)
        shape = _native_array_parse_shape(ones)
        result = _native_array_full(shape, "1", dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    elif arange != "":
        source = "arange"
        if dtype == "auto" or dtype == "":
            if _native_array_arange_uses_float(arange):
                dtype = "float64"
            else:
                dtype = "int64"
        dtype = _native_array_normalize_dtype(dtype)
        result = _native_array_arange(arange, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    elif literal != "":
        parsed = _native_array_literal_shape_and_diagnostics(literal)
        shape = parsed[0]
        diagnostics = parsed[1]
        values = _native_array_literal_values(literal)
        source = "literal"
        if dtype == "auto" or dtype == "":
            if _native_list_contains(diagnostics, "PCC-ARRAY-RAGGED"):
                dtype = "object"
            else:
                dtype = _native_array_literal_dtype(literal)
    else:
        shape = _native_array_parse_shape(shape_text)
    if require_rectangular and _native_list_contains(diagnostics, "PCC-ARRAY-RAGGED"):
        diagnostics.append("PCC-ARRAY-REQUIRES-RECTANGULAR")
    fill_dtype_auto = dtype == "auto" or dtype == ""
    dtype = _native_array_normalize_dtype(dtype)
    if literal == "" and arange == "" and fill is not None:
        if fill_dtype_auto:
            dtype = _native_array_literal_dtype(fill)
        dtype = _native_array_normalize_dtype(dtype)
        result = _native_array_full(shape, fill, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and values is not None:
        values = _native_array_cast_values(values, dtype)
    if literal != "" and zeros_like:
        result = _native_array_full(shape, "0", dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and ones_like:
        result = _native_array_full(shape, "1", dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and full_like != "":
        result = _native_array_full(shape, full_like, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and unary != "":
        result = _native_array_unary_op(shape, values, dtype, unary, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and clip != "":
        result = _native_array_clip(shape, values, dtype, clip, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and broadcast_to != "":
        base_shape = shape
        result = _native_array_broadcast_to(
            shape, values, dtype, broadcast_to, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        strides_override = result[3]
        c_contiguous = result[4]
        view = True
        owns_data = False
    if literal != "" and repeat != "":
        result = _native_array_repeat(
            shape, values, dtype, repeat, axis_text, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and tile != "":
        tile_reps = _native_array_parse_shape(tile)
        result = _native_array_tile_reps(shape, values, dtype, tile_reps, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and roll != "":
        result = _native_array_roll(shape, values, dtype, roll, axis_text, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and rot90 != "":
        old_shape = shape
        old_dtype = dtype
        result = _native_array_rot90(shape, values, dtype, rot90, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        base_shape = old_shape
        strides_override = None
        c_contiguous = True
        if len(old_shape) == 2:
            turns = int(rot90) % 4
            itemsize = _native_array_dtype_itemsize(old_dtype)
            stride0 = old_shape[1] * itemsize
            stride1 = itemsize
            if turns == 1:
                strides_override = [-stride1, stride0]
                c_contiguous = False
            elif turns == 2:
                strides_override = [-stride0, -stride1]
                c_contiguous = False
            elif turns == 3:
                strides_override = [stride1, -stride0]
                c_contiguous = False
    if literal != "" and op != "":
        if rhs == "":
            rhs = "0"
        result = _native_array_binary_op(shape, values, dtype, rhs, op, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and matmul != "":
        result = _native_array_matmul(shape, values, dtype, matmul, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and concat != "":
        result = _native_array_concat(
            shape, values, dtype, concat, axis_text, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and stack != "":
        result = _native_array_stack(
            shape, values, dtype, stack, axis_text, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and compare != "":
        if rhs == "":
            rhs = "0"
        result = _native_array_compare(shape, values, dtype, rhs, compare, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and index != "":
        result = _native_array_index(shape, values, dtype, index, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and diagonal != "":
        base_shape = shape
        result = _native_array_diagonal(shape, values, dtype, diagonal, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        if len(base_shape) == 2:
            itemsize = _native_array_dtype_itemsize(dtype)
            strides_override = [(base_shape[1] + 1) * itemsize]
            c_contiguous = len(shape) == 0 or shape[0] <= 1
    if literal != "" and reshape != "":
        base_shape = shape
        result = _native_array_reshape(shape, values, dtype, reshape, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        strides_override = None
        c_contiguous = True
    if literal != "" and ravel:
        base_shape = shape
        result = _native_array_ravel(shape, values, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        strides_override = None
        c_contiguous = True
    if literal != "" and flatten:
        result = _native_array_ravel(shape, values, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and flip:
        old_shape = shape
        result = _native_array_flip(shape, values, dtype, axis_text, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        base_shape = old_shape
        itemsize = _native_array_dtype_itemsize(dtype)
        if len(old_shape) == 1:
            strides_override = [-itemsize]
        elif len(old_shape) == 2:
            stride0 = old_shape[1] * itemsize
            stride1 = itemsize
            axis_value_for_stride = _native_array_axis_value(axis_text)
            if axis_value_for_stride == -999999:
                stride0 = -stride0
                stride1 = -stride1
            else:
                normalized_axis_for_stride = _native_array_axis_normalize(
                    axis_value_for_stride, len(old_shape)
                )
                if normalized_axis_for_stride == 0:
                    stride0 = -stride0
                elif normalized_axis_for_stride == 1:
                    stride1 = -stride1
            strides_override = [stride0, stride1]
        else:
            strides_override = None
        c_contiguous = len(old_shape) == 0
    if literal != "" and transpose:
        base_shape = shape
        old_shape = shape
        old_dtype = dtype
        result = _native_array_transpose(shape, values, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        if len(old_shape) == 2:
            itemsize = _native_array_dtype_itemsize(old_dtype)
            strides_override = [itemsize, old_shape[1] * itemsize]
            c_contiguous = False
    if literal != "" and swapaxes != "":
        base_shape = shape
        old_shape = shape
        old_dtype = dtype
        result = _native_array_swapaxes(shape, values, dtype, swapaxes, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        strides_override = None
        c_contiguous = True
        if len(old_shape) == 2 and shape != old_shape:
            itemsize = _native_array_dtype_itemsize(old_dtype)
            strides_override = [itemsize, old_shape[1] * itemsize]
            c_contiguous = False
    if literal != "" and moveaxis != "":
        base_shape = shape
        old_shape = shape
        old_dtype = dtype
        result = _native_array_moveaxis(shape, values, dtype, moveaxis, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        strides_override = None
        c_contiguous = True
        if len(old_shape) == 2 and shape != old_shape:
            itemsize = _native_array_dtype_itemsize(old_dtype)
            strides_override = [itemsize, old_shape[1] * itemsize]
            c_contiguous = False
    if literal != "" and squeeze:
        base_shape = shape
        result = _native_array_squeeze(
            shape, values, dtype, squeeze_axis_text, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        strides_override = None
        c_contiguous = True
    if literal != "" and expand_dims_text != "":
        base_shape = shape
        result = _native_array_expand_dims(
            shape, values, dtype, expand_dims_text, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = True
        owns_data = False
        strides_override = None
        c_contiguous = True
    if literal != "" and reduce_name != "":
        result = _native_array_reduce(
            shape, values, dtype, reduce_name, axis_text, keepdims, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and argreduce_name != "":
        result = _native_array_arg_reduce(
            shape, values, dtype, argreduce_name, axis_text, keepdims, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and count_nonzero:
        result = _native_array_count_nonzero(
            shape, values, dtype, axis_text, keepdims, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
    if literal != "" and nonzero:
        result = _native_array_nonzero(shape, values, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and argwhere:
        result = _native_array_argwhere(shape, values, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and flatnonzero:
        result = _native_array_flatnonzero(shape, values, dtype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and cumulative != "":
        result = _native_array_cumulative(
            shape, values, dtype, cumulative, axis_text, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and sort_value:
        result = _native_array_sort(shape, values, dtype, axis_text, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and argsort_value:
        result = _native_array_argsort(shape, values, dtype, axis_text, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and searchsorted != "":
        result = _native_array_searchsorted(
            shape, values, dtype, searchsorted, search_side, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and partition != "":
        result = _native_array_partition(
            shape, values, dtype, partition, axis_text, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and argpartition != "":
        result = _native_array_argpartition(
            shape, values, dtype, argpartition, axis_text, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and take != "":
        result = _native_array_take(shape, values, dtype, take, axis_text, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and put != "":
        result = _native_array_put(shape, values, dtype, put, put_values, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and putmask != "":
        result = _native_array_putmask(
            shape, values, dtype, putmask, putmask_values, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and mask != "":
        result = _native_array_mask(shape, values, dtype, mask, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and compress != "":
        result = _native_array_compress(
            shape, values, dtype, compress, axis_text, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and where != "":
        if otherwise == "":
            otherwise = "0"
        result = _native_array_where(
            where, shape, values, dtype, otherwise, diagnostics
        )
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and astype != "":
        result = _native_array_astype(shape, values, dtype, astype, diagnostics)
        shape = result[0]
        values = result[1]
        dtype = result[2]
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    if literal != "" and copy_value:
        values = _native_array_copy_values(values)
        view = False
        owns_data = True
        base_shape = None
        strides_override = None
        c_contiguous = True
    report = _native_array_core_json(
        shape,
        dtype,
        source,
        diagnostics,
        values,
        fill,
        arange,
        zeros,
        ones,
        zeros_like,
        ones_like,
        full_like,
        eye,
        linspace,
        op,
        rhs,
        matmul,
        concat,
        stack,
        unary,
        clip,
        broadcast_to,
        repeat,
        tile,
        roll,
        rot90,
        compare,
        index,
        diagonal,
        reshape,
        ravel,
        flatten,
        flip,
        transpose,
        swapaxes,
        moveaxis,
        squeeze,
        squeeze_axis_text,
        expand_dims_text,
        reduce_name,
        argreduce_name,
        count_nonzero,
        nonzero,
        argwhere,
        flatnonzero,
        cumulative,
        sort_value,
        argsort_value,
        searchsorted,
        search_side,
        partition,
        argpartition,
        axis_text,
        keepdims,
        take,
        put,
        put_values,
        putmask,
        putmask_values,
        mask,
        compress,
        where,
        otherwise,
        astype,
        copy_value,
        view,
        owns_data,
        base_shape,
        strides_override,
        c_contiguous,
    )
    _write_text(report)
    return 2 if _native_find_from(report, '"ok": false', 0) >= 0 else 0


def _native_find_tool_json(names, search_paths):
    found_name = names[0]
    found_path = None
    probe_ok = False
    probe_output = ""
    i = 0
    while i < len(names):
        j = 0
        while j < len(search_paths):
            candidate = search_paths[j] + "/" + names[i]
            if os.path.isfile(candidate):
                found_name = names[i]
                found_path = candidate
                probe = _native_probe_tool(candidate, names[i])
                probe_ok = probe[0]
                probe_output = probe[1]
                if probe_ok:
                    break
            j += 1
        if found_path is not None and probe_ok:
            break
        i += 1
    out = "{"
    out += '"found": ' + ("true" if found_path is not None and probe_ok else "false")
    out += ', "name": ' + _json_str(found_name)
    out += ', "path": ' + _json_str_or_null(found_path)
    out += ', "probe_ok": ' + ("true" if probe_ok else "false")
    out += ', "probe_output": ' + _json_str(probe_output)
    out += "}"
    return out


def _native_probe_tool(path: str, name: str):
    arg = "--version"
    if name == "cython" or name == "cython3":
        arg = "-V"
    output_path = "/tmp/pcc_tool_probe_" + str(os.getpid()) + "_" + name
    command = (
        "'"
        + path.replace("'", "'\"'\"'")
        + "' "
        + arg
        + " > '"
        + output_path
        + "' 2>&1"
    )
    ok = True
    try:
        subprocess.run(["/bin/sh", "-c", command], check=True)
    except Exception as exc:
        ok = False
    try:
        with open(output_path, "r") as fh:
            output = fh.read()
    except Exception:
        output = ""
    try:
        subprocess.run(["rm", "-f", output_path], check=True)
    except Exception:
        pass
    return [ok, output.strip()]


def _native_version_parts(text: str):
    start = -1
    i = 0
    while i < len(text):
        ch = text[i]
        if ch >= "0" and ch <= "9":
            start = i
            break
        i += 1
    if start < 0:
        return []
    token = ""
    i = start
    while i < len(text):
        ch = text[i]
        if (ch >= "0" and ch <= "9") or ch == ".":
            token += ch
        else:
            break
        i += 1
    raw_parts = token.split(".")
    parts = []
    i = 0
    while i < len(raw_parts):
        if raw_parts[i] != "":
            try:
                parts.append(int(raw_parts[i]))
            except Exception:
                parts.append(0)
        i += 1
    return parts


def _native_version_less(actual_text: str, minimum_text: str) -> bool:
    actual = _native_version_parts(actual_text)
    minimum = _native_version_parts(minimum_text)
    if len(actual) == 0 or len(minimum) == 0:
        return False
    length = len(actual)
    if len(minimum) > length:
        length = len(minimum)
    i = 0
    while i < length:
        a = actual[i] if i < len(actual) else 0
        b = minimum[i] if i < len(minimum) else 0
        if a < b:
            return True
        if a > b:
            return False
        i += 1
    return False


def _native_tool_found(names, search_paths) -> bool:
    i = 0
    while i < len(names):
        j = 0
        while j < len(search_paths):
            candidate = search_paths[j] + "/" + names[i]
            if os.path.isfile(candidate) and _native_probe_tool(candidate, names[i])[0]:
                return True
            j += 1
        i += 1
    return False


def _native_find_tool_path(names, search_paths):
    i = 0
    while i < len(names):
        j = 0
        while j < len(search_paths):
            candidate = search_paths[j] + "/" + names[i]
            if os.path.isfile(candidate):
                return candidate
            j += 1
        i += 1
    return None


def _native_find_library_json(names, library_dirs):
    found_name = names[0]
    found_path = None
    suffixes = [".so", ".dylib", ".a", ".dll"]
    i = 0
    while i < len(library_dirs):
        root = library_dirs[i]
        try:
            entries = sorted(os.listdir(root))
        except Exception:
            entries = []
        j = 0
        while j < len(entries):
            entry = entries[j]
            path = root + "/" + entry
            if os.path.isfile(path):
                lower = entry.lower()
                k = 0
                while k < len(names):
                    prefix1 = "lib" + names[k]
                    prefix2 = names[k]
                    m = 0
                    while m < len(suffixes):
                        if (
                            lower.startswith(prefix1) or lower.startswith(prefix2)
                        ) and lower.endswith(suffixes[m]):
                            found_name = names[k]
                            found_path = path
                        m += 1
                    k += 1
            if found_path is not None:
                break
            j += 1
        if found_path is not None:
            break
        i += 1
    out = "{"
    out += '"checked_dirs": ' + _json_str_list(library_dirs)
    out += ', "found": ' + ("true" if found_path is not None else "false")
    out += ', "name": ' + _json_str(found_name)
    out += ', "path": ' + _json_str_or_null(found_path)
    out += "}"
    return out


def _native_library_found(names, library_dirs) -> bool:
    suffixes = [".so", ".dylib", ".a", ".dll"]
    i = 0
    while i < len(library_dirs):
        root = library_dirs[i]
        try:
            entries = sorted(os.listdir(root))
        except Exception:
            entries = []
        j = 0
        while j < len(entries):
            lower = entries[j].lower()
            k = 0
            while k < len(names):
                m = 0
                while m < len(suffixes):
                    if (
                        lower.startswith("lib" + names[k]) or lower.startswith(names[k])
                    ) and lower.endswith(suffixes[m]):
                        return True
                    m += 1
                k += 1
            j += 1
        i += 1
    return False


def _native_find_library_binding_values(request: str, library_dirs):
    names = [request]
    if request == "blas":
        names = ["openblas", "blas"]
    suffixes = [".so", ".dylib", ".a", ".dll"]
    i = 0
    while i < len(library_dirs):
        root = library_dirs[i]
        try:
            entries = sorted(os.listdir(root))
        except Exception:
            entries = []
        j = 0
        while j < len(entries):
            entry = entries[j]
            lower = entry.lower()
            k = 0
            while k < len(names):
                name = names[k]
                m = 0
                while m < len(suffixes):
                    suffix = suffixes[m]
                    if (
                        lower.startswith("lib" + name) or lower.startswith(name)
                    ) and lower.endswith(suffix):
                        link_name = entry
                        if link_name.startswith("lib"):
                            link_name = link_name[3:]
                        if link_name.endswith(suffix):
                            link_name = link_name[: len(link_name) - len(suffix)]
                        return [True, link_name, root + "/" + entry]
                    m += 1
                k += 1
            j += 1
        i += 1
    return [False, request, None]


def _native_vendor_bindings_json(libraries, library_dirs, diagnostics) -> str:
    out = "["
    i = 0
    while i < len(libraries):
        binding = _native_find_library_binding_values(libraries[i], library_dirs)
        if i > 0:
            out += ", "
        out += "{"
        out += '"found": ' + ("true" if binding[0] else "false")
        out += ', "link_name": ' + _json_str(binding[1])
        out += ', "path": ' + _json_str_or_null(binding[2])
        out += ', "request": ' + _json_str(libraries[i])
        out += "}"
        if not binding[0] and not _native_list_contains(
            diagnostics, "PCC-PKG-MISSING-LIBRARY"
        ):
            diagnostics.append("PCC-PKG-MISSING-LIBRARY")
        i += 1
    out += "]"
    return out


def _native_toolchain_json(
    search_paths,
    library_dirs,
    require_fortran: bool,
    require_blas: bool,
    require_lapack: bool,
    require_cython: bool,
    require_f2py: bool,
    min_cython_version,
) -> str:
    diagnostics = []
    if require_fortran and not _native_tool_found(
        ["gfortran", "flang", "ifx", "ifort"], search_paths
    ):
        diagnostics.append("PCC-PKG-MISSING-FORTRAN")
    if require_cython and not _native_tool_found(["cython", "cython3"], search_paths):
        diagnostics.append("PCC-PKG-MISSING-CYTHON")
    cython_json = _native_find_tool_json(["cython", "cython3"], search_paths)
    if (
        require_cython
        and min_cython_version is not None
        and _native_find_from(cython_json, '"found": true', 0) >= 0
    ):
        marker = '"probe_output": "'
        pos = _native_find_from(cython_json, marker, 0)
        output = ""
        if pos >= 0:
            start = pos + len(marker)
            end = _native_find_from(cython_json, '"', start)
            if end > start:
                output = cython_json[start:end]
        if _native_version_less(output, min_cython_version):
            diagnostics.append("PCC-PKG-CYTHON-VERSION-TOO-OLD")
    if require_f2py and not _native_tool_found(["f2py", "f2py3"], search_paths):
        diagnostics.append("PCC-PKG-MISSING-F2PY")
    if require_blas and not _native_library_found(["openblas", "blas"], library_dirs):
        diagnostics.append("PCC-PKG-MISSING-BLAS")
    if require_lapack and not _native_library_found(["lapack"], library_dirs):
        diagnostics.append("PCC-PKG-MISSING-LAPACK")

    diag_json = "["
    i = 0
    while i < len(diagnostics):
        if i > 0:
            diag_json += ", "
        diag_json += "{"
        diag_json += '"code": ' + _json_str(diagnostics[i])
        diag_json += (
            ', "message": "required package build toolchain component is missing"'
        )
        diag_json += "}"
        i += 1
    diag_json += "]"

    out = "{"
    out += '"diagnostics": ' + diag_json
    out += ', "libraries": {'
    out += '"blas": ' + _native_find_library_json(["openblas", "blas"], library_dirs)
    out += ', "lapack": ' + _native_find_library_json(["lapack"], library_dirs)
    out += "}"
    out += ', "library_dirs": ' + _json_str_list(library_dirs)
    out += ', "ok": ' + ("true" if len(diagnostics) == 0 else "false")
    out += ', "requirements": {'
    out += '"blas": ' + ("true" if require_blas else "false")
    out += ', "cython": ' + ("true" if require_cython else "false")
    out += ', "f2py": ' + ("true" if require_f2py else "false")
    out += ', "fortran": ' + ("true" if require_fortran else "false")
    out += ', "lapack": ' + ("true" if require_lapack else "false")
    out += "}"
    out += ', "version_requirements": {'
    out += '"cython": ' + _json_str_or_null(min_cython_version)
    out += "}"
    out += ', "search_paths": ' + _json_str_list(search_paths)
    out += ', "tools": {'
    out += '"c_compiler": ' + _native_find_tool_json(
        ["cc", "clang", "gcc"], search_paths
    )
    out += ', "cxx_compiler": ' + _native_find_tool_json(
        ["c++", "clang++", "g++"], search_paths
    )
    out += ', "cython": ' + cython_json
    out += ', "f2py": ' + _native_find_tool_json(["f2py", "f2py3"], search_paths)
    out += ', "fortran_compiler": ' + _native_find_tool_json(
        ["gfortran", "flang", "ifx", "ifort"], search_paths
    )
    out += "}"
    out += "}"
    return out


def _run_native_package_toolchain_from_pcc1(module_args) -> int:
    search_paths = []
    library_dirs = []
    require_fortran = False
    require_blas = False
    require_lapack = False
    require_cython = False
    require_f2py = False
    min_cython_version = None
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--search-path":
            if i + 1 >= len(module_args):
                _write_text("Error: --search-path requires a value", err=True)
                return 2
            search_paths.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--search-path="):
            search_paths.append(arg.split("=", 1)[1])
        elif arg == "--library-dir":
            if i + 1 >= len(module_args):
                _write_text("Error: --library-dir requires a value", err=True)
                return 2
            library_dirs.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--library-dir="):
            library_dirs.append(arg.split("=", 1)[1])
        elif arg == "--require-fortran":
            require_fortran = True
        elif arg == "--require-blas":
            require_blas = True
        elif arg == "--require-lapack":
            require_lapack = True
        elif arg == "--require-cython":
            require_cython = True
        elif arg == "--require-f2py":
            require_f2py = True
        elif arg == "--min-cython-version":
            if i + 1 >= len(module_args):
                _write_text("Error: --min-cython-version requires a value", err=True)
                return 2
            min_cython_version = module_args[i + 1]
            i += 1
        elif arg.startswith("--min-cython-version="):
            min_cython_version = arg.split("=", 1)[1]
        elif arg == "--json":
            pass
        i += 1
    report = _native_toolchain_json(
        search_paths,
        library_dirs,
        require_fortran,
        require_blas,
        require_lapack,
        require_cython,
        require_f2py,
        min_cython_version,
    )
    _write_text(report)
    if (
        (
            require_fortran
            and not _native_tool_found(
                ["gfortran", "flang", "ifx", "ifort"], search_paths
            )
        )
        or (
            require_cython
            and not _native_tool_found(["cython", "cython3"], search_paths)
        )
        or (require_f2py and not _native_tool_found(["f2py", "f2py3"], search_paths))
        or (
            require_blas
            and not _native_library_found(["openblas", "blas"], library_dirs)
        )
        or (require_lapack and not _native_library_found(["lapack"], library_dirs))
        or (
            require_cython
            and min_cython_version is not None
            and _native_find_from(report, "PCC-PKG-CYTHON-VERSION-TOO-OLD", 0) >= 0
        )
    ):
        return 2
    return 0


def _native_text_has_libpython(text: str) -> bool:
    lower = text.lower()
    return (
        _native_find_from(lower, "libpython", 0) >= 0
        or _native_find_from(lower, "-lpython", 0) >= 0
        or _native_find_from(lower, "python.framework", 0) >= 0
        or _native_find_from(lower, ".dll", 0) >= 0
        and _native_find_from(lower, "python", 0) >= 0
    )


def _native_libpython_edge(text: str) -> str:
    lower = text.lower()
    for marker in ("libpython", "-lpython", "python.framework", "python"):
        idx = _native_find_from(lower, marker, 0)
        if idx >= 0:
            end = idx
            while end < len(text):
                ch = text[end]
                if ch == " " or ch == "\n" or ch == "\t" or ch == '"' or ch == "'":
                    break
                end += 1
            return text[idx:end]
    return "libpython"


def _native_libpython_grep_pattern() -> str:
    return "libpython|-lpython|Python[.]framework|python[0-9.]*[.]dll"


def _native_command_output_line(command: str, label: str) -> str:
    output_path = "/tmp/pcc_" + label + "_" + str(os.getpid())
    redirected = command + " > " + _native_shell_quote(output_path) + " 2>/dev/null"
    try:
        subprocess.run(["/bin/sh", "-c", redirected], check=True)
    except Exception:
        pass
    try:
        with open(output_path, "r") as fh:
            text = fh.read()
    except Exception:
        text = ""
    try:
        subprocess.run(["rm", "-f", output_path], check=True)
    except Exception:
        pass
    pos = _native_find_from(text, "\n", 0)
    if pos >= 0:
        text = text[:pos]
    return text.strip()


def _native_runtime_artifact_libpython_edge(path: str) -> str:
    # pcc1 string indexing is intentionally minimal and slow for very large
    # binary blobs. Probe native artifacts through platform tools and scan only
    # their small textual output instead of reading full .so/.dylib contents.
    quoted = _native_shell_quote(path)
    pattern = _native_shell_quote(_native_libpython_grep_pattern())
    commands = [
        "if command -v otool >/dev/null 2>&1; then otool -L "
        + quoted
        + " | grep -i -m 1 -E "
        + pattern
        + "; fi",
        "if command -v readelf >/dev/null 2>&1; then readelf -d "
        + quoted
        + " | grep -i -m 1 -E "
        + pattern
        + "; fi",
        "if command -v objdump >/dev/null 2>&1; then objdump -p "
        + quoted
        + " | grep -i -m 1 -E "
        + pattern
        + "; fi",
        "if command -v strings >/dev/null 2>&1; then strings -a "
        + quoted
        + " | grep -i -m 1 -E "
        + pattern
        + "; fi",
    ]
    i = 0
    while i < len(commands):
        text = _native_command_output_line(commands[i], "linkage_scan")
        if text != "" and _native_text_has_libpython(text):
            return _native_libpython_edge(text)
        i += 1
    return ""


def _native_is_archive_artifact_path(path: str) -> bool:
    lower = path.lower()
    return (
        lower.endswith(".whl")
        or lower.endswith(".zip")
        or lower.endswith(".tar.gz")
        or lower.endswith(".tar.bz2")
        or lower.endswith(".tar.xz")
        or lower.endswith(".tgz")
    )


def _native_archive_mentions_libpython(path: str) -> str:
    lower = path.lower()
    if not _native_is_archive_artifact_path(path):
        return ""
    extract_root = (
        "/tmp/pcc-linkage-scan-" + _native_basename(path) + "." + str(os.getpid())
    )
    try:
        subprocess.run(["rm", "-rf", extract_root], check=True)
        subprocess.run(["mkdir", "-p", extract_root], check=True)
        if lower.endswith(".whl") or lower.endswith(".zip"):
            subprocess.run(
                ["env", "LC_ALL=C", "LANG=C", "unzip", "-q", path, "-d", extract_root],
                check=True,
            )
        else:
            subprocess.run(
                ["env", "LC_ALL=C", "LANG=C", "tar", "-xf", path, "-C", extract_root],
                check=True,
            )
        edges = _native_linkage_edges_for_root(extract_root)
        subprocess.run(["rm", "-rf", extract_root], check=True)
        if len(edges) > 0:
            return edges[0]
    except Exception:
        try:
            subprocess.run(["rm", "-rf", extract_root], check=True)
        except Exception:
            pass
    return ""


def _native_artifact_mentions_libpython(path: str) -> str:
    if path.lower().endswith(".a"):
        return ""
    if _native_is_archive_artifact_path(path):
        return _native_archive_mentions_libpython(path)
    if _native_is_native_artifact(path):
        return _native_runtime_artifact_libpython_edge(path)
    try:
        with open(path, "r") as fh:
            text = fh.read()
    except Exception:
        text = ""
    if _native_text_has_libpython(text):
        return _native_libpython_edge(text)
    edge = _native_archive_mentions_libpython(path)
    if edge:
        return edge
    return ""


def _native_name_uses_cpython_extension_abi(path: str) -> bool:
    lower = _native_basename(path).lower()
    if _native_find_from(lower, ".cpython-", 0) >= 0:
        return True
    if _native_find_from(lower, "-cpython-", 0) >= 0:
        return True
    if _native_find_from(lower, "_cpython-", 0) >= 0:
        return True
    if _native_find_from(lower, ".abi3", 0) >= 0:
        return True
    if _native_find_from(lower, "-abi3", 0) >= 0:
        return True
    if _native_find_from(lower, "_abi3", 0) >= 0:
        return True
    first_cp = _native_find_from(lower, "-cp", 0)
    if first_cp >= 0 and _native_find_from(lower, "-cp", first_cp + 3) >= 0:
        return True
    return False


def _native_archive_uses_cpython_extension_abi(path: str) -> bool:
    lower = path.lower()
    if not _native_is_archive_artifact_path(path):
        return False
    extract_root = (
        "/tmp/pcc-linkage-abi-scan-" + _native_basename(path) + "." + str(os.getpid())
    )
    try:
        subprocess.run(["rm", "-rf", extract_root], check=True)
        subprocess.run(["mkdir", "-p", extract_root], check=True)
        if lower.endswith(".whl") or lower.endswith(".zip"):
            subprocess.run(
                ["env", "LC_ALL=C", "LANG=C", "unzip", "-q", path, "-d", extract_root],
                check=True,
            )
        else:
            subprocess.run(
                ["env", "LC_ALL=C", "LANG=C", "tar", "-xf", path, "-C", extract_root],
                check=True,
            )
        artifacts = _native_collect_artifacts(extract_root)
        i = 0
        while i < len(artifacts):
            if _native_name_uses_cpython_extension_abi(artifacts[i]):
                subprocess.run(["rm", "-rf", extract_root], check=True)
                return True
            i += 1
        subprocess.run(["rm", "-rf", extract_root], check=True)
    except Exception:
        try:
            subprocess.run(["rm", "-rf", extract_root], check=True)
        except Exception:
            pass
    return False


def _native_artifact_uses_cpython_extension_abi(path: str) -> bool:
    if _native_is_archive_artifact_path(path):
        return _native_archive_uses_cpython_extension_abi(path)
    if _native_is_native_artifact(path):
        return _native_name_uses_cpython_extension_abi(path)
    return False


def _native_is_native_artifact(path: str) -> bool:
    lower = path.lower()
    return (
        lower.endswith(".so")
        or lower.endswith(".dylib")
        or lower.endswith(".pyd")
        or lower.endswith(".dll")
    )


def _native_collect_artifacts(root: str):
    artifacts = []
    if os.path.isfile(root):
        if _native_is_native_artifact(root):
            artifacts.append(root)
        return artifacts
    stack = [root]
    while len(stack) > 0:
        current = stack.pop()
        try:
            names = sorted(os.listdir(current))
        except Exception:
            names = []
        i = 0
        while i < len(names):
            path = current + "/" + names[i]
            if os.path.isdir(path):
                stack.append(path)
            elif os.path.isfile(path) and _native_is_native_artifact(path):
                artifacts.append(path)
            i += 1
    return artifacts


def _native_linkage_edges_for_root(root: str):
    edges = []
    artifacts = _native_collect_artifacts(root)
    i = 0
    while i < len(artifacts):
        edge = _native_artifact_mentions_libpython(artifacts[i])
        if edge and not _native_list_contains(edges, edge):
            edges.append(edge)
        i += 1
    return edges


def _native_cpython_extension_abi_paths_for_root(root: str):
    out = []
    artifacts = _native_collect_artifacts(root)
    i = 0
    while i < len(artifacts):
        path = artifacts[i]
        if _native_artifact_uses_cpython_extension_abi(
            path
        ) and not _native_list_contains(out, path):
            out.append(path)
        i += 1
    return out


def _native_linkage_diagnostics_json(edges, cpython_abi_paths=None) -> str:
    if cpython_abi_paths is None:
        cpython_abi_paths = []
    diagnostics = "["
    i = 0
    while i < len(edges):
        if i > 0:
            diagnostics += ", "
        diagnostics += "{"
        diagnostics += '"code": "PCC-PKG-003"'
        diagnostics += ', "edge": ' + _json_str(edges[i])
        diagnostics += (
            ', "message": "native artifact mentions libpython under pcc-native mode"'
        )
        diagnostics += "}"
        i += 1
    i = 0
    while i < len(cpython_abi_paths):
        if diagnostics != "[":
            diagnostics += ", "
        diagnostics += "{"
        diagnostics += '"code": "PCC-PKG-004"'
        diagnostics += ', "message": "native artifact name declares a CPython extension ABI under pcc-native mode"'
        diagnostics += ', "path": ' + _json_str(cpython_abi_paths[i])
        diagnostics += "}"
        i += 1
    diagnostics += "]"
    return diagnostics


def _native_linkage_json(artifacts, roots, commands, abi_mode: str) -> str:
    edges = []
    cpython_abi_paths = []
    scans = "["

    def add_scan(kind: str, path, edge: str, uses_cpython_abi: bool) -> None:
        nonlocal scans
        if scans != "[":
            scans += ", "
        scans += "{"
        scans += '"kind": ' + _json_str(kind)
        scans += ', "link_libpython_edges": '
        scans += _json_str_list([edge] if edge else [])
        scans += ', "links_libpython": ' + ("true" if edge else "false")
        scans += ', "path": ' + _json_str_or_null(path)
        scans += ', "uses_cpython_extension_abi": ' + (
            "true" if uses_cpython_abi else "false"
        )
        scans += "}"
        if edge and not _native_list_contains(edges, edge):
            edges.append(edge)
        if (
            uses_cpython_abi
            and path is not None
            and not _native_list_contains(cpython_abi_paths, path)
        ):
            cpython_abi_paths.append(path)

    i = 0
    while i < len(commands):
        command = commands[i]
        add_scan(
            "link_command",
            None,
            (
                _native_libpython_edge(command)
                if _native_text_has_libpython(command)
                else ""
            ),
            False,
        )
        i += 1
    i = 0
    while i < len(artifacts):
        add_scan(
            "artifact",
            artifacts[i],
            _native_artifact_mentions_libpython(artifacts[i]),
            _native_artifact_uses_cpython_extension_abi(artifacts[i]),
        )
        i += 1
    i = 0
    while i < len(roots):
        found = _native_collect_artifacts(roots[i])
        j = 0
        while j < len(found):
            add_scan(
                "artifact",
                found[j],
                _native_artifact_mentions_libpython(found[j]),
                _native_artifact_uses_cpython_extension_abi(found[j]),
            )
            j += 1
        i += 1
    scans += "]"

    diagnostics = _native_linkage_diagnostics_json(edges, cpython_abi_paths)

    links = len(edges) > 0
    uses_cpython_abi = len(cpython_abi_paths) > 0
    ok = ((not links) or abi_mode == "libpython") and (
        (not uses_cpython_abi)
        or abi_mode == "libpython"
        or abi_mode == "cpython-compat"
    )
    out = "{"
    out += '"abi_mode": ' + _json_str(abi_mode)
    out += ', "cpython_extension_abi_paths": ' + _json_str_list(cpython_abi_paths)
    out += ', "diagnostics": ' + diagnostics
    out += ', "link_libpython_edges": ' + _json_str_list(edges)
    out += ', "links_libpython": ' + ("true" if links else "false")
    out += ', "no_libpython_runtime": ' + (
        "true"
        if (not links and not uses_cpython_abi and abi_mode == "pcc-native")
        else "false"
    )
    out += ', "ok": ' + ("true" if ok else "false")
    out += ', "scans": ' + scans
    out += ', "uses_cpython_extension_abi": ' + (
        "true" if uses_cpython_abi else "false"
    )
    out += "}"
    return out


def _native_is_repo_artifact(path: str) -> bool:
    lower = path.lower()
    return (
        lower.endswith(".whl")
        or lower.endswith(".zip")
        or lower.endswith(".tar.gz")
        or lower.endswith(".tar.bz2")
        or lower.endswith(".tar.xz")
        or lower.endswith(".tgz")
    )


def _native_repo_artifact_kind(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".whl"):
        return "wheel"
    if (
        lower.endswith(".zip")
        or lower.endswith(".tar.gz")
        or lower.endswith(".tar.bz2")
        or lower.endswith(".tar.xz")
        or lower.endswith(".tgz")
    ):
        return "sdist"
    return "artifact"


def _native_strip_repo_suffix(name: str) -> str:
    suffixes = [".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip", ".whl"]
    i = 0
    while i < len(suffixes):
        suffix = suffixes[i]
        if name.endswith(suffix):
            return name[: len(name) - len(suffix)]
        i += 1
    return name


def _native_abs_path(path: str) -> str:
    if path.startswith("/"):
        return path
    return os.getcwd() + "/" + path


def _native_basename(path: str) -> str:
    last = -1
    i = 0
    while i < len(path):
        if path[i] == "/":
            last = i
        i += 1
    return path[last + 1 :]


def _native_str_equal(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    i = 0
    while i < len(left):
        if left[i] != right[i]:
            return False
        i += 1
    return True


def _native_wheel_tag_fields(path: str):
    base = _native_basename(path)
    stem = _native_strip_repo_suffix(base)
    parts = stem.split("-")
    if len(parts) >= 5 and path.lower().endswith(".whl"):
        return [
            parts[0],
            parts[len(parts) - 3],
            parts[len(parts) - 2],
            parts[len(parts) - 1],
        ]
    name = parts[0] if len(parts) > 0 else stem
    return [name, "", "", ""]


def _native_collect_repo_artifacts(root: str):
    artifacts = []
    if os.path.isfile(root):
        if _native_is_repo_artifact(root):
            artifacts.append(root)
        return artifacts
    if not os.path.isdir(root):
        return artifacts
    stack = [root]
    while len(stack) > 0:
        current = stack.pop()
        try:
            names = sorted(os.listdir(current))
        except Exception:
            names = []
        i = 0
        while i < len(names):
            name = names[i]
            path = current + "/" + name
            if os.path.isdir(path):
                if name != "__pycache__" and name != ".git":
                    stack.append(path)
            elif os.path.isfile(path) and _native_is_repo_artifact(path):
                artifacts.append(path)
            i += 1
    return artifacts


def _native_repo_compatibility(
    kind: str, python_tag: str, abi_tag: str, platform_tag: str
):
    if kind == "sdist":
        return [True, "source_artifact"]
    if kind != "wheel":
        return [False, "unsupported_artifact_kind"]
    if python_tag == "py3" and abi_tag == "none" and platform_tag == "any":
        return [True, "pure_python_wheel"]
    if (
        python_tag == "pcc3"
        and abi_tag == "pcc_native"
        and platform_tag == _native_current_platform_tag()
    ):
        return [True, "pcc_native_wheel"]
    return [False, "wheel_tag_not_pcc_native_compatible"]


def _native_wheel_repository_json(
    root: str, add_artifacts, abi_mode: str, write_manifest: bool
) -> str:
    repo_root = _native_abs_path(root)
    copied = []
    if len(add_artifacts) > 0:
        try:
            subprocess.run(["mkdir", "-p", repo_root], check=True)
        except Exception:
            pass
        i = 0
        while i < len(add_artifacts):
            source = _native_abs_path(add_artifacts[i])
            dest = repo_root + "/" + _native_basename(source)
            if os.path.isfile(source):
                try:
                    if not _native_str_equal(source, dest):
                        subprocess.run(["cp", source, dest], check=True)
                    copied.append(dest)
                except Exception:
                    pass
            i += 1
    artifacts = _native_collect_repo_artifacts(repo_root)
    rows = "["
    diagnostics = "["
    artifact_count = 0
    ok = True
    i = 0
    while i < len(artifacts):
        path = artifacts[i]
        kind = _native_repo_artifact_kind(path)
        fields = _native_wheel_tag_fields(path)
        name = fields[0]
        python_tag = fields[1]
        abi_tag = fields[2]
        platform_tag = fields[3]
        compat = _native_repo_compatibility(kind, python_tag, abi_tag, platform_tag)
        linkage = _native_linkage_json([path], [], [], abi_mode)
        if not compat[0] or _native_find_from(linkage, '"ok": false', 0) >= 0:
            ok = False
        if i > 0:
            rows += ", "
        rows += "{"
        rows += '"abi_tag": ' + _json_str_or_null(abi_tag if abi_tag != "" else None)
        rows += ', "compatibility_reason": ' + _json_str(compat[1])
        rows += ', "diagnostics": ['
        diag_written = False
        if not compat[0]:
            rows += "{"
            rows += '"code": "PCC-REPO-TAG-INCOMPATIBLE"'
            rows += ', "message": "artifact wheel tag is not compatible with pcc-native repository policy"'
            rows += ', "reason": ' + _json_str(compat[1])
            rows += "}"
            diag_written = True
            if diagnostics != "[":
                diagnostics += ", "
            diagnostics += "{"
            diagnostics += '"code": "PCC-REPO-TAG-INCOMPATIBLE"'
            diagnostics += ', "path": ' + _json_str(path)
            diagnostics += ', "reason": ' + _json_str(compat[1])
            diagnostics += "}"
        if _native_find_from(linkage, '"links_libpython": true', 0) >= 0:
            if diag_written:
                rows += ", "
            rows += "{"
            rows += '"code": "PCC-PKG-003"'
            rows += ', "message": "repository artifact mentions libpython under pcc-native mode"'
            rows += "}"
            if diagnostics != "[":
                diagnostics += ", "
            diagnostics += "{"
            diagnostics += '"code": "PCC-PKG-003"'
            diagnostics += ', "path": ' + _json_str(path)
            diagnostics += "}"
        rows += "]"
        rows += ', "linkage": ' + linkage
        rows += ', "links_libpython": ' + (
            "true"
            if _native_find_from(linkage, '"links_libpython": true', 0) >= 0
            else "false"
        )
        rows += ', "name": ' + _json_str(name)
        rows += ', "no_libpython_runtime": ' + (
            "true"
            if _native_find_from(linkage, '"no_libpython_runtime": true', 0) >= 0
            else "false"
        )
        rows += ', "path": ' + _json_str(path)
        rows += ', "pcc_native_compatible": ' + ("true" if compat[0] else "false")
        rows += ', "platform_tag": ' + _json_str_or_null(
            platform_tag if platform_tag != "" else None
        )
        rows += ', "python_tag": ' + _json_str_or_null(
            python_tag if python_tag != "" else None
        )
        rows += ', "source_kind": ' + _json_str(kind)
        rows += "}"
        artifact_count += 1
        i += 1
    rows += "]"
    diagnostics += "]"
    manifest_path = None
    if write_manifest:
        try:
            subprocess.run(["mkdir", "-p", repo_root], check=True)
            manifest_path = repo_root + "/pcc-wheel-repository.json"
            out_preview = "{"
            out_preview += '"artifact_count": ' + str(artifact_count)
            out_preview += ', "artifacts": ' + rows
            out_preview += ', "current_platform_tag": ' + _json_str(
                _native_current_platform_tag()
            )
            out_preview += ', "diagnostics": ' + diagnostics
            out_preview += ', "manifest_path": ' + _json_str(manifest_path)
            out_preview += ', "ok": ' + ("true" if ok else "false")
            out_preview += ', "pcc_native_wheel_tag": ' + _json_str(
                _native_pcc_wheel_tag()
            )
            out_preview += ', "root": ' + _json_str(repo_root)
            out_preview += ', "schema": "pcc.wheel-repository.v1"'
            out_preview += "}"
            with open(manifest_path, "w") as fh:
                fh.write(out_preview)
        except Exception:
            manifest_path = None
    out = "{"
    out += '"artifact_count": ' + str(artifact_count)
    out += ', "artifacts": ' + rows
    out += ', "copied_artifacts": ' + _json_str_list(copied)
    out += ', "current_platform_tag": ' + _json_str(_native_current_platform_tag())
    out += ', "diagnostics": ' + diagnostics
    out += ', "manifest_path": ' + _json_str_or_null(manifest_path)
    out += ', "ok": ' + ("true" if ok else "false")
    out += ', "pcc_native_wheel_tag": ' + _json_str(_native_pcc_wheel_tag())
    out += ', "root": ' + _json_str(repo_root)
    out += ', "schema": "pcc.wheel-repository.v1"'
    out += "}"
    return out


def _run_native_package_wheel_repo_from_pcc1(module_args) -> int:
    root = None
    add_artifacts = []
    abi_mode = "pcc-native"
    write_manifest = False
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--json":
            pass
        elif arg == "--root":
            if i + 1 >= len(module_args):
                _write_text("Error: --root requires a value", err=True)
                return 2
            root = module_args[i + 1]
            i += 1
        elif arg.startswith("--root="):
            root = arg.split("=", 1)[1]
        elif arg == "--add":
            if i + 1 >= len(module_args):
                _write_text("Error: --add requires a value", err=True)
                return 2
            add_artifacts.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--add="):
            add_artifacts.append(arg.split("=", 1)[1])
        elif arg == "--abi":
            if i + 1 >= len(module_args):
                _write_text("Error: --abi requires a value", err=True)
                return 2
            abi_mode = module_args[i + 1]
            i += 1
        elif arg.startswith("--abi="):
            abi_mode = arg.split("=", 1)[1]
        elif arg == "--write-manifest":
            write_manifest = True
        i += 1
    if root is None:
        _write_text('{"error": "missing repository root", "ok": false}')
        return 2
    report = _native_wheel_repository_json(
        root, add_artifacts, abi_mode, write_manifest
    )
    _write_text(report)
    return 2 if _native_find_from(report, '"ok": false', 0) >= 0 else 0


def _run_native_package_linkage_from_pcc1(module_args) -> int:
    artifacts = []
    roots = []
    commands = []
    abi_mode = "pcc-native"
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--artifact":
            if i + 1 >= len(module_args):
                _write_text("Error: --artifact requires a value", err=True)
                return 2
            artifacts.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--artifact="):
            artifacts.append(arg.split("=", 1)[1])
        elif arg == "--root":
            if i + 1 >= len(module_args):
                _write_text("Error: --root requires a value", err=True)
                return 2
            roots.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--root="):
            roots.append(arg.split("=", 1)[1])
        elif arg == "--command":
            if i + 1 >= len(module_args):
                _write_text("Error: --command requires a value", err=True)
                return 2
            commands.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--command="):
            commands.append(arg.split("=", 1)[1])
        elif arg == "--abi":
            if i + 1 >= len(module_args):
                _write_text("Error: --abi requires a value", err=True)
                return 2
            abi_mode = module_args[i + 1]
            i += 1
        elif arg.startswith("--abi="):
            abi_mode = arg.split("=", 1)[1]
        elif arg == "--json":
            pass
        i += 1
    report = _native_linkage_json(artifacts, roots, commands, abi_mode)
    _write_text(report)
    if _native_find_from(report, '"ok": true', 0) >= 0:
        return 0
    return 2


def _run_native_pip_shim_from_pcc1(module_args) -> int:
    raw = _copy_seq(module_args)
    if len(raw) == 0:
        raw = ["install", "--dry-run"]
    if len(raw) > 0 and (raw[0] == "-h" or raw[0] == "--help"):
        _write_text("usage: pcc -m pip [install] [packages...] [--dry-run]")
        return 0
    command = raw[0] if len(raw) > 0 else "install"
    dry_run = 0
    target_dir = ""
    cache_dir = ""
    abi = "pcc-native"
    report_path = ""
    find_links = []
    index_urls = []
    no_index = False
    packages = []
    i = 1
    while i < len(raw):
        arg = raw[i]
        if arg == "--dry-run":
            dry_run = 1
        elif arg == "--json":
            pass
        elif arg == "--report":
            dry_run = 1
            if i + 1 < len(raw) and not raw[i + 1].startswith("-"):
                report_path = raw[i + 1]
                i += 1
        elif arg.startswith("--report="):
            dry_run = 1
            report_path = arg.split("=", 1)[1]
        elif arg == "--target" or arg == "--target-dir":
            if i + 1 >= len(raw):
                _write_text(
                    '{"command": "install", "error": "--target requires a value", "ok": false}'
                )
                return 2
            target_dir = raw[i + 1]
            i += 1
        elif arg == "--cache-dir":
            if i + 1 >= len(raw):
                _write_text(
                    '{"command": "install", "error": "--cache-dir requires a value", "ok": false}'
                )
                return 2
            cache_dir = raw[i + 1]
            i += 1
        elif arg == "--find-links" or arg == "-f":
            if i + 1 >= len(raw):
                _write_text(
                    '{"command": "install", "error": "--find-links requires a value", "ok": false}'
                )
                return 2
            find_links.append(raw[i + 1])
            i += 1
        elif arg.startswith("--find-links="):
            find_links.append(arg.split("=", 1)[1])
        elif arg == "--no-index":
            no_index = True
        elif arg == "--index-url" or arg == "-i":
            if i + 1 >= len(raw):
                _write_text(
                    '{"command": "install", "error": "--index-url requires a value", "ok": false}'
                )
                return 2
            index_urls.append(raw[i + 1])
            i += 1
        elif arg.startswith("--index-url="):
            index_urls.append(arg.split("=", 1)[1])
        elif arg == "--extra-index-url":
            if i + 1 >= len(raw):
                _write_text(
                    '{"command": "install", "error": "--extra-index-url requires a value", "ok": false}'
                )
                return 2
            index_urls.append(raw[i + 1])
            i += 1
        elif arg.startswith("--extra-index-url="):
            index_urls.append(arg.split("=", 1)[1])
        elif arg == "--abi":
            if i + 1 >= len(raw):
                _write_text(
                    '{"command": "install", "error": "--abi requires a value", "ok": false}'
                )
                return 2
            abi = raw[i + 1]
            i += 1
        elif arg.startswith("--abi="):
            abi = arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            packages.append(arg)
        i += 1
    if command != "install":
        _write_text(
            '{"command": '
            + _json_str(command)
            + ', "error": "only install dry-run is supported", "ok": false}'
        )
        return 2
    if len(packages) == 0:
        _write_text(
            '{"command": "install", "error": '
            + _json_str("no packages requested")
            + ', "ok": false}'
        )
        return 2
    if no_index:
        index_urls = []
    if len(find_links) > 0 or len(index_urls) > 0:
        packages = _native_resolve_install_order(
            packages, cache_dir, find_links, abi, index_urls
        )
    if dry_run == 0:
        installs = "["
        all_ok = True
        k = 0
        while k < len(packages):
            if k > 0:
                installs += ", "
            install_json = _native_install_manifest_json(
                packages[k],
                target_dir,
                cache_dir,
                find_links,
                abi,
                index_urls,
            )
            if _native_find_from(install_json, '"ok": false', 0) >= 0:
                all_ok = False
            installs += install_json
            k += 1
        installs += "]"
        out = "{"
        out += '"command": "install"'
        out += ', "abi": ' + _json_str(abi)
        out += ', "dry_run": false'
        out += ', "find_links": ' + _json_str_list(find_links)
        out += ', "index_urls": ' + _json_str_list(index_urls)
        out += ', "installs": ' + installs
        out += ', "no_index": ' + ("true" if no_index else "false")
        out += ', "ok": ' + ("true" if all_ok else "false")
        out += ', "packages": ' + _json_str_list(packages)
        out += ', "report_path": ' + _json_str_or_null(
            None if report_path == "" else report_path
        )
        out += ', "resolver_diagnostics": []'
        out += "}"
        if report_path != "":
            try:
                with open(report_path, "w") as fh:
                    fh.write(out)
            except Exception:
                _write_text(
                    '{"command": "install", "error": "failed to write report", "ok": false}'
                )
                return 2
        _write_text(out)
        return 0 if all_ok else 2
    inspections = "["
    j = 0
    while j < len(packages):
        if j > 0:
            inspections += ", "
        inspections += _package_inspection_json(packages[j], None)
        j += 1
    inspections += "]"
    out = "{"
    out += '"command": "install"'
    out += ', "abi": ' + _json_str(abi)
    out += ', "dry_run": true'
    out += ', "find_links": ' + _json_str_list(find_links)
    out += ', "index_urls": ' + _json_str_list(index_urls)
    out += ', "inspections": ' + inspections
    out += ', "no_index": ' + ("true" if no_index else "false")
    out += ', "ok": true'
    out += ', "packages": ' + _json_str_list(packages)
    out += ', "report_path": ' + _json_str_or_null(
        None if report_path == "" else report_path
    )
    out += ', "resolver_diagnostics": []'
    out += "}"
    if report_path != "":
        try:
            with open(report_path, "w") as fh:
                fh.write(out)
        except Exception:
            _write_text(
                '{"command": "install", "error": "failed to write report", "ok": false}'
            )
            return 2
    _write_text(out)
    return 0


def _native_package_basename(spec: str) -> str:
    base = os.path.basename(spec)
    if base == "":
        base = spec
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip", ".whl"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    if "-" in base:
        return base.split("-")[0]
    return base or "package"


def _native_normalized_package_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _native_artifact_project_name(path: str) -> str:
    base = os.path.basename(path)
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip", ".whl"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    if "-" in base:
        return base.split("-")[0]
    return base


def _native_artifact_version_text(path: str) -> str:
    base = os.path.basename(path)
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip", ".whl"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    parts = base.split("-")
    if path.endswith(".whl") and len(parts) >= 5:
        return parts[1]
    if len(parts) >= 2:
        return parts[1]
    return "0"


def _native_version_numbers(path: str):
    text = _native_artifact_version_text(path)
    nums = []
    current = -1
    i = 0
    while i < len(text):
        ch = text[i]
        if "0" <= ch <= "9":
            if current < 0:
                current = 0
            current = current * 10 + (ord(ch) - ord("0"))
        else:
            if current >= 0:
                nums.append(current)
                current = -1
        i += 1
    if current >= 0:
        nums.append(current)
    while len(nums) < 4:
        nums.append(0)
    return nums


def _native_artifact_better(candidate: str, best: str) -> bool:
    left = _native_version_numbers(candidate)
    right = _native_version_numbers(best)
    i = 0
    while i < 4:
        if left[i] > right[i]:
            return True
        if left[i] < right[i]:
            return False
        i += 1
    return candidate > best


def _native_artifact_rank(reason: str) -> int:
    if reason == "pcc_native_wheel":
        return 3
    if reason == "pure_python_wheel":
        return 2
    if reason == "source_artifact":
        return 1
    return 0


def _native_artifact_better_for_reason(
    candidate: str,
    candidate_reason: str,
    best: str,
    best_reason: str,
) -> bool:
    left = _native_version_numbers(candidate)
    right = _native_version_numbers(best)
    i = 0
    while i < 4:
        if left[i] > right[i]:
            return True
        if left[i] < right[i]:
            return False
        i += 1
    left_rank = _native_artifact_rank(candidate_reason)
    right_rank = _native_artifact_rank(best_reason)
    if left_rank > right_rank:
        return True
    if left_rank < right_rank:
        return False
    return candidate > best


def _native_artifact_compatibility_for_abi(path: str, abi: str):
    if abi != "pcc-native":
        return [True, "abi_mode_allows_artifact"]
    kind = _native_repo_artifact_kind(path)
    fields = _native_wheel_tag_fields(path)
    compat = _native_repo_compatibility(kind, fields[1], fields[2], fields[3])
    if not compat[0]:
        return compat
    edge = _native_artifact_mentions_libpython(path)
    if edge:
        return [False, "links_libpython"]
    if _native_artifact_uses_cpython_extension_abi(path):
        return [False, "cpython_extension_abi"]
    return compat


def _native_artifact_compatibility_for_name(name: str, abi: str):
    if abi != "pcc-native":
        return [True, "abi_mode_allows_artifact"]
    kind = _native_repo_artifact_kind(name)
    fields = _native_wheel_tag_fields(name)
    return _native_repo_compatibility(kind, fields[1], fields[2], fields[3])


def _native_json_string_after(text: str, marker: str, start: int):
    idx = _native_find_from(text, marker, start)
    if idx < 0:
        return None
    i = idx + len(marker)
    out = ""
    escaped = False
    while i < len(text):
        ch = text[i]
        if escaped:
            out += ch
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            return out
        else:
            out += ch
        i += 1
    return None


def _native_json_bool_value(text: str, key: str):
    idx = _native_find_from(text, key, 0)
    if idx < 0:
        return None
    colon = _native_find_from(text, ":", idx + len(key))
    if colon < 0:
        return None
    i = colon + 1
    while i < len(text) and (text[i] == " " or text[i] == "\n" or text[i] == "\t"):
        i += 1
    if _native_find_from(text, "true", i) == i:
        return True
    if _native_find_from(text, "false", i) == i:
        return False
    return None


def _native_find_manifest_artifact(root: str, expected: str, abi: str):
    manifest_path = root + "/pcc-wheel-repository.json"
    if not os.path.isfile(manifest_path):
        return [None, ""]
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return [None, ""]
    best = None
    best_reason = ""
    pos = 0
    marker = '"path": "'
    while pos < len(text):
        path = _native_json_string_after(text, marker, pos)
        idx = _native_find_from(text, marker, pos)
        if path is None or idx < 0:
            break
        pos = idx + len(marker)
        if not path.startswith("/"):
            path = root + "/" + path
        if os.path.isfile(path):
            project = _native_artifact_project_name(path)
            if _native_normalized_package_name(project) == expected:
                start = idx - 1500
                if start < 0:
                    start = 0
                end = idx + 1500
                if end > len(text):
                    end = len(text)
                window = text[start:end]
                compatible = _native_json_bool_value(window, '"pcc_native_compatible"')
                links = _native_json_bool_value(window, '"links_libpython"')
                reason = _native_json_string_after(
                    window, '"compatibility_reason": "', 0
                )
                if reason is None:
                    reason = ""
                allowed = True
                if abi == "pcc-native":
                    if compatible is None or not compatible:
                        allowed = False
                    if links:
                        allowed = False
                if allowed:
                    compat = _native_artifact_compatibility_for_abi(path, abi)
                    if compat[0]:
                        reason = compat[1]
                        if best is None or _native_artifact_better_for_reason(
                            path,
                            reason,
                            best,
                            best_reason,
                        ):
                            best = os.path.abspath(path)
                            best_reason = reason
    return [best, best_reason]


def _native_find_links_artifact_result(spec: str, find_links, abi: str):
    expected = _native_normalized_package_name(spec)
    best = None
    best_reason = ""
    manifest_best = None
    manifest_best_reason = ""
    i = 0
    while i < len(find_links):
        root = find_links[i]
        candidates = []
        if os.path.isfile(root):
            candidates.append(root)
        elif os.path.isdir(root):
            manifest = _native_find_manifest_artifact(root, expected, abi)
            if manifest[0] is not None:
                if manifest_best is None or _native_artifact_better_for_reason(
                    manifest[0],
                    manifest[1],
                    manifest_best,
                    manifest_best_reason,
                ):
                    manifest_best = manifest[0]
                    manifest_best_reason = manifest[1]
            try:
                names = sorted(os.listdir(root))
            except Exception:
                names = []
            j = 0
            while j < len(names):
                candidates.append(root + "/" + names[j])
                j += 1
        j = 0
        while j < len(candidates):
            candidate = candidates[j]
            lower = candidate.lower()
            is_artifact = (
                lower.endswith(".whl")
                or lower.endswith(".tar.gz")
                or lower.endswith(".tar.bz2")
                or lower.endswith(".tar.xz")
                or lower.endswith(".tgz")
                or lower.endswith(".zip")
            )
            if is_artifact and os.path.isfile(candidate):
                project = _native_artifact_project_name(candidate)
                if _native_normalized_package_name(project) == expected:
                    compat = _native_artifact_compatibility_for_abi(candidate, abi)
                    if compat[0] and (
                        best is None
                        or _native_artifact_better_for_reason(
                            candidate,
                            compat[1],
                            best,
                            best_reason,
                        )
                    ):
                        best = os.path.abspath(candidate)
                        best_reason = compat[1]
            j += 1
        i += 1
    if manifest_best is not None:
        return [manifest_best, "wheel-repository"]
    if best is not None:
        return [best, "find-links"]
    return [None, None]


def _native_find_links_artifact(spec: str, find_links, abi: str = "pcc-native"):
    result = _native_find_links_artifact_result(spec, find_links, abi)
    return result[0]


def _native_url_basename(url: str) -> str:
    end = len(url)
    i = 0
    while i < len(url):
        if url[i] == "?" or url[i] == "#":
            end = i
            break
        i += 1
    last = -1
    i = 0
    while i < end:
        if url[i] == "/":
            last = i
        i += 1
    return url[last + 1 : end]


def _native_url_origin(url: str) -> str:
    scheme_end = _native_find_from(url, "://", 0)
    if scheme_end < 0:
        return ""
    i = scheme_end + 3
    while i < len(url):
        if url[i] == "/":
            return url[:i]
        i += 1
    return url


def _native_url_join(page_url: str, href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return _native_url_origin(page_url) + href
    end = len(page_url)
    i = 0
    while i < len(page_url):
        if page_url[i] == "?" or page_url[i] == "#":
            end = i
            break
        i += 1
    last = -1
    i = 0
    while i < end:
        if page_url[i] == "/":
            last = i
        i += 1
    if last >= 0:
        return page_url[: last + 1] + href
    return href


def _native_index_package_url(index_url: str, spec: str) -> str:
    base = index_url
    if not base.endswith("/"):
        base += "/"
    return base + _native_normalized_package_name(spec) + "/"


def _native_extract_href_links(text: str, page_url: str):
    links = []
    lower_text = text.lower()
    pos = 0
    while pos < len(text):
        href_idx = _native_find_from(lower_text, "href", pos)
        if href_idx < 0:
            break
        eq_idx = _native_find_from(text, "=", href_idx)
        if eq_idx < 0:
            break
        i = eq_idx + 1
        while i < len(text) and (text[i] == " " or text[i] == "\n" or text[i] == "\t"):
            i += 1
        if i >= len(text):
            break
        quote = text[i]
        if quote != '"' and quote != "'":
            pos = i + 1
            continue
        i += 1
        start = i
        while i < len(text) and text[i] != quote:
            i += 1
        href = text[start:i]
        url = _native_url_join(page_url, href)
        name = _native_url_basename(url)
        if name != "":
            links.append([url, name])
        pos = i + 1
    return links


def _native_find_index_artifact_result(spec: str, cache_dir, index_urls, abi: str):
    expected = _native_normalized_package_name(spec)
    cache_root = (
        cache_dir or os.environ.get("PCC_PACKAGE_CACHE") or "/tmp/pcc-package-cache"
    )
    scratch = cache_root + "/index-pages"
    downloads = cache_root + "/downloads"
    best_url = None
    best_name = ""
    best_reason = ""
    try:
        subprocess.run(["mkdir", "-p", scratch], check=True)
        subprocess.run(["mkdir", "-p", downloads], check=True)
    except Exception:
        return [None, None]
    i = 0
    while i < len(index_urls):
        page_url = _native_index_package_url(index_urls[i], spec)
        page_path = (
            scratch
            + "/"
            + _native_normalized_package_name(spec)
            + "."
            + str(i)
            + ".html"
        )
        if os._pcc_http_download_to_file(page_url, page_path) != 0:
            i += 1
            continue
        try:
            with open(page_path, "r", encoding="utf-8") as fh:
                page = fh.read()
        except Exception:
            i += 1
            continue
        links = _native_extract_href_links(page, page_url)
        j = 0
        while j < len(links):
            url = links[j][0]
            name = links[j][1]
            if _native_is_repo_artifact(name):
                project = _native_artifact_project_name(name)
                if _native_normalized_package_name(project) == expected:
                    compat = _native_artifact_compatibility_for_name(name, abi)
                    if compat[0] and (
                        best_url is None
                        or _native_artifact_better_for_reason(
                            name,
                            compat[1],
                            best_name,
                            best_reason,
                        )
                    ):
                        best_url = url
                        best_name = name
                        best_reason = compat[1]
            j += 1
        i += 1
    if best_url is None:
        return [None, None]
    dest = downloads + "/" + best_name
    if os._pcc_http_download_to_file(best_url, dest) != 0:
        return [None, None]
    return [os.path.abspath(dest), "index-url"]


def _native_find_links_package_names(find_links):
    packages = []
    i = 0
    while i < len(find_links):
        root = find_links[i]
        candidates = []
        if os.path.isfile(root):
            candidates.append(root)
        elif os.path.isdir(root):
            try:
                names = sorted(os.listdir(root))
            except Exception:
                names = []
            j = 0
            while j < len(names):
                candidates.append(root + "/" + names[j])
                j += 1
        j = 0
        while j < len(candidates):
            candidate = candidates[j]
            lower = candidate.lower()
            is_artifact = (
                lower.endswith(".whl")
                or lower.endswith(".tar.gz")
                or lower.endswith(".tar.bz2")
                or lower.endswith(".tar.xz")
                or lower.endswith(".tgz")
                or lower.endswith(".zip")
            )
            if is_artifact and os.path.isfile(candidate):
                project = _native_artifact_project_name(candidate)
                if project not in packages:
                    packages.append(project)
            j += 1
        i += 1
    return packages


def _native_parse_requires_dist_name(line: str):
    prefix = "Requires-Dist:"
    if not line.startswith(prefix):
        return None
    rest = line[len(prefix) :].strip()
    out = ""
    i = 0
    while i < len(rest):
        ch = rest[i]
        ok = (
            ("a" <= ch <= "z")
            or ("A" <= ch <= "Z")
            or ("0" <= ch <= "9")
            or ch == "_"
            or ch == "-"
            or ch == "."
        )
        if not ok:
            break
        out += ch
        i += 1
    return out or None


def _native_requires_from_metadata_file(path: str):
    deps = []
    try:
        with open(path, "r") as fh:
            text = fh.read()
    except Exception:
        return deps
    for line in text.split("\n"):
        dep = _native_parse_requires_dist_name(line)
        if dep is not None:
            deps.append(dep)
    return deps


def _native_requires_from_tree(root: str):
    deps = []
    stack = [root]
    while len(stack) > 0:
        current = stack.pop()
        try:
            names = sorted(os.listdir(current))
        except Exception:
            names = []
        i = 0
        while i < len(names):
            name = names[i]
            path = current + "/" + name
            if os.path.isdir(path):
                stack.append(path)
            elif name == "METADATA" or name == "PKG-INFO":
                file_deps = _native_requires_from_metadata_file(path)
                j = 0
                while j < len(file_deps):
                    if file_deps[j] not in deps:
                        deps.append(file_deps[j])
                    j += 1
            i += 1
    return deps


def _native_artifact_requires_dist(source, scratch_root: str):
    if source is None:
        return []
    if os.path.isdir(source):
        return _native_requires_from_tree(source)
    lower = source.lower()
    is_zip = lower.endswith(".whl") or lower.endswith(".zip")
    is_tar = (
        lower.endswith(".tar.gz")
        or lower.endswith(".tar.bz2")
        or lower.endswith(".tar.xz")
        or lower.endswith(".tgz")
    )
    if not (is_zip or is_tar):
        return []
    extract_root = scratch_root + "/" + _native_package_basename(source) + ".deps"
    try:
        subprocess.run(["rm", "-rf", extract_root], check=True)
        subprocess.run(["mkdir", "-p", extract_root], check=True)
        if is_zip:
            subprocess.run(
                [
                    "env",
                    "LC_ALL=C",
                    "LANG=C",
                    "unzip",
                    "-q",
                    source,
                    "-d",
                    extract_root,
                ],
                check=True,
            )
        else:
            subprocess.run(
                ["env", "LC_ALL=C", "LANG=C", "tar", "-xf", source, "-C", extract_root],
                check=True,
            )
        deps = _native_requires_from_tree(extract_root)
        subprocess.run(["rm", "-rf", extract_root], check=True)
        return deps
    except Exception:
        try:
            subprocess.run(["rm", "-rf", extract_root], check=True)
        except Exception:
            pass
        return []


def _native_resolve_install_order(
    packages,
    cache_dir,
    find_links,
    abi: str = "pcc-native",
    index_urls=None,
):
    if index_urls is None:
        index_urls = []
    ordered = []
    visiting = []
    done = []
    scratch = (
        cache_dir or os.environ.get("PCC_PACKAGE_CACHE") or "/tmp/pcc-package-cache"
    )

    def visit(pkg: str) -> None:
        if _native_package_list_contains(done, pkg) != 0:
            return
        if _native_package_list_contains(visiting, pkg) != 0:
            return
        visiting.append(pkg)
        source = _native_resolve_install_source(
            pkg, cache_dir, find_links, abi, index_urls
        )
        if source is not None:
            deps = _native_artifact_requires_dist(source, scratch)
            i = 0
            while i < len(deps):
                dep = deps[i]
                if (
                    _native_resolve_install_source(
                        dep, cache_dir, find_links, abi, index_urls
                    )
                    is not None
                ):
                    visit(dep)
                i += 1
        visiting.pop()
        if _native_package_list_contains(done, pkg) == 0:
            done.append(pkg)
            ordered.append(pkg)

    i = 0
    if len(find_links) > 0:
        wheelhouse_packages = _native_find_links_package_names(find_links)
        while i < len(wheelhouse_packages):
            if _native_package_list_contains(packages, wheelhouse_packages[i]) == 0:
                visit(wheelhouse_packages[i])
            i += 1
        i = 0
    while i < len(packages):
        visit(packages[i])
        i += 1
    return ordered


def _native_resolve_install_source_result(
    spec: str,
    cache_dir,
    find_links=None,
    abi: str = "pcc-native",
    index_urls=None,
):
    if os.path.exists(spec):
        return [os.path.abspath(spec), "direct"]
    if find_links is None:
        find_links = []
    if index_urls is None:
        index_urls = []
    linked = _native_find_links_artifact_result(spec, find_links, abi)
    if linked[0] is not None:
        return linked
    if cache_dir is not None and cache_dir != "":
        direct = os.path.join(cache_dir, spec)
        if os.path.exists(direct):
            return [os.path.abspath(direct), "cache"]
    path = _native_package_path(spec, None)
    if path is not None:
        return [path, "projects"]
    if len(index_urls) > 0:
        indexed = _native_find_index_artifact_result(spec, cache_dir, index_urls, abi)
        if indexed[0] is not None:
            return indexed
    return [None, None]


def _native_resolve_install_source(
    spec: str,
    cache_dir,
    find_links=None,
    abi: str = "pcc-native",
    index_urls=None,
):
    result = _native_resolve_install_source_result(
        spec, cache_dir, find_links, abi, index_urls
    )
    return result[0]


def _native_package_list_contains(items, value) -> int:
    key = _native_normalized_package_name(value)
    i = 0
    while i < len(items):
        if _native_normalized_package_name(items[i]) == key:
            return 1
        i += 1
    return 0


def _native_cython_build_requirement(root: str) -> str:
    path = os.path.join(root, "pyproject.toml")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r") as fh:
            text = fh.read()
    except Exception:
        return ""
    lower = text.lower()
    pos = _native_find_from(lower, "cython", 0)
    if pos < 0:
        return ""
    ge = _native_find_from(lower, ">=", pos)
    if ge < 0:
        return "Cython"
    i = ge + 2
    while i < len(text) and (text[i] == " " or text[i] == "\t"):
        i += 1
    version = ""
    while i < len(text):
        ch = text[i]
        if ("0" <= ch <= "9") or ch == ".":
            version += ch
            i += 1
        else:
            break
    if version == "":
        return "Cython"
    return "Cython>=" + version


def _native_prepare_build_tools(root: str) -> str:
    requirement = _native_cython_build_requirement(root)
    if requirement == "":
        return ""
    tool_dir = "/tmp/pcc_build_tools_" + str(os.getpid())
    try:
        subprocess.run(["rm", "-rf", tool_dir], check=True)
        subprocess.run(["mkdir", "-p", tool_dir], check=True)
        script = "#!/bin/sh\n"
        script += "exec uv run --with " + _native_shell_quote(requirement)
        script += ' cython "$@"\n'
        for name in ["cython", "cython3"]:
            path = tool_dir + "/" + name
            with open(path, "w") as fh:
                fh.write(script)
            subprocess.run(["chmod", "+x", path], check=True)
    except Exception:
        return ""
    return tool_dir


def _native_path_prefix(tool_dir: str) -> str:
    if tool_dir == "":
        return ""
    return "PATH=" + _native_shell_quote(tool_dir) + ":$PATH "


def _native_shell_command_succeeds(command: str) -> bool:
    try:
        subprocess.run(["/bin/sh", "-c", command], check=True)
        return True
    except Exception:
        return False


def _native_meson_setup_shell_command(source: str, build_dir: str, tool_dir: str):
    prefix = _native_path_prefix(tool_dir)
    if _native_shell_command_succeeds(prefix + "command -v meson >/dev/null 2>&1"):
        return (
            prefix
            + "meson setup "
            + _native_shell_quote(build_dir)
            + " "
            + _native_shell_quote(source)
        )
    vendored = os.path.join(source, "vendored-meson", "meson", "meson.py")
    if os.path.isfile(vendored):
        python = os.environ.get("PCC_BUILD_PYTHON") or "python3"
        return (
            prefix
            + _native_shell_quote(python)
            + " "
            + _native_shell_quote(vendored)
            + " setup "
            + _native_shell_quote(build_dir)
            + " "
            + _native_shell_quote(source)
        )
    return None


def _native_build_report_action_json(
    kind: str, command: str, status: str, returncode
) -> str:
    out = "{"
    out += '"command": ' + _json_str(command)
    out += ', "kind": ' + _json_str(kind)
    out += ', "returncode": ' + ("null" if returncode is None else str(returncode))
    out += ', "status": ' + _json_str(status)
    out += "}"
    return out


def _native_redirected_shell_command(command: str, label: str) -> list:
    log_path = "/tmp/pcc_build_" + str(os.getpid()) + "_" + label + ".log"
    redirected = command + " > " + _native_shell_quote(log_path) + " 2>&1"
    return [redirected, log_path]


def _native_ensure_meson_build_outputs_json(source) -> str:
    if source is None or not os.path.isdir(source):
        return (
            '{"actions": [], "ok": true, "reason": "not_source_tree", "skipped": true}'
        )
    if not os.path.isfile(os.path.join(source, "meson.build")):
        return (
            '{"actions": [], "ok": true, "reason": "no_meson_build", "skipped": true}'
        )

    build_dir = os.path.join(source, "build", "pcc-package", "meson-build")
    tool_dir = _native_prepare_build_tools(source)
    actions = "["
    ok = True
    skipped = False
    reason = None

    def add_action(kind: str, command: str, status: str, returncode) -> None:
        nonlocal actions, ok
        if actions != "[":
            actions += ", "
        actions += _native_build_report_action_json(kind, command, status, returncode)
        if status != "passed":
            ok = False

    try:
        if not os.path.isfile(os.path.join(build_dir, "build.ninja")):
            setup_command = _native_meson_setup_shell_command(
                source, build_dir, tool_dir
            )
            if setup_command is None:
                skipped = True
                reason = "meson_not_available"
            else:
                redirected = _native_redirected_shell_command(
                    setup_command, "meson_setup"
                )
                try:
                    subprocess.run(["mkdir", "-p", build_dir], check=True)
                    subprocess.run(["/bin/sh", "-c", redirected[0]], check=True)
                    add_action("meson_setup", setup_command, "passed", 0)
                except Exception:
                    add_action("meson_setup", setup_command, "failed", 127)
                try:
                    subprocess.run(["rm", "-f", redirected[1]], check=True)
                except Exception:
                    pass
        if ok and not skipped:
            ninja_command = (
                _native_path_prefix(tool_dir)
                + "ninja -C "
                + _native_shell_quote(build_dir)
            )
            redirected = _native_redirected_shell_command(ninja_command, "meson_build")
            try:
                subprocess.run(["/bin/sh", "-c", redirected[0]], check=True)
                add_action("meson_build", ninja_command, "passed", 0)
            except Exception:
                add_action("meson_build", ninja_command, "failed", 127)
            try:
                subprocess.run(["rm", "-f", redirected[1]], check=True)
            except Exception:
                pass
    finally:
        if tool_dir != "":
            try:
                subprocess.run(["rm", "-rf", tool_dir], check=True)
            except Exception:
                pass

    actions += "]"
    out = "{"
    out += '"actions": ' + actions
    out += ', "ok": ' + ("true" if ok else "false")
    out += ', "reason": ' + _json_str_or_null(reason)
    out += ', "skipped": ' + ("true" if skipped else "false")
    out += "}"
    return out


def _native_install_importable_payload(source, target: str, name: str) -> str:
    install_root = os.path.abspath(os.path.join(target, name))
    if source is not None and not os.path.isdir(source):
        lower = source.lower()
        is_zip = lower.endswith(".whl") or lower.endswith(".zip")
        is_tar = (
            lower.endswith(".tar.gz")
            or lower.endswith(".tar.bz2")
            or lower.endswith(".tar.xz")
            or lower.endswith(".tgz")
        )
        if is_zip or is_tar:
            extract_root = os.path.abspath(
                os.path.join(target, "." + name + ".extract")
            )
            subprocess.run(["rm", "-rf", extract_root], check=True)
            subprocess.run(["mkdir", "-p", extract_root], check=True)
            if is_zip:
                subprocess.run(
                    [
                        "env",
                        "LC_ALL=C",
                        "LANG=C",
                        "unzip",
                        "-q",
                        source,
                        "-d",
                        extract_root,
                    ],
                    check=True,
                )
            else:
                subprocess.run(
                    [
                        "env",
                        "LC_ALL=C",
                        "LANG=C",
                        "tar",
                        "-xf",
                        source,
                        "-C",
                        extract_root,
                    ],
                    check=True,
                )
            install_root = _native_install_importable_payload(
                extract_root, target, name
            )
            subprocess.run(["rm", "-rf", extract_root], check=True)
            return install_root
    if source is None or not os.path.isdir(source):
        subprocess.run(["mkdir", "-p", install_root], check=True)
        return install_root

    subprocess.run(["mkdir", "-p", target], check=True)
    copied = False

    def copy_payload(path: str) -> None:
        dest = os.path.join(target, os.path.basename(path))
        subprocess.run(["rm", "-rf", dest], check=True)
        subprocess.run(["cp", "-R", path, target], check=True)

    def overlay_payload(path: str) -> None:
        dest = os.path.join(target, os.path.basename(path))
        subprocess.run(["mkdir", "-p", dest], check=True)
        subprocess.run(["cp", "-R", path + "/.", dest], check=True)

    if os.path.isfile(os.path.join(source, "__init__.py")):
        copy_payload(source)
        copied = True
    else:
        bases = [source, os.path.join(source, "src")]
        visible_dirs = []
        try:
            top_names = sorted(os.listdir(source))
        except Exception:
            top_names = []
        j = 0
        while j < len(top_names):
            top_name = top_names[j]
            top_child = os.path.join(source, top_name)
            if (
                os.path.isdir(top_child)
                and not top_name.startswith(".")
                and top_name != "__pycache__"
            ):
                visible_dirs.append(top_child)
            j += 1
        if len(visible_dirs) == 1:
            bases.append(visible_dirs[0])
            bases.append(visible_dirs[0] + "/src")
        for base in bases:
            if not os.path.isdir(base):
                continue
            try:
                names = sorted(os.listdir(base))
            except Exception:
                names = []
            for child_name in names:
                if child_name.startswith(".") or child_name == "__pycache__":
                    continue
                child = os.path.join(base, child_name)
                if os.path.isdir(child) and os.path.isfile(
                    os.path.join(child, "__init__.py")
                ):
                    copy_payload(child)
                    copied = True
                elif (
                    os.path.isfile(child)
                    and child.endswith(".py")
                    and child_name != "setup.py"
                ):
                    dest = os.path.join(target, child_name)
                    subprocess.run(["rm", "-f", dest], check=True)
                    subprocess.run(["cp", child, dest], check=True)
                    copied = True
    build_root = os.path.join(source, "build", "pcc-package", "meson-build")
    if os.path.isdir(build_root):
        try:
            build_names = sorted(os.listdir(build_root))
        except Exception:
            build_names = []
        b = 0
        while b < len(build_names):
            build_name = build_names[b]
            build_child = os.path.join(build_root, build_name)
            if (
                os.path.isdir(build_child)
                and not build_name.startswith(".")
                and build_name != "__pycache__"
                and os.path.isfile(os.path.join(build_child, "__init__.py"))
            ):
                overlay_payload(build_child)
                copied = True
            b += 1
    if not copied:
        subprocess.run(["mkdir", "-p", install_root], check=True)
    elif not os.path.exists(install_root):
        subprocess.run(["mkdir", "-p", install_root], check=True)
    return install_root


def _native_install_manifest_json(
    spec: str,
    target_dir,
    cache_dir,
    find_links,
    abi: str,
    index_urls=None,
) -> str:
    if index_urls is None:
        index_urls = []
    resolved = _native_resolve_install_source_result(
        spec, cache_dir, find_links, abi, index_urls
    )
    source = resolved[0]
    resolved_from = resolved[1]
    name = _native_package_basename(source if source is not None else spec)
    target = (
        target_dir or os.environ.get("PCC_PACKAGE_SITE") or "/tmp/pcc-site-packages"
    )
    cache_root = (
        cache_dir or os.environ.get("PCC_PACKAGE_CACHE") or "/tmp/pcc-package-cache"
    )
    cache_record = os.path.abspath(os.path.join(cache_root, name))
    try:
        build_report = _native_ensure_meson_build_outputs_json(source)
        build_ok = _native_find_from(build_report, '"ok": false', 0) < 0
        install_root = _native_install_importable_payload(
            source, os.path.abspath(target), name
        )
        link_edges = _native_linkage_edges_for_root(install_root)
        links_libpython = len(link_edges) > 0
        cpython_abi_paths = _native_cpython_extension_abi_paths_for_root(install_root)
        uses_cpython_abi = len(cpython_abi_paths) > 0
        install_ok = (
            ((not links_libpython) or abi == "libpython")
            and (
                (not uses_cpython_abi) or abi == "libpython" or abi == "cpython-compat"
            )
            and build_ok
        )
        if install_ok:
            if source is not None and os.path.abspath(source) != cache_record:
                _native_install_importable_payload(
                    source, os.path.abspath(cache_root), name
                )
            subprocess.run(["mkdir", "-p", cache_record], check=True)
        manifest_path = os.path.join(install_root, "pcc-package.json")
        manifest = "{"
        manifest += '"abi_mode": ' + _json_str(abi)
        manifest += ', "build_report": ' + build_report
        manifest += ', "cache_record": ' + _json_str(cache_record)
        manifest += ', "cpython_extension_abi_paths": ' + _json_str_list(
            cpython_abi_paths
        )
        manifest += ', "diagnostics": ' + _native_linkage_diagnostics_json(
            link_edges, cpython_abi_paths
        )
        manifest += ', "installed_path": ' + _json_str(install_root)
        manifest += ', "index_urls": ' + _json_str_list(index_urls)
        manifest += ', "link_libpython_edges": ' + _json_str_list(link_edges)
        manifest += ', "links_libpython": ' + ("true" if links_libpython else "false")
        manifest += ', "metadata": ' + _native_package_metadata_json(name, source)
        manifest += ', "name": ' + _json_str(name)
        manifest += ', "no_libpython_runtime": ' + (
            "true"
            if (abi == "pcc-native" and not links_libpython and not uses_cpython_abi)
            else "false"
        )
        manifest += ', "ok": ' + ("true" if install_ok else "false")
        manifest += ', "pcc_native_wheel_tag": ' + _json_str(_native_pcc_wheel_tag())
        manifest += ', "schema_version": 1'
        manifest += ', "source_path": ' + _json_str_or_null(source)
        manifest += ', "resolved_from": ' + _json_str_or_null(resolved_from)
        manifest += ', "spec": ' + _json_str(spec)
        manifest += ', "uses_cpython_extension_abi": ' + (
            "true" if uses_cpython_abi else "false"
        )
        manifest += "}"
        with open(manifest_path, "w", encoding="utf-8") as fh:
            fh.write(manifest)
        if install_ok:
            with open(
                os.path.join(cache_record, "pcc-package.json"), "w", encoding="utf-8"
            ) as fh:
                fh.write(manifest)
    except Exception:
        return (
            '{"error": "pcc1 package install failed", "name": '
            + _json_str(name)
            + ', "ok": false, "spec": '
            + _json_str(spec)
            + "}"
        )
    out = "{"
    out += '"abi_mode": ' + _json_str(abi)
    out += ', "build_report": ' + build_report
    out += ', "cache_record": ' + _json_str(cache_record)
    cpython_abi_paths = _native_cpython_extension_abi_paths_for_root(install_root)
    uses_cpython_abi = len(cpython_abi_paths) > 0
    out += ', "cpython_extension_abi_paths": ' + _json_str_list(cpython_abi_paths)
    out += ', "diagnostics": ' + _native_linkage_diagnostics_json(
        link_edges, cpython_abi_paths
    )
    out += ', "installed_path": ' + _json_str(install_root)
    out += ', "index_urls": ' + _json_str_list(index_urls)
    out += ', "link_libpython_edges": ' + _json_str_list(link_edges)
    out += ', "links_libpython": ' + ("true" if links_libpython else "false")
    out += ', "manifest_path": ' + _json_str(
        os.path.join(install_root, "pcc-package.json")
    )
    out += ', "name": ' + _json_str(name)
    out += ', "no_libpython_runtime": ' + (
        "true"
        if (abi == "pcc-native" and not links_libpython and not uses_cpython_abi)
        else "false"
    )
    out += ', "ok": ' + ("true" if install_ok else "false")
    out += ', "resolved_from": ' + _json_str_or_null(resolved_from)
    out += ', "source_path": ' + _json_str_or_null(source)
    out += ', "spec": ' + _json_str(spec)
    out += ', "uses_cpython_extension_abi": ' + (
        "true" if uses_cpython_abi else "false"
    )
    out += "}"
    return out


def _run_native_package_install_from_pcc1(module_args) -> int:
    spec = ""
    target_dir = ""
    cache_dir = ""
    find_links = []
    index_urls = []
    no_index = False
    abi = "pcc-native"
    i = 0
    while i < len(module_args):
        arg = module_args[i]
        if arg == "--target" or arg == "--target-dir":
            if i + 1 >= len(module_args):
                _write_text("Error: --target requires a value", err=True)
                return 2
            target_dir = module_args[i + 1]
            i += 1
        elif arg == "--cache-dir":
            if i + 1 >= len(module_args):
                _write_text("Error: --cache-dir requires a value", err=True)
                return 2
            cache_dir = module_args[i + 1]
            i += 1
        elif arg == "--find-links" or arg == "-f":
            if i + 1 >= len(module_args):
                _write_text("Error: --find-links requires a value", err=True)
                return 2
            find_links.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--find-links="):
            find_links.append(arg.split("=", 1)[1])
        elif arg == "--no-index":
            no_index = True
        elif arg == "--index-url" or arg == "-i":
            if i + 1 >= len(module_args):
                _write_text("Error: --index-url requires a value", err=True)
                return 2
            index_urls.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--index-url="):
            index_urls.append(arg.split("=", 1)[1])
        elif arg == "--extra-index-url":
            if i + 1 >= len(module_args):
                _write_text("Error: --extra-index-url requires a value", err=True)
                return 2
            index_urls.append(module_args[i + 1])
            i += 1
        elif arg.startswith("--extra-index-url="):
            index_urls.append(arg.split("=", 1)[1])
        elif arg == "--abi":
            if i + 1 >= len(module_args):
                _write_text("Error: --abi requires a value", err=True)
                return 2
            abi = module_args[i + 1]
            i += 1
        elif arg.startswith("--abi="):
            abi = arg.split("=", 1)[1]
        elif arg != "--json" and not arg.startswith("-"):
            spec = arg
        i += 1
    if spec == "":
        _write_text('{"error": "missing package spec", "ok": false}')
        return 2
    if no_index:
        index_urls = []
    result = _native_install_manifest_json(
        spec, target_dir, cache_dir, find_links, abi, index_urls
    )
    _write_text(result)
    return 2 if _native_find_from(result, '"ok": false', 0) >= 0 else 0


def _run_host_python_module_from_pcc1(argv) -> int:
    if len(argv) < 2:
        _write_text("Error: -m requires a module name", err=True)
        return 2
    module_name = argv[1]
    module_args = _copy_seq(argv[2:])
    if module_name in ("pip", "pip3"):
        return _run_native_pip_shim_from_pcc1(module_args)
    if module_name == "pcc.package.pip_shim":
        return _run_native_pip_shim_from_pcc1(module_args)
    if module_name == "pcc.package.inspect":
        return _run_native_package_inspect_from_pcc1(module_args)
    if module_name == "pcc.package.campaign":
        return _run_native_package_campaign_from_pcc1(module_args)
    if module_name == "pcc.package.array_core":
        return _run_native_package_array_core_from_pcc1(module_args)
    if module_name == "pcc.package.toolchain":
        return _run_native_package_toolchain_from_pcc1(module_args)
    if module_name == "pcc.package.wheel_repo":
        return _run_native_package_wheel_repo_from_pcc1(module_args)
    if module_name == "pcc.package.linkage":
        return _run_native_package_linkage_from_pcc1(module_args)
    if module_name == "pcc.package.extension_abi":
        return _run_native_package_ext_abi_from_pcc1(module_args)
    if module_name == "pcc.package.build_plan":
        return _run_native_package_build_plan_from_pcc1(module_args)
    if module_name == "pcc.package.build_exec":
        return _run_native_package_build_exec_from_pcc1(module_args)
    if module_name == "pcc.package.install":
        return _run_native_package_install_from_pcc1(module_args)
    if module_name == "pcc.package":
        if len(module_args) > 0 and module_args[0] == "campaign":
            return _run_native_package_campaign_from_pcc1(module_args[1:])
        if len(module_args) > 0 and module_args[0] == "array-core":
            return _run_native_package_array_core_from_pcc1(module_args[1:])
        if len(module_args) > 0 and module_args[0] == "toolchain":
            return _run_native_package_toolchain_from_pcc1(module_args[1:])
        if len(module_args) > 0 and module_args[0] == "wheel-repo":
            return _run_native_package_wheel_repo_from_pcc1(module_args[1:])
        if len(module_args) > 0 and module_args[0] == "linkage":
            return _run_native_package_linkage_from_pcc1(module_args[1:])
        if len(module_args) > 0 and module_args[0] == "ext-abi":
            return _run_native_package_ext_abi_from_pcc1(module_args[1:])
        if len(module_args) > 0 and module_args[0] == "build-plan":
            return _run_native_package_build_plan_from_pcc1(module_args[1:])
        if len(module_args) > 0 and module_args[0] == "build-exec":
            return _run_native_package_build_exec_from_pcc1(module_args[1:])
        if len(module_args) > 0 and module_args[0] == "install":
            return _run_native_package_install_from_pcc1(module_args[1:])
        if len(module_args) > 0 and module_args[0] == "inspect":
            return _run_native_package_inspect_from_pcc1(module_args[1:])
        return _run_native_package_inspect_from_pcc1(module_args)
    host = os.environ.get("PCC_HOST_PYTHON") or "python3"
    # Keep this in the statement-only subprocess.run(check=True) shape that
    # the self-host lowering handles natively; keyword env= reintroduces
    # libpython fallback in the stage1 closure.
    cmd = [
        "env",
        "PYTHONPATH=" + os.getcwd(),
        host,
        "-m",
        module_name,
    ]
    i = 2
    while i < len(argv):
        cmd.append(argv[i])
        i += 1
    try:
        subprocess.run(cmd, check=True)
    except Exception:
        _write_text("Error: pcc1 host python module run failed", err=True)
        return 1
    return 0


def _is_host_cli_c_indicator(arg) -> bool:
    if arg is None:
        return False
    if arg.endswith(".c"):
        return True
    if arg in (
        "--sources-from-make",
        "--separate-tus",
        "--system-link",
        "--emit-obj",
        "--emit-asm",
        "--link-arg",
        "--cpp-arg",
        "--include-dir",
        "--jobs",
    ):
        return True
    return (
        arg.startswith("--sources-from-make=")
        or arg.startswith("--link-arg=")
        or arg.startswith("--cpp-arg=")
        or arg.startswith("--include-dir=")
        or arg.startswith("-I")
        or arg.startswith("-D")
        or arg.startswith("-U")
    )


def _should_delegate_to_host_cli(argv) -> bool:
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            return False
        if _is_host_cli_c_indicator(arg):
            return True
        if not arg.startswith("-"):
            if arg.endswith(".py"):
                return False
            if os.path.isdir(arg):
                return True
        i += 1
    return False


def _run_host_pcc_from_pcc1(argv) -> int:
    host = os.environ.get("PCC_HOST_PCC")
    if host:
        if host == sys.executable:
            _write_text(
                "Error: PCC_HOST_PCC points at this bootstrap binary; "
                "refusing recursive C delegation",
                err=True,
            )
            return 2
        cmd = [host]
    else:
        host_python = os.environ.get("PCC_HOST_PYTHON") or "python3"
        cmd = [host_python, "-m", "pcc.pcc"]

    i = 0
    while i < len(argv):
        cmd.append(argv[i])
        i += 1

    try:
        subprocess.run(cmd, check=True)
    except Exception:
        _write_text("Error: pcc1 host pcc delegation failed", err=True)
        return 1
    return 0


def _option_value(arg):
    idx = arg.find("=")
    if idx >= 0:
        return arg[idx + 1 :]
    return ""


class ObservabilityOptions:
    def __init__(
        self,
        diagnostic_format="text",
        profile_json=None,
        explain_fallback=False,
        phase="compile",
        entry="pcc",
    ) -> None:
        fmt = (diagnostic_format or "text").strip().lower()
        if fmt not in _VALID_DIAGNOSTIC_FORMATS:
            raise ValueError(
                "invalid diagnostic format "
                f"{diagnostic_format!r}; expected text, json, or sarif"
            )
        self.diagnostic_format = fmt
        self.profile_json = profile_json
        self.explain_fallback = bool(explain_fallback)
        self.phase = phase
        self.entry = entry


class ObservedCompileError(RuntimeError):
    def __init__(self, formatted: str) -> None:
        super().__init__(formatted)
        self.formatted = formatted


def parse_observability_cli_option(arg: str, argv: list[str], index: int):
    if arg.startswith("--diagnostic-format="):
        return ("diagnostic_format", arg.split("=", 1)[1], index + 1)
    if arg == "--diagnostic-format":
        if index + 1 >= len(argv):
            raise ValueError("--diagnostic-format requires a value")
        return ("diagnostic_format", argv[index + 1], index + 2)
    if arg.startswith("--profile-json="):
        return ("profile_json", arg.split("=", 1)[1], index + 1)
    if arg == "--profile-json":
        if index + 1 >= len(argv):
            raise ValueError("--profile-json requires a value")
        return ("profile_json", argv[index + 1], index + 2)
    if arg == "--explain-fallback":
        return ("explain_fallback", "1", index + 1)
    return None


def _observability_json_str(value) -> str:
    text = str(value)
    out = '"'
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            out += "\\\\"
        elif ch == '"':
            out += '\\"'
        elif ch == "\n":
            out += "\\n"
        elif ch == "\r":
            out += "\\r"
        elif ch == "\t":
            out += "\\t"
        else:
            out += ch
        i += 1
    out += '"'
    return out


def _json_float(value) -> str:
    try:
        return str(value)
    except Exception:
        return "0"


def _json_ns_from_ms(value) -> str:
    try:
        ms = int(value)
    except Exception:
        ms = 0
    return str(ms * 1000000)


def _json_int(value) -> str:
    try:
        return str(int(value))
    except Exception:
        return "0"


def _json_seconds_from_ms(value) -> str:
    try:
        ms = int(value)
    except Exception:
        ms = 0
    sign = ""
    if ms < 0:
        sign = "-"
        ms = -ms
    whole = ms // 1000
    frac = ms % 1000
    frac_s = str(frac)
    while len(frac_s) < 3:
        frac_s = "0" + frac_s
    return sign + str(whole) + "." + frac_s


def _format_compile_error(exc, *, options, metadata):
    if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        tb_lines = []
        tb = getattr(exc, "__traceback__", None)
        i = 0
        while tb is not None and i < 12:
            frame = getattr(tb, "tb_frame", None)
            if frame is not None:
                code = getattr(frame, "f_code", None)
                if code is not None:
                    tb_lines.append(
                        f"tb[{i}]: "
                        + str(code.co_filename)
                        + ":"
                        + str(tb.tb_lineno)
                        + " "
                        + str(code.co_name)
                    )
                else:
                    tb_lines.append("tb[" + str(i) + "]: <frame-missing>")
            tb = getattr(tb, "tb_next", None)
            i += 1
        _write_text("debug: compile exception type=" + str(type(exc).__name__))
        _write_text("debug: compile exception args=" + repr(getattr(exc, "args", None)))
        _write_text("debug: compile exception repr=" + repr(exc))
        _write_text("debug: compile traceback=" + ("; ".join(tb_lines) if tb_lines else "<none>"))
        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            _write_text("debug: cause=" + repr(cause))
        cause = getattr(exc, "__context__", None)
        if cause is not None:
            _write_text("debug: context=" + repr(cause))
        _write_text(
            "debug: metadata_keys="
            + (
                ",".join(sorted(metadata.keys()))
                if isinstance(metadata, dict)
                else "<non-dict>"
            )
        )
        try:
            _write_text("debug: compile exception message=" + str(exc))
        except Exception:
            _write_text("debug: compile exception message unavailable")
    message = str(exc)
    if not message:
        message = "compile failed"
    # Keep this bootstrap formatter deliberately static. The stage1 binary
    # runs this path inside the pcc-py runtime after arbitrary compiler
    # exceptions; probing type(exc).__name__ or repr(metadata) can itself
    # require dynamic attribute/dict formatting and mask the original error.
    note = "exception_type=Exception"
    if options.explain_fallback:
        note += (
            "; fallback_explain=libpython fallback is controlled by "
            "--python-libpython/PCC_PYTHON_LIBPYTHON"
        )
    if options.diagnostic_format == "json" or options.diagnostic_format == "sarif":
        return (
            "{\n"
            '  "schema": "pcc.diagnostics.v1",\n'
            '  "diagnostics": [\n'
            "    {\n"
            '      "code": "PCC-PY-COMPILE-001",\n'
            '      "severity": "error",\n'
            '      "phase": ' + _observability_json_str(options.phase) + ",\n"
            '      "message": ' + _observability_json_str(message) + ",\n"
            '      "notes": [' + _observability_json_str(note) + "],\n"
            '      "metadata": {}\n'
            "    }\n"
            "  ],\n"
            '  "has_errors": true\n'
            "}"
        )
    return (
        "error: PCC-PY-COMPILE-001: ["
        + options.phase
        + "] "
        + message
        + "\n  note: "
        + note
    )


def _write_profile_json(path: str, options, metadata) -> None:
    entry = options.entry
    phase = options.phase
    emit_llvm = metadata.get("emit_llvm", False) if metadata else False
    output_path = metadata.get("output_path", None) if metadata else None
    counters = metadata.get("counters", {}) if metadata else {}
    phase_totals = metadata.get("phase_totals_ms", {}) if metadata else {}
    events = metadata.get("events", []) if metadata else []
    total_ms = 0
    if phase_totals:
        total_ms = phase_totals.get(
            "compile_python_total",
            phase_totals.get("compile_python_multi_total", 0),
        )
    with open(path, "w", encoding="utf-8") as f:
        f.write("{\n")
        f.write('  "schema": "pcc.profile.v1",\n')
        f.write('  "total_ns": ' + _json_ns_from_ms(total_ms) + ",\n")
        f.write('  "total_ms": ' + _json_int(total_ms) + ",\n")
        f.write('  "total_s": ' + _json_seconds_from_ms(total_ms) + ",\n")
        f.write('  "metadata": {\n')
        f.write('    "entry": ' + _observability_json_str(entry) + ",\n")
        f.write('    "phase": ' + _observability_json_str(phase) + ",\n")
        f.write('    "time_unit": "seconds",\n')
        f.write('    "emit_llvm": ' + ("true" if emit_llvm else "false"))
        if output_path is not None:
            f.write(",\n")
            f.write('    "output_path": ' + _observability_json_str(output_path) + "\n")
        else:
            f.write("\n")
        f.write("  },\n")
        f.write('  "counters": {')
        if counters:
            f.write("\n")
            first_counter = True
            for key in counters:
                if not first_counter:
                    f.write(",\n")
                first_counter = False
                f.write(
                    "    "
                    + _observability_json_str(key)
                    + ": "
                    + _json_float(counters.get(key, 0))
                )
            f.write("\n  ")
        f.write("}")
        f.write(",\n")
        f.write('  "phase_totals_ms": {')
        if phase_totals:
            f.write("\n")
            first_total = True
            for key in phase_totals:
                if not first_total:
                    f.write(",\n")
                first_total = False
                f.write(
                    "    "
                    + _observability_json_str(key)
                    + ": "
                    + _json_int(phase_totals.get(key, 0))
                )
            f.write("\n  ")
        f.write("}")
        f.write(",\n")
        f.write('  "phase_totals_s": {')
        if phase_totals:
            f.write("\n")
            first_total = True
            for key in phase_totals:
                if not first_total:
                    f.write(",\n")
                first_total = False
                f.write(
                    "    "
                    + _observability_json_str(key)
                    + ": "
                    + _json_seconds_from_ms(phase_totals.get(key, 0))
                )
            f.write("\n  ")
        f.write("}")
        f.write(",\n")
        f.write('  "events": [')
        if events:
            f.write("\n")
            i = 0
            while i < len(events):
                event = events[i]
                if i > 0:
                    f.write(",\n")
                name = ""
                ms = 0
                detail = None
                if event:
                    name = event.get("name", "")
                    ms = event.get("ms", 0)
                    detail = event.get("detail", None)
                f.write("    {\n")
                f.write('      "name": ' + _observability_json_str(name) + ",\n")
                f.write('      "ms": ' + _json_int(ms) + ",\n")
                f.write('      "s": ' + _json_seconds_from_ms(ms))
                if detail is not None:
                    f.write(",\n")
                    f.write('      "detail": ' + _observability_json_str(detail) + "\n")
                else:
                    f.write("\n")
                f.write("    }")
                i += 1
            f.write("\n  ")
        f.write("]")
        f.write(",\n")
        f.write('  "top_events": [')
        if events:
            f.write("\n")
            i = 0
            while i < len(events):
                event = events[i]
                if i > 0:
                    f.write(",\n")
                name = ""
                ms = 0
                detail = None
                if event:
                    name = event.get("name", "")
                    ms = event.get("ms", 0)
                    detail = event.get("detail", None)
                f.write("    {\n")
                f.write('      "name": ' + _observability_json_str(name) + ",\n")
                f.write('      "ms": ' + _json_int(ms) + ",\n")
                f.write('      "s": ' + _json_seconds_from_ms(ms))
                if detail is not None:
                    f.write(",\n")
                    f.write('      "detail": ' + _observability_json_str(detail) + "\n")
                else:
                    f.write("\n")
                f.write("    }")
                i += 1
            f.write("\n  ")
        f.write("]")
        f.write("\n")
        f.write("}\n")


def _observed_compile_python(
    compile_input,
    compile_output,
    *,
    options,
    metadata,
    verbose,
    emit_llvm_only,
    libpython_mode,
    ir_scaffold_mode,
    backend,
    python_library,
):
    """Bootstrap-specialized observed compile path.

    This intentionally calls ``_compile_python`` directly instead of
    passing it as a first-class callable through ``observed_compile``.
    The self-host path does not yet have a native ``callable(*args,
    **kwargs)`` ABI, and the bootstrap CLI has a fixed compile shape.
    """
    try:
        _compile_python(
            compile_input,
            compile_output,
            verbose=verbose,
            emit_llvm_only=emit_llvm_only,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            backend=backend,
            python_library=python_library,
            profile=metadata,
        )
        return None
    except Exception as exc:
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            tb_lines = []
            tb = getattr(exc, "__traceback__", None)
            i = 0
            while tb is not None and i < 12:
                frame = getattr(tb, "tb_frame", None)
                if frame is not None:
                    code = getattr(frame, "f_code", None)
                    if code is not None:
                        tb_lines.append(
                            "tb["
                            + str(i)
                            + "]: "
                            + str(code.co_filename)
                            + ":"
                            + str(tb.tb_lineno)
                            + " "
                            + str(code.co_name)
                        )
                    else:
                        tb_lines.append("tb[" + str(i) + "]: <frame-missing>")
                tb = getattr(tb, "tb_next", None)
                i = i + 1
            _write_text(
                "debug: raw_exception_traceback="
                + ("; ".join(tb_lines) if tb_lines else "<unavailable>")
            )
            _write_text("debug: raw_exception_type=" + str(type(exc).__name__))
            _write_text("debug: raw_exception_message=" + str(getattr(exc, "message", "")))
            try:
                _write_text("debug: raw_exception_hint=" + repr(getattr(exc, "hint", None)))
            except Exception:
                pass
            _write_text("debug: raw_exception_args=" + repr(getattr(exc, "args", None)))
        try:
            formatted = _format_compile_error(
                exc,
                options=options,
                metadata=metadata or {},
            )
        except Exception:
            formatted = (
                "error: PCC-PY-COMPILE-001: ["
                + options.phase
                + "] compile failed\n"
                + "  note: diagnostic formatter failed"
            )
        try:
            _write_text(formatted, err=True)
        except Exception:
            sys.stderr.write("error: PCC-PY-COMPILE-001: compile failed\n")
        return "error"
    finally:
        if options.profile_json:
            _write_profile_json(options.profile_json, options, metadata or {})


def _parse_python_libpython(value):
    lowered = (value or "").strip().lower()
    if lowered not in ("auto", "on", "off"):
        raise ValueError(
            "invalid --python-libpython " f"{value!r}; expected auto, on, or off"
        )
    return lowered


def _parse_ir_scaffold(value):
    lowered = (value or "").strip().lower()
    if lowered not in ("auto", "on", "off"):
        raise ValueError(
            "invalid --ir-scaffold " f"{value!r}; expected off, on, or auto"
        )
    return lowered


def _should_consume_emit_llvm_value(argv, index, path):
    next_index = index + 1
    if next_index >= len(argv):
        return False
    candidate = argv[next_index]
    if candidate == "--" or candidate.startswith("-"):
        return False
    if candidate.endswith(".ll") or candidate.endswith(".bc"):
        return True
    if path is not None:
        return True
    if next_index + 1 < len(argv):
        return True
    return False


def parse_bootstrap_cli_args(argv=None):
    if argv is None:
        argv = _normalized_sys_argv()
    else:
        argv = _copy_seq(argv)

    normalized_argv = []
    i = 0
    while i < len(argv):
        normalized_argv.append((argv[i] or "") + "")
        i += 1
    argv = normalized_argv

    if len(argv) == 1:
        arg0 = argv[0]
        if arg0 == "-h" or arg0 == "--help":
            return None, 0, None

    path = None
    output_path = None
    emit_llvm = None
    verbose = False
    python_libpython = None
    python_library = False
    ir_scaffold = None
    backend = None
    diagnostic_format = "text"
    profile_json = None
    explain_fallback = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            return None, 0, None
        if arg in ("-v", "--verbose"):
            verbose = True
            i += 1
            continue
        if arg.startswith("--diagnostic-format="):
            diagnostic_format = _option_value(arg) or "text"
            i += 1
            continue
        if arg == "--diagnostic-format":
            if i + 1 >= len(argv):
                return None, 2, "--diagnostic-format requires a value"
            diagnostic_format = argv[i + 1] or "text"
            i += 2
            continue
        if arg.startswith("--profile-json="):
            profile_json = _option_value(arg)
            i += 1
            continue
        if arg == "--profile-json":
            if i + 1 >= len(argv):
                return None, 2, "--profile-json requires a value"
            profile_json = argv[i + 1]
            i += 2
            continue
        if arg == "--explain-fallback":
            explain_fallback = True
            i += 1
            continue
        if arg == "--python-library":
            python_library = True
            i += 1
            continue
        if arg.startswith("--pass="):
            i += 1
            continue
        if arg == "--pass":
            if i + 1 >= len(argv):
                return None, 2, "--pass requires a value"
            i += 2
            continue
        if arg.startswith("--disable-pass="):
            i += 1
            continue
        if arg == "--disable-pass":
            if i + 1 >= len(argv):
                return None, 2, "--disable-pass requires a value"
            i += 2
            continue
        if arg.startswith("--backend="):
            backend = _option_value(arg)
            i += 1
            continue
        if arg == "--backend":
            if i + 1 >= len(argv):
                return None, 2, "--backend requires a value"
            backend = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--python-libpython="):
            try:
                python_libpython = _parse_python_libpython(_option_value(arg))
            except ValueError as exc:
                return None, 2, str(exc)
            i += 1
            continue
        if arg == "--python-libpython":
            if i + 1 >= len(argv):
                return None, 2, "--python-libpython requires a value"
            try:
                python_libpython = _parse_python_libpython(argv[i + 1])
            except ValueError as exc:
                return None, 2, str(exc)
            i += 2
            continue
        if arg.startswith("--ir-scaffold="):
            try:
                ir_scaffold = _parse_ir_scaffold(_option_value(arg))
            except ValueError as exc:
                return None, 2, str(exc)
            i += 1
            continue
        if arg == "--ir-scaffold":
            if i + 1 >= len(argv):
                return None, 2, "--ir-scaffold requires a value"
            try:
                ir_scaffold = _parse_ir_scaffold(argv[i + 1])
            except ValueError as exc:
                return None, 2, str(exc)
            i += 2
            continue
        if arg.startswith("--emit-llvm="):
            emit_llvm = _option_value(arg) or _DEFAULT_EMIT_LL
            i += 1
            continue
        if arg == "--emit-llvm":
            if _should_consume_emit_llvm_value(argv, i, path):
                emit_llvm = argv[i + 1]
                i += 2
            else:
                emit_llvm = _DEFAULT_EMIT_LL
                i += 1
            continue
        if arg.startswith("-o") and arg != "-o":
            output_path = arg[2:]
            i += 1
            continue
        if arg == "-o":
            if i + 1 >= len(argv):
                return None, 2, "-o requires a value"
            output_path = argv[i + 1]
            i += 2
            continue
        if arg.startswith("-"):
            return None, 2, f"unknown option: {arg}"
        if path is None:
            path = arg
        else:
            return None, 2, "bootstrap entry does not support program args"
        i += 1

    import os as _os

    if _os.environ.get("PCC_DEBUG_BOOTSTRAP"):
        _write_text("[bootstrap] argv=" + repr(_normalized_sys_argv()) + "\n")

    if path is None:
        return None, 2, "missing required PATH"

    return (
        (
            path,
            output_path,
            emit_llvm,
            verbose,
            python_libpython,
            python_library,
            ir_scaffold,
            backend,
            diagnostic_format,
            profile_json,
            explain_fallback,
        ),
        0,
        None,
    )


def bootstrap_cli_main(argv=None) -> int:
    if argv is None:
        raw_argv = _normalized_sys_argv()
    else:
        raw_argv = _copy_seq(argv)
    if len(raw_argv) > 0 and raw_argv[0] == "--pcc-python-multi-codegen-worker":
        if len(raw_argv) != 2:
            _write_text(
                "Error: --pcc-python-multi-codegen-worker requires a manifest path",
                err=True,
            )
            return 2
        return _run_python_multi_codegen_worker(raw_argv[1])
    if _is_pytest_request(raw_argv):
        return _run_pytest_from_pcc1(raw_argv)
    if _is_module_request(raw_argv):
        return _run_host_python_module_from_pcc1(raw_argv)
    if _should_delegate_to_host_cli(raw_argv):
        return _run_host_pcc_from_pcc1(raw_argv)

    parsed, status, err = parse_bootstrap_cli_args(argv)
    if parsed is None:
        if err is None:
            _write_text(_HELP_TEXT, nl=False)
        else:
            _write_text("Error: " + str(err), err=True)
            _write_text(_HELP_TEXT, err=True, nl=False)
        return status

    (
        path,
        output_path,
        emit_llvm,
        verbose,
        python_libpython,
        python_library,
        ir_scaffold,
        backend,
        diagnostic_format,
        profile_json,
        explain_fallback,
    ) = parsed
    path = (path or "") + ""
    output_path = None if output_path is None else (output_path or "") + ""
    emit_llvm = None if emit_llvm is None else (emit_llvm or "") + ""
    verbose = True if verbose else False
    python_libpython = (
        None if python_libpython is None else (python_libpython or "") + ""
    )
    python_library = True if python_library else False
    ir_scaffold = None if ir_scaffold is None else (ir_scaffold or "") + ""
    backend = None if backend is None else (backend or "") + ""

    try:
        observability = ObservabilityOptions(
            diagnostic_format=diagnostic_format,
            profile_json=profile_json,
            explain_fallback=explain_fallback,
            phase="python-frontend",
            entry="cli_bootstrap",
        )
    except ValueError as exc:
        _write_text("Error: " + str(exc), err=True)
        return 2

    if not path.endswith(".py"):
        _write_text(
            "Error: bootstrap entry only supports Python inputs; "
            "use the full `pcc` CLI for C inputs",
            err=True,
        )
        return 1

    if not os.path.exists(path):
        _write_text("Error: input file not found: " + path, err=True)
        return 1

    if output_path is None and emit_llvm is None:
        _write_text(
            "Error: bootstrap entry requires -o PATH or --emit-llvm for Python inputs",
            err=True,
        )
        return 1

    if emit_llvm is not None:
        if emit_llvm == _DEFAULT_EMIT_LL:
            ll_out = output_path if output_path else path[:-3] + ".ll"
        else:
            ll_out = output_path if output_path else emit_llvm
        formatted_error = _observed_compile_python(
            path,
            ll_out,
            options=observability,
            metadata={"emit_llvm": True, "output_path": ll_out},
            verbose=verbose,
            emit_llvm_only=True,
            libpython_mode=python_libpython,
            ir_scaffold_mode=ir_scaffold,
            backend=backend,
            python_library=python_library,
        )
        if formatted_error is not None:
            return 1
    else:
        formatted_error = _observed_compile_python(
            path,
            output_path,
            options=observability,
            metadata={"emit_llvm": False, "output_path": output_path},
            verbose=verbose,
            emit_llvm_only=False,
            libpython_mode=python_libpython,
            ir_scaffold_mode=ir_scaffold,
            backend=backend,
            python_library=python_library,
        )
        if formatted_error is not None:
            return 1
    return 0


def bootstrap_cli_sys_argv_exit() -> None:
    code = bootstrap_cli_main()
    if code != 0:
        from sys import exit as _exit

        _exit(code)
