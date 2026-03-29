import re
import json
import hashlib
from ctypes.util import find_library
import llvmlite.ir as ir
import llvmlite.binding as llvm
import os
import multiprocessing
import subprocess
import shutil
import sys
import tempfile
import platform
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from ..codegen.c_codegen import LLVMCodeGenerator, postprocess_ir_text
from ..parse.c_parser import CParser
from ..preprocessor import preprocess
from ..project import TranslationUnit

from ctypes import (
    CFUNCTYPE,
    c_float,
    c_double,
    c_int64,
    c_int32,
    c_int16,
    c_int8,
    c_char_p,
    c_void_p,
    POINTER,
)


_TYPEDEF_CLEANUP = re.compile(
    r"typedef\s+(int|char|short|long|double|float|void)\s+\1\s*;"
)
# Compiler builtins that survive cc -E but pycparser doesn't know about.
# Replace all occurrences with va_list, then clean up self-referential typedefs.
_VA_TYPEDEF_NORMALIZE = re.compile(
    r"^typedef\s+(?:__builtin_va_list|__darwin_va_list|__gnuc_va_list)\s+(\w+)\s*;$",
    re.MULTILINE,
)
_SELF_TYPEDEF = re.compile(
    r"^typedef\s+(\w+)\s+\1\s*;$", re.MULTILINE
)
_SIZEOF_TYPEOF_SIZE_T = re.compile(
    r"^\s*typedef\s+__typeof\s*\(\s*sizeof\s*\(\s*int\s*\)\s*\)\s+size_t\s*;$",
    re.MULTILINE,
)
_TYPEOF_ID = re.compile(r"\b(?:__typeof__|__typeof|typeof)\s*\(\s*([A-Za-z_]\w*)\s*\)")
_TAGGED_VAR_DECL = re.compile(
    r"^\s*(struct|union|enum)\s+([A-Za-z_]\w*)\s*\{.*\}\s*([A-Za-z_]\w*)\s*;\s*$"
)
_TYPEDEF_TAG_ALIAS = re.compile(
    r"^\s*typedef\s+(struct|union|enum)\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*;\s*$"
)
_PLAIN_ALIAS_VAR_DECL = re.compile(
    r"^\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*;\s*$"
)
_SIMPLE_RANGE_DESIGNATOR = re.compile(
    r"\{\s*\[\s*0\s*\.\.\.\s*(\d+)\s*\]\s*=\s*([^,{}]+?)\s*\}"
)

# Some large MCJIT executions on the current llvmlite/LLVM build abort if
# Python GC later revisits detached engine/module wrappers. Keep detached JIT
# wrappers process-global so they are only dropped during interpreter shutdown.
_DETACHED_MCJIT_WRAPPERS = []
_COMPILE_CACHE_VERSION = "v1"


def _default_compile_cache_dir():
    override = os.environ.get("PCC_COMPILE_CACHE_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(tempfile.gettempdir(), "pcc-compile-cache")


def _compile_cache_enabled(use_compile_cache):
    if not use_compile_cache:
        return False
    flag = os.environ.get("PCC_DISABLE_COMPILE_CACHE", "")
    return flag.lower() not in {"1", "true", "yes", "on"}


def _normalize_compile_cache_dir(cache_dir):
    return os.path.abspath(
        os.path.expanduser(cache_dir or _default_compile_cache_dir())
    )


def _compiler_cache_fingerprint():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tracked_files = [
        os.path.abspath(__file__),
        os.path.join(base_dir, "codegen", "c_codegen.py"),
        os.path.join(base_dir, "parse", "c_parser.py"),
        os.path.join(base_dir, "lex", "c_lexer.py"),
        os.path.join(base_dir, "preprocessor.py"),
    ]
    hasher = hashlib.sha256()
    for tracked_path in tracked_files:
        hasher.update(tracked_path.encode("utf-8"))
        try:
            st = os.stat(tracked_path)
            hasher.update(str(st.st_mtime_ns).encode("ascii"))
            hasher.update(str(st.st_size).encode("ascii"))
        except OSError:
            hasher.update(b"missing")
    hasher.update(sys.version.encode("utf-8"))
    hasher.update(sys.platform.encode("utf-8"))
    hasher.update(platform.machine().encode("utf-8"))
    return hasher.hexdigest()


_COMPILER_CACHE_FINGERPRINT = _compiler_cache_fingerprint()


def _compile_cache_key(unit_name, preprocessed_source):
    hasher = hashlib.sha256()
    for piece in (
        _COMPILE_CACHE_VERSION,
        _COMPILER_CACHE_FINGERPRINT,
        unit_name or "",
        preprocessed_source,
    ):
        hasher.update(piece.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def _compile_cache_path(cache_dir, cache_key):
    return os.path.join(cache_dir, cache_key[:2], f"{cache_key[2:]}.json")


def _load_compiled_artifact(cache_dir, cache_key):
    path = _compile_cache_path(cache_dir, cache_key)
    try:
        with open(path, encoding="utf-8") as f:
            artifact = json.load(f)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(artifact, dict):
        return None
    if "ir_text" not in artifact or "unit_name" not in artifact:
        return None
    artifact.setdefault("external_defs", [])
    artifact.setdefault("func_return_types", {})
    artifact.setdefault("return_type", None)
    return artifact


def _store_compiled_artifact(cache_dir, cache_key, artifact):
    path = _compile_cache_path(cache_dir, cache_key)
    parent = os.path.dirname(path)
    tmp_path = None
    try:
        os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(artifact, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def get_c_type_from_ir(ir_type):
    if isinstance(ir_type, ir.VoidType):
        return None
    elif isinstance(ir_type, ir.IntType):
        if ir_type.width == 8:
            return c_int8
        elif ir_type.width == 16:
            return c_int16
        elif ir_type.width == 32:
            return c_int32
        return c_int64
    elif isinstance(ir_type, ir.DoubleType):
        return c_double
    elif isinstance(ir_type, ir.PointerType):
        point_type = get_c_type_from_ir(ir_type.pointee)
        return POINTER(point_type)
    else:
        return c_int64


def get_c_type_from_serialized_ir(ir_type_desc):
    if ir_type_desc is None:
        return None
    kind = ir_type_desc[0]
    if kind == "void":
        return None
    if kind == "int":
        width = ir_type_desc[1]
        if width == 8:
            return c_int8
        if width == 16:
            return c_int16
        if width == 32:
            return c_int32
        return c_int64
    if kind == "float":
        return c_float
    if kind == "double":
        return c_double
    if kind == "ptr":
        pointee = ir_type_desc[1]
        if pointee is None or pointee[0] == "void":
            return c_void_p
        pointee_type = get_c_type_from_serialized_ir(pointee)
        if pointee_type is None:
            return c_void_p
        return POINTER(pointee_type)
    return c_int64


def _serialize_ir_type(ir_type):
    if ir_type is None:
        return None
    if isinstance(ir_type, ir.VoidType):
        return ("void",)
    if isinstance(ir_type, ir.IntType):
        return ("int", ir_type.width)
    if isinstance(ir_type, ir.FloatType):
        return ("float",)
    if isinstance(ir_type, ir.DoubleType):
        return ("double",)
    if isinstance(ir_type, ir.PointerType):
        return ("ptr", _serialize_ir_type(ir_type.pointee))
    return ("int", 64)


def _resolve_dynamic_link_libraries(link_args):
    link_args = list(link_args or [])
    search_dirs = []
    i = 0
    while i < len(link_args):
        arg = link_args[i]
        if arg == "-L" and i + 1 < len(link_args):
            search_dirs.append(os.path.abspath(link_args[i + 1]))
            i += 2
            continue
        if arg.startswith("-L") and len(arg) > 2:
            search_dirs.append(os.path.abspath(arg[2:]))
        i += 1

    resolved = []
    seen = set()

    def add_path(path):
        if not path or path in seen:
            return
        seen.add(path)
        resolved.append(path)

    def resolve_library(name):
        aliases = [name]
        if name == "termcap":
            aliases.extend(["ncurses", "curses", "tinfo"])
        for alias in aliases:
            found = find_library(alias)
            if found:
                return found
            for directory in search_dirs:
                for ext in (".dylib", ".so", ".bundle"):
                    candidate = os.path.join(directory, f"lib{alias}{ext}")
                    if os.path.isfile(candidate):
                        return candidate
        return None

    i = 0
    while i < len(link_args):
        arg = link_args[i]
        if arg == "-l" and i + 1 < len(link_args):
            add_path(resolve_library(link_args[i + 1]))
            i += 2
            continue
        if arg.startswith("-l") and len(arg) > 2:
            add_path(resolve_library(arg[2:]))
            i += 1
            continue
        if os.path.isabs(arg) and os.path.splitext(arg)[1] in {".dylib", ".so", ".bundle"}:
            add_path(arg)
        i += 1

    return resolved


def _load_mcjit_link_libraries(link_args):
    for library in _resolve_dynamic_link_libraries(link_args):
        llvm.load_library_permanently(library)


def _normalize_simple_typeof_identifiers(codestr):
    var_types = {}
    typedef_aliases = set()
    normalized_lines = []

    for raw_line in codestr.splitlines():
        line = _TYPEOF_ID.sub(
            lambda match: var_types.get(match.group(1), match.group(0)),
            raw_line,
        )
        normalized_lines.append(line)
        stripped = line.strip()

        tagged_match = _TAGGED_VAR_DECL.match(stripped)
        if tagged_match:
            tag_kind, tag_name, var_name = tagged_match.groups()
            var_types[var_name] = f"{tag_kind} {tag_name}"
            continue

        typedef_match = _TYPEDEF_TAG_ALIAS.match(stripped)
        if typedef_match:
            _tag_kind, _tag_name, alias = typedef_match.groups()
            typedef_aliases.add(alias)
            continue

        plain_decl_match = _PLAIN_ALIAS_VAR_DECL.match(stripped)
        if plain_decl_match:
            type_name, var_name = plain_decl_match.groups()
            if type_name in typedef_aliases:
                var_types[var_name] = type_name

    return "\n".join(normalized_lines)


def _strip_gnu_asm_statements(codestr):
    def is_ident_char(ch):
        return ch.isalnum() or ch == "_"

    def token_at(index, token):
        end = index + len(token)
        if codestr[index:end] != token:
            return False
        if index > 0 and is_ident_char(codestr[index - 1]):
            return False
        if end < len(codestr) and is_ident_char(codestr[end]):
            return False
        return True

    def skip_ws(index):
        while index < len(codestr) and codestr[index].isspace():
            index += 1
        return index

    def prev_nonspace(index):
        j = index - 1
        while j >= 0 and codestr[j].isspace():
            j -= 1
        return codestr[j] if j >= 0 else None

    def consume_parens(index):
        if index >= len(codestr) or codestr[index] != "(":
            return None
        depth = 0
        i = index
        while i < len(codestr):
            ch = codestr[i]
            if ch in ("'", '"'):
                quote = ch
                i += 1
                while i < len(codestr):
                    if codestr[i] == "\\":
                        i += 2
                        continue
                    if codestr[i] == quote:
                        i += 1
                        break
                    i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return None

    out = []
    i = 0
    asm_tokens = ("__asm__", "__asm", "asm")
    asm_qualifiers = ("__volatile__", "__volatile", "volatile", "goto")
    stmt_prefix_chars = {None, ";", "{", "}", "(", ")", ":"}

    while i < len(codestr):
        matched = None
        for token in asm_tokens:
            if token_at(i, token):
                matched = token
                break
        if matched is None or prev_nonspace(i) not in stmt_prefix_chars:
            out.append(codestr[i])
            i += 1
            continue

        j = skip_ws(i + len(matched))
        while True:
            qualifier = None
            for token in asm_qualifiers:
                if token_at(j, token):
                    qualifier = token
                    break
            if qualifier is None:
                break
            j = skip_ws(j + len(qualifier))

        if j >= len(codestr) or codestr[j] != "(":
            out.append(codestr[i])
            i += 1
            continue

        end = consume_parens(j)
        if end is None:
            out.append(codestr[i])
            i += 1
            continue
        end = skip_ws(end)
        if end < len(codestr) and codestr[end] == ";":
            end += 1
        out.append(";")
        i = end

    return "".join(out)


def _expand_simple_gnu_range_designators(codestr):
    def repl(match):
        upper = int(match.group(1))
        value = match.group(2).strip()
        count = upper + 1
        if count <= 0 or count > 4096:
            return match.group(0)
        return "{ " + ", ".join([value] * count) + " }"

    return _SIMPLE_RANGE_DESIGNATOR.sub(repl, codestr)


def _normalize_preprocessed_source(codestr):
    codestr = _normalize_simple_typeof_identifiers(codestr)
    codestr = _strip_gnu_asm_statements(codestr)
    codestr = _expand_simple_gnu_range_designators(codestr)
    return codestr


def _preprocess_translation_unit_source(
    source, base_dir, use_system_cpp, include_dirs=None, cpp_args=None
):
    codestr = source
    if use_system_cpp:
        codestr = CEvaluator._system_cpp(
            codestr,
            base_dir,
            include_dirs=include_dirs,
            cpp_args=cpp_args,
        )
        codestr = _TYPEDEF_CLEANUP.sub("", codestr)
        # Normalize compiler-specific va_list typedef chains to plain pointer
        # typedefs that pycparser can ingest.
        codestr = _VA_TYPEDEF_NORMALIZE.sub(r"typedef char * \1;", codestr)
        codestr = _SELF_TYPEDEF.sub("", codestr)
        codestr = _SIZEOF_TYPEOF_SIZE_T.sub("typedef unsigned long size_t;", codestr)
    else:
        if cpp_args:
            raise ValueError("cpp_args require use_system_cpp=True")
        codestr = preprocess(codestr, base_dir=base_dir)
    return _normalize_preprocessed_source(codestr)


def _compile_preprocessed_translation_unit_artifact(unit_name, codestr):
    ast = CParser().parse(codestr)
    codegen = LLVMCodeGenerator(translation_unit_name=unit_name)
    codegen.generate_code(ast)
    return {
        "unit_name": unit_name,
        "ir_text": postprocess_ir_text(str(codegen.module)),
        "return_type": _serialize_ir_type(getattr(codegen, "return_type", None)),
        "external_defs": [list(item) for item in codegen.external_definitions()],
        "func_return_types": {
            name: _serialize_ir_type(ir_type)
            for name, ir_type in getattr(codegen, "func_return_types", {}).items()
        },
    }


def _artifact_to_compiled_unit(artifact):
    return (
        artifact["unit_name"],
        artifact["ir_text"],
        artifact.get("return_type"),
        [tuple(item) for item in artifact.get("external_defs", [])],
    )


def _entry_return_type_from_artifact(artifact, entry):
    func_return_types = artifact.get("func_return_types", {})
    serialized = func_return_types.get(entry, artifact.get("return_type"))
    return get_c_type_from_serialized_ir(serialized) or c_int32


def _compile_translation_unit_artifact_job(
    unit,
    base_dir,
    use_system_cpp,
    include_dirs,
    cpp_args,
    cache_dir,
    use_compile_cache,
):
    unit_base_dir = os.path.dirname(unit.path) if unit.path else base_dir
    codestr = _preprocess_translation_unit_source(
        unit.source,
        unit_base_dir,
        use_system_cpp,
        include_dirs=include_dirs,
        cpp_args=cpp_args,
    )

    if _compile_cache_enabled(use_compile_cache):
        normalized_cache_dir = _normalize_compile_cache_dir(cache_dir)
        cache_key = _compile_cache_key(unit.name, codestr)
        cached = _load_compiled_artifact(normalized_cache_dir, cache_key)
        if cached is not None:
            return cached
        artifact = _compile_preprocessed_translation_unit_artifact(unit.name, codestr)
        _store_compiled_artifact(normalized_cache_dir, cache_key, artifact)
        return artifact

    return _compile_preprocessed_translation_unit_artifact(unit.name, codestr)


def _raise_if_duplicate_external_definitions(compiled_units):
    seen = {}
    for unit_name, _, _, external_defs in compiled_units:
        for kind, symbol_name, display_name in external_defs:
            previous = seen.get(symbol_name)
            if previous is None:
                seen[symbol_name] = (unit_name, kind, display_name)
                continue
            prev_unit, prev_kind, prev_name = previous
            if prev_kind == kind and prev_name == display_name:
                raise ValueError(
                    f"duplicate external {kind} definition for '{display_name}' "
                    f"across translation units '{prev_unit}' and '{unit_name}'"
                )
            raise ValueError(
                f"conflicting external definitions for symbol '{symbol_name}' "
                f"across translation units '{prev_unit}' and '{unit_name}'"
            )


def _run_linked_mcjit_worker(
    compiled_units, optimize, llvmdump, args, prog_args, link_args, result_path
):
    def _write_result_and_exit(payload, exit_code):
        with open(result_path, "w") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os._exit(exit_code)

    try:
        evaluator = CEvaluator()
        target_machine = evaluator.target.create_target_machine()
        _load_mcjit_link_libraries(link_args)
        llvmmod, main_return_type = evaluator._prepare_linked_llvm_module(
            compiled_units,
            target_machine,
            optimize=optimize,
            llvmdump=llvmdump,
        )
        ee = llvm.create_mcjit_compiler(llvmmod, target_machine)
        ee.finalize_object()

        return_type = get_c_type_from_serialized_ir(main_return_type)
        if main_return_type is None:
            return_type = c_int32

        main_addr = ee.get_function_address("main")

        if prog_args:
            argv_strings = ["pcc"] + list(prog_args)
            argc = len(argv_strings)
            ArgvType = c_char_p * (argc + 1)
            argv = ArgvType(*[s.encode() for s in argv_strings], None)
            fptr = CFUNCTYPE(return_type, c_int32, POINTER(c_char_p))(main_addr)
            result = fptr(argc, argv)
        else:
            fptr = CFUNCTYPE(return_type)(main_addr)
            if args is None:
                args = []
            result = fptr(*args)

        _write_result_and_exit({"ok": True, "result": int(result)}, 0)
    except KeyboardInterrupt:
        _write_result_and_exit(
            {"ok": False, "error": "KeyboardInterrupt: compilation interrupted"},
            1,
        )
    except Exception as exc:
        _write_result_and_exit({"ok": False, "error": repr(exc)}, 1)


class CEvaluator(object):

    def __init__(self):

        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()

        self.codegen = LLVMCodeGenerator()
        self.parser = CParser()
        self.target = llvm.Target.from_default_triple()
        self.ee = None
        self._bound_modules = []
        self._bound_target_machine = None

    def _detach_execution_engine(self):
        leaked = []
        if self.ee is not None and not self.ee.closed:
            self.ee.detach()
            leaked.append(self.ee)
        self.ee = None
        for module in self._bound_modules:
            if module is not None and not module.closed:
                module.detach()
                leaked.append(module)
        self._bound_modules = []
        if (
            self._bound_target_machine is not None
            and not self._bound_target_machine.closed
        ):
            self._bound_target_machine.detach()
            leaked.append(self._bound_target_machine)
        self._bound_target_machine = None
        if leaked:
            _DETACHED_MCJIT_WRAPPERS.extend(leaked)

    def evaluate(
        self,
        codestr,
        optimize=True,
        llvmdump=False,
        args=None,
        base_dir=None,
        use_system_cpp=None,
        prog_args=None,
        entry="main",
        include_dirs=None,
        cpp_args=None,
        link_args=None,
        use_compile_cache=True,
        cache_dir=None,
    ):
        if not isinstance(codestr, str):
            raise TypeError(
                f"evaluate() expects a string of C source code, "
                f"got {type(codestr).__name__}"
            )
        if not codestr.strip():
            raise ValueError("evaluate() received empty source code")
        if use_system_cpp is None:
            use_system_cpp = self._has_system_cpp()
        snippet_base_dir = os.path.abspath(base_dir) if base_dir else os.getcwd()
        snippet_unit = TranslationUnit(
            name="__pcc_eval__.c",
            path=os.path.join(snippet_base_dir, "__pcc_eval__.c"),
            source=codestr,
        )
        artifact = _compile_translation_unit_artifact_job(
            snippet_unit,
            snippet_base_dir,
            use_system_cpp,
            include_dirs,
            cpp_args,
            cache_dir,
            use_compile_cache,
        )
        ir_text = artifact["ir_text"]

        if llvmdump:
            with open("temp.ir", "w") as f:
                f.write(ir_text)

        llvmmod = llvm.parse_assembly(ir_text)

        if optimize:
            target_machine = self.target.create_target_machine()
            pto = llvm.create_pipeline_tuning_options(speed_level=2, size_level=0)
            pb = llvm.create_pass_builder(target_machine, pto)
            pm = pb.getModulePassManager()
            pm.run(llvmmod, pb)

            if llvmdump:
                tempbcode = str(llvmmod)
                with open("temp.ooptimize.bcode", "w") as f:
                    f.write(tempbcode)

        target_machine = self.target.create_target_machine()
        _load_mcjit_link_libraries(link_args)

        self.ee = llvm.create_mcjit_compiler(llvmmod, target_machine)
        self.ee.finalize_object()

        if llvmdump:
            tempbcode = target_machine.emit_assembly(llvmmod)
            with open("temp.bcode", "w") as f:
                f.write(tempbcode)

        return_type = _entry_return_type_from_artifact(artifact, entry)

        main_addr = self.ee.get_function_address(entry)

        if prog_args:
            # Build argc/argv for main(int argc, char **argv)
            argv_strings = ["pcc"] + list(prog_args)
            argc = len(argv_strings)
            ArgvType = c_char_p * (argc + 1)
            argv = ArgvType(*[s.encode() for s in argv_strings], None)
            fptr = CFUNCTYPE(return_type, c_int32, POINTER(c_char_p))(main_addr)
            result = fptr(argc, argv)
        else:
            fptr = CFUNCTYPE(return_type)(main_addr)
            if args is None:
                args = []
            result = fptr(*args)

        return result

    def _compile_translation_units(
        self,
        units,
        base_dir,
        use_system_cpp,
        jobs,
        include_dirs=None,
        cpp_args=None,
        cache_dir=None,
        use_compile_cache=True,
    ):
        if jobs <= 1 or len(units) <= 1:
            return [
                _compile_translation_unit_artifact_job(
                    unit,
                    base_dir,
                    use_system_cpp,
                    include_dirs,
                    cpp_args,
                    cache_dir,
                    use_compile_cache,
                )
                for unit in units
            ]

        max_workers = min(jobs, len(units))
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            return list(
                executor.map(
                    _compile_translation_unit_artifact_job,
                    units,
                    repeat(base_dir),
                    repeat(use_system_cpp),
                    repeat(include_dirs),
                    repeat(cpp_args),
                    repeat(cache_dir),
                    repeat(use_compile_cache),
                )
            )

    def _prepare_llvm_module(
        self, unit_name, ir_text, target_machine, optimize=True, llvmdump=False
    ):
        safe_name = re.sub(r"\W+", "_", unit_name)
        if llvmdump:
            with open(f"temp.{safe_name}.ir", "w") as f:
                f.write(ir_text)

        llvmmod = llvm.parse_assembly(ir_text)

        if optimize:
            pto = llvm.create_pipeline_tuning_options(speed_level=2, size_level=0)
            pb = llvm.create_pass_builder(target_machine, pto)
            pm = pb.getModulePassManager()
            pm.run(llvmmod, pb)

            if llvmdump:
                with open(f"temp.{safe_name}.opt.ll", "w") as f:
                    f.write(str(llvmmod))

        return llvmmod

    def _prepare_linked_llvm_module(
        self, compiled_units, target_machine, optimize=True, llvmdump=False
    ):
        combined = None
        main_return_type = None

        for unit_name, ir_text, unit_return_type, _external_defs in compiled_units:
            llvmmod = self._prepare_llvm_module(
                unit_name,
                ir_text,
                target_machine,
                optimize=optimize,
                llvmdump=llvmdump,
            )
            if combined is None:
                combined = llvmmod
            else:
                combined.link_in(llvmmod)
            if unit_return_type is not None:
                main_return_type = unit_return_type

        if combined is None:
            raise ValueError("No translation units provided")

        return combined, main_return_type

    def compile_translation_units(
        self,
        units,
        base_dir=None,
        use_system_cpp=None,
        jobs=1,
        include_dirs=None,
        cpp_args=None,
        use_compile_cache=True,
        cache_dir=None,
    ):
        if use_system_cpp is None:
            use_system_cpp = self._has_system_cpp()

        artifacts = self._compile_translation_units(
            units,
            base_dir,
            use_system_cpp,
            jobs,
            include_dirs=include_dirs,
            cpp_args=cpp_args,
            cache_dir=cache_dir,
            use_compile_cache=use_compile_cache,
        )
        compiled_units = [_artifact_to_compiled_unit(artifact) for artifact in artifacts]
        _raise_if_duplicate_external_definitions(compiled_units)
        return compiled_units

    def evaluate_compiled_translation_units(
        self,
        compiled_units,
        optimize=True,
        llvmdump=False,
        args=None,
        prog_args=None,
        link_args=None,
    ):
        if sys.platform == "darwin":
            return self._evaluate_compiled_translation_units_via_subprocess(
                compiled_units,
                optimize=optimize,
                llvmdump=llvmdump,
                args=args,
                prog_args=prog_args,
                link_args=link_args,
            )

        return self._evaluate_compiled_translation_units_in_process(
            compiled_units,
            optimize=optimize,
            llvmdump=llvmdump,
            args=args,
            prog_args=prog_args,
            link_args=link_args,
        )

    def evaluate_translation_units(
        self,
        units,
        optimize=True,
        llvmdump=False,
        args=None,
        base_dir=None,
        use_system_cpp=None,
        prog_args=None,
        jobs=1,
        include_dirs=None,
        cpp_args=None,
        link_args=None,
        use_compile_cache=True,
        cache_dir=None,
    ):
        if not units:
            raise ValueError("evaluate_translation_units() received no translation units")
        compiled_units = self.compile_translation_units(
            units,
            base_dir=base_dir,
            use_system_cpp=use_system_cpp,
            jobs=jobs,
            include_dirs=include_dirs,
            cpp_args=cpp_args,
            use_compile_cache=use_compile_cache,
            cache_dir=cache_dir,
        )
        return self.evaluate_compiled_translation_units(
            compiled_units,
            optimize=optimize,
            llvmdump=llvmdump,
            args=args,
            prog_args=prog_args,
            link_args=link_args,
        )

    def _evaluate_compiled_translation_units_via_subprocess(
        self,
        compiled_units,
        optimize=True,
        llvmdump=False,
        args=None,
        prog_args=None,
        link_args=None,
    ):
        _raise_if_duplicate_external_definitions(compiled_units)

        fd, result_path = tempfile.mkstemp(prefix="pcc_mcjit_result_", suffix=".json")
        os.close(fd)
        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(
            target=_run_linked_mcjit_worker,
            args=(
                compiled_units,
                optimize,
                llvmdump,
                args,
                prog_args,
                link_args,
                result_path,
            ),
        )
        proc.start()
        proc.join()

        try:
            payload = None
            if os.path.exists(result_path) and os.path.getsize(result_path) > 0:
                with open(result_path) as f:
                    payload = json.load(f)

            if proc.exitcode != 0:
                if payload and not payload.get("ok", False):
                    raise RuntimeError(
                        f"MCJIT subprocess failed: {payload.get('error', 'unknown error')}"
                    )
                if proc.exitcode > 0 and payload is None:
                    return proc.exitcode
                raise RuntimeError(f"MCJIT subprocess exited with code {proc.exitcode}")

            if payload is None:
                return 0
            if not payload.get("ok", False):
                raise RuntimeError(
                    f"MCJIT subprocess failed: {payload.get('error', 'unknown error')}"
                )
            return payload["result"]
        finally:
            if os.path.exists(result_path):
                os.unlink(result_path)

    def _evaluate_compiled_translation_units_in_process(
        self,
        compiled_units,
        optimize=True,
        llvmdump=False,
        args=None,
        prog_args=None,
        link_args=None,
    ):
        _raise_if_duplicate_external_definitions(compiled_units)

        target_machine = self.target.create_target_machine()
        _load_mcjit_link_libraries(link_args)
        llvmmod, main_return_type = self._prepare_linked_llvm_module(
            compiled_units,
            target_machine,
            optimize=optimize,
            llvmdump=llvmdump,
        )
        self.ee = llvm.create_mcjit_compiler(llvmmod, target_machine)
        self._bound_modules = [llvmmod]
        self._bound_target_machine = target_machine

        try:
            self.ee.finalize_object()

            return_type = get_c_type_from_serialized_ir(main_return_type)
            if main_return_type is None:
                return_type = c_int32

            main_addr = self.ee.get_function_address("main")

            if prog_args:
                argv_strings = ["pcc"] + list(prog_args)
                argc = len(argv_strings)
                ArgvType = c_char_p * (argc + 1)
                argv = ArgvType(*[s.encode() for s in argv_strings], None)
                fptr = CFUNCTYPE(return_type, c_int32, POINTER(c_char_p))(main_addr)
                result = fptr(argc, argv)
            else:
                fptr = CFUNCTYPE(return_type)(main_addr)
                if args is None:
                    args = []
                result = fptr(*args)

            return result
        finally:
            # MCJIT disposal is unstable for some large multi-TU programs on
            # this llvmlite/LLVM combination. Detach wrappers after execution
            # so Python GC does not call back into engine teardown.
            self._detach_execution_engine()

    def run_compiled_translation_units_with_system_cc(
        self,
        compiled_units,
        optimize=True,
        llvmdump=False,
        base_dir=None,
        prog_args=None,
        link_args=None,
        timeout=120,
        capture_output=True,
        text=True,
    ):
        _raise_if_duplicate_external_definitions(compiled_units)

        cc = self._system_cc()
        target_machine = self.target.create_target_machine()
        tmpdir = tempfile.mkdtemp(prefix="pcc_system_link_")
        obj_paths = []
        link_args = list(link_args or [])

        for unit_name, ir_text, _unit_return_type, _external_defs in compiled_units:
            llvmmod = self._prepare_llvm_module(
                unit_name,
                ir_text,
                target_machine,
                optimize=optimize,
                llvmdump=llvmdump,
            )
            obj_path = os.path.join(
                tmpdir, f"{re.sub(r'\\W+', '_', unit_name) or 'unit'}.o"
            )
            os.makedirs(os.path.dirname(obj_path), exist_ok=True)
            with open(obj_path, "wb") as f:
                f.write(target_machine.emit_object(llvmmod))
            obj_paths.append(obj_path)

        bin_path = os.path.join(tmpdir, "a.out")
        link_cmd = [cc] + obj_paths + ["-o", bin_path] + link_args
        link_run = subprocess.run(
            link_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if link_run.returncode != 0:
            detail = (link_run.stderr or link_run.stdout or "unknown linker error")[
                :400
            ]
            raise RuntimeError(f"system cc link failed: {detail}")

        run_cmd = [bin_path] + [str(arg) for arg in (prog_args or [])]
        return subprocess.run(
            run_cmd,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            cwd=base_dir or os.getcwd(),
        )

    def run_translation_units_with_system_cc(
        self,
        units,
        optimize=True,
        llvmdump=False,
        base_dir=None,
        use_system_cpp=None,
        prog_args=None,
        jobs=1,
        link_args=None,
        timeout=120,
        capture_output=True,
        text=True,
        include_dirs=None,
        cpp_args=None,
        use_compile_cache=True,
        cache_dir=None,
    ):
        compiled_units = self.compile_translation_units(
            units,
            base_dir,
            use_system_cpp,
            jobs,
            include_dirs=include_dirs,
            cpp_args=cpp_args,
            use_compile_cache=use_compile_cache,
            cache_dir=cache_dir,
        )
        return self.run_compiled_translation_units_with_system_cc(
            compiled_units,
            optimize=optimize,
            llvmdump=llvmdump,
            base_dir=base_dir,
            prog_args=prog_args,
            link_args=link_args,
            timeout=timeout,
            capture_output=capture_output,
            text=text,
        )

    @staticmethod
    def _has_system_cpp():
        return shutil.which("cc") is not None or shutil.which("gcc") is not None

    @staticmethod
    def _system_cc():
        cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
        if not cc:
            raise RuntimeError("No system C compiler found for linking")
        return cc

    @staticmethod
    def _system_cpp(source, base_dir=None, include_dirs=None, cpp_args=None):
        """Use system C preprocessor (cc -E) for fast preprocessing.

        Uses -nostdinc + fake libc headers so output is pycparser-compatible.
        """
        cc = CEvaluator._system_cc()
        cpp_args = list(cpp_args or [])

        # Find fake libc headers (shipped with pcc)
        # __file__ = pcc/evaluater/c_evaluator.py → project root is 2 levels up
        pcc_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        fake_libc = os.path.join(pcc_root, "utils", "fake_libc_include")
        base_dir = os.path.abspath(base_dir) if base_dir else os.getcwd()
        user_include_dirs = []
        seen = set()
        for include_dir in [base_dir] + list(include_dirs or []):
            if not include_dir:
                continue
            include_dir = os.path.abspath(include_dir)
            if include_dir in seen:
                continue
            seen.add(include_dir)
            user_include_dirs.append(include_dir)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".c",
            delete=False,
            encoding="utf-8",
            errors="surrogateescape",
        ) as f:
            f.write(source)
            tmp_path = f.name
        try:
            platform_defs = []
            prefer_system_headers = any(
                marker in include_dir
                for include_dir in user_include_dirs
                for marker in ("zstd-", "openssl-", "postgresql-")
            )
            postgres_header_compat = any(
                "postgresql-" in include_dir for include_dir in user_include_dirs
            )
            system_header_compat_defs = []
            def _postprocess_preprocessed_text(text):
                if postgres_header_compat:
                    text = re.sub(r"\b__restrict__\b", "", text)
                    text = re.sub(r"\b__restrict\b", "", text)
                    text = text.replace("({ do { ; } while(0); 1; })", "1")
                    # PostgreSQL frontend headers typedef int128 via a bare
                    # __int128 spelling that pycparser cannot parse. libpq's
                    # frontend sources do not rely on 128-bit semantics here,
                    # so narrow it to 64-bit during preprocessing.
                    text = re.sub(
                        r"\bunsigned\s+__int128\b", "unsigned long long", text
                    )
                    text = re.sub(r"\b__int128\b", "long long", text)
                return text
            if sys.platform == "darwin":
                # fake libc headers do not provide the stdio macro remaps that
                # macOS uses for FILE* globals.
                platform_defs.extend(
                    [
                        "-D__PCC_HOST_DARWIN__=1",
                        "-Dstdin=__stdinp",
                        "-Dstdout=__stdoutp",
                        "-Dstderr=__stderrp",
                        "-U__BLOCKS__",
                    ]
                )
                # pcc does not support ARM vector intrinsics; prefer scalar
                # fallbacks instead of pulling in headers like arm_neon.h.
                if platform.machine() in {"arm64", "aarch64"}:
                    platform_defs.extend(
                        [
                            "-U__ARM_NEON",
                            "-U__ARM_NEON__",
                            "-U__ARM_FEATURE_CRC32",
                        ]
                    )
            compat_defs = [
                # Standard limits (fake headers don't have these)
                "-DLLONG_MAX=9223372036854775807LL",
                "-DLLONG_MIN=(-9223372036854775807LL-1)",
                "-DULLONG_MAX=18446744073709551615ULL",
                "-DLONG_MAX=9223372036854775807L",
                "-DINT_MAX=2147483647",
                "-DINT_MIN=(-2147483647-1)",
                "-DLONG_MIN=(-9223372036854775807L-1)",
                "-DUINT_MAX=4294967295U",
                "-DCHAR_BIT=8",
                "-DSHRT_MAX=32767",
                "-DUSHRT_MAX=65535",
                "-DCHAR_MAX=127",
                "-DUCHAR_MAX=255",
                "-DSIG_DFL=0",
                "-DSIG_IGN=1",
                "-DSIGINT=2",
                "-DCLOCKS_PER_SEC=1000000",
                "-DLC_ALL=0",
                "-DLC_COLLATE=1",
                "-DLC_CTYPE=2",
                "-DLC_MONETARY=3",
                "-DLC_NUMERIC=4",
                "-DLC_TIME=5",
                "-Doffsetof(t,m)=((long)&((t*)0)->m)",
                "-D__builtin_offsetof(t,m)=((long)&((t*)0)->m)",
                "-DDBL_MANT_DIG=53",
                "-DFLT_MANT_DIG=24",
                "-DDBL_MAX_EXP=1024",
                "-DFLT_MAX_EXP=128",
                "-DDBL_MAX=1.7976931348623158e+308",
                "-DHUGE_VAL=1e309",
                "-DHUGE_VALF=1e39f",
                "-DDBL_MAX_10_EXP=308",
                "-DFLT_MAX_10_EXP=38",
                "-DDBL_MIN_EXP=-1021",
                "-DDBL_EPSILON=2.2204460492503131e-16",
                "-DLDBL_MANT_DIG=53",
                "-DLDBL_MAX_EXP=1024",
                "-DLDBL_MAX_10_EXP=308",
                "-DLDBL_MIN_EXP=-1021",
                "-DLDBL_EPSILON=2.2204460492503131e-16L",
                "-D__ORDER_LITTLE_ENDIAN__=1234",
                "-D__ORDER_BIG_ENDIAN__=4321",
                "-D__BYTE_ORDER__=__ORDER_LITTLE_ENDIAN__",
                "-D__WCHAR_WIDTH__=32",
                "-D_IONBF=2",
                "-D_IOLBF=1",
                "-D_IOFBF=0",
                # GCC/Clang extensions that pycparser doesn't understand.
                "-D__attribute(x)=",
                "-D__attribute__(x)=",
                "-D__extension__=",
                "-D__FUNCTION__=__func__",
                "-D__inline=inline",
                "-D__inline__=inline",
                "-D__restrict=restrict",
                "-D__restrict__=restrict",
                "-D_Atomic(x)=x",
                "-Dasm(...)=",
                "-D__asm__(x)=",
                "-D__asm(x)=",
                "-D_Nonnull=",
                "-D_Nullable=",
                "-D_Null_unspecified=",
                "-D__nonnull=",
                "-D__nullable=",
                "-D__null_unspecified=",
                "-D__int128_t=long long",
                "-D__uint128_t=unsigned long long",
                "-D__builtin_memcpy=memcpy",
                "-D__builtin_memmove=memmove",
                "-D__builtin_memcmp=memcmp",
                "-D__builtin_memchr=memchr",
                "-D__builtin_memset=memset",
                "-D__builtin_malloc=malloc",
                "-D__builtin_free=free",
                "-D__builtin_abs=abs",
                "-D__builtin_bzero(p,n)=memset(p,0,n)",
                "-D__builtin___memcpy_chk(a,b,c,d)=memcpy(a,b,c)",
                "-D__builtin___memmove_chk(a,b,c,d)=memmove(a,b,c)",
                "-D__builtin___memset_chk(a,b,c,d)=memset(a,b,c)",
                "-D__builtin___strcpy_chk(a,b,c)=strcpy(a,b)",
                "-D__builtin___strcat_chk(a,b,c)=strcat(a,b)",
                "-D__builtin___strncpy_chk(a,b,c,d)=strncpy(a,b,c)",
                "-D__builtin___strncat_chk(a,b,c,d)=strncat(a,b,c)",
                "-D__builtin___strlcpy_chk(a,b,c,d)=strlcpy(a,b,c)",
                "-D__builtin___strlcat_chk(a,b,c,d)=strlcat(a,b,c)",
                "-D__builtin___printf_chk(flag,fmt,...)=printf(fmt,##__VA_ARGS__)",
                "-D__builtin___fprintf_chk(stream,flag,fmt,...)=fprintf(stream,fmt,##__VA_ARGS__)",
                "-D__builtin___sprintf_chk(buf,flag,obj,fmt,...)=sprintf(buf,fmt,##__VA_ARGS__)",
                "-D__builtin___snprintf_chk(buf,size,flag,obj,fmt,...)=snprintf(buf,size,fmt,##__VA_ARGS__)",
                "-D__builtin___vsprintf_chk(buf,flag,obj,fmt,ap)=vsprintf(buf,fmt,ap)",
                "-D__builtin___vsnprintf_chk(buf,size,flag,obj,fmt,ap)=vsnprintf(buf,size,fmt,ap)",
                "-D__builtin_printf=printf",
                "-D__builtin_fprintf=fprintf",
                "-D__builtin_abort()=abort()",
                "-D__builtin_return_address(level)=((void*)0)",
                "-D__builtin_choose_expr(cond,a,b)=((cond)?(a):(b))",
                "-D__builtin_constant_p(x)=0",
                "-D__builtin_strlen(x)=strlen(x)",
                "-D__builtin_strcmp(a,b)=strcmp(a,b)",
                "-D__sync_add_and_fetch(ptr,val)=(*(ptr)+=(val))",
                "-D__builtin_object_size(ptr,type)=((size_t)-1)",
                "-D__builtin_fabsf=fabsf",
                "-D__builtin_fabs=fabs",
                "-D__builtin_fabsl=fabsl",
                "-D__builtin_inff()=(1e39f)",
                "-D__builtin_inf()=(1e309)",
                "-D__builtin_infl()=((long double)1e309)",
                "-D__builtin_huge_val()=(1e309)",
                "-D__builtin_huge_valf()=(1e39f)",
                "-D__builtin_huge_vall()=((long double)1e309)",
                "-D_Alignas(x)=",
                "-Dalignas(x)=",
                "-D_Static_assert(x,y)=",
                "-Dstatic_assert(x,y)=",
            ]
            # Host headers on macOS emit __builtin_va_arg(ap, type), while
            # pcc already supports the fake-libc-expanded shape that casts a
            # pointer returned from __builtin_va_arg(&(ap), sizeof(type)).
            # Apply this only on the host-header preprocessing path below.
            system_header_compat_defs.append(
                "-D__builtin_va_arg(ap,t)=(*((t*)__builtin_va_arg(&(ap),sizeof(t))))"
            )
            if any("openssl-" in include_dir for include_dir in user_include_dirs):
                # OpenSSL's bn_local.h enables inline-asm/GNU statement-expression
                # helpers when the host compiler supports them. pcc does not,
                # so force the standard C fallback path during preprocessing.
                system_header_compat_defs.append("-DOPENSSL_NO_INLINE_ASM")
                # Prefer OpenSSL's non-C11 atomics fallbacks. This avoids
                # host stdatomic expansions that pcc can't parse yet and keeps
                # TSAN_QUALIFIER on its volatile/plain-C paths.
                system_header_compat_defs.extend(
                    [
                        "-DOPENSSL_DEV_NO_ATOMICS",
                        "-D__STDC_NO_ATOMICS__=1",
                        "-DATOMIC_POINTER_LOCK_FREE=0",
                        "-D__GCC_ATOMIC_POINTER_LOCK_FREE=0",
                    ]
                )
            if not prefer_system_headers:
                cmd = [
                    cc,
                    "-E",
                    "-P",
                    "-nostdinc",  # skip real system headers
                    "-isystem",
                    fake_libc,  # fake libc as system headers
                    *[
                        opt
                        for include_dir in user_include_dirs
                        for opt in ("-I", include_dir)
                    ],
                    *compat_defs,
                    *platform_defs,
                    *cpp_args,
                    tmp_path,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    return _postprocess_preprocessed_text(result.stdout)
            # Fallback or preferred path: use the host headers.
            cmd = [
                cc,
                "-E",
                "-P",
                *[
                    opt
                    for include_dir in user_include_dirs
                    for opt in ("-I", include_dir)
                ],
                *compat_defs,
                *system_header_compat_defs,
                *platform_defs,
                *cpp_args,
                tmp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return _postprocess_preprocessed_text(result.stdout)
        finally:
            os.unlink(tmp_path)
