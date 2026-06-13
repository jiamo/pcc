"""Python frontend compilation pipeline.

Orchestrates the full pipeline for a single ``.py`` file:

    source(.py)
      -> pcc.py_frontend.parser.parse()        -> Module AST
      -> pcc.py_frontend.type_infer.infer_module() -> typed Module
      -> pcc.py_frontend.codegen.layer1.L1CodeGen().generate() -> LLVM IR text
      -> write .ll to a temp file
      -> clang .ll + pcc/py_runtime/libpy_runtime.a -> native exe

This is the Phase 1 MVP dispatcher. See
``docs/plans/python-frontend-interfaces.md`` for the frozen v0.1
interface contract and ``docs/plans/python-frontend-plan.md`` for the
Phase 1 scope.
"""

from __future__ import annotations

import gc
import os
import importlib
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional

from ..backend.self_backend_aarch64_darwin import (
    emit_aarch64_darwin_asm as _emit_aarch64_darwin_asm_native,
)
from ..backend.self_backend_parse import (
    parse_self_backend_target_triple as _parse_self_backend_target_triple_native,
)
from ..backend.self_backend_target_match import (
    is_aarch64_darwin_triple as _is_aarch64_darwin_triple_native,
)
from .export_meta import encode_type
from .codegen.host_contract import (
    L1_CODEGEN_HOST_ATTRS,
    L1_CODEGEN_HOST_CLASS,
    L1_CODEGEN_HOST_METHODS,
    PROBE_POLICY_CONTEXTUAL_MIXIN,
    PROBE_POLICY_STANDALONE,
    contextual_host_for_module,
    contextual_per_module_modules,
    l1_codegen_lowering_host_contract,
    per_module_probe_policy,
)
from .codegen.layer1_support import (
    _dataclass_field_names as _self_host_ast_field_names,
    _default_native_module_exports,
)


def _runtime_dir_has_runtime_files(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    include_h = os.path.isfile(os.path.join(path, "include", "py_runtime.h"))
    makefile = os.path.isfile(os.path.join(path, "Makefile"))
    maybe_lib = os.path.isfile(os.path.join(path, "libpy_runtime.a"))
    return include_h or makefile or maybe_lib


def _load_pcc_gpu_kernel_module():
    return importlib.import_module("pcc.gpu_kernel")


def _load_pcc_gpu_metal_module():
    return importlib.import_module("pcc.gpu_metal")


def _bootstrap_append_unique_path(paths: list[str], path: Optional[str]) -> None:
    if path is None:
        return
    path = str(path or "").strip()
    if not path:
        return
    path = os.path.abspath(path)
    for existing in paths:
        if existing == path:
            return
    paths.append(path)


def _bootstrap_append_pcc_dir_candidate(paths: list[str], path: Optional[str]) -> None:
    if path is None:
        return
    path = str(path or "").strip()
    if not path:
        return
    path = os.path.abspath(path)
    _bootstrap_append_unique_path(paths, path)
    _bootstrap_append_unique_path(paths, os.path.join(path, "pcc"))
    name = os.path.basename(path)
    if name in ("py_frontend", "py_runtime", "py_stdlib", "stdlib", "backend"):
        _bootstrap_append_unique_path(paths, os.path.dirname(path))


def _bootstrap_append_pcc_dir_ancestors(
    paths: list[str],
    path: Optional[str],
) -> None:
    if path is None:
        return
    path = str(path or "").strip()
    if not path:
        return
    cur = os.path.abspath(path)
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)
    while cur:
        _bootstrap_append_pcc_dir_candidate(paths, cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent


def _pcc_dir_has_source_files(path: str) -> bool:
    return (
        os.path.isfile(os.path.join(path, "__init__.py"))
        and os.path.isfile(os.path.join(path, "backend", "self_backend_dispatch.py"))
        and (
            os.path.isfile(os.path.join(path, "py_stdlib", "__init__.py"))
            or _runtime_dir_has_runtime_files(os.path.join(path, "py_runtime"))
        )
    )


def _resolve_pcc_dir_from_environment() -> str:
    raw_pipeline_dir = str(os.path.dirname(os.path.abspath(__file__)))
    raw_pcc_dir = str(os.path.dirname(raw_pipeline_dir))
    candidates: list[str] = []
    _bootstrap_append_pcc_dir_candidate(candidates, os.environ.get("PCC_SOURCE_ROOT"))
    _bootstrap_append_pcc_dir_candidate(candidates, os.environ.get("PCC_REPO_ROOT"))
    _bootstrap_append_pcc_dir_candidate(
        candidates, os.environ.get("PCC_PY_STDLIB_ROOT")
    )
    _bootstrap_append_pcc_dir_candidate(candidates, raw_pcc_dir)
    _bootstrap_append_pcc_dir_candidate(candidates, raw_pipeline_dir)
    try:
        if len(sys.argv) > 0:
            _bootstrap_append_pcc_dir_ancestors(candidates, sys.argv[0])
    except Exception:
        pass
    try:
        _bootstrap_append_pcc_dir_ancestors(candidates, sys.executable)
    except Exception:
        pass
    for candidate in candidates:
        if _pcc_dir_has_source_files(candidate):
            return candidate
    return raw_pcc_dir


# Resolve pcc/py_runtime/ at import time. In CPython source mode this
# file lives under ``.../pcc/py_frontend/``. In compiled bootstrap mode
# ``__file__`` is synthetic and can resolve to the user's current working
# directory, so derive the package root from stable stage-binary ancestors.
_PCC_DIR = str(_resolve_pcc_dir_from_environment())
_PIPELINE_DIR_CANDIDATE = str(os.path.join(_PCC_DIR, "py_frontend"))
_PIPELINE_DIR = (
    _PIPELINE_DIR_CANDIDATE
    if os.path.isdir(_PIPELINE_DIR_CANDIDATE)
    else str(os.path.dirname(os.path.abspath(__file__)))
)


_PY_RUNTIME_DIR_CANDIDATE_1 = str(os.path.join(_PCC_DIR, "pcc", "py_runtime"))
_PY_RUNTIME_DIR_CANDIDATE_2 = str(os.path.join(_PCC_DIR, "py_runtime"))
_PY_RUNTIME_DIR_CANDIDATE_3 = str(os.path.join(_PIPELINE_DIR, "py_runtime"))
_PY_RUNTIME_DIR_CANDIDATE_4 = str(
    os.path.join(
        _PIPELINE_DIR,
        "pcc",
        "py_runtime",
    )
)
_PY_RUNTIME_DIR_CANDIDATE_5 = str(
    os.path.join(
        os.getcwd(),
        "pcc",
        "py_runtime",
    )
)
_PY_RUNTIME_DIR_FALLBACK = str(os.path.join(_PCC_DIR, "py_runtime"))
_PY_RUNTIME_DIR = str(
    _PY_RUNTIME_DIR_CANDIDATE_1
    if _runtime_dir_has_runtime_files(_PY_RUNTIME_DIR_CANDIDATE_1)
    else (
        _PY_RUNTIME_DIR_CANDIDATE_2
        if _runtime_dir_has_runtime_files(_PY_RUNTIME_DIR_CANDIDATE_2)
        else (
            _PY_RUNTIME_DIR_CANDIDATE_3
            if _runtime_dir_has_runtime_files(_PY_RUNTIME_DIR_CANDIDATE_3)
            else (
                _PY_RUNTIME_DIR_CANDIDATE_4
                if _runtime_dir_has_runtime_files(_PY_RUNTIME_DIR_CANDIDATE_4)
                else (
                    _PY_RUNTIME_DIR_CANDIDATE_5
                    if _runtime_dir_has_runtime_files(_PY_RUNTIME_DIR_CANDIDATE_5)
                    else _PY_RUNTIME_DIR_FALLBACK
                )
            )
        )
    )
)

if os.environ.get("PCC_DEBUG_RUNTIME", "").strip():
    try:
        with open("/tmp/pcc_runtime_debug_probe.txt", "a", encoding="utf-8") as _f:
            _f.write("[probe] _PIPELINE_DIR=" + _PIPELINE_DIR + "\n")
            _f.write("[probe] _PCC_DIR=" + _PCC_DIR + "\n")
            _f.write(
                "[probe] candidates="
                + ",".join(
                    [
                        _PY_RUNTIME_DIR_CANDIDATE_1,
                        _PY_RUNTIME_DIR_CANDIDATE_2,
                        _PY_RUNTIME_DIR_CANDIDATE_3,
                        _PY_RUNTIME_DIR_CANDIDATE_4,
                    ]
                )
                + "\n"
            )
    except Exception:
        pass
_PY_RUNTIME_ARCHIVE = str(os.path.join(_PY_RUNTIME_DIR, "libpy_runtime.a"))
_PY_RUNTIME_ARCHIVE_LIBPYTHON = str(
    os.path.join(
        _PY_RUNTIME_DIR,
        "libpy_runtime_libpython.a",
    )
)
_PY_RUNTIME_ARCHIVE_PCC = str(
    os.path.join(
        _PY_RUNTIME_DIR,
        "libpy_runtime_pcc.a",
    )
)
_PY_RUNTIME_ARCHIVE_PCC_PY = str(
    os.path.join(
        _PY_RUNTIME_DIR,
        "libpy_runtime_pcc_py.a",
    )
)
_PY_RUNTIME_ARCHIVE_PCC_PY_LIBPYTHON = str(
    os.path.join(
        _PY_RUNTIME_DIR,
        "libpy_runtime_pcc_py_libpython.a",
    )
)
_PY_LIBPYTHON_MODE_ENV = "PCC_PYTHON_LIBPYTHON"
_IR_SCAFFOLD_MODE_ENV = "PCC_IR_SCAFFOLD"
_PYTHON_IR_PASSES_ENV = "PCC_PYTHON_IR_PASSES"
_PYTHON_IR_PASS_TRANSPORT_ENV = "PCC_PYTHON_IR_PASS_TRANSPORT"
_PYTHON_IR_PASS_JOBS_ENV = "PCC_PYTHON_IR_PASS_JOBS"
_PYTHON_IR_PASS_TIMEOUT_ENV = "PCC_PYTHON_IR_PASS_TIMEOUT"
_PYTHON_IR_PASS_STRICT_NO_LIBPYTHON_ENV = "PCC_PYTHON_IR_PASS_STRICT_NO_LIBPYTHON"
_PYTHON_IR_PASS_SPLIT_LARGE_MODULES_ENV = "PCC_PYTHON_IR_PASS_SPLIT_LARGE_MODULES"
_PYTHON_IR_PASS_SPLIT_THRESHOLD_BYTES_ENV = "PCC_PYTHON_IR_PASS_SPLIT_THRESHOLD_BYTES"
_PYTHON_IR_PASS_SPLIT_SHARD_BYTES_ENV = "PCC_PYTHON_IR_PASS_SPLIT_SHARD_BYTES"
_PYTHON_IR_PASS_SKIP_MODULE_PREFIXES_ENV = "PCC_PYTHON_IR_PASS_SKIP_MODULE_PREFIXES"
_PY_FRONTEND_JOBS_ENV = "PCC_PY_FRONTEND_JOBS"
_PY_FRONTEND_WORKER_TIMING_ENV = "PCC_PY_FRONTEND_WORKER_TIMING"
_PY_FRONTEND_WORKER_ARG = "--pcc-python-multi-codegen-worker"
_SELF_BACKEND_EMIT_WORKER_ARG = "--pcc-self-backend-emit-worker"
_SELF_BACKEND_SPLIT_WORKER_ARG = "--pcc-self-backend-split-worker"
_PY_FRONTEND_WORKER_MANIFEST_V1 = "pcc.py_frontend.codegen_worker.v1"
_PY_FRONTEND_WORKER_MANIFEST_V2 = "pcc.py_frontend.codegen_worker.v2"
_PY_FRONTEND_WORKER_MANIFEST_V3 = "pcc.py_frontend.codegen_worker.v3"
_PY_FRONTEND_WORKER_MANIFEST_V4 = "pcc.py_frontend.codegen_worker.v4"
_PY_FRONTEND_AST_WIRE_ENV = "PCC_PY_FRONTEND_AST_WIRE"
_PY_AST_WIRE_SCHEMA = "pcc.py_frontend.py_ast.v1"
_PY_AST_WIRE_NODE_KEY = "__pcc_py_ast_v1__"
_PY_AST_WIRE_BYTES_KEY = "__pcc_bytes_v1__"
_PY_RUNTIME_CC_ENV = "PCC_RUNTIME_CC"
_PY_RUNTIME_HIGH_ENV = "PCC_RUNTIME_HIGH"
_PY_RUNTIME_ARCHIVE_ENV = "PCC_RUNTIME_ARCHIVE"
_PY_RUNTIME_DIR_ENV = "PCC_RUNTIME_DIR"
_GPU_BACKEND_ENV = "PCC_GPU_BACKEND"
_DEFAULT_GPU_BACKEND = "none"
_KNOWN_GPU_BACKENDS = ("metal", "none")
_SELF_BACKEND_JOBS_ENV = "PCC_SELF_BACKEND_JOBS"
_SELF_BACKEND_SKIP_LL_TEMP_ENV = "PCC_SELF_BACKEND_SKIP_LL_TEMP"
_SELF_BACKEND_SPLIT_LARGE_MODULES_ENV = "PCC_SELF_BACKEND_SPLIT_LARGE_MODULES"
_SELF_BACKEND_SPLIT_THRESHOLD_BYTES_ENV = "PCC_SELF_BACKEND_SPLIT_THRESHOLD_BYTES"
_SELF_BACKEND_SPLIT_SHARD_BYTES_ENV = "PCC_SELF_BACKEND_SPLIT_SHARD_BYTES"
_SELF_BACKEND_PUBLISH_SYNC_ENV = "PCC_SELF_BACKEND_PUBLISH_SYNC"
_SELF_BACKEND_OBJECT_CACHE_ENV = "PCC_SELF_BACKEND_OBJECT_CACHE"
_SELF_BACKEND_OBJECT_CACHE_DIR_ENV = "PCC_SELF_BACKEND_OBJECT_CACHE_DIR"
_SELF_BACKEND_OBJECT_CACHE_IDENTITY_ENV = "PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY"
_SELF_BACKEND_OBJECT_CACHE_VERSION = "pcc.self-backend-object-cache.v2"
_COMPILE_TIME_ONLY_IMPORT_FROMS = {
    "abc": frozenset({"ABC", "abstractmethod"}),
    "dataclasses": frozenset({"dataclass", "field", "replace"}),
}
_COMPILE_TIME_ONLY_IMPORT_MODULES = frozenset(
    {
        "__future__",
        "typing",
        "click",
        "abc",
    }
)
_TEST_FACADE_IMPORT_MODULES = (
    "pytest",
    "pcc.test_runner",
)
_ANNOTATION_ONLY_IMPORT_MODULES = frozenset(
    {
        "llvmlite.binding",
        "llvmlite.ir",
    }
)
_NATIVE_BUILTIN_IMPORTS = frozenset(
    {
        "builtins",
        "sys",
        "os",
        "time",
        "string",
        "platform",
        "subprocess",
        "tempfile",
        "shutil",
        "shlex",
        "sysconfig",
        "math",
        "json",
        "re",
        "gc",
        "weakref",
        "copy",
        "functools",
        "pickle",
        "threading",
        "pcc.virtual_thread",
        "pcc",
        "importlib",
        "inspect",
        "contextlib",
        "contextvars",
        # ``textwrap.dedent`` has a dedicated native lowering.  Treat the
        # module consistently with import lowering instead of recursively
        # compiling the host ``textwrap.py`` implementation (whose class-body
        # regex setup is unrelated to the supported native surface).
        "textwrap",
        # ``enum`` has native ``Enum`` / ``IntEnum`` / ``auto`` support
        # via ``pcc/py_frontend/codegen/class_gen.py::_is_enum_like_class``
        # + ``_enum_member_value`` and the
        # ``_maybe_emit_enum_member_attr`` lookup in dynamic_type_lowering.
        # Skipping recursive compile of ``pcc/py_stdlib/enum.py`` avoids
        # pulling its heavy metaclass machinery into the closure (which
        # otherwise emits ``py_cpy_*`` fallbacks). The class_gen path
        # handles ``class X(Enum)`` / ``class X(IntEnum)`` natively.
        "enum",
    }
)
_NATIVE_IMPORT_FROMS = {
    "builtins": frozenset(
        {
            "bool",
            "bytes",
            "bytearray",
            "complex",
            "dict",
            "float",
            "int",
            "list",
            "memoryview",
            "object",
            "str",
            "tuple",
        }
    ),
    "sys": frozenset({"exit", "stdin", "stdout", "stderr"}),
    "os": frozenset({"path", "sep", "linesep", "altsep"}),
    "time": frozenset({"monotonic", "perf_counter", "time", "strftime"}),
    "functools": frozenset({"partial"}),
    "string": frozenset(
        {
            "ascii_lowercase",
            "ascii_uppercase",
            "ascii_letters",
            "digits",
            "hexdigits",
            "octdigits",
            "punctuation",
            "whitespace",
            "printable",
        }
    ),
    "math": frozenset(
        {
            "floor",
            "ceil",
            "sqrt",
            "trunc",
            "gcd",
            "factorial",
            "isqrt",
            "pow",
            "pi",
            "e",
            "tau",
            "inf",
            "nan",
        }
    ),
    "re": frozenset({"match", "search", "fullmatch"}),
    "gc": frozenset(
        {
            "collect",
            "disable",
            "enable",
            "isenabled",
            "is_tracked",
            "is_finalized",
            "get_count",
            "get_threshold",
            "set_threshold",
            "get_stats",
            "freeze",
            "unfreeze",
            "get_freeze_count",
            "get_objects",
            "get_referents",
            "get_referrers",
        }
    ),
    "weakref": frozenset({"ref"}),
    "threading": frozenset(
        {
            "Thread",
            "Lock",
            "RLock",
            "Event",
            "Condition",
            "Semaphore",
            "current_thread",
            "get_ident",
        }
    ),
    "pcc.virtual_thread": frozenset(
        {
            "spawn",
            "run",
            "run_until_idle",
            "carrier_pool_start",
            "carrier_pool_stop",
            "result",
            "state",
            "sleep",
            "block_on_fd",
        }
    ),
    "contextlib": frozenset({"contextmanager"}),
    "contextvars": frozenset({"ContextVar"}),
    "pcc": frozenset({"valueclass"}),
    "enum": frozenset({"Enum", "IntEnum", "auto"}),
    "typing": frozenset(
        {
            "Generic",
            "Protocol",
            "TypeVar",
            "runtime_checkable",
            "get_origin",
            "get_args",
            "Optional",
        }
    ),
}
_SCAFFOLD_IMPORT_MODULES = frozenset(
    {
        "pcc.extern",
        "pcc.llvm_capi",
        "pcc.llvm_capi.compat",
        "pcc.unsafe",
    }
)
_PYTHON_IR_PASS_FAST_PRESET = (
    "mem2reg",
    "sroa",
)
_PYTHON_IR_PASS_UNSAFE_MODULES = frozenset(
    {
        # mem2reg+sroa on the compiler's string-global mixin produced a pcc2
        # that lowered traceback C string globals as zero-filled arrays. Keep
        # the default pass-on bootstrap broad, but do not optimize the helper
        # that constructs literal byte payloads for generated IR globals.
        "pcc.py_frontend.codegen.string_globals_lowering",
        # The compiler's in-repo llvmlite-compatible IR layer owns
        # Constant(ArrayType(i8), [...]) rendering. Optimizing this module in
        # stage2 produced a pcc2 that rendered non-empty C strings as all-zero
        # byte arrays.
        "pcc.llvm_capi.ir",
    }
)
_PYTHON_IR_PASS_UNSAFE_MODULE_PREFIXES = (
    # Stage2 pcc1 with memory mem2reg/sroa produced a pcc2 that generated
    # zero-filled traceback string globals unless the codegen package was left
    # unoptimized. Keep pass-on enabled for the rest of the compiler closure
    # while preserving bootstrap correctness for the semantic lowering layer.
    "pcc.py_frontend.codegen",
)
_PYTHON_IR_PASS_PRESETS = {
    "quick": ("mem2reg", "sroa", "sccp", "dce"),
    "fast": _PYTHON_IR_PASS_FAST_PRESET,
    "default": _PYTHON_IR_PASS_FAST_PRESET,
    "all": ("all",),
    "full": ("all",),
}


def _profile_now_ms() -> int:
    return int(time.monotonic() * 1000.0)


def _profile_events(profile):
    if profile is None:
        return None
    events = profile.get("events")
    if events is None:
        events = []
        profile["events"] = events
    return events


def _profile_totals(profile):
    if profile is None:
        return None
    totals = profile.get("phase_totals_ms")
    if totals is None:
        totals = {}
        profile["phase_totals_ms"] = totals
    return totals


def _profile_counters(profile):
    if profile is None:
        return None
    counters = profile.get("counters")
    if counters is None:
        counters = {}
        profile["counters"] = counters
    return counters


def _profile_begin(profile) -> int:
    if profile is None:
        return 0
    return _profile_now_ms()


def _profile_end(
    profile,
    name: str,
    start_ms: int,
    detail: Optional[str] = None,
) -> None:
    if profile is None:
        return
    elapsed = _profile_now_ms() - start_ms
    events = _profile_events(profile)
    if events is not None:
        event = {"name": name, "ms": elapsed}
        if detail is not None:
            event["detail"] = detail
        events.append(event)
    totals = _profile_totals(profile)
    if totals is not None:
        totals[name] = totals.get(name, 0) + elapsed


def _profile_counter(profile, name: str, value) -> None:
    counters = _profile_counters(profile)
    if counters is not None:
        counters[name] = value


def _normalize_gpu_backend_name(value: Optional[str]) -> str:
    if value is None:
        return _DEFAULT_GPU_BACKEND
    candidate = str(value or "").strip().lower()
    if not candidate:
        return _DEFAULT_GPU_BACKEND
    if candidate in ("off", "disabled", "host", "cpu"):
        return "none"
    return candidate


def _resolve_gpu_backend_kind(requested: Optional[str]) -> str:
    env_raw = os.environ.get(_GPU_BACKEND_ENV)
    kind = _normalize_gpu_backend_name(requested if requested is not None else env_raw)
    if kind not in _KNOWN_GPU_BACKENDS:
        known = ", ".join(sorted(_KNOWN_GPU_BACKENDS))
        raise ValueError(f"unknown gpu backend {kind!r}; expected one of: {known}")
    return kind


def _self_backend_publish_sync_enabled() -> bool:
    value = os.environ.get(_SELF_BACKEND_PUBLISH_SYNC_ENV, "").strip().lower()
    if value in ("0", "false", "no", "off"):
        return False
    return True


_SELF_BACKEND_HOST_CODE = (
    "import sys\n"
    "import os\n"
    "pcc_source_root = sys.argv[1]\n"
    "if pcc_source_root and pcc_source_root not in sys.path:\n"
    "    sys.path.insert(0, pcc_source_root)\n"
    "if pcc_source_root:\n"
    "    os.environ.setdefault('PCC_SOURCE_ROOT', pcc_source_root)\n"
    "    os.environ.setdefault('PCC_REPO_ROOT', pcc_source_root)\n"
    "from pcc.backend.self_backend_dispatch import emit_self_asm, "
    "self_backend_target_identity\n"
    "from pcc.backend.self_backend_parse import "
    "parse_self_backend_target_triple\n"
    "path = sys.argv[2]\n"
    "with open(path, 'r', encoding='utf-8') as f:\n"
    "    text = f.read()\n"
    "triple = parse_self_backend_target_triple(text)\n"
    "sys.stdout.write(self_backend_target_identity(triple) + '\\n')\n"
    "sys.stdout.write(emit_self_asm(text))\n"
)
_SELF_BACKEND_HOST_MANY_CODE = (
    "import glob\n"
    "import hashlib\n"
    "import multiprocessing as mp\n"
    "import os\n"
    "import platform\n"
    "import shutil\n"
    "import subprocess\n"
    "import sys\n"
    "import time\n"
    "pcc_source_root = sys.argv[1]\n"
    "if pcc_source_root and pcc_source_root not in sys.path:\n"
    "    sys.path.insert(0, pcc_source_root)\n"
    "if pcc_source_root:\n"
    "    os.environ.setdefault('PCC_SOURCE_ROOT', pcc_source_root)\n"
    "    os.environ.setdefault('PCC_REPO_ROOT', pcc_source_root)\n"
    "from pcc.backend.self_backend_dispatch import emit_self_asm, "
    "self_backend_target_identity\n"
    "from pcc.backend.self_backend_parse import "
    "parse_self_backend_target_triple\n"
    "\n"
    "_OBJECT_CACHE_VERSION = 'pcc.self-backend-object-cache.v1'\n"
    "_OBJECT_CACHE_ENV = 'PCC_SELF_BACKEND_OBJECT_CACHE'\n"
    "_OBJECT_CACHE_DIR_ENV = 'PCC_SELF_BACKEND_OBJECT_CACHE_DIR'\n"
    "_OBJECT_CACHE_IDENTITY = None\n"
    "\n"
    "def _object_cache_enabled():\n"
    "    value = str(os.environ.get(_OBJECT_CACHE_ENV, '') or '').strip().lower()\n"
    "    return value not in ('0', 'false', 'no', 'off', 'disable', 'disabled')\n"
    "\n"
    "def _object_cache_dir():\n"
    "    override = str(os.environ.get(_OBJECT_CACHE_DIR_ENV, '') or '').strip()\n"
    "    if override:\n"
    "        return os.path.expanduser(override)\n"
    "    xdg_cache_home = str(os.environ.get('XDG_CACHE_HOME', '') or '').strip()\n"
    "    if xdg_cache_home:\n"
    "        base = xdg_cache_home\n"
    "    else:\n"
    "        base = os.path.join(os.path.expanduser('~'), '.cache')\n"
    "    return os.path.join(base, 'pcc', 'self-backend-object-cache')\n"
    "\n"
    "def _hash_file_into(h, path):\n"
    "    try:\n"
    "        with open(path, 'rb') as f:\n"
    "            while True:\n"
    "                chunk = f.read(1024 * 1024)\n"
    "                if not chunk:\n"
    "                    break\n"
    "                h.update(chunk)\n"
    "    except OSError:\n"
    "        h.update(b'<unreadable>')\n"
    "\n"
    "def _object_cache_identity():\n"
    "    global _OBJECT_CACHE_IDENTITY\n"
    "    if _OBJECT_CACHE_IDENTITY is not None:\n"
    "        return _OBJECT_CACHE_IDENTITY\n"
    "    h = hashlib.sha256()\n"
    "    h.update(_OBJECT_CACHE_VERSION.encode('utf-8'))\n"
    "    h.update(b'\\0')\n"
    "    h.update(sys.platform.encode('utf-8'))\n"
    "    h.update(b'\\0')\n"
    "    h.update((platform.machine() or '').encode('utf-8'))\n"
    "    h.update(b'\\0')\n"
    "    for name in ('PCC_SELF_TARGET_PASSES', 'PCC_SELF_TARGET_PASS_TRANSPORT'):\n"
    "        h.update(name.encode('utf-8'))\n"
    "        h.update(b'=')\n"
    "        h.update(str(os.environ.get(name, '') or '').encode('utf-8'))\n"
    "        h.update(b'\\0')\n"
    "    try:\n"
    "        import pcc.backend.self_backend_dispatch as _dispatch_module\n"
    "        backend_dir = os.path.dirname(os.path.abspath(_dispatch_module.__file__))\n"
    "        backend_files = sorted(glob.glob(os.path.join(backend_dir, 'self_backend*.py')))\n"
    "    except Exception:\n"
    "        backend_files = []\n"
    "    for path in backend_files:\n"
    "        h.update(os.path.basename(path).encode('utf-8'))\n"
    "        h.update(b'\\0')\n"
    "        _hash_file_into(h, path)\n"
    "        h.update(b'\\0')\n"
    "    _OBJECT_CACHE_IDENTITY = h.hexdigest()\n"
    "    return _OBJECT_CACHE_IDENTITY\n"
    "\n"
    "def _object_cache_path(text, target_id):\n"
    "    if not _object_cache_enabled():\n"
    "        return None\n"
    "    h = hashlib.sha256()\n"
    "    h.update(_object_cache_identity().encode('utf-8'))\n"
    "    h.update(b'\\0')\n"
    "    h.update(str(cc).encode('utf-8'))\n"
    "    h.update(b'\\0')\n"
    "    h.update(str(target_id).encode('utf-8'))\n"
    "    h.update(b'\\0')\n"
    "    h.update(text.encode('utf-8'))\n"
    "    digest = h.hexdigest()\n"
    "    return os.path.join(_object_cache_dir(), digest[:2], digest + '.o')\n"
    "\n"
    "def _populate_object_cache(cache_path, obj_path):\n"
    "    if not cache_path:\n"
    "        return\n"
    "    tmp_path = ''\n"
    "    try:\n"
    "        os.makedirs(os.path.dirname(cache_path), exist_ok=True)\n"
    "        tmp_path = cache_path + '.' + str(os.getpid()) + '.tmp'\n"
    "        shutil.copyfile(obj_path, tmp_path)\n"
    "        os.replace(tmp_path, cache_path)\n"
    "    except OSError:\n"
    "        try:\n"
    "            os.unlink(tmp_path)\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "def _host_target_triple_for_self_backend():\n"
    "    if sys.platform == 'darwin':\n"
    "        machine = platform.machine() or 'unknown'\n"
    "        if machine == 'arm64':\n"
    "            machine = 'aarch64'\n"
    "        return machine + '-apple-darwin'\n"
    "    if sys.platform.startswith('linux'):\n"
    "        machine = platform.machine() or 'unknown'\n"
    "        if machine in ('amd64', 'x64'):\n"
    "            machine = 'x86_64'\n"
    "        return machine + '-unknown-linux-gnu'\n"
    "    return 'unknown-unknown-unknown'\n"
    "\n"
    "def _self_backend_ir_text(ir_text):\n"
    "    ir_text = str(ir_text)\n"
    "    placeholder = 'target triple = \"unknown-unknown-unknown\"'\n"
    "    header = ir_text[:4096]\n"
    "    idx = header.find(placeholder)\n"
    "    if idx >= 0:\n"
    "        replacement = 'target triple = \"' + "
    "_host_target_triple_for_self_backend() + '\"'\n"
    "        return ir_text[:idx] + replacement + ir_text[idx + len(placeholder):]\n"
    "    if 'target triple = \"' not in header:\n"
    "        return 'target triple = \"' + _host_target_triple_for_self_backend() "
    "+ '\"\\n' + ir_text\n"
    "    return ir_text\n"
    "\n"
    "def _emit_one(item):\n"
    "    idx, path = item\n"
    "    with open(path, 'r', encoding='utf-8') as f:\n"
    "        text = _self_backend_ir_text(f.read())\n"
    "    triple = parse_self_backend_target_triple(text)\n"
    "    target_id = self_backend_target_identity(triple)\n"
    "    asm_path = path + '.s'\n"
    "    obj_path = path + '.o'\n"
    "    cache_path = _object_cache_path(text, target_id)\n"
    "    if cache_path and os.path.isfile(cache_path):\n"
    "        shutil.copyfile(cache_path, obj_path)\n"
    "        return idx, target_id, obj_path, 0, 0, len(text), 'hit'\n"
    "    cache_status = 'miss' if cache_path else 'off'\n"
    "    t0 = time.monotonic()\n"
    "    asm_text = emit_self_asm(text)\n"
    "    emit_ms = int((time.monotonic() - t0) * 1000)\n"
    "    with open(asm_path, 'w', encoding='utf-8') as f:\n"
    "        f.write(asm_text)\n"
    "    t1 = time.monotonic()\n"
    "    subprocess.run([cc, '-c', asm_path, '-o', obj_path], check=True)\n"
    "    cc_ms = int((time.monotonic() - t1) * 1000)\n"
    "    _populate_object_cache(cache_path, obj_path)\n"
    "    return idx, target_id, obj_path, emit_ms, cc_ms, len(text), cache_status\n"
    "\n"
    "jobs = int(sys.argv[2])\n"
    "cc = sys.argv[3]\n"
    "split_large_modules = sys.argv[4] == '1'\n"
    "result_path = sys.argv[5]\n"
    "paths = list(sys.argv[6:])\n"
    "if split_large_modules:\n"
    "    _pipeline = __import__('pcc.py_frontend.pipeline', "
    "fromlist=['_split_self_backend_large_ir_modules'])\n"
    "    _split_self_backend_large_ir_modules = getattr(_pipeline, "
    "'_split_self_backend_large_ir_modules')\n"
    "    texts = []\n"
    "    for path in paths:\n"
    "        with open(path, 'r', encoding='utf-8') as f:\n"
    "            texts.append(_self_backend_ir_text(f.read()))\n"
    "    texts = _split_self_backend_large_ir_modules(texts)\n"
    "    expanded_paths = []\n"
    "    result_dir = os.path.dirname(result_path)\n"
    "    for idx, text in enumerate(texts):\n"
    "        path = os.path.join(result_dir, 'self_backend_expanded_' + "
    "str(idx) + '.ll')\n"
    "        with open(path, 'w', encoding='utf-8') as f:\n"
    "            f.write(text)\n"
    "        expanded_paths.append(path)\n"
    "    items = list(enumerate(expanded_paths))\n"
    "else:\n"
    "    items = list(enumerate(paths))\n"
    "if jobs <= 0:\n"
    "    jobs = os.cpu_count() or 1\n"
    "jobs = max(1, min(len(items), jobs))\n"
    "if jobs > 1 and len(items) > 1:\n"
    "    try:\n"
    "        mp.set_start_method('fork')\n"
    "    except (RuntimeError, ValueError):\n"
    "        pass\n"
    "    if mp.get_start_method(allow_none=True) == 'fork':\n"
    "        with mp.Pool(processes=jobs) as pool:\n"
    "            results = pool.map(_emit_one, items, chunksize=1)\n"
    "    else:\n"
    "        results = [_emit_one(item) for item in items]\n"
    "else:\n"
    "    results = [_emit_one(item) for item in items]\n"
    "with open(result_path, 'w', encoding='utf-8') as out:\n"
    "    for idx, target_id, obj_path, emit_ms, cc_ms, byte_len, cache_status in sorted(results):\n"
    "        out.write(str(idx) + '\\t' + target_id + '\\t' + obj_path + '\\t' + str(emit_ms) + '\\t' + str(cc_ms) + '\\t' + str(byte_len) + '\\t' + cache_status + '\\n')\n"
)


_SELF_BACKEND_OBJECT_CACHE_PLAN_CODE = (
    "import hashlib\n"
    "import os\n"
    "import shutil\n"
    "import subprocess\n"
    "import sys\n"
    "version = sys.argv[1]\n"
    "identity = sys.argv[2]\n"
    "target_id = sys.argv[3]\n"
    "cc = sys.argv[4]\n"
    "cache_dir = sys.argv[5]\n"
    "manifest_path = sys.argv[6]\n"
    "plan_path = sys.argv[7]\n"
    "platform_id = sys.platform + ':' + (os.uname().machine or '')\n"
    "try:\n"
    "    cc_identity = subprocess.check_output([cc, '--version'], stderr=subprocess.STDOUT)\n"
    "except Exception:\n"
    "    cc_identity = b'<unknown-toolchain>'\n"
    "rows = []\n"
    "with open(manifest_path, 'r', encoding='utf-8') as f:\n"
    "    manifest_lines = f.read().splitlines()\n"
    "for line in manifest_lines:\n"
    "    parts = line.split('\\t')\n"
    "    if len(parts) != 4:\n"
    "        raise RuntimeError('invalid self-backend object-cache manifest')\n"
    "    index_text, ir_path, result_path, obj_path = parts\n"
    "    h = hashlib.sha256()\n"
    "    for value in (version, identity, platform_id, target_id, cc):\n"
    "        h.update(value.encode('utf-8'))\n"
    "        h.update(b'\\0')\n"
    "    h.update(cc_identity)\n"
    "    h.update(b'\\0')\n"
    "    with open(ir_path, 'rb') as ir_file:\n"
    "        while True:\n"
    "            block = ir_file.read(1024 * 1024)\n"
    "            if not block:\n"
    "                break\n"
    "            h.update(block)\n"
    "    digest = h.hexdigest()\n"
    "    cache_path = os.path.join(cache_dir, digest[:2], digest + '.o')\n"
    "    checksum_path = cache_path + '.sha256'\n"
    "    status = 'miss'\n"
    "    cache_valid = False\n"
    "    if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0 and os.path.isfile(checksum_path):\n"
    "        try:\n"
    "            with open(checksum_path, 'r', encoding='utf-8') as checksum_file:\n"
    "                expected_checksum = checksum_file.read().strip()\n"
    "            object_hash = hashlib.sha256()\n"
    "            with open(cache_path, 'rb') as cache_file:\n"
    "                while True:\n"
    "                    block = cache_file.read(1024 * 1024)\n"
    "                    if not block:\n"
    "                        break\n"
    "                    object_hash.update(block)\n"
    "            cache_valid = len(expected_checksum) == 64 and object_hash.hexdigest() == expected_checksum\n"
    "        except OSError:\n"
    "            cache_valid = False\n"
    "    if cache_valid:\n"
    "        try:\n"
    "            shutil.copyfile(cache_path, obj_path)\n"
    "            with open(result_path, 'w', encoding='utf-8') as result_file:\n"
    "                result_file.write(target_id + '\\n')\n"
    "                result_file.write(obj_path + '\\n')\n"
    "                result_file.write('hit\\n')\n"
    "            status = 'hit'\n"
    "        except OSError:\n"
    "            status = 'miss'\n"
    "    rows.append(index_text + '\\t' + cache_path + '\\t' + status)\n"
    "with open(plan_path, 'w', encoding='utf-8') as f:\n"
    "    f.write('\\n'.join(rows))\n"
    "    if rows:\n"
    "        f.write('\\n')\n"
)


_SELF_BACKEND_OBJECT_CACHE_PUBLISH_CODE = (
    "import hashlib\n"
    "import os\n"
    "import shutil\n"
    "import sys\n"
    "manifest_path = sys.argv[1]\n"
    "with open(manifest_path, 'r', encoding='utf-8') as f:\n"
    "    rows = f.read().splitlines()\n"
    "for index, row in enumerate(rows):\n"
    "    parts = row.split('\\t')\n"
    "    if len(parts) != 2:\n"
    "        raise RuntimeError('invalid self-backend object-cache publish manifest')\n"
    "    cache_path, obj_path = parts\n"
    "    os.makedirs(os.path.dirname(cache_path), exist_ok=True)\n"
    "    tmp_path = cache_path + '.' + str(os.getpid()) + '.' + str(index) + '.tmp'\n"
    "    checksum_path = cache_path + '.sha256'\n"
    "    checksum_tmp_path = tmp_path + '.sha256'\n"
    "    try:\n"
    "        shutil.copyfile(obj_path, tmp_path)\n"
    "        object_hash = hashlib.sha256()\n"
    "        with open(tmp_path, 'rb') as object_file:\n"
    "            while True:\n"
    "                block = object_file.read(1024 * 1024)\n"
    "                if not block:\n"
    "                    break\n"
    "                object_hash.update(block)\n"
    "        with open(checksum_tmp_path, 'w', encoding='utf-8') as checksum_file:\n"
    "            checksum_file.write(object_hash.hexdigest() + '\\n')\n"
    "        os.replace(tmp_path, cache_path)\n"
    "        os.replace(checksum_tmp_path, checksum_path)\n"
    "    finally:\n"
    "        for leftover_path in (tmp_path, checksum_tmp_path):\n"
    "            try:\n"
    "                os.unlink(leftover_path)\n"
    "            except OSError:\n"
    "                pass\n"
)


class PyPipelineError(RuntimeError):
    """Raised when the Python pipeline fails in a user-visible way."""


def _normalize_native_backend_name(value: Optional[str]) -> str:
    if value is None:
        value = os.environ.get("PCC_BACKEND")
    candidate = str(value or "").strip().lower()
    if not candidate:
        return "llvm"
    if candidate == "llvmlite":
        return "llvm"
    if candidate == "llvm-capi":
        return "llvm_capi"
    return candidate


def _resolve_native_backend(backend: Optional[str]) -> str:
    kind = _normalize_native_backend_name(backend)
    if kind not in ("llvm", "self"):
        if kind == "llvm_capi":
            raise PyPipelineError(
                "Python native emission backend "
                f"{kind!r} is not supported; expected llvm or self"
            )
        raise PyPipelineError(
            "unknown backend " f"{kind!r}; expected one of: llvm, llvm_capi, self"
        )
    return kind


def _native_backend_kind(backend) -> str:
    kind = str(getattr(backend, "kind", backend) or "")
    if kind not in ("llvm", "self"):
        raise PyPipelineError(
            "Python native emission backend "
            f"{kind!r} is not supported; expected llvm or self"
        )
    return kind


def _resolve_libpython_mode(mode: Optional[str]) -> str:
    raw = mode
    if raw is None:
        raw = os.environ.get(_PY_LIBPYTHON_MODE_ENV, "")
    normalized = str(raw or "").strip().lower()
    if not normalized:
        return "off"
    if normalized == "auto":
        return "auto"
    if normalized in ("on", "true", "yes", "1"):
        return "on"
    if normalized in ("off", "false", "no", "0"):
        return "off"
    raise PyPipelineError(
        "invalid libpython mode " f"{raw!r}; expected auto, on, or off"
    )


def _resolve_ir_scaffold_mode(mode: Optional[str]) -> str:
    """Resolve the ``--ir-scaffold`` mode to a canonical value.

    Path A (closed-world) is now the default. ``on`` routes
    ``self.builder.X(...)`` and ``ir.Y(...)`` call sites to direct
    native IR lowering instead of ``py_cpy_*`` dispatch; methods not
    yet implemented raise a clear error rather than silently falling
    back. ``off`` remains as an explicit compatibility escape hatch.
    """
    raw = mode
    if raw is None:
        raw = os.environ.get(_IR_SCAFFOLD_MODE_ENV, "")
    normalized = str(raw or "").strip().lower()
    if not normalized or normalized == "auto":
        return "on"
    if normalized in ("on", "true", "yes", "1"):
        return "on"
    if normalized in ("off", "false", "no", "0"):
        return "off"
    raise PyPipelineError(
        "invalid ir scaffold mode " f"{raw!r}; expected off, on, or auto"
    )


def _finalize_libpython_mode(
    *,
    detected: bool,
    mode: str,
    context: str,
    reasons: list[str],
) -> bool:
    if mode == "on":
        return True
    if mode == "off" and detected:
        suffix = ""
        if reasons:
            suffix = " (" + "; ".join(reasons) + ")"
        if bool(os.environ.get("PCC_DEBUG_LIBPYTHON_GATE_BYPASS")):
            sys.stderr.write("[libpython_gate_bypass] " + context + suffix + "\n")
            return detected
        raise PyPipelineError(
            "Python pipeline requires libpython fallback for "
            + context
            + suffix
            + "; rerun with --python-libpython=auto/on or "
            + "PCC_PYTHON_LIBPYTHON=auto/on"
        )
    return detected


def _log(verbose: bool, msg: str) -> None:
    if verbose:
        sys.stderr.write("[pcc.py] " + msg + "\n")


def _join_dotted_parts(parts: list[str]) -> str:
    return _join_strings(parts, ".")


def _join_strings(parts: list[str], sep: str) -> str:
    if not parts:
        return ""
    out = parts[0]
    i = 1
    while i < len(parts):
        out += sep + parts[i]
        i += 1
    return out


def _first_string(items: list[str]) -> str:
    return items[0]


def _module_name_from_src(src_path: str) -> str:
    base = str(os.path.basename(src_path))
    if base.endswith(".py"):
        base = base[:-3]
    if not base:
        return "<module>"

    abs_path = str(os.path.abspath(src_path))
    parent_dir = str(os.path.dirname(abs_path))
    if base == "__main__":
        init_py = str(os.path.join(parent_dir, "__init__.py"))
        package_name = str(os.path.basename(parent_dir))
        if package_name and os.path.isfile(init_py):
            return _join_dotted_parts([package_name, "__main__"])

    pkg_parts = []
    cur_dir = parent_dir
    while cur_dir:
        init_py = str(os.path.join(cur_dir, "__init__.py"))
        if not os.path.isfile(init_py):
            break
        pkg_parts.append(str(os.path.basename(cur_dir)))
        parent = str(os.path.dirname(cur_dir))
        if parent == cur_dir:
            break
        cur_dir = parent

    if not pkg_parts:
        return base

    ordered_parts = []
    i = len(pkg_parts) - 1
    while i >= 0:
        ordered_parts.append(pkg_parts[i])
        i -= 1
    if base == "__init__":
        return _join_dotted_parts(ordered_parts)
    return _join_dotted_parts(ordered_parts + [base])


def _module_root_from_src(src_path: str, module_name: str) -> str:
    abs_path = str(os.path.abspath(src_path))
    cur_dir = str(os.path.dirname(abs_path))
    parts = module_name.split(".")
    up = (
        len(parts)
        if os.path.basename(abs_path) == "__init__.py"
        else max(
            0,
            len(parts) - 1,
        )
    )
    i = 0
    while i < up:
        parent = str(os.path.dirname(cur_dir))
        if parent == cur_dir:
            break
        cur_dir = parent
        i += 1
    return cur_dir


def _package_parts_for_module(src_path: str, module_name: str) -> list[str]:
    parts = module_name.split(".")
    if os.path.basename(src_path) == "__init__.py":
        return parts
    return parts[:-1]


def _resolve_module_src(root_dir: str, dotted_name: str) -> Optional[str]:
    parts = dotted_name.split(".")
    py_path = str(os.path.join(root_dir, *parts)) + ".py"
    if os.path.isfile(py_path):
        return py_path
    init_path = str(os.path.join(root_dir, *parts, "__init__.py"))
    if os.path.isfile(init_path):
        return init_path
    return None


def _package_site_roots() -> list[str]:
    roots: list[str] = []
    raw = str(os.environ.get("PCC_PACKAGE_SITE", "") or "").strip()
    if raw:
        path_sep = ";"
        if not sys.platform.startswith("win"):
            path_sep = ":"
        start = 0
        i = 0
        while i <= len(raw):
            if i == len(raw) or raw[i] == path_sep:
                item = raw[start:i].strip()
                start = i + 1
            else:
                i += 1
                continue
            if item:
                roots.append(str(os.path.abspath(item)))
            i += 1
    home = str(os.environ.get("HOME", "") or "")
    default_root = ""
    if home:
        default_root = str(os.path.abspath(home + "/.cache/pcc/site-packages"))
    if os.path.isdir(default_root):
        roots.append(default_root)
    out: list[str] = []
    seen: set[str] = set()
    for root in roots:
        if root in seen or not os.path.isdir(root):
            continue
        seen.add(root)
        out.append(root)
    return out


_NATIVE_EXTENSION_SUFFIXES = (".so", ".dylib", ".pyd", ".dll")


def _native_extension_name_uses_cpython_abi(path: str) -> bool:
    lower = os.path.basename(str(path or "")).lower()
    if ".cpython-" in lower or "-cpython-" in lower or "_cpython-" in lower:
        return True
    if ".abi3" in lower or "-abi3" in lower or "_abi3" in lower:
        return True
    first_cp = lower.find("-cp")
    return first_cp >= 0 and lower.find("-cp", first_cp + 3) >= 0


def _resolve_pcc_native_extension_path(module_name: str) -> Optional[str]:
    """Return a pcc-native extension artifact for ``module_name`` if present.

    This is intentionally generic package-site logic, not NumPy-specific logic.
    CPython ABI artifacts (``*.cpython-*``, ``abi3``, ``cp*-cp*``) are rejected
    here so no-libpython mode can distinguish pcc-native extension imports from
    imports that still need the CPython fallback.
    """
    rel = str(module_name or "").replace(".", os.sep)
    if not rel:
        return None
    for site_root in _package_site_roots():
        base = str(os.path.join(site_root, rel))
        for suffix in _NATIVE_EXTENSION_SUFFIXES:
            candidate = base + suffix
            if os.path.isfile(
                candidate
            ) and not _native_extension_name_uses_cpython_abi(candidate):
                return str(os.path.abspath(candidate))
        parent = str(os.path.dirname(base))
        leaf = str(os.path.basename(base))
        if not parent or not os.path.isdir(parent):
            continue
        try:
            names = sorted(os.listdir(parent))
        except OSError:
            names = []
        for name in names:
            full = str(os.path.join(parent, name))
            if not os.path.isfile(full):
                continue
            if not name.startswith(leaf + "."):
                continue
            if not name.lower().endswith(_NATIVE_EXTENSION_SUFFIXES):
                continue
            if _native_extension_name_uses_cpython_abi(name):
                continue
            return str(os.path.abspath(full))
    return None


def _resolve_module_src_for_import(root_dir: str, dotted_name: str) -> Optional[str]:
    target = _resolve_module_src(root_dir, dotted_name)
    if target is not None:
        return target
    for site_root in _package_site_roots():
        target = _resolve_module_src(site_root, dotted_name)
        if target is not None:
            return target
    return None


def _package_site_package_root_for_src(src_path: str) -> Optional[str]:
    abs_src = str(os.path.abspath(src_path))
    for site_root in _package_site_roots():
        site_root = str(os.path.abspath(site_root))
        prefix = site_root
        if not prefix.endswith(os.sep):
            prefix = prefix + os.sep
        if not abs_src.startswith(prefix):
            continue
        rel = abs_src[len(prefix) :]
        first = rel.split(os.sep, 1)[0]
        if not first:
            continue
        pkg_root = str(os.path.join(site_root, first))
        if os.path.isfile(os.path.join(pkg_root, "pcc-package.json")):
            return pkg_root
    return None


def _package_site_package_root_for_module_name(module_name: str) -> Optional[str]:
    top = str(module_name or "").split(".", 1)[0]
    if not top:
        return None
    for site_root in _package_site_roots():
        pkg_root = str(os.path.join(site_root, top))
        if os.path.isfile(os.path.join(pkg_root, "pcc-package.json")):
            return pkg_root
    return None


def _package_root_no_libpython_diagnostic(root: str) -> Optional[tuple[str, str]]:
    queue = [str(root)]
    queue_i = 0
    while queue_i < len(queue):
        cur = queue[queue_i]
        queue_i += 1
        try:
            names = os.listdir(cur)
        except OSError:
            names = []
        for name in names:
            path = str(os.path.join(cur, name))
            if os.path.isdir(path):
                queue.append(path)
                continue
            lower = name.lower()
            if not lower.endswith(_NATIVE_EXTENSION_SUFFIXES):
                continue
            if _native_extension_name_uses_cpython_abi(name):
                return ("PCC-PKG-004", path)
    return None


def _validate_package_site_no_libpython_abi(
    src_paths: list[str],
    *,
    libpython_mode: str,
) -> None:
    """Reject installed packages that cannot satisfy a no-libpython compile.

    The package installer records pcc-native metadata, but older installs may
    have been produced before the CPython-extension-ABI gate existed. Re-scan
    installed package roots here so ``pcc --python-libpython=off`` fails at the
    package boundary instead of generating thousands of opaque ``py_cpy_*``
    fallback calls later in codegen.
    """
    if libpython_mode != "off":
        return
    roots: list[str] = []
    seen: set[str] = set()
    for src in src_paths:
        root = _package_site_package_root_for_src(src)
        if root is None or root in seen:
            pass
        else:
            seen.add(root)
            roots.append(root)
        try:
            with open(src, "r", encoding="utf-8") as f:
                source = f.read()
        except OSError:
            source = ""
        for import_name in _iter_source_import_specs(
            source,
            top_level_only=False,
        ):
            root = _package_site_package_root_for_module_name(import_name)
            if root is None or root in seen:
                continue
            seen.add(root)
            roots.append(root)
        for module_spec, imported_names in _iter_source_import_from_specs(
            source,
            top_level_only=False,
        ):
            root = _package_site_package_root_for_module_name(module_spec)
            if root is not None and root not in seen:
                seen.add(root)
                roots.append(root)
            if module_spec.startswith("."):
                continue
            for imported_name in imported_names:
                if not imported_name or imported_name == "*":
                    continue
                root = _package_site_package_root_for_module_name(
                    module_spec + "." + imported_name
                )
                if root is None or root in seen:
                    continue
                seen.add(root)
                roots.append(root)
    if not roots:
        return
    for root in roots:
        diagnostic = _package_root_no_libpython_diagnostic(root)
        if diagnostic is None:
            continue
        code, path = diagnostic
        raise PyPipelineError(
            code
            + ": installed package cannot be used by pcc-native no-libpython import: "
            + path
            + "; reinstall with --abi=pcc-native from source, or choose an explicit "
            + "--abi=libpython / --abi=cpython-compat mode"
        )


def _top_level_import_targets(
    root_dir: str,
    source: str,
    *,
    top_level_only: bool,
) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add_candidate(module_name: str) -> None:
        if not module_name or module_name.startswith(".") or module_name in seen:
            return
        src = _resolve_module_src_for_import(root_dir, module_name)
        if src is None:
            return
        seen.add(module_name)
        targets.append((src, module_name))

    for target_mod in _iter_source_import_specs(
        source,
        top_level_only=top_level_only,
    ) + _iter_source_importlib_literal_specs(
        source,
        top_level_only=top_level_only,
    ):
        add_candidate(target_mod)

    for module_spec, imported_names in _iter_source_import_from_specs(
        source,
        top_level_only=top_level_only,
    ):
        if module_spec.startswith("."):
            continue
        add_candidate(module_spec)
        # ``from pkg import submodule`` — also try each imported name as a
        # SUBMODULE of the package, so the submodule file is discovered and
        # compiled natively.  Without this, ``from p import sub`` fell back to
        # ``py_cpy_import`` (``p.sub`` was never added to the compile set, so
        # the from-import lowering's ``_native_import_from_submodule`` lookup
        # missed it), even though ``import p.sub`` and ``from p.sub import W``
        # already work.  ``add_candidate`` only adds names that resolve to a
        # real module file, so an imported name that is actually a
        # function/class/constant export of the package (not a submodule) is
        # correctly skipped.
        for imported_name in imported_names:
            if imported_name and imported_name != "*":
                add_candidate(module_spec + "." + imported_name)
    return targets


def _source_module_scope_lines(source: str) -> list[tuple[str, bool]]:
    """Classify source lines as module-scope, including control-flow suites.

    Package initialization commonly nests imports under a module-level
    ``try``/``if``/``else``.  Leading whitespace alone cannot distinguish
    those eager imports from lazy imports inside a function or class.  This
    small bootstrap-safe indentation scanner masks function/class suites while
    retaining module-level control-flow suites for closure discovery.
    """
    out: list[tuple[str, bool]] = []
    blocked_indent = -1
    blocked_header_complete = False
    blocked_paren_depth = 0
    for raw_line in source.splitlines():
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip())
        if blocked_indent >= 0:
            if not blocked_header_complete:
                code = stripped.split("#", 1)[0].rstrip()
                blocked_paren_depth += code.count("(") - code.count(")")
                if blocked_paren_depth <= 0 and code.endswith(":"):
                    blocked_header_complete = True
                out.append((raw_line, False))
                continue
            if not stripped or stripped.startswith("#"):
                out.append((raw_line, False))
                continue
            if indent > blocked_indent:
                out.append((raw_line, False))
                continue
            blocked_indent = -1
            blocked_header_complete = False
            blocked_paren_depth = 0

        code = stripped.split("#", 1)[0].rstrip()
        opens_local_scope = (
            code.startswith("def ")
            or code.startswith("async def ")
            or code.startswith("class ")
        )
        if opens_local_scope:
            blocked_indent = indent
            blocked_paren_depth = code.count("(") - code.count(")")
            blocked_header_complete = blocked_paren_depth <= 0 and code.endswith(":")
            out.append((raw_line, False))
            continue
        out.append((raw_line, True))
    return out


def _iter_source_import_specs(source: str, *, top_level_only: bool) -> list[str]:
    """Return module names from simple ``import mod[, other]`` lines."""
    out: list[str] = []
    lines = (
        _source_module_scope_lines(source)
        if top_level_only
        else [(line, True) for line in source.splitlines()]
    )
    for raw_line, at_module_scope in lines:
        if top_level_only and not at_module_scope:
            continue
        stripped = raw_line.strip()
        if not stripped.startswith("import "):
            continue
        rest = stripped[len("import ") :]
        if "#" in rest:
            rest = rest.split("#", 1)[0].strip()
        for item in rest.split(","):
            item = item.strip()
            if not item:
                continue
            if " as " in item:
                item = item.split(" as ", 1)[0].strip()
            if item:
                out.append(item)
    return out


def _iter_source_importlib_literal_specs(
    source: str,
    *,
    top_level_only: bool,
) -> list[str]:
    """Return literal modules from importlib.import_module("mod") calls.

    This is intentionally textual and narrow, matching the package-closure
    scanners above.  It exists so strict no-libpython builds can compile a
    sibling module named by a literal dynamic import without materialising a
    CPython module object.
    """
    out: list[str] = []
    marker = "importlib.import_module("
    lines = (
        _source_module_scope_lines(source)
        if top_level_only
        else [(line, True) for line in source.splitlines()]
    )
    for raw_line, at_module_scope in lines:
        if top_level_only and not at_module_scope:
            continue
        stripped = raw_line.strip()
        if "#" in stripped:
            stripped = stripped.split("#", 1)[0].strip()
        if marker not in stripped:
            continue
        rest = stripped.split(marker, 1)[1].strip()
        quote = rest[:1]
        if quote != "'" and quote != '"':
            continue
        rest = rest[1:]
        if quote not in rest:
            continue
        mod_name = rest.split(quote, 1)[0]
        if mod_name and not mod_name.startswith("."):
            out.append(mod_name)
    return out


def _append_source_import_from_spec(specs, stmt: str) -> None:
    stmt = stmt.strip()
    if not stmt.startswith("from "):
        return
    rest = stmt[5:]
    split_token = " import "
    split_idx = rest.find(split_token)
    if split_idx < 0:
        return
    module_spec = rest[:split_idx].strip()
    names_spec = rest[split_idx + len(split_token) :].strip()
    if not module_spec:
        return
    if "#" in names_spec:
        names_spec = names_spec.split("#", 1)[0].strip()
    if names_spec.startswith("(") and names_spec.endswith(")"):
        names_spec = names_spec[1:-1].strip()
    imported_names = []
    saw_star = False
    for raw_name in names_spec.split(","):
        raw_name = raw_name.strip()
        if not raw_name:
            continue
        if raw_name == "*":
            # ``from pkg import *`` — record the MODULE so it is discovered and
            # compiled natively (the star binding itself is handled by the
            # AST-based import lowering, which already binds all public exports
            # of a native sibling).  Without recording the spec, a ``*``-only
            # import produced no discovery entry, the module was never compiled,
            # and the import fell through to ``py_cpy_import`` (no-libpython gate
            # tripped).  No imported NAME is recorded, so the submodule-candidate
            # loops below add nothing spurious.  See investigation
            # docs/investigations/python-star-import-no-libpython.md
            saw_star = True
            continue
        if " as " in raw_name:
            raw_name = raw_name.split(" as ", 1)[0].strip()
        imported_names.append(raw_name)
    if imported_names or saw_star:
        specs.append((module_spec, imported_names))


def _iter_source_import_from_specs(
    source: str, *, top_level_only: bool
) -> list[tuple[str, list[str]]]:
    """Return ``[(module_spec, [imported_name...]), ...]`` from source text.

    Keep this intentionally narrow: package-closure discovery only needs
    textual ``from ... import ...`` statements, not full Python AST
    fidelity. Avoiding CPython AST objects here keeps the compiled
    bootstrap path away from fragile runtime attribute walks.
    """
    specs: list[tuple[str, list[str]]] = []
    pending = ""
    pending_active = False
    paren_depth = 0

    lines = (
        _source_module_scope_lines(source)
        if top_level_only
        else [(line, True) for line in source.splitlines()]
    )
    for raw_line, at_module_scope in lines:
        stripped = raw_line.strip()
        if not pending_active:
            if not stripped:
                continue
            if top_level_only and not at_module_scope:
                continue
            if not stripped.startswith("from "):
                continue
            pending = stripped
            pending_active = True
            paren_depth = stripped.count("(") - stripped.count(")")
            if paren_depth <= 0 and not stripped.endswith("\\"):
                _append_source_import_from_spec(specs, pending)
                pending = ""
                pending_active = False
        else:
            if "#" in stripped:
                stripped = stripped.split("#", 1)[0].rstrip()
            pending = pending + " " + stripped
            paren_depth += stripped.count("(") - stripped.count(")")
            if paren_depth <= 0 and not stripped.endswith("\\"):
                _append_source_import_from_spec(specs, pending)
                pending = ""
                pending_active = False

    if pending_active:
        _append_source_import_from_spec(specs, pending)
    return specs


def _without_attribute_error_handler_imports(source: str) -> str:
    """Leave strict package fallback imports for runtime diagnostics.

    Compatibility shims commonly try a modern attribute and import a legacy
    module only from ``except AttributeError``. Pulling that legacy module into
    the closed-world source set makes an unreachable Python-2 fallback part of
    the no-libpython claim. The import statement remains in compiled code; if
    the primary path really is unavailable it raises the normal strict import
    diagnostic instead of silently disappearing.
    """
    out: list[str] = []
    handler_indent = -1
    for raw_line in source.splitlines(keepends=True):
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip())
        if handler_indent >= 0 and stripped and indent <= handler_indent:
            handler_indent = -1
        if stripped.startswith("except AttributeError") and stripped.endswith(":"):
            handler_indent = indent
            out.append(raw_line)
            continue
        if (
            handler_indent >= 0
            and indent > handler_indent
            and (stripped.startswith("import ") or stripped.startswith("from "))
        ):
            out.append("\n" if raw_line.endswith("\n") else "")
            continue
        out.append(raw_line)
    return "".join(out)


def _package_import_targets(
    src_path: str,
    mod_name: str,
    *,
    root_dir: Optional[str] = None,
    top_level_only: bool = False,
    include_relative: bool = True,
    include_same_package_absolute: bool = True,
) -> list[tuple[str, str]]:
    """Return package-local import targets for ``src_path``.

    This includes:
    - relative imports such as ``from .foo import bar``
    - absolute same-package imports such as ``from pcc.cli_core import cli_main``

    The second form is kept intentionally narrow: only imports whose module path
    starts with the current package root are considered, so stdlib / third-party
    absolute imports still stay out of the native source closure.
    """
    if "." not in mod_name and os.path.basename(src_path) != "__init__.py":
        return []
    if root_dir is None:
        root_dir = _module_root_from_src(src_path, mod_name)

    with open(src_path, "r", encoding="utf-8") as f:
        source = f.read()
    source = _without_attribute_error_handler_imports(source)

    current_pkg = _package_parts_for_module(src_path, mod_name)
    package_root = mod_name.split(".")[0]
    targets: list[tuple[str, str]] = []
    seen_targets: set[str] = set()
    import_specs = _iter_source_import_from_specs(
        source,
        top_level_only=top_level_only,
    )
    for module_spec, imported_names in import_specs:
        candidate_mods: list[str] = []
        level = 0
        while level < len(module_spec) and module_spec[level] == ".":
            level += 1
        module_name = module_spec[level:]
        if level > 0 and include_relative:
            up = level - 1
            if up > len(current_pkg):
                continue
            base_pkg = current_pkg[: len(current_pkg) - up]
            if module_name:
                candidate_mods.append(
                    _join_dotted_parts(base_pkg + module_name.split("."))
                )
                for imported_name in imported_names:
                    candidate_mods.append(
                        _join_dotted_parts(
                            base_pkg + module_name.split(".") + imported_name.split(".")
                        )
                    )
            else:
                # ``from . import name`` may bind an attribute exported by the
                # package ``__init__.py`` rather than a sibling module.  Add
                # the package only when at least one imported name does not
                # resolve as a real module; otherwise preserve the bounded
                # sibling-only closure used by bootstrap.
                package_attribute_needed = not imported_names
                for imported_name in imported_names:
                    imported_mod = _join_dotted_parts(
                        base_pkg + imported_name.split(".")
                    )
                    candidate_mods.append(imported_mod)
                    if _resolve_module_src_for_import(root_dir, imported_mod) is None:
                        package_attribute_needed = True
                if package_attribute_needed:
                    candidate_mods.insert(0, _join_dotted_parts(base_pkg))
        elif module_name and include_same_package_absolute:
            mod_parts = module_name.split(".")
            if mod_parts and mod_parts[0] == package_root:
                candidate_mods.append(module_name)
                for imported_name in imported_names:
                    candidate_mods.append(
                        _join_dotted_parts(mod_parts + imported_name.split("."))
                    )
        for target_mod in candidate_mods:
            if not target_mod or target_mod in seen_targets:
                continue
            target_src = _resolve_module_src_for_import(root_dir, target_mod)
            if target_src is None:
                continue
            seen_targets.add(target_mod)
            targets.append((target_src, target_mod))
    return targets


def _collect_relative_module_closure(
    entry_src: str,
    *,
    include_same_package_absolute: bool = False,
    recurse_same_package_absolute: bool = False,
) -> tuple[list[str], list[str]]:
    """Return ``([src...], [module_name...])`` for a package entry plus
    recursively imported relative siblings and, optionally, a one-hop
    same-package absolute-import leaf set."""
    entry_src = str(os.path.abspath(entry_src))
    entry_mod = _module_name_from_src(entry_src)
    if "." not in entry_mod:
        root_dir = str(os.path.dirname(entry_src))
        ordered_srcs = [entry_src]
        ordered_mods = [entry_mod]
        seen = {entry_mod: entry_src}
        queue = [(entry_src, entry_mod)]
        while queue:
            src_path, mod_name = queue.pop(0)
            with open(src_path, "r", encoding="utf-8") as f:
                source = f.read()
            # The ENTRY module's imports are scanned INCLUDING indented ones
            # (module-level ``try:``/``if:`` blocks and lazy function-level
            # imports), so e.g. ``try: from p import real`` / ``if cond: import
            # p.sub`` is discovered and compiled natively instead of tripping
            # the no-libpython gate ("imports still lower through CPython
            # fallback").  ``add_candidate`` only adds names that resolve to a
            # real module file under the source root, so a missing / optional
            # C-extension import inside a ``try`` is still left to
            # ``py_cpy_import`` (its runtime ImportError is caught as usual).
            # The entry retains its existing all-indentation scan for lazy
            # imports.  Discovered dependencies use the module-scope scanner:
            # it includes eager imports nested in try/if/else suites while
            # excluding function/class-body lazy imports that are outside the
            # initialization claim.  Resolution remains bounded to real files
            # under root_dir / PCC_PACKAGE_SITE.
            is_entry = mod_name == entry_mod
            for target_src, target_mod in _top_level_import_targets(
                root_dir,
                source,
                top_level_only=not is_entry,
            ):
                if target_mod in seen:
                    continue
                target_src = str(os.path.abspath(target_src))
                seen[target_mod] = target_src
                ordered_srcs.append(target_src)
                ordered_mods.append(target_mod)
                queue.append((target_src, target_mod))
            local_root = _module_root_from_src(src_path, mod_name)
            for target_src, target_mod in _package_import_targets(
                src_path,
                mod_name,
                root_dir=local_root,
                top_level_only=not is_entry,
                include_relative=True,
                include_same_package_absolute=True,
            ):
                if target_mod in seen:
                    continue
                target_src = str(os.path.abspath(target_src))
                seen[target_mod] = target_src
                ordered_srcs.append(target_src)
                ordered_mods.append(target_mod)
                queue.append((target_src, target_mod))
        return ordered_srcs, ordered_mods

    root_dir = _module_root_from_src(entry_src, entry_mod)
    ordered_srcs = [entry_src]
    ordered_mods = [entry_mod]
    seen = {entry_mod: entry_src}
    queue = [(entry_src, entry_mod)]

    if include_same_package_absolute:
        for target_src, target_mod in _package_import_targets(
            entry_src,
            entry_mod,
            root_dir=root_dir,
            include_relative=False,
            include_same_package_absolute=True,
        ):
            if target_mod in seen:
                continue
            target_src = str(os.path.abspath(target_src))
            seen[target_mod] = target_src
            ordered_srcs.append(target_src)
            ordered_mods.append(target_mod)
            if recurse_same_package_absolute:
                queue.append((target_src, target_mod))

    while queue:
        src_path, mod_name = queue.pop(0)
        for target_src, target_mod in _package_import_targets(
            src_path,
            mod_name,
            root_dir=root_dir,
            include_relative=True,
            include_same_package_absolute=recurse_same_package_absolute,
        ):
            if target_mod in seen:
                continue
            target_src = str(os.path.abspath(target_src))
            seen[target_mod] = target_src
            ordered_srcs.append(target_src)
            ordered_mods.append(target_mod)
            queue.append((target_src, target_mod))

    return ordered_srcs, ordered_mods


def _collect_multi_source_relative_closure(
    src_paths: list[str],
    module_names: list[str],
    *,
    recursive_stdlib: bool = False,
) -> tuple[list[str], list[str]]:
    """Return explicit sources plus their package-local source closure.

    Relative imports retain the historical recursive scan used by the
    bootstrap compiler closure.  Module-scope absolute imports rooted in the
    same top-level package are included too; without that second edge,
    ``from pcc.diagnostics import DiagnosticSpan`` could emit an external class
    reference while omitting the module that owns its definition.  Absolute
    imports inside functions stay lazy and stdlib/third-party packages remain
    outside this closure.

    When ``recursive_stdlib=True`` (Issue 11.B.1), also pulls in any
    pure-Python stdlib module that's transitively imported by the seed
    set, allowing them to be compiled natively rather than routed
    through ``py_cpy_import``. Modules whose source can't be located,
    aren't ``.py`` files (C extensions / built-ins), or fail pcc's
    parser silently fall back to the dynamic path."""
    ordered_srcs: list[str] = []
    ordered_mods: list[str] = []
    for p in src_paths:
        ordered_srcs.append(str(os.path.abspath(p)))
    for m in module_names:
        ordered_mods.append(str(m))
    seen = {
        mod_name: src_path for src_path, mod_name in zip(ordered_srcs, ordered_mods)
    }
    queue = list(zip(ordered_srcs, ordered_mods))
    queue_i = 0
    while queue_i < len(queue):
        src_path, mod_name = queue[queue_i]
        queue_i += 1
        if "." not in mod_name:
            continue
        root_dir = _module_root_from_src(src_path, mod_name)
        targets = _package_import_targets(
            src_path,
            mod_name,
            root_dir=root_dir,
            include_relative=True,
            include_same_package_absolute=False,
        )
        targets.extend(
            _package_import_targets(
                src_path,
                mod_name,
                root_dir=root_dir,
                top_level_only=True,
                include_relative=False,
                include_same_package_absolute=True,
            )
        )
        for target_src, target_mod in targets:
            if target_mod in seen:
                continue
            target_src = str(os.path.abspath(target_src))
            seen[target_mod] = target_src
            ordered_srcs.append(target_src)
            ordered_mods.append(target_mod)
            queue.append((target_src, target_mod))

    if recursive_stdlib:
        _expand_recursive_stdlib(ordered_srcs, ordered_mods, seen)

    _expand_native_extension_module_object_ports(
        ordered_srcs,
        ordered_mods,
        seen,
    )

    return ordered_srcs, ordered_mods


def _filter_ir_scaffold_closure(
    src_paths: list[str],
    module_names: list[str],
    *,
    ir_scaffold_mode: str,
) -> tuple[list[str], list[str]]:
    """Map ON-mode IR scaffold imports to their real link provider.

    ``from pcc.llvm_capi.compat import ir`` is a compile-time scaffold
    import in ON mode. The linked stage binary needs definitions from
    ``pcc.llvm_capi.ir`` for the emitted ``user_pcc_llvm_capi_ir_*``
    calls, but it does not need ``compat.py`` or the LLVM-C/JIT
    ``binding.py`` module. Keeping those in the source closure is what
    drags libpython back into the self backend path.
    """
    if ir_scaffold_mode != "on":
        return src_paths, module_names
    skip = {"pcc.llvm_capi.compat", "pcc.llvm_capi.binding"}
    saw_compat = False
    need_ir_provider = False
    out_srcs: list[str] = []
    out_mods: list[str] = []
    seen: set[str] = set()
    root_dir = None

    limit = len(src_paths)
    if len(module_names) < limit:
        limit = len(module_names)
    i = 0
    while i < limit:
        src = src_paths[i]
        mod = module_names[i]
        if root_dir is None and (mod == "pcc" or mod.startswith("pcc.")):
            root_dir = _module_root_from_src(src, mod)
        if mod == "pcc.py_frontend.pipeline" or mod.startswith(
            "pcc.py_frontend.codegen"
        ):
            need_ir_provider = True
        if mod == "pcc.llvm_capi.compat":
            saw_compat = True
            need_ir_provider = True
            if "pcc.llvm_capi.ir" not in seen and root_dir is not None:
                ir_src = _resolve_module_src(root_dir, "pcc.llvm_capi.ir")
                if ir_src is not None:
                    out_srcs.append(str(os.path.abspath(ir_src)))
                    out_mods.append("pcc.llvm_capi.ir")
                    seen.add("pcc.llvm_capi.ir")
            i += 1
            continue
        if mod in skip:
            i += 1
            continue
        if mod in seen:
            i += 1
            continue
        out_srcs.append(src)
        out_mods.append(mod)
        seen.add(mod)
        i += 1
    if saw_compat or need_ir_provider:
        if "pcc.llvm_capi.ir" not in seen and root_dir is not None:
            ir_src = _resolve_module_src(root_dir, "pcc.llvm_capi.ir")
            if ir_src is not None:
                out_srcs.append(str(os.path.abspath(ir_src)))
                out_mods.append("pcc.llvm_capi.ir")
                seen.add("pcc.llvm_capi.ir")
    return out_srcs, out_mods


def _host_find_spec_origin(mod_name: str) -> str:
    py_cmd = str(os.environ.get("PCC_HOST_PYTHON", "") or "python3").strip()
    probe = (
        "import importlib.util,sys\n"
        "try:\n"
        "    spec=importlib.util.find_spec(sys.argv[1])\n"
        "except ModuleNotFoundError:\n"
        "    spec=None\n"
        "origin='' if spec is None or spec.origin is None else spec.origin\n"
        "print(origin)"
    )
    try:
        out = subprocess.check_output([py_cmd, "-c", probe, mod_name], text=True)
    except Exception:
        return ""
    return out.strip()


_HOST_STDLIB_ROOTS_CACHE: Optional[list[str]] = None
_HOST_SITE_ROOTS_CACHE: Optional[list[str]] = None


def _host_sysconfig_roots(keys: list[str]) -> list[str]:
    py_cmd = str(os.environ.get("PCC_HOST_PYTHON", "") or "python3").strip()
    probe = (
        "import os,sys,sysconfig\n"
        "paths=sysconfig.get_paths()\n"
        "for key in sys.argv[1:]:\n"
        "    value=paths.get(key,'') or ''\n"
        "    if value:\n"
        "        print(os.path.realpath(os.path.abspath(value)))"
    )
    try:
        out = subprocess.check_output([py_cmd, "-c", probe] + list(keys), text=True)
    except Exception:
        return []
    roots: list[str] = []
    for raw in out.splitlines():
        value = str(raw or "").strip()
        if value:
            _append_unique_path(roots, value)
    return roots


def _host_stdlib_roots() -> list[str]:
    global _HOST_STDLIB_ROOTS_CACHE
    if _HOST_STDLIB_ROOTS_CACHE is None:
        _HOST_STDLIB_ROOTS_CACHE = _host_sysconfig_roots(["stdlib", "platstdlib"])
    return list(_HOST_STDLIB_ROOTS_CACHE)


def _host_site_roots() -> list[str]:
    global _HOST_SITE_ROOTS_CACHE
    if _HOST_SITE_ROOTS_CACHE is None:
        _HOST_SITE_ROOTS_CACHE = _host_sysconfig_roots(["purelib", "platlib"])
    return list(_HOST_SITE_ROOTS_CACHE)


def _append_unique_path(paths: list[str], path: Optional[str]) -> None:
    if path is None:
        return
    path = str(path or "").strip()
    if not path:
        return
    path = os.path.abspath(path)
    for existing in paths:
        if existing == path:
            return
    paths.append(path)


def _path_is_under(path: str, root: str) -> bool:
    if not path or not root:
        return False
    normalized = os.path.realpath(os.path.abspath(path))
    normalized_root = os.path.realpath(os.path.abspath(root))
    try:
        return os.path.commonpath([normalized_root, normalized]) == normalized_root
    except ValueError:
        return False


def _path_is_under_any(path: str, roots: list[str]) -> bool:
    for root in roots:
        if _path_is_under(path, root):
            return True
    return False


def _host_origin_is_stdlib_py(origin: str) -> bool:
    if origin == "" or origin == "built-in":
        return False
    if not origin.endswith(".py"):
        return False
    if _path_is_under_any(origin, _host_site_roots()):
        return False
    return _path_is_under_any(origin, _host_stdlib_roots())


def _append_pcc_package_dir_candidate(paths: list[str], path: Optional[str]) -> None:
    if path is None:
        return
    path = str(path or "").strip()
    if not path:
        return
    path = os.path.abspath(path)
    _append_unique_path(paths, path)
    _append_unique_path(paths, os.path.join(path, "pcc"))
    if os.path.basename(path) == "py_stdlib":
        _append_unique_path(paths, os.path.dirname(path))


def _append_pcc_package_dir_ancestors(paths: list[str], path: Optional[str]) -> None:
    if path is None:
        return
    path = str(path or "").strip()
    if not path:
        return
    cur = os.path.abspath(path)
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)
    while cur:
        _append_pcc_package_dir_candidate(paths, cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent


def _pcc_package_dir_has_native_stdlib(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "py_stdlib", "__init__.py"))


def _pcc_package_dir_candidates() -> list[str]:
    candidates: list[str] = []
    _append_pcc_package_dir_candidate(
        candidates,
        os.environ.get("PCC_PY_STDLIB_ROOT"),
    )
    _append_pcc_package_dir_candidate(
        candidates,
        os.environ.get("PCC_SOURCE_ROOT"),
    )
    _append_pcc_package_dir_candidate(
        candidates,
        os.environ.get("PCC_REPO_ROOT"),
    )
    _append_pcc_package_dir_candidate(candidates, _PCC_DIR)
    _append_pcc_package_dir_candidate(candidates, _PIPELINE_DIR)
    _append_pcc_package_dir_candidate(candidates, os.path.dirname(_PY_RUNTIME_DIR))
    try:
        if len(sys.argv) > 0:
            _append_pcc_package_dir_ancestors(candidates, sys.argv[0])
    except Exception:
        pass
    try:
        _append_pcc_package_dir_ancestors(candidates, sys.executable)
    except Exception:
        pass
    _append_pcc_package_dir_ancestors(candidates, os.getcwd())
    out: list[str] = []
    for candidate in candidates:
        if _pcc_package_dir_has_native_stdlib(candidate):
            _append_unique_path(out, candidate)
    return out


def _locate_stdlib_module_source(mod_name: str) -> Optional[str]:
    """Resolve ``mod_name`` to a ``.py`` source path for the recursive
    stdlib walker (Issue 11.C.1).

    Search order:
    1. ``pcc/py_stdlib/<name>.py`` — first-class pcc-native stdlib
       ports used by normal CPython spellings such as ``import string``.
    2. ``pcc/stdlib/<name>.py`` — legacy port registry kept for older
       struct/_float_bits tests.
    3. CPython's stdlib via an external ``python3`` find-spec probe.

    Returns ``None`` for built-ins, C extensions, or modules that
    can't be located.
    """

    def _port_candidates(root: str) -> list[str]:
        rel = mod_name.replace(".", os.sep)
        return [
            os.path.join(root, f"{rel}.py"),
            os.path.join(root, rel, "__init__.py"),
            # Legacy flat dotted filename form.
            os.path.join(root, f"{mod_name}.py"),
        ]

    for pcc_package_dir in _pcc_package_dir_candidates():
        pcc_py_stdlib_dir = os.path.join(pcc_package_dir, "py_stdlib")
        for pcc_port in _port_candidates(pcc_py_stdlib_dir):
            if os.path.isfile(pcc_port):
                return pcc_port
        pcc_stdlib_dir = os.path.join(
            pcc_package_dir,
            "stdlib",
        )
        for pcc_port in _port_candidates(pcc_stdlib_dir):
            if os.path.isfile(pcc_port):
                return pcc_port
    try:
        origin = _host_find_spec_origin(mod_name)
    except Exception:
        return None
    if not _host_origin_is_stdlib_py(origin):
        return None
    return origin


def _native_stdlib_root_for_path(path: str) -> Optional[str]:
    normalized = os.path.abspath(path)
    for pcc_package_dir in _pcc_package_dir_candidates():
        for dirname in ("py_stdlib", "stdlib"):
            root = os.path.abspath(os.path.join(pcc_package_dir, dirname))
            try:
                common = os.path.commonpath([root, normalized])
            except ValueError:
                continue
            if common == root:
                return root
    return None


def _pcc_log_channel_enabled(channel: str) -> bool:
    raw = str(os.environ.get("PCC_LOG", "") or "").strip()
    if not raw:
        return False
    normalized = raw.lower()
    if normalized in ("1", "true", "yes", "on", "all"):
        return True
    for part in normalized.replace(";", ",").split(","):
        item = part.strip()
        if item == channel or item == "all":
            return True
    return False


def _pcc_json_escape(value: object) -> str:
    text = str(value or "")
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


def _pcc_import_log_line(
    *,
    module: str,
    classification: str,
    source: str,
    provider: Optional[str] = None,
) -> str:
    top = module.split(".")[0] if module else ""
    provider_text = "" if provider is None else provider
    native = classification in (
        "compile_time_only",
        "native_user_module",
        "builtin_native_dispatch",
        "native_stdlib",
    )
    return (
        "{"
        '"schema":"pcc.import_log.v1",'
        '"category":"import",'
        '"event":"route",'
        '"module":"' + _pcc_json_escape(module) + '",'
        '"top":"' + _pcc_json_escape(top) + '",'
        '"classification":"' + _pcc_json_escape(classification) + '",'
        '"native":' + ("true" if native else "false") + ","
        '"provider":"' + _pcc_json_escape(provider_text) + '",'
        '"source":"' + _pcc_json_escape(source) + '"'
        "}"
    )


def _pcc_emit_import_log(
    *,
    module: str,
    classification: str,
    source: str,
    provider: Optional[str] = None,
) -> None:
    if not _pcc_log_channel_enabled("import"):
        return
    line = _pcc_import_log_line(
        module=module,
        classification=classification,
        source=source,
        provider=provider,
    )
    target = str(os.environ.get("PCC_LOG_FILE", "") or "")
    if target and target != "-":
        with open(target, "a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
        return
    sys.stderr.write(line + "\n")


def _record_import_classification(
    mod_name: str,
    classification: str,
    *,
    source: str = "classify",
    provider: Optional[str] = None,
) -> str:
    _pcc_emit_import_log(
        module=mod_name,
        classification=classification,
        source=source,
        provider=provider,
    )
    return classification


def _classify_python_import(
    mod_name: str,
    *,
    native_modules: Optional[set[str]] = None,
) -> str:
    """Classify an import without changing the public CPython spelling.

    Return values are deliberately stable strings so tests and bootstrap
    diagnostics can assert the routing boundary:

    - ``compile_time_only``
    - ``native_user_module``
    - ``builtin_native_dispatch``
    - ``native_stdlib``
    - ``cpython_fallback``
    """
    if not mod_name:
        return _record_import_classification(
            mod_name,
            "cpython_fallback",
            source="empty",
        )
    if mod_name in _TEST_FACADE_IMPORT_MODULES:
        return _record_import_classification(
            mod_name,
            "compile_time_only",
            source="test_facade",
        )
    top = mod_name.split(".")[0]
    if top in _COMPILE_TIME_ONLY_IMPORT_MODULES:
        return _record_import_classification(
            mod_name,
            "compile_time_only",
            source="compile_time_only",
        )
    if native_modules is not None and mod_name in native_modules:
        return _record_import_classification(
            mod_name,
            "native_user_module",
            source="native_modules",
        )
    if mod_name in _NATIVE_BUILTIN_IMPORTS or top in _NATIVE_BUILTIN_IMPORTS:
        return _record_import_classification(
            mod_name,
            "builtin_native_dispatch",
            source="builtin_native_dispatch",
        )
    located = _locate_stdlib_module_source(mod_name)
    if located is not None and _native_stdlib_root_for_path(located) is not None:
        return _record_import_classification(
            mod_name,
            "native_stdlib",
            source="native_stdlib",
            provider=located,
        )
    return _record_import_classification(
        mod_name,
        "cpython_fallback",
        source="missing_native_provider",
        provider=located,
    )


def _source_uses_native_stdlib(src_path: str) -> bool:
    # A package can call a small factory during module initialization whose
    # body imports a pcc-owned stdlib port (simplejson's OrderedDict chooser is
    # one real shape).  Scan function bodies for the bounded decision to turn
    # on recursive stdlib closure; the closure itself still admits lazy
    # imports only when their provider is owned under pcc/py_stdlib.
    for mod_name in _stdlib_absolute_imports_in(
        src_path,
        include_function_bodies=True,
    ):
        if _classify_python_import(mod_name) == "native_stdlib":
            return True
    return False


def _sources_use_native_stdlib(src_paths: list[str]) -> bool:
    for src_path in src_paths:
        if _source_uses_native_stdlib(src_path):
            return True
    return False


def _source_pcc_native_extension_paths(src_path: str) -> list[str]:
    """Installed pcc-native extension artifacts named by one source file."""
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add_module(module_name: str) -> None:
        path = _resolve_pcc_native_extension_path(module_name)
        if path is None or path in seen:
            return
        seen.add(path)
        out.append(path)

    for module_name in _iter_source_import_specs(
        source,
        top_level_only=False,
    ):
        add_module(module_name)
    for module_name, imported_names in _iter_source_import_from_specs(
        source,
        top_level_only=False,
    ):
        if module_name.startswith("."):
            continue
        add_module(module_name)
        for imported_name in imported_names:
            add_module(module_name + "." + imported_name)
    return out


def _source_imports_pcc_native_extension(src_path: str) -> bool:
    """Whether source names an installed pcc-native extension module."""
    return bool(_source_pcc_native_extension_paths(src_path))


def _is_ascii_module_candidate(text: str) -> bool:
    if not text or len(text) > 512:
        return False
    parts = text.split(".")
    for part in parts:
        if not part:
            return False
        first = part[0]
        if not (first == "_" or ("a" <= first <= "z") or ("A" <= first <= "Z")):
            return False
        for ch in part[1:]:
            if not (
                ch == "_"
                or ("a" <= ch <= "z")
                or ("A" <= ch <= "Z")
                or ("0" <= ch <= "9")
            ):
                return False
    return True


def _native_extension_literal_module_candidates(path: str) -> list[str]:
    """Extract bounded ASCII identifier tokens from a native artifact.

    The result is only a candidate set. The closure caller requires a real
    Python source provider under a configured package root before accepting a
    token, which filters C symbols, source filenames, diagnostics, and other
    binary strings without relying on an external ``strings`` process.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []

    out: list[str] = []
    seen: set[str] = set()
    token_chars: list[str] = []
    token_too_long = False
    i = 0
    while i <= len(data):
        byte = data[i] if i < len(data) else 0
        is_token_byte = (
            byte == 46
            or byte == 95
            or 48 <= byte <= 57
            or 65 <= byte <= 90
            or 97 <= byte <= 122
        )
        if is_token_byte:
            if len(token_chars) < 512:
                token_chars.append(chr(byte))
            else:
                token_too_long = True
            i += 1
            continue
        if token_chars and not token_too_long:
            candidate = "".join(token_chars)
            if candidate not in seen and _is_ascii_module_candidate(candidate):
                seen.add(candidate)
                out.append(candidate)
        token_chars = []
        token_too_long = False
        i += 1
    return out


def _expand_native_extension_module_object_ports(
    ordered_srcs: list[str],
    ordered_mods: list[str],
    seen: dict[str, str],
) -> None:
    """Publish explicitly imported builtin ports beside native extensions.

    Compiler-recognized builtins normally lower directly and therefore do not
    need runtime module objects.  A pcc-native C extension can observe them via
    ``PyImport_ImportModule``, though.  When such an extension is in the source
    graph, add only builtin modules explicitly imported by that graph and only
    when a pcc-Python port exists.  They then use the ordinary compiled-sibling
    registry; no requesting-package dispatch or C semantic module is added.
    """
    extension_paths: list[str] = []
    seen_extension_paths: set[str] = set()
    for src in ordered_srcs:
        for path in _source_pcc_native_extension_paths(src):
            if path in seen_extension_paths:
                continue
            seen_extension_paths.add(path)
            extension_paths.append(path)
    if not extension_paths:
        return

    package_queue: list[tuple[str, str]] = []
    for extension_path in extension_paths:
        for module_name in _native_extension_literal_module_candidates(extension_path):
            # A dotted spelling plus a source provider under an explicit
            # package root is the minimum evidence that a binary token is a
            # module dependency rather than a C symbol or diagnostic word.
            if "." not in module_name or module_name in seen:
                continue
            provider = None
            for site_root in _package_site_roots():
                provider = _resolve_module_src(site_root, module_name)
                if provider is not None:
                    break
            if provider is None:
                continue
            provider = str(os.path.abspath(provider))
            seen[module_name] = provider
            ordered_srcs.append(provider)
            ordered_mods.append(module_name)
            package_queue.append((provider, module_name))

    queue_i = 0
    while queue_i < len(package_queue):
        src_path, mod_name = package_queue[queue_i]
        queue_i += 1
        root_dir = _module_root_from_src(src_path, mod_name)
        for target_src, target_mod in _package_import_targets(
            src_path,
            mod_name,
            root_dir=root_dir,
            top_level_only=True,
            include_relative=True,
            include_same_package_absolute=True,
        ):
            if target_mod in seen:
                continue
            target_src = str(os.path.abspath(target_src))
            seen[target_mod] = target_src
            ordered_srcs.append(target_src)
            ordered_mods.append(target_mod)
            package_queue.append((target_src, target_mod))

    seed_srcs = list(ordered_srcs)
    for src_path in seed_srcs:
        for module_name in _stdlib_absolute_imports_in(src_path):
            top = module_name.split(".", 1)[0]
            if module_name in seen or top not in _NATIVE_BUILTIN_IMPORTS:
                continue
            provider = _locate_stdlib_module_source(module_name)
            if provider is None or _native_stdlib_root_for_path(provider) is None:
                continue
            provider = str(os.path.abspath(provider))
            seen[module_name] = provider
            ordered_srcs.append(provider)
            ordered_mods.append(module_name)


def _stdlib_absolute_imports_in(
    src_path: str,
    *,
    include_function_bodies: bool = False,
) -> list[str]:
    """Return absolute imports reachable during module initialization.

    Function bodies are normally excluded because eagerly pulling every lazy
    import turns a small package into the transitive closure of optional
    helpers (for example pydoc -> http.server -> email).  Callers may request
    them for the narrower pcc-owned-provider scan; host stdlib expansion still
    uses the default module-initialization boundary.
    """
    from .py_ast import ClassDef as _ClassDef
    from .py_ast import ExceptHandler as _ExceptHandler
    from .py_ast import For as _For
    from .py_ast import FuncDef as _FuncDef
    from .py_ast import If as _If
    from .py_ast import Import as _Import
    from .py_ast import ImportFrom as _ImportFrom
    from .py_ast import Try as _Try
    from .py_ast import While as _While
    from .py_ast import With as _With
    from ..parse.py_lift import parse_and_lift

    with open(src_path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        ast_mod = parse_and_lift(text, src_path, "<scan>")
    except Exception:
        return []
    out: list[str] = []

    pending = [_py_ast_field_value(ast_mod, "body", ())]
    while pending:
        stmts = pending.pop()
        for stmt in stmts:
            if _closed_world_is_node(stmt, _Import):
                for mod_name, _ in _py_ast_field_value(stmt, "names", ()):
                    out.append(mod_name)
            elif _closed_world_is_node(stmt, _ImportFrom):
                module = _py_ast_field_value(stmt, "module", None)
                level = _py_ast_field_value(stmt, "level", 0)
                names = _py_ast_field_value(stmt, "names", ())
                if module is not None and level == 0:
                    # `from X import a, b` where every name is in
                    # _COMPILE_TIME_ONLY_IMPORT_FROMS[X] is a compile-time
                    # macro / decorator import (e.g. `from dataclasses import
                    # dataclass, field`). Don't treat it as evidence that the
                    # source uses module X at runtime — otherwise the native
                    # stdlib closure pulls in pcc/py_stdlib/X.py and forces
                    # libpython through any py_cpy_* fallbacks in that port.
                    compile_only = _COMPILE_TIME_ONLY_IMPORT_FROMS.get(module)
                    if compile_only is not None:
                        compile_only_only = True
                        for alias_name, _ in names:
                            if alias_name not in compile_only:
                                compile_only_only = False
                                break
                        if compile_only_only:
                            pass
                        else:
                            out.append(module)
                    else:
                        out.append(module)
            if _closed_world_is_node(stmt, (_ClassDef, _With, _ExceptHandler)) or (
                include_function_bodies and _closed_world_is_node(stmt, _FuncDef)
            ):
                pending.append(_py_ast_field_value(stmt, "body", ()))
            elif _closed_world_is_node(stmt, (_If, _While, _For)):
                pending.append(_py_ast_field_value(stmt, "else_body", ()))
                pending.append(_py_ast_field_value(stmt, "body", ()))
            elif _closed_world_is_node(stmt, _Try):
                pending.append(_py_ast_field_value(stmt, "finally_body", ()))
                pending.append(_py_ast_field_value(stmt, "else_body", ()))
                pending.append(_py_ast_field_value(stmt, "handlers", ()))
                pending.append(_py_ast_field_value(stmt, "body", ()))
    return out


def _stdlib_module_compiles(src_path: str, mod_name: str) -> bool:
    """Fail-soft recursive-stdlib codegen probe."""
    if _native_stdlib_root_for_path(src_path) is not None:
        return True
    from .type_infer import infer_module
    from .codegen.layer1 import L1CodeGen
    from ..parse.py_lift import parse_and_lift

    try:
        with open(src_path, "r", encoding="utf-8") as f:
            text = f.read()
        ast_mod = parse_and_lift(text, src_path, mod_name)
        typed = infer_module(ast_mod)
        cg = L1CodeGen(typed)
        cg.generate(typed)
        return True
    except Exception:
        return False


def _expand_recursive_stdlib(
    ordered_srcs: list[str],
    ordered_mods: list[str],
    seen: dict[str, str],
) -> None:
    """Issue 11.B.1: pull pure-Python stdlib modules transitively into
    the native compile set. In-place: appends to ordered_srcs /
    ordered_mods.

    Walks the seed sources, looks up each absolute non-relative,
    non-whitelisted import via ``importlib.util.find_spec``. A module
    is added to the closure only if pcc can BOTH parse AND codegen
    it (Issue 11.B.2 — fail-soft). Modules that parse but fail
    codegen are excluded; their importers fall back to
    ``py_cpy_import`` for them.
    """
    queue = []
    for mod_name in ordered_mods:
        queue.append(mod_name)
    failures: list[str] = []

    while queue:
        cur_mod = queue.pop(0)
        cur_src = seen.get(cur_mod)
        if cur_src is None:
            continue
        import_names = _stdlib_absolute_imports_in(cur_src)
        # Include lazy imports only for first-class pcc-owned stdlib providers.
        # This covers module-init factories without admitting arbitrary host
        # stdlib/optional dependency trees into the no-libpython closure.
        for lazy_name in _stdlib_absolute_imports_in(
            cur_src,
            include_function_bodies=True,
        ):
            if lazy_name in import_names:
                continue
            lazy_provider = _locate_stdlib_module_source(lazy_name)
            if (
                lazy_provider is not None
                and _native_stdlib_root_for_path(lazy_provider) is not None
            ):
                import_names.append(lazy_name)
        for import_name in import_names:
            top = import_name.split(".")[0]
            if (
                import_name in seen
                or import_name in failures
                or import_name in _TEST_FACADE_IMPORT_MODULES
                or top in _COMPILE_TIME_ONLY_IMPORT_MODULES
                or top in _NATIVE_BUILTIN_IMPORTS
                or import_name in _SCAFFOLD_IMPORT_MODULES
            ):
                continue
            # Skip pcc internals UNLESS we're pulling pcc.stdlib (the
            # port registry — Issue 11.C.1). Other pcc.* modules are
            # already handled by the relative-import walker for the
            # bootstrap closure.
            if top == "pcc" and not import_name.startswith("pcc.stdlib."):
                continue
            target_src = _locate_stdlib_module_source(import_name)
            if target_src is None:
                continue
            if not _stdlib_module_compiles(target_src, import_name):
                # Issue 11.B.2: parse-OK but codegen-FAIL. Fall back to
                # py_cpy_import for this module by NOT adding it to
                # the native closure.
                failures.append(import_name)
                continue
            seen[import_name] = target_src
            ordered_srcs.append(target_src)
            ordered_mods.append(import_name)
            queue.append(import_name)


def _relative_import_targets(
    src_path: str,
    mod_name: str,
    *,
    root_dir: Optional[str] = None,
    top_level_only: bool = False,
) -> list[tuple[str, str]]:
    """Return package-local import targets for init ordering."""
    return _package_import_targets(
        src_path,
        mod_name,
        root_dir=root_dir,
        top_level_only=top_level_only,
    )


def _order_module_init_deps_for(
    mod_name: str,
    module_to_src,
    module_set,
    dep_cache,
) -> list[str]:
    cached = dep_cache.get(mod_name)
    if cached is not None:
        return cached
    src_path = module_to_src[mod_name]
    deps: list[str] = []
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError:
        source = ""

    def add_dep(dep_mod: str) -> None:
        if dep_mod in module_set and dep_mod != mod_name and dep_mod not in deps:
            deps.append(dep_mod)

    for dep_mod in _iter_source_import_specs(source, top_level_only=True):
        add_dep(dep_mod)
        top_mod = dep_mod.split(".", 1)[0]
        add_dep(top_mod)
    for module_spec, imported_names in _iter_source_import_from_specs(
        source,
        top_level_only=True,
    ):
        if module_spec.startswith("."):
            continue
        add_dep(module_spec)
        for imported_name in imported_names:
            if imported_name and imported_name != "*":
                add_dep(module_spec + "." + imported_name)

    for _dep_src, dep_mod in _relative_import_targets(
        src_path,
        mod_name,
        top_level_only=True,
    ):
        resolved_dep = dep_mod
        if (
            dep_mod == "pcc.llvm_capi.compat"
            and dep_mod not in module_set
            and "pcc.llvm_capi.ir" in module_set
        ):
            # ON-mode scaffold filtering replaces compat with the real
            # pcc.llvm_capi.ir provider. Preserve the top-init dependency
            # so IR type class objects exist before runtime_abi/layer1 use
            # VoidType(), IntType(), FunctionType(), etc.
            resolved_dep = "pcc.llvm_capi.ir"
        if resolved_dep in module_set and resolved_dep != mod_name:
            deps.append(resolved_dep)
    dep_cache[mod_name] = deps
    return deps


def _order_module_inits(
    src_paths: list[str],
    module_names: list[str],
    entry_module: str,
) -> list[str]:
    """Return a dependency-first order for sibling module top-inits."""
    if len(module_names) <= 1:
        return []
    module_to_src = {
        mod_name: str(os.path.abspath(src_path))
        for src_path, mod_name in zip(src_paths, module_names)
    }
    module_set = set(module_names)
    dep_cache: dict[str, list[str]] = {}
    ordered: list[str] = []
    visiting: set[str] = set()
    emitted: set[str] = set()

    roots = [entry_module]
    for mod_name in module_names:
        if mod_name != entry_module:
            roots.append(mod_name)
    for root in roots:
        stack = [(root, False)]
        while stack:
            mod_name, expanded = stack.pop()
            if mod_name in emitted:
                continue
            if expanded:
                visiting.discard(mod_name)
                emitted.add(mod_name)
                if mod_name != entry_module:
                    ordered.append(mod_name)
                continue
            if mod_name in visiting:
                continue
            visiting.add(mod_name)
            stack.append((mod_name, True))
            deps = _order_module_init_deps_for(
                mod_name,
                module_to_src,
                module_set,
                dep_cache,
            )
            dep_i = len(deps) - 1
            while dep_i >= 0:
                dep_mod = deps[dep_i]
                if dep_mod != entry_module and dep_mod not in emitted:
                    stack.append((dep_mod, False))
                dep_i -= 1
    return ordered


def _export_param_types(args):
    """Return normalized runtime param types for cross-module exports.

    Multi-file extern declarations only need the lowered runtime
    signature shape. Treat missing annotations as DynType and skip the
    bare ``*`` separator, matching codegen's own parameter handling.
    """
    param_tys = []
    for a in args:
        name = _py_ast_field_value(a, "name", "")
        if not isinstance(name, str) or name == "":
            continue
        ann = _export_annotation_or_none(a)
        param_tys.append(encode_type(ann) if ann is not None else ("dyn",))
    return param_tys


def _export_return_type(ret_ty):
    if ret_ty is None:
        return ("dyn",)
    return encode_type(ret_ty)


def _export_typed_int_unboxed_abi_mode() -> str:
    mode = os.environ.get("PCC_PYTHON_TYPED_INT_ABI", "auto").strip().lower()
    if mode == "0":
        return "off"
    if mode == "off":
        return "off"
    if mode == "false":
        return "off"
    if mode == "boxed":
        return "off"
    if mode == "unsafe-i64":
        return "unsafe-i64"
    if mode == "unsafe_i64":
        return "unsafe-i64"
    if mode == "raw-i64":
        return "unsafe-i64"
    if mode == "raw_i64":
        return "unsafe-i64"
    if mode == "i64":
        return "unsafe-i64"
    return "auto"


def _export_typed_int_unboxed_abi_enabled() -> bool:
    return _export_typed_int_unboxed_abi_mode() != "off"


def _export_int_literal_fits_i64(expr) -> bool:
    value = int(_py_ast_field_value(expr, "value", 0))
    return -(1 << 63) <= value <= (1 << 63) - 1


def _export_literal_value_or_none(expr):
    return _py_ast_field_value(expr, "value", None)


def _closed_world_node_kind(node) -> str:
    try:
        return type(node).__name__
    except AttributeError:
        return ""


def _closed_world_expected_kind(expected_type) -> str:
    try:
        name = expected_type.__name__
        if isinstance(name, str) and name:
            return name
    except Exception:
        pass
    try:
        text = str(expected_type)
    except Exception:
        return ""
    dot = text.rfind(".")
    end = text.rfind("'")
    if dot >= 0 and end > dot:
        return text[dot + 1 : end]
    return text


def _closed_world_is_node(node, expected_types) -> bool:
    if node is None:
        return False
    if isinstance(expected_types, tuple):
        for expected_type in expected_types:
            if _closed_world_is_node(node, expected_type):
                return True
        return False
    if isinstance(node, expected_types):
        return True
    expected_kind = _closed_world_expected_kind(expected_types)
    return expected_kind != "" and _closed_world_node_kind(node) == expected_kind


_PY_AST_FIELD_NAME_OVERRIDES = {
    "SourceSpan": ("file", "line", "col", "end_line", "end_col"),
    "Type": ("name",),
    "IntType": ("name", "width", "signed"),
    "FloatType": ("name", "width"),
    "ComplexType": ("name",),
    "BoolType": ("name",),
    "NoneType": ("name",),
    "StrType": ("name",),
    "BytesType": ("name",),
    "ByteArrayType": ("name",),
    "MemoryViewType": ("name",),
    "ListType": ("name", "elem"),
    "SetType": ("name", "elem"),
    "DictType": ("name", "key", "value"),
    "TupleType": ("name", "elems"),
    "FuncType": ("name", "params", "ret"),
    "ClassType": ("name", "module", "fields", "bases", "properties", "valueclass"),
    "ValueClassType": (
        "name",
        "module",
        "fields",
        "bases",
        "properties",
        "valueclass",
        "flattened",
        "nullable_fields",
    ),
    "DynType": ("name",),
    "Expr": ("span", "ty"),
    "IntLit": ("span", "ty", "value"),
    "FloatLit": ("span", "ty", "value"),
    "ComplexLit": ("span", "ty", "real", "imag"),
    "BoolLit": ("span", "ty", "value"),
    "NoneLit": ("span", "ty"),
    "StrLit": ("span", "ty", "value"),
    "BytesLit": ("span", "ty", "value"),
    "Name": ("span", "ty", "ident"),
    "BinOp": ("span", "ty", "op", "lhs", "rhs"),
    "UnaryOp": ("span", "ty", "op", "operand"),
    "Compare": ("span", "ty", "op", "lhs", "rhs"),
    "BoolExpr": ("span", "ty", "op", "left", "right"),
    "Call": ("span", "ty", "func", "args", "kwargs"),
    "Attr": ("span", "ty", "obj", "name"),
    "Subscript": ("span", "ty", "obj", "idx"),
    "Slice": ("span", "ty", "lo", "hi", "step"),
    "ListExpr": ("span", "ty", "elems"),
    "DictExpr": ("span", "ty", "pairs"),
    "TupleExpr": ("span", "ty", "elems"),
    "IfExpr": ("span", "ty", "cond", "then_e", "else_e"),
    "Lambda": ("span", "ty", "params", "body"),
    "Stmt": ("span",),
    "Assign": ("span", "targets", "value", "annotation"),
    "AugAssign": ("span", "target", "op", "value"),
    "ExprStmt": ("span", "expr"),
    "If": ("span", "cond", "body", "else_body"),
    "While": ("span", "cond", "body", "else_body"),
    "For": ("span", "target", "iter", "body", "else_body", "is_async"),
    "Return": ("span", "value"),
    "Pass": ("span",),
    "Break": ("span",),
    "Continue": ("span",),
    "Raise": ("span", "exc", "cause"),
    "Try": ("span", "body", "handlers", "else_body", "finally_body"),
    "ExceptHandler": ("exc_type", "name", "body", "span"),
    "With": ("span", "items", "body", "is_async"),
    "Import": ("span", "names"),
    "ImportFrom": ("span", "module", "names", "level"),
    "Global": ("span", "names"),
    "Nonlocal": ("span", "names"),
    "Delete": ("span", "targets"),
    "Arg": ("name", "annotation", "default", "kind", "has_default"),
    "FuncDef": (
        "span",
        "name",
        "args",
        "return_ty",
        "body",
        "decorators",
        "is_method",
        "is_async",
    ),
    "ClassDef": ("span", "name", "bases", "keywords", "body", "decorators"),
    "Module": ("name", "body", "docstring"),
}


_PY_AST_BASE_NAME_OVERRIDES = {
    "IntType": ("Type",),
    "FloatType": ("Type",),
    "ComplexType": ("Type",),
    "BoolType": ("Type",),
    "NoneType": ("Type",),
    "StrType": ("Type",),
    "BytesType": ("Type",),
    "ByteArrayType": ("Type",),
    "MemoryViewType": ("Type",),
    "ListType": ("Type",),
    "SetType": ("Type",),
    "DictType": ("Type",),
    "TupleType": ("Type",),
    "FuncType": ("Type",),
    "ClassType": ("Type",),
    "ValueClassType": ("ClassType",),
    "DynType": ("Type",),
    "IntLit": ("Expr",),
    "FloatLit": ("Expr",),
    "ComplexLit": ("Expr",),
    "BoolLit": ("Expr",),
    "NoneLit": ("Expr",),
    "StrLit": ("Expr",),
    "BytesLit": ("Expr",),
    "Name": ("Expr",),
    "BinOp": ("Expr",),
    "UnaryOp": ("Expr",),
    "Compare": ("Expr",),
    "BoolExpr": ("Expr",),
    "Call": ("Expr",),
    "Attr": ("Expr",),
    "Subscript": ("Expr",),
    "Slice": ("Expr",),
    "ListExpr": ("Expr",),
    "DictExpr": ("Expr",),
    "TupleExpr": ("Expr",),
    "IfExpr": ("Expr",),
    "Lambda": ("Expr",),
    "Assign": ("Stmt",),
    "AugAssign": ("Stmt",),
    "ExprStmt": ("Stmt",),
    "If": ("Stmt",),
    "While": ("Stmt",),
    "For": ("Stmt",),
    "Return": ("Stmt",),
    "Pass": ("Stmt",),
    "Break": ("Stmt",),
    "Continue": ("Stmt",),
    "Raise": ("Stmt",),
    "Try": ("Stmt",),
    "With": ("Stmt",),
    "Import": ("Stmt",),
    "ImportFrom": ("Stmt",),
    "Global": ("Stmt",),
    "Nonlocal": ("Stmt",),
    "Delete": ("Stmt",),
    "FuncDef": ("Stmt",),
    "ClassDef": ("Stmt",),
}


_PY_AST_FIELD_TYPE_OVERRIDES = {
    "SourceSpan": {
        "file": "str",
        "line": "int",
        "col": "int",
        "end_line": "int",
        "end_col": "int",
    },
    "Type": {"name": "str"},
    "IntType": {"name": "str", "width": "int", "signed": "bool"},
    "FloatType": {"name": "str", "width": "int"},
    "ComplexType": {"name": "str"},
    "BoolType": {"name": "str"},
    "NoneType": {"name": "str"},
    "StrType": {"name": "str"},
    "BytesType": {"name": "str"},
    "ByteArrayType": {"name": "str"},
    "MemoryViewType": {"name": "str"},
    "ListType": {"name": "str", "elem": "Type"},
    "SetType": {"name": "str", "elem": "Type"},
    "DictType": {"name": "str", "key": "Type", "value": "Type"},
    "TupleType": {"name": "str", "elems": "tuple[Type, ...]"},
    "FuncType": {"name": "str", "params": "tuple[Type, ...]", "ret": "Type"},
    "ClassType": {
        "name": "str",
        "module": "str",
        "fields": "tuple[tuple[str, Type], ...]",
        "bases": "tuple[ClassType, ...]",
        "properties": "tuple[tuple[str, Type], ...]",
        "valueclass": "bool",
    },
    "ValueClassType": {
        "name": "str",
        "module": "str",
        "fields": "tuple[tuple[str, Type], ...]",
        "bases": "tuple[ClassType, ...]",
        "properties": "tuple[tuple[str, Type], ...]",
        "valueclass": "bool",
        "flattened": "bool",
        "nullable_fields": "bool",
    },
    "DynType": {"name": "str"},
    "Expr": {"span": "SourceSpan", "ty": "Type"},
    "IntLit": {"span": "SourceSpan", "ty": "Type", "value": "int"},
    "FloatLit": {"span": "SourceSpan", "ty": "Type", "value": "float"},
    "ComplexLit": {
        "span": "SourceSpan",
        "ty": "Type",
        "real": "float",
        "imag": "float",
    },
    "BoolLit": {"span": "SourceSpan", "ty": "Type", "value": "bool"},
    "NoneLit": {"span": "SourceSpan", "ty": "Type"},
    "StrLit": {"span": "SourceSpan", "ty": "Type", "value": "str"},
    "BytesLit": {"span": "SourceSpan", "ty": "Type", "value": "bytes"},
    "Name": {"span": "SourceSpan", "ty": "Type", "ident": "str"},
    "BinOp": {
        "span": "SourceSpan",
        "ty": "Type",
        "op": "str",
        "lhs": "Expr",
        "rhs": "Expr",
    },
    "UnaryOp": {
        "span": "SourceSpan",
        "ty": "Type",
        "op": "str",
        "operand": "Expr",
    },
    "Compare": {
        "span": "SourceSpan",
        "ty": "Type",
        "op": "str",
        "lhs": "Expr",
        "rhs": "Expr",
    },
    "BoolExpr": {
        "span": "SourceSpan",
        "ty": "Type",
        "op": "str",
        "left": "Expr",
        "right": "Expr",
    },
    "Call": {
        "span": "SourceSpan",
        "ty": "Type",
        "func": "Expr",
        "args": "tuple[Expr, ...]",
        "kwargs": "tuple[tuple[str, Expr], ...]",
    },
    "Attr": {"span": "SourceSpan", "ty": "Type", "obj": "Expr", "name": "str"},
    "Subscript": {
        "span": "SourceSpan",
        "ty": "Type",
        "obj": "Expr",
        "idx": "Expr",
    },
    "Slice": {
        "span": "SourceSpan",
        "ty": "Type",
        "lo": "Expr",
        "hi": "Expr",
        "step": "Expr",
    },
    "ListExpr": {"span": "SourceSpan", "ty": "Type", "elems": "tuple[Expr, ...]"},
    "DictExpr": {
        "span": "SourceSpan",
        "ty": "Type",
        "pairs": "tuple[tuple[Expr, Expr], ...]",
    },
    "TupleExpr": {"span": "SourceSpan", "ty": "Type", "elems": "tuple[Expr, ...]"},
    "IfExpr": {
        "span": "SourceSpan",
        "ty": "Type",
        "cond": "Expr",
        "then_e": "Expr",
        "else_e": "Expr",
    },
    "Lambda": {
        "span": "SourceSpan",
        "ty": "Type",
        "params": "tuple[Arg, ...]",
        "body": "Expr",
    },
    "Stmt": {"span": "SourceSpan"},
    "Assign": {
        "span": "SourceSpan",
        "targets": "tuple[Expr, ...]",
        "value": "Expr",
        "annotation": "Type",
    },
    "AugAssign": {
        "span": "SourceSpan",
        "target": "Expr",
        "op": "str",
        "value": "Expr",
    },
    "ExprStmt": {"span": "SourceSpan", "expr": "Expr"},
    "If": {
        "span": "SourceSpan",
        "cond": "Expr",
        "body": "tuple[Stmt, ...]",
        "else_body": "tuple[Stmt, ...]",
    },
    "While": {
        "span": "SourceSpan",
        "cond": "Expr",
        "body": "tuple[Stmt, ...]",
        "else_body": "tuple[Stmt, ...]",
    },
    "For": {
        "span": "SourceSpan",
        "target": "Expr",
        "iter": "Expr",
        "body": "tuple[Stmt, ...]",
        "else_body": "tuple[Stmt, ...]",
        "is_async": "bool",
    },
    "Return": {"span": "SourceSpan", "value": "Expr"},
    "Pass": {"span": "SourceSpan"},
    "Break": {"span": "SourceSpan"},
    "Continue": {"span": "SourceSpan"},
    "Raise": {"span": "SourceSpan", "exc": "Expr", "cause": "Expr"},
    "Try": {
        "span": "SourceSpan",
        "body": "tuple[Stmt, ...]",
        "handlers": "tuple[ExceptHandler, ...]",
        "else_body": "tuple[Stmt, ...]",
        "finally_body": "tuple[Stmt, ...]",
    },
    "ExceptHandler": {
        "exc_type": "Expr",
        "name": "str",
        "body": "tuple[Stmt, ...]",
        "span": "SourceSpan",
    },
    "With": {
        "span": "SourceSpan",
        "items": "tuple[tuple[Expr, Expr], ...]",
        "body": "tuple[Stmt, ...]",
        "is_async": "bool",
    },
    "Import": {"span": "SourceSpan", "names": "tuple[tuple[str, str], ...]"},
    "ImportFrom": {
        "span": "SourceSpan",
        "module": "str",
        "names": "tuple[tuple[str, str], ...]",
        "level": "int",
    },
    "Global": {"span": "SourceSpan", "names": "tuple[str, ...]"},
    "Nonlocal": {"span": "SourceSpan", "names": "tuple[str, ...]"},
    "Delete": {"span": "SourceSpan", "targets": "tuple[Expr, ...]"},
    "Arg": {
        "name": "str",
        "annotation": "Type",
        "default": "Expr",
        "kind": "str",
        "has_default": "bool",
    },
    "FuncDef": {
        "span": "SourceSpan",
        "name": "str",
        "args": "tuple[Arg, ...]",
        "return_ty": "Type",
        "body": "tuple[Stmt, ...]",
        "decorators": "tuple[Expr, ...]",
        "is_method": "bool",
        "is_async": "bool",
    },
    "ClassDef": {
        "span": "SourceSpan",
        "name": "str",
        "bases": "tuple[Expr, ...]",
        "keywords": "tuple[tuple[str, Expr], ...]",
        "body": "tuple[Stmt, ...]",
        "decorators": "tuple[Expr, ...]",
    },
    "Module": {"name": "str", "body": "tuple[Stmt, ...]", "docstring": "str"},
}


def _py_ast_field_type_override(class_name: str, field_name: str):
    pairs = ()
    if class_name == "SourceSpan":
        pairs = (
            ("file", "str"),
            ("line", "int"),
            ("col", "int"),
            ("end_line", "int"),
            ("end_col", "int"),
        )
    elif class_name == "Type":
        pairs = (("name", "str"),)
    elif class_name == "Expr":
        pairs = (("span", "SourceSpan"), ("ty", "Type"))
    elif class_name == "Stmt":
        pairs = (("span", "SourceSpan"),)
    elif class_name == "Name":
        pairs = (("span", "SourceSpan"), ("ty", "Type"), ("ident", "str"))
    elif class_name == "Arg":
        pairs = (
            ("name", "str"),
            ("annotation", "Type"),
            ("default", "Expr"),
            ("kind", "str"),
            ("has_default", "bool"),
        )
    elif class_name == "FuncDef":
        pairs = (
            ("span", "SourceSpan"),
            ("name", "str"),
            ("args", "tuple[Arg, ...]"),
            ("return_ty", "Type"),
            ("body", "tuple[Stmt, ...]"),
            ("decorators", "tuple[Expr, ...]"),
            ("is_method", "bool"),
            ("is_async", "bool"),
        )
    elif class_name == "ClassDef":
        pairs = (
            ("span", "SourceSpan"),
            ("name", "str"),
            ("bases", "tuple[Expr, ...]"),
            ("keywords", "tuple[tuple[str, Expr], ...]"),
            ("body", "tuple[Stmt, ...]"),
            ("decorators", "tuple[Expr, ...]"),
        )
    elif class_name == "Assign":
        pairs = (
            ("span", "SourceSpan"),
            ("targets", "tuple[Expr, ...]"),
            ("value", "Expr"),
            ("annotation", "Type"),
        )
    elif class_name == "For":
        pairs = (
            ("span", "SourceSpan"),
            ("target", "Expr"),
            ("iter", "Expr"),
            ("body", "tuple[Stmt, ...]"),
            ("else_body", "tuple[Stmt, ...]"),
            ("is_async", "bool"),
        )
    elif class_name == "Return":
        pairs = (("span", "SourceSpan"), ("value", "Expr"))
    elif class_name == "ExprStmt":
        pairs = (("span", "SourceSpan"), ("expr", "Expr"))
    elif class_name == "If" or class_name == "While":
        pairs = (
            ("span", "SourceSpan"),
            ("cond", "Expr"),
            ("body", "tuple[Stmt, ...]"),
            ("else_body", "tuple[Stmt, ...]"),
        )
    elif class_name == "Call":
        pairs = (
            ("span", "SourceSpan"),
            ("ty", "Type"),
            ("func", "Expr"),
            ("args", "tuple[Expr, ...]"),
            ("kwargs", "tuple[tuple[str, Expr], ...]"),
        )
    elif class_name == "Attr":
        pairs = (
            ("span", "SourceSpan"),
            ("ty", "Type"),
            ("obj", "Expr"),
            ("name", "str"),
        )
    elif class_name == "TupleExpr" or class_name == "ListExpr":
        pairs = (
            ("span", "SourceSpan"),
            ("ty", "Type"),
            ("elems", "tuple[Expr, ...]"),
        )
    if pairs:
        for override_field_name, field_type_text in pairs:
            if override_field_name == field_name:
                return field_type_text
        return None
    for override_class_name, field_map in _PY_AST_FIELD_TYPE_OVERRIDES.items():
        if override_class_name != class_name:
            continue
        for override_field_name, field_type_text in field_map.items():
            if override_field_name == field_name:
                return field_type_text
        return None
    return None


def _py_ast_bytes_to_wire(value):
    items = []
    i = 0
    while i < len(value):
        items.append(int(value[i]))
        i += 1
    return {_PY_AST_WIRE_BYTES_KEY: items}


def _py_ast_to_wire(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return _py_ast_bytes_to_wire(value)
    kind = _closed_world_node_kind(value)
    if kind == "bytes":
        return _py_ast_bytes_to_wire(value)
    if isinstance(value, (tuple, list)):
        return [_py_ast_to_wire(item) for item in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out[str(key)] = _py_ast_to_wire(item)
        return out
    field_names = _PY_AST_FIELD_NAME_OVERRIDES.get(kind)
    if field_names is None:
        raise PyPipelineError("cannot serialize py_ast node kind " + kind)
    fields = {}
    for field_name in field_names:
        fields[field_name] = _py_ast_to_wire(
            _py_ast_field_value(value, field_name, None)
        )
    return {_PY_AST_WIRE_NODE_KEY: kind, "fields": fields}


def _py_ast_wire_bytes(items):
    if items is None:
        return b""
    raw = []
    for item in items:
        raw.append(int(item))
    return bytes(raw)


def _py_ast_wire_field(fields, name: str, default=None):
    if not isinstance(fields, dict):
        return default
    if name not in fields:
        return default
    return _py_ast_from_wire(fields.get(name))


def _py_ast_wire_tuple_field(fields, name: str):
    value = _py_ast_wire_field(fields, name, ())
    return () if value is None else value


def _py_ast_wire_bool_field(fields, name: str, default: bool):
    value = _py_ast_wire_field(fields, name, default)
    return default if value is None else value


def _py_ast_from_wire(value):
    if isinstance(value, dict):
        if _PY_AST_WIRE_BYTES_KEY in value:
            return _py_ast_wire_bytes(value.get(_PY_AST_WIRE_BYTES_KEY))
        kind = value.get(_PY_AST_WIRE_NODE_KEY)
        if isinstance(kind, str) and kind:
            return _py_ast_node_from_wire(kind, value.get("fields", {}))
        out = {}
        for key, item in value.items():
            out[str(key)] = _py_ast_from_wire(item)
        return out
    if isinstance(value, list):
        return tuple(_py_ast_from_wire(item) for item in value)
    return value


def _py_ast_node_from_wire(kind: str, fields):
    from . import py_ast as _pa

    if kind == "SourceSpan":
        return _pa.SourceSpan(
            _py_ast_wire_field(fields, "file", ""),
            _py_ast_wire_field(fields, "line", 0),
            _py_ast_wire_field(fields, "col", 0),
            _py_ast_wire_field(fields, "end_line", 0),
            _py_ast_wire_field(fields, "end_col", 0),
        )
    if kind == "Type":
        return _pa.Type(_py_ast_wire_field(fields, "name", ""))
    if kind == "IntType":
        return _pa.IntType(
            _py_ast_wire_field(fields, "name", "int"),
            _py_ast_wire_field(fields, "width", 64),
            _py_ast_wire_field(fields, "signed", True),
        )
    if kind == "FloatType":
        return _pa.FloatType(
            _py_ast_wire_field(fields, "name", "float"),
            _py_ast_wire_field(fields, "width", 64),
        )
    if kind == "ComplexType":
        return _pa.ComplexType(_py_ast_wire_field(fields, "name", "complex"))
    if kind == "BoolType":
        return _pa.BoolType(_py_ast_wire_field(fields, "name", "bool"))
    if kind == "NoneType":
        return _pa.NoneType(_py_ast_wire_field(fields, "name", "None"))
    if kind == "StrType":
        return _pa.StrType(_py_ast_wire_field(fields, "name", "str"))
    if kind == "BytesType":
        return _pa.BytesType(_py_ast_wire_field(fields, "name", "bytes"))
    if kind == "ByteArrayType":
        return _pa.ByteArrayType(_py_ast_wire_field(fields, "name", "bytearray"))
    if kind == "MemoryViewType":
        return _pa.MemoryViewType(_py_ast_wire_field(fields, "name", "memoryview"))
    if kind == "ListType":
        return _pa.ListType(
            _py_ast_wire_field(fields, "name", "list"),
            _py_ast_wire_field(fields, "elem"),
        )
    if kind == "SetType":
        return _pa.SetType(
            _py_ast_wire_field(fields, "name", "set"),
            _py_ast_wire_field(fields, "elem"),
        )
    if kind == "DictType":
        return _pa.DictType(
            _py_ast_wire_field(fields, "name", "dict"),
            _py_ast_wire_field(fields, "key"),
            _py_ast_wire_field(fields, "value"),
        )
    if kind == "TupleType":
        return _pa.TupleType(
            _py_ast_wire_field(fields, "name", "tuple"),
            _py_ast_wire_field(fields, "elems", ()),
        )
    if kind == "FuncType":
        return _pa.FuncType(
            _py_ast_wire_field(fields, "name", "func"),
            _py_ast_wire_field(fields, "params", ()),
            _py_ast_wire_field(fields, "ret"),
        )
    if kind == "ClassType":
        return _pa.ClassType(
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "module", ""),
            _py_ast_wire_tuple_field(fields, "fields"),
            _py_ast_wire_tuple_field(fields, "bases"),
            _py_ast_wire_tuple_field(fields, "properties"),
            _py_ast_wire_bool_field(fields, "valueclass", False),
        )
    if kind == "ValueClassType":
        return _pa.ValueClassType(
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "module", ""),
            _py_ast_wire_tuple_field(fields, "fields"),
            _py_ast_wire_tuple_field(fields, "bases"),
            _py_ast_wire_tuple_field(fields, "properties"),
            _py_ast_wire_bool_field(fields, "valueclass", True),
            _py_ast_wire_bool_field(fields, "flattened", True),
            _py_ast_wire_bool_field(fields, "nullable_fields", False),
        )
    if kind == "DynType":
        return _pa.DynType(_py_ast_wire_field(fields, "name", "dyn"))
    if kind == "IntLit":
        return _pa.IntLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", 0),
        )
    if kind == "FloatLit":
        return _pa.FloatLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", 0.0),
        )
    if kind == "ComplexLit":
        return _pa.ComplexLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "real", 0.0),
            _py_ast_wire_field(fields, "imag", 0.0),
        )
    if kind == "BoolLit":
        return _pa.BoolLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", False),
        )
    if kind == "NoneLit":
        return _pa.NoneLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
        )
    if kind == "StrLit":
        return _pa.StrLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", ""),
        )
    if kind == "BytesLit":
        return _pa.BytesLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", b""),
        )
    if kind == "Name":
        return _pa.Name(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "ident", ""),
        )
    if kind == "BinOp":
        return _pa.BinOp(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "lhs"),
            _py_ast_wire_field(fields, "rhs"),
        )
    if kind == "UnaryOp":
        return _pa.UnaryOp(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "operand"),
        )
    if kind == "Compare":
        return _pa.Compare(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "lhs"),
            _py_ast_wire_field(fields, "rhs"),
        )
    if kind == "BoolExpr":
        return _pa.BoolExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "left"),
            _py_ast_wire_field(fields, "right"),
        )
    if kind == "Call":
        return _pa.Call(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "func"),
            _py_ast_wire_field(fields, "args", ()),
            _py_ast_wire_field(fields, "kwargs", ()),
        )
    if kind == "Attr":
        return _pa.Attr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "obj"),
            _py_ast_wire_field(fields, "name", ""),
        )
    if kind == "Subscript":
        return _pa.Subscript(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "obj"),
            _py_ast_wire_field(fields, "idx"),
        )
    if kind == "Slice":
        return _pa.Slice(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "lo"),
            _py_ast_wire_field(fields, "hi"),
            _py_ast_wire_field(fields, "step"),
        )
    if kind == "ListExpr":
        return _pa.ListExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "elems", ()),
        )
    if kind == "DictExpr":
        return _pa.DictExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "pairs", ()),
        )
    if kind == "TupleExpr":
        return _pa.TupleExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "elems", ()),
        )
    if kind == "IfExpr":
        return _pa.IfExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "cond"),
            _py_ast_wire_field(fields, "then_e"),
            _py_ast_wire_field(fields, "else_e"),
        )
    if kind == "Lambda":
        return _pa.Lambda(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "params", ()),
            _py_ast_wire_field(fields, "body"),
        )
    if kind == "Assign":
        return _pa.Assign(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "targets", ()),
            _py_ast_wire_field(fields, "value"),
            _py_ast_wire_field(fields, "annotation"),
        )
    if kind == "AugAssign":
        return _pa.AugAssign(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "target"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "value"),
        )
    if kind == "ExprStmt":
        return _pa.ExprStmt(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "expr"),
        )
    if kind == "If":
        return _pa.If(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "cond"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "else_body", ()),
        )
    if kind == "While":
        return _pa.While(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "cond"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "else_body", ()),
        )
    if kind == "For":
        return _pa.For(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "target"),
            _py_ast_wire_field(fields, "iter"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "else_body", ()),
            _py_ast_wire_field(fields, "is_async", False),
        )
    if kind == "Return":
        return _pa.Return(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "value"),
        )
    if kind == "Pass":
        return _pa.Pass(_py_ast_wire_field(fields, "span"))
    if kind == "Break":
        return _pa.Break(_py_ast_wire_field(fields, "span"))
    if kind == "Continue":
        return _pa.Continue(_py_ast_wire_field(fields, "span"))
    if kind == "Raise":
        return _pa.Raise(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "exc"),
            _py_ast_wire_field(fields, "cause"),
        )
    if kind == "Try":
        return _pa.Try(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "handlers", ()),
            _py_ast_wire_field(fields, "else_body", ()),
            _py_ast_wire_field(fields, "finally_body", ()),
        )
    if kind == "ExceptHandler":
        return _pa.ExceptHandler(
            _py_ast_wire_field(fields, "exc_type"),
            _py_ast_wire_field(fields, "name"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "span"),
        )
    if kind == "With":
        return _pa.With(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "items", ()),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "is_async", False),
        )
    if kind == "Import":
        return _pa.Import(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "names", ()),
        )
    if kind == "ImportFrom":
        return _pa.ImportFrom(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "module", ""),
            _py_ast_wire_field(fields, "names", ()),
            _py_ast_wire_field(fields, "level", 0),
        )
    if kind == "Global":
        return _pa.Global(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "names", ()),
        )
    if kind == "Nonlocal":
        return _pa.Nonlocal(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "names", ()),
        )
    if kind == "Delete":
        return _pa.Delete(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "targets", ()),
        )
    if kind == "Arg":
        return _pa.Arg(
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "annotation"),
            _py_ast_wire_field(fields, "default"),
            _py_ast_wire_field(fields, "kind", "pos"),
            _py_ast_wire_field(fields, "has_default", False),
        )
    if kind == "FuncDef":
        return _pa.FuncDef(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "args", ()),
            _py_ast_wire_field(fields, "return_ty"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "decorators", ()),
            _py_ast_wire_field(fields, "is_method", False),
            _py_ast_wire_field(fields, "is_async", False),
        )
    if kind == "ClassDef":
        return _pa.ClassDef(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "bases", ()),
            _py_ast_wire_field(fields, "keywords", ()),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "decorators", ()),
        )
    if kind == "Module":
        return _pa.Module(
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "docstring"),
        )
    raise PyPipelineError("unknown py_ast wire node kind " + kind)


def _write_py_ast_wire(path: str, ast_mod) -> None:
    payload = {"schema": _PY_AST_WIRE_SCHEMA, "module": _py_ast_to_wire(ast_mod)}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload))


def _read_py_ast_wire(path: str):
    with open(path, "r", encoding="utf-8") as f:
        payload = json.loads(f.read())
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if schema != _PY_AST_WIRE_SCHEMA:
        raise PyPipelineError(
            "invalid frontend py_ast wire file " + path + " schema=" + str(schema)
        )
    return _py_ast_from_wire(payload.get("module"))


def _export_default_is_native_typed_int_shape(expr) -> bool:
    from .py_ast import BoolLit as _BoolLit
    from .py_ast import IntLit as _IntLit

    if _closed_world_is_node(expr, _IntLit):
        return _export_int_literal_fits_i64(expr)
    if _closed_world_is_node(expr, _BoolLit):
        return True
    return False


def _export_func_uses_unboxed_typed_int_abi(fd) -> bool:
    """Small export-table mirror of the typed-int ABI signature gate.

    This intentionally stays local to ``pipeline.py``: importing
    ``layer1`` here pulls the whole codegen package into the compiled
    pcc_multi+pipeline closure and reintroduces no-libpython fallback.
    """
    from .py_ast import BoolType as _BoolType
    from .py_ast import FloatType as _FloatType
    from .py_ast import IntType as _IntType

    mode = _export_typed_int_unboxed_abi_mode()
    if mode == "off":
        return False
    if (
        _py_ast_field_value(fd, "is_async", False)
        or _py_ast_field_value(fd, "is_method", False)
        or len(_py_ast_field_value(fd, "decorators", ())) != 0
    ):
        return False
    if mode == "unsafe-i64":
        if not _closed_world_is_node(
            _export_return_ty_or_none(fd),
            (_IntType, _FloatType),
        ):
            return False
    else:
        if not _closed_world_is_node(_export_return_ty_or_none(fd), _FloatType):
            return False
    for arg in _py_ast_field_value(fd, "args", ()):
        arg_name = _py_ast_field_value(arg, "name", "")
        if arg_name == "":
            continue
        if _py_ast_field_value(arg, "kind", "pos") not in (
            "pos",
            "pos_only",
            "kw_only",
        ):
            return False
        if mode == "unsafe-i64":
            if not _closed_world_is_node(
                _export_annotation_or_none(arg),
                (_IntType, _BoolType, _FloatType),
            ):
                return False
        else:
            if not _closed_world_is_node(_export_annotation_or_none(arg), _FloatType):
                return False
        arg_default = _py_ast_field_value(arg, "default", None)
        if arg_default is not None and not _export_default_is_native_typed_int_shape(
            arg_default
        ):
            return False
    return True


def _export_static_literal_type(expr):
    """Return a shallow static type for top-level literal containers.

    This feeds the multi-file export table for module globals such as
    ``VALUES = {"a": 1}``.  The defining module still owns and initializes
    the real object; importers only need the storage type so name lookup can
    load the native extern module-global slot instead of falling back to
    CPython import.
    """
    from .py_ast import BoolLit as _BoolLit
    from .py_ast import BoolType as _BoolType
    from .py_ast import Call as _Call
    from .py_ast import DictExpr as _DictExpr
    from .py_ast import DictType as _DictType
    from .py_ast import DynType as _DynType
    from .py_ast import FloatLit as _FloatLit
    from .py_ast import FloatType as _FloatType
    from .py_ast import IntLit as _IntLit
    from .py_ast import IntType as _IntType
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import ListType as _ListType
    from .py_ast import SetType as _SetType
    from .py_ast import Attr as _Attr
    from .py_ast import Name as _Name
    from .py_ast import NoneLit as _NoneLit
    from .py_ast import NoneType as _NoneType
    from .py_ast import StrLit as _StrLit
    from .py_ast import StrType as _StrType
    from .py_ast import TupleExpr as _TupleExpr
    from .py_ast import TupleType as _TupleType

    if _closed_world_is_node(expr, _StrLit):
        return _StrType("str")
    if _closed_world_is_node(expr, _IntLit):
        return _IntType("int")
    if _closed_world_is_node(expr, _BoolLit):
        return _BoolType("bool")
    if _closed_world_is_node(expr, _FloatLit):
        return _FloatType("float")
    if _closed_world_is_node(expr, _NoneLit):
        return _NoneType("None")
    if _closed_world_is_node(expr, _Name):
        return _DynType("dyn")
    if _closed_world_is_node(expr, _Attr):
        # ``X = other.attr`` — pcc does not statically know the precise
        # type but the export is real (the defining module's init code
        # populates it).  Register as DynType so downstream
        # ``mod.attr`` access on this name resolves via the
        # ``.modvar.<mod>.<name>`` extern instead of falling back to
        # ``py_obj_getattr`` on the module-name string.  Surfaced by
        # numpy/matrixlib/__init__.py:7 ``__all__ = defmatrix.__all__``
        # which made ``_mat.__all__`` in numpy/__init__.py:681 fail with
        # AttributeError.  See investigation
        # ``docs/investigations/python-native-module-alias-module-global-attr-attribute-error.md``.
        return _DynType("dyn")
    if _closed_world_is_node(expr, _Call):
        func = _py_ast_field_value(expr, "func", None)
        if _closed_world_is_node(func, _Name):
            func_name = _py_ast_field_value(func, "ident", "")
            if func_name in ("set", "frozenset", "_set_comp", "__setcomp__"):
                set_name = "frozenset" if func_name == "frozenset" else "set"
                return _SetType(name=set_name, elem=_DynType("dyn"))
    if _closed_world_is_node(expr, _TupleExpr):
        elems = []
        for item in _py_ast_field_value(expr, "elems", ()):
            item_ty = _export_static_literal_type(item)
            elems.append(item_ty if item_ty is not None else _DynType("dyn"))
        return _TupleType(name="tuple", elems=tuple(elems))
    if _closed_world_is_node(expr, _ListExpr):
        elem_ty = _export_common_static_type(
            tuple(
                _export_static_literal_type(item)
                for item in _py_ast_field_value(expr, "elems", ())
            )
        )
        return _ListType(name="list", elem=elem_ty)
    if _closed_world_is_node(expr, _DictExpr):
        key_types = []
        value_types = []
        for key, value in _py_ast_field_value(expr, "pairs", ()):
            key_types.append(_export_static_literal_type(key))
            value_types.append(_export_static_literal_type(value))
        return _DictType(
            name="dict",
            key=_export_common_static_type(tuple(key_types)),
            value=_export_common_static_type(tuple(value_types)),
        )
    return None


def _export_static_all_names(expr):
    from .py_ast import BinOp as _BinOp
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import StrLit as _StrLit
    from .py_ast import TupleExpr as _TupleExpr

    if _closed_world_is_node(expr, (_ListExpr, _TupleExpr)):
        names = []
        for item in _py_ast_field_value(expr, "elems", ()):
            if not _closed_world_is_node(item, _StrLit):
                return None
            value = _export_literal_value_or_none(item)
            if not isinstance(value, str):
                return None
            names.append(value)
        return tuple(names)
    if (
        _closed_world_is_node(expr, _BinOp)
        and _py_ast_field_value(
            expr,
            "op",
            "",
        )
        == "+"
    ):
        lhs = _export_static_all_names(_py_ast_field_value(expr, "lhs", None))
        rhs = _export_static_all_names(_py_ast_field_value(expr, "rhs", None))
        if lhs is None or rhs is None:
            return None
        return lhs + rhs
    return None


def _export_common_static_type(types):
    from .py_ast import DynType as _DynType

    concrete = []
    for ty in types:
        if ty is not None:
            concrete.append(ty)
    if not concrete:
        return _DynType("dyn")
    first = concrete[0]
    first_key = encode_type(first)
    for ty in concrete[1:]:
        if encode_type(ty) != first_key:
            return _DynType("dyn")
    return first


def _decorator_name(dec):
    from .py_ast import Attr, Call, Name

    if _closed_world_is_node(dec, Call):
        return _decorator_name(_py_ast_field_value(dec, "func", None))
    if _closed_world_is_node(dec, Name):
        return _py_ast_field_value(dec, "ident", "")
    if _closed_world_is_node(dec, Attr):
        base = _decorator_name(_py_ast_field_value(dec, "obj", None))
        if base:
            return base + "." + _py_ast_field_value(dec, "name", "")
    return None


def _split_top_level_type_args(text: str) -> tuple[str, ...]:
    out = []
    start = 0
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "," and depth == 0:
            part = text[start:i].strip()
            if part:
                out.append(part)
            start = i + 1
        i += 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return tuple(out)


def _normalise_export_annotation_text(text: str):
    from .py_ast import BoolType as _BoolType
    from .py_ast import ByteArrayType as _ByteArrayType
    from .py_ast import BytesType as _BytesType
    from .py_ast import ClassType as _ClassType
    from .py_ast import ComplexType as _ComplexType
    from .py_ast import DictType as _DictType
    from .py_ast import DynType as _DynType
    from .py_ast import FloatType as _FloatType
    from .py_ast import IntType as _IntType
    from .py_ast import ListType as _ListType
    from .py_ast import SetType as _SetType
    from .py_ast import MemoryViewType as _MemoryViewType
    from .py_ast import NoneType as _NoneType
    from .py_ast import StrType as _StrType
    from .py_ast import TupleType as _TupleType

    text = text.strip()
    if not text:
        return None
    if text == "..." or text == "Ellipsis":
        return None
    if text.startswith("typing."):
        text = text[len("typing.") :]
    if text == "list" or text == "List":
        return _ListType("list", _DynType("dyn"))
    if text in ("set", "Set", "frozenset", "FrozenSet"):
        name = "frozenset" if text in ("frozenset", "FrozenSet") else "set"
        return _SetType(name, _DynType("dyn"))
    if text == "dict" or text == "Dict":
        return _DictType("dict", _DynType("dyn"), _DynType("dyn"))
    if text == "tuple" or text == "Tuple":
        return _TupleType("tuple", (_DynType("dyn"),))
    if text == "str":
        return _StrType("str")
    if text == "int":
        return _IntType("int")
    if text == "bool":
        return _BoolType("bool")
    if text == "float":
        return _FloatType("float")
    if text == "complex":
        return _ComplexType("complex")
    if text == "bytes":
        return _BytesType("bytes")
    if text == "bytearray":
        return _ByteArrayType("bytearray")
    if text == "memoryview":
        return _MemoryViewType("memoryview")
    if text == "None" or text == "NoneType":
        return _NoneType("None")
    if text == "object" or text == "Any":
        return _DynType("dyn")
    open_bracket = _find_substring(text, "[", 0)
    if open_bracket >= 0 and text.endswith("]"):
        head = text[:open_bracket].strip()
        inner = text[open_bracket + 1 : -1]
        if head.startswith("typing."):
            head = head[len("typing.") :]
        args = _split_top_level_type_args(inner)
        if head == "list" or head == "List":
            elem = (
                _normalise_export_annotation_text(args[0]) if len(args) == 1 else None
            )
            return _ListType("list", elem or _DynType("dyn"))
        if head in ("set", "Set", "frozenset", "FrozenSet"):
            elem = (
                _normalise_export_annotation_text(args[0]) if len(args) == 1 else None
            )
            name = "frozenset" if head in ("frozenset", "FrozenSet") else "set"
            return _SetType(name, elem or _DynType("dyn"))
        if head == "dict" or head == "Dict":
            key = _normalise_export_annotation_text(args[0]) if len(args) == 2 else None
            value = (
                _normalise_export_annotation_text(args[1]) if len(args) == 2 else None
            )
            return _DictType(
                "dict",
                key or _DynType("dyn"),
                value or _DynType("dyn"),
            )
        if head == "tuple" or head == "Tuple":
            elems = []
            for arg in args:
                if arg == "..." or arg == "Ellipsis":
                    continue
                elem = _normalise_export_annotation_text(arg)
                elems.append(elem or _DynType("dyn"))
            if not elems:
                elems.append(_DynType("dyn"))
            return _TupleType("tuple", tuple(elems))
        if head == "Optional" and len(args) == 1:
            return _normalise_export_annotation_text(args[0])
        return _DynType("dyn")
    if "." in text:
        last_dot = -1
        i = 0
        while i < len(text):
            if text[i] == ".":
                last_dot = i
            i += 1
        return _ClassType(text[last_dot + 1 :], text[:last_dot], (), ())
    return _ClassType(text, "", (), ())


def _normalise_export_annotation(ann):
    if ann is None:
        return None
    from .py_ast import ClassType as _ClassType
    from .py_ast import DynType as _DynType
    from .py_ast import Type as _Type

    if isinstance(ann, _ClassType):
        class_name = str(getattr(ann, "name", "") or "")
        if class_name == "list":
            from .py_ast import ListType as _ListType

            return _ListType("list", _DynType("dyn"))
        if class_name in ("set", "frozenset"):
            from .py_ast import SetType as _SetType

            return _SetType(class_name, _DynType("dyn"))
        if class_name == "dict":
            from .py_ast import DictType as _DictType

            return _DictType("dict", _DynType("dyn"), _DynType("dyn"))
        if class_name == "tuple":
            from .py_ast import TupleType as _TupleType

            return _TupleType("tuple", (_DynType("dyn"),))
        if class_name == "object":
            return _DynType("dyn")
    if isinstance(ann, _Type):
        return ann
    if isinstance(ann, str):
        text = ann.strip()
    else:
        try:
            text = str(ann.__name__)
        except Exception:
            text = str(ann)
    return _normalise_export_annotation_text(text)


def _export_annotation_or_none(obj):
    return _normalise_export_annotation(_py_ast_field_value(obj, "annotation", None))


def _export_return_ty_or_none(obj):
    return _py_ast_field_value(obj, "return_ty", None)


def _class_is_dataclass(cd) -> bool:
    for dec in _py_ast_field_value(cd, "decorators", ()):
        name = _decorator_name(dec)
        if name in ("dataclass", "dataclasses.dataclass"):
            return True
    return False


def _export_default_native_func_ref(expr, owning_module, top_level_func_names):
    if expr is None or not owning_module:
        return None
    from .py_ast import Name as _Name

    if not _closed_world_is_node(expr, _Name):
        return None
    ident = str(_py_ast_field_value(expr, "ident", ""))
    if ident not in top_level_func_names:
        return None
    return {
        "owning_module": str(owning_module),
        "name": ident,
    }


def _export_default_native_global_ref(expr, owning_module, top_level_func_names):
    """Record a default rooted at the defining module's own global.

    This covers both ``def f(x=MODULE_CONST)`` and attribute chains such as
    ``def f(match=WHITESPACE.match)``.  A cross-module caller cannot re-emit
    the bare root Name in its own namespace; it must load the defining
    module's export first and then apply the recorded attributes.
    """
    if expr is None or not owning_module:
        return None
    from .py_ast import Attr as _Attr
    from .py_ast import Name as _Name

    attrs = []
    root = expr
    while _closed_world_is_node(root, _Attr):
        attr_name = str(_py_ast_field_value(root, "name", ""))
        if not attr_name:
            return None
        attrs.append(attr_name)
        root = _py_ast_field_value(root, "obj", None)
    if not _closed_world_is_node(root, _Name):
        return None
    ident = str(_py_ast_field_value(root, "ident", ""))
    if not ident or ident in top_level_func_names:
        return None
    ref = {
        "owning_module": str(owning_module),
        "name": ident,
    }
    if attrs:
        ref["attrs"] = tuple(reversed(attrs))
    return ref


def _export_call_sig(args, owning_module=None, top_level_func_names=()):
    sig = []
    top_level_func_names = set(top_level_func_names or ())
    for a in args:
        ann = _export_annotation_or_none(a)
        default = _py_ast_field_value(a, "default", None)
        item = {
            "name": _py_ast_field_value(a, "name", ""),
            "kind": _py_ast_field_value(a, "kind", "pos"),
            "annotation": encode_type(ann),
            "default": default,
            "has_default": _py_ast_field_value(a, "has_default", False),
        }
        default_native_func = _export_default_native_func_ref(
            default,
            owning_module,
            top_level_func_names,
        )
        if default_native_func is not None:
            item["default_native_func"] = default_native_func
        else:
            default_native_global = _export_default_native_global_ref(
                default,
                owning_module,
                top_level_func_names,
            )
            if default_native_global is not None:
                item["default_native_global"] = default_native_global
        sig.append(item)
    return tuple(sig)


_EXPORT_DEFAULT_WIRE_KEY = "__pcc_export_default_v1__"


def _export_default_to_wire(expr):
    if expr is None:
        return {_EXPORT_DEFAULT_WIRE_KEY: "absent"}
    from .py_ast import BoolLit as _BoolLit
    from .py_ast import BytesLit as _BytesLit
    from .py_ast import DictExpr as _DictExpr
    from .py_ast import FloatLit as _FloatLit
    from .py_ast import IntLit as _IntLit
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import Attr as _Attr
    from .py_ast import Name as _Name
    from .py_ast import NoneLit as _NoneLit
    from .py_ast import StrLit as _StrLit
    from .py_ast import TupleExpr as _TupleExpr
    from .py_ast import UnaryOp as _UnaryOp

    if isinstance(expr, _NoneLit):
        return {_EXPORT_DEFAULT_WIRE_KEY: "none"}
    if isinstance(expr, _Name):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "name",
            "ident": str(_py_ast_field_value(expr, "ident", "")),
        }
    if isinstance(expr, _Attr):
        obj_wire = _export_default_to_wire(_py_ast_field_value(expr, "obj", None))
        if not _export_default_wire_is_safe(obj_wire):
            return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "attr",
            "obj": obj_wire,
            "name": str(_py_ast_field_value(expr, "name", "")),
        }
    if isinstance(expr, _UnaryOp):
        operand_wire = _export_default_to_wire(
            _py_ast_field_value(expr, "operand", None)
        )
        if not _export_default_wire_is_safe(operand_wire):
            return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "unary",
            "op": str(_py_ast_field_value(expr, "op", "")),
            "operand": operand_wire,
        }
    if isinstance(expr, _BoolLit):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "bool",
            "value": bool(_py_ast_field_value(expr, "value", False)),
        }
    if isinstance(expr, _IntLit):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "int",
            "value": int(_py_ast_field_value(expr, "value", 0)),
        }
    if isinstance(expr, _FloatLit):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "float",
            "value": float(_py_ast_field_value(expr, "value", 0.0)),
        }
    if isinstance(expr, _StrLit):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "str",
            "value": str(_py_ast_field_value(expr, "value", "")),
        }
    if isinstance(expr, _BytesLit):
        raw = _py_ast_field_value(expr, "value", b"")
        values = []
        i = 0
        while i < len(raw):
            values.append(int(raw[i]))
            i += 1
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "bytes",
            "value": values,
        }
    if isinstance(expr, _TupleExpr):
        elems = []
        for elem in _py_ast_field_value(expr, "elems", ()):
            elem_wire = _export_default_to_wire(elem)
            if not _export_default_wire_is_safe(elem_wire):
                return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
            elems.append(elem_wire)
        return {_EXPORT_DEFAULT_WIRE_KEY: "tuple", "elems": elems}
    if isinstance(expr, _ListExpr):
        elems = []
        for elem in _py_ast_field_value(expr, "elems", ()):
            elem_wire = _export_default_to_wire(elem)
            if not _export_default_wire_is_safe(elem_wire):
                return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
            elems.append(elem_wire)
        return {_EXPORT_DEFAULT_WIRE_KEY: "list", "elems": elems}
    if isinstance(expr, _DictExpr):
        pairs = []
        for key, item in _py_ast_field_value(expr, "pairs", ()):
            key_wire = _export_default_to_wire(key)
            item_wire = _export_default_to_wire(item)
            if not _export_default_wire_is_safe(
                key_wire
            ) or not _export_default_wire_is_safe(item_wire):
                return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
            pairs.append((key_wire, item_wire))
        return {_EXPORT_DEFAULT_WIRE_KEY: "dict", "pairs": pairs}
    return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}


def _export_default_wire_is_safe(wire) -> bool:
    if not isinstance(wire, dict):
        return False
    kind = wire.get(_EXPORT_DEFAULT_WIRE_KEY)
    if kind == "complex":
        return False
    if kind in ("tuple", "list"):
        for elem in wire.get("elems", ()):
            if not _export_default_wire_is_safe(elem):
                return False
    if kind == "dict":
        for key, item in wire.get("pairs", ()):
            if not _export_default_wire_is_safe(
                key
            ) or not _export_default_wire_is_safe(item):
                return False
    return True


def _export_default_from_wire(wire):
    if not isinstance(wire, dict):
        return None
    kind = wire.get(_EXPORT_DEFAULT_WIRE_KEY)
    if kind == "absent" or kind == "complex":
        return None
    from .py_ast import BoolLit as _BoolLit
    from .py_ast import BoolType as _BoolType
    from .py_ast import BytesLit as _BytesLit
    from .py_ast import BytesType as _BytesType
    from .py_ast import DictExpr as _DictExpr
    from .py_ast import DictType as _DictType
    from .py_ast import DynType as _DynType
    from .py_ast import FloatLit as _FloatLit
    from .py_ast import FloatType as _FloatType
    from .py_ast import IntLit as _IntLit
    from .py_ast import IntType as _IntType
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import ListType as _ListType
    from .py_ast import Attr as _Attr
    from .py_ast import Name as _Name
    from .py_ast import NoneLit as _NoneLit
    from .py_ast import NoneType as _NoneType
    from .py_ast import SourceSpan as _SourceSpan
    from .py_ast import StrLit as _StrLit
    from .py_ast import StrType as _StrType
    from .py_ast import TupleExpr as _TupleExpr
    from .py_ast import TupleType as _TupleType
    from .py_ast import UnaryOp as _UnaryOp

    span = _SourceSpan("<extern-default>", 0, 0, 0, 0)
    if kind == "name":
        return _Name(span, _DynType("dyn"), str(wire.get("ident", "")))
    if kind == "attr":
        obj = _export_default_from_wire(wire.get("obj"))
        if obj is None:
            return None
        return _Attr(span, _DynType("dyn"), obj, str(wire.get("name", "")))
    if kind == "unary":
        operand = _export_default_from_wire(wire.get("operand"))
        if operand is None:
            return None
        return _UnaryOp(span, _DynType("dyn"), str(wire.get("op", "")), operand)
    if kind == "none":
        return _NoneLit(span, _NoneType("None"))
    if kind == "bool":
        return _BoolLit(span, _BoolType("bool"), bool(wire.get("value", False)))
    if kind == "int":
        return _IntLit(span, _IntType("int"), int(wire.get("value", 0)))
    if kind == "float":
        return _FloatLit(span, _FloatType("float"), float(wire.get("value", 0.0)))
    if kind == "str":
        return _StrLit(span, _StrType("str"), str(wire.get("value", "")))
    if kind == "bytes":
        return _BytesLit(span, _BytesType("bytes"), bytes(wire.get("value", ())))
    if kind == "tuple":
        elems = tuple(_export_default_from_wire(elem) for elem in wire.get("elems", ()))
        elem_types = tuple(getattr(elem, "ty", _DynType("dyn")) for elem in elems)
        return _TupleExpr(span, _TupleType("tuple", elem_types), elems)
    if kind == "list":
        elems = tuple(_export_default_from_wire(elem) for elem in wire.get("elems", ()))
        elem_ty = getattr(elems[0], "ty", _DynType("dyn")) if elems else _DynType("dyn")
        return _ListExpr(span, _ListType("list", elem_ty), elems)
    if kind == "dict":
        pairs = tuple(
            (
                _export_default_from_wire(pair[0]),
                _export_default_from_wire(pair[1]),
            )
            for pair in wire.get("pairs", ())
        )
        if pairs:
            key_ty = getattr(pairs[0][0], "ty", _DynType("dyn"))
            value_ty = getattr(pairs[0][1], "ty", _DynType("dyn"))
        else:
            key_ty = _DynType("dyn")
            value_ty = _DynType("dyn")
        return _DictExpr(span, _DictType("dict", key_ty, value_ty), pairs)
    return None


def _native_export_arg_to_wire(arg):
    out = {}
    default_safe = True
    for key, value in arg.items():
        if key == "default":
            default_wire = _export_default_to_wire(value)
            default_safe = _export_default_wire_is_safe(default_wire)
            out[key] = default_wire
        else:
            out[key] = _native_export_to_wire(value)
    if not default_safe:
        out["has_default"] = False
    return out


def _native_export_to_wire(value):
    if isinstance(value, dict):
        if (
            "name" in value
            and "kind" in value
            and "annotation" in value
            and "default" in value
            and "has_default" in value
        ):
            return _native_export_arg_to_wire(value)
        out = {}
        for key, item in value.items():
            out[str(key)] = _native_export_to_wire(item)
        return out
    if isinstance(value, (tuple, list)):
        out = []
        for item in value:
            out.append(_native_export_to_wire(item))
        return out
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _native_export_from_wire(value):
    if isinstance(value, dict):
        if _EXPORT_DEFAULT_WIRE_KEY in value:
            return _export_default_from_wire(value)
        out = {}
        for key, item in value.items():
            out[key] = _native_export_from_wire(item)
        return out
    if isinstance(value, list):
        return tuple(_native_export_from_wire(item) for item in value)
    return value


def _write_native_exports_wire(
    path: str,
    native_exports,
    derived_class_map,
    function_object_uses=(),
) -> None:
    native_exports_wire = _native_export_to_wire(native_exports)
    derived_class_map_wire = _native_export_to_wire(derived_class_map)
    payload = {
        "schema": "pcc.py_frontend.native_exports.v1",
        "native_exports": native_exports_wire,
        "derived_class_map": derived_class_map_wire,
        "function_object_uses": _native_export_to_wire(function_object_uses),
    }
    text = json.dumps(payload)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read_native_exports_wire(path: str, include_function_object_uses: bool = False):
    with open(path, "r", encoding="utf-8") as f:
        payload = json.loads(f.read())
    if payload.get("schema") != "pcc.py_frontend.native_exports.v1":
        raise PyPipelineError("invalid frontend native exports file")
    native_exports = _native_export_from_wire(payload.get("native_exports", {}))
    derived_class_map = _native_export_from_wire(payload.get("derived_class_map", {}))
    if include_function_object_uses:
        function_object_uses = _native_export_from_wire(
            payload.get("function_object_uses", ())
        )
        return native_exports, derived_class_map, function_object_uses
    return native_exports, derived_class_map


def _export_method_symbol(
    module_name: str,
    class_name: str,
    method_name: str,
    top_level_func_names,
) -> str:
    sanitised_mod = module_name.replace(".", "_").replace("-", "_")
    if class_name + "_" + method_name in top_level_func_names:
        return f"user_{sanitised_mod}_{class_name}__method_{method_name}"
    return f"user_{sanitised_mod}_{class_name}_{method_name}"


def _resolve_ast_import_from_module(src_path: str, mod_name: str, stmt) -> str:
    """Resolve an AST ImportFrom's source module for closed-world exports."""
    level = _py_ast_field_value(stmt, "level", 0) or 0
    module = _py_ast_field_value(stmt, "module", "") or ""
    if level <= 0:
        return module
    cur_pkg = _package_parts_for_module(src_path, mod_name)
    up = level - 1
    if up > len(cur_pkg):
        return module
    base = cur_pkg[: len(cur_pkg) - up]
    if module:
        return _join_dotted_parts(base + module.split("."))
    return _join_dotted_parts(base)


def _closed_world_star_export_items(src_exports):
    all_info = src_exports.get("__all__")
    all_names = None
    if isinstance(all_info, dict):
        all_names = all_info.get("export_names")
    if all_names is not None:
        items = []
        for export_name in all_names:
            info = src_exports.get(export_name)
            if info is not None:
                items.append((export_name, info))
        return items
    items = []
    for export_name, info in src_exports.items():
        if export_name.startswith("_"):
            continue
        items.append((export_name, info))
    return items


def _closed_world_module_block_assign_targets(stmt):
    from .py_ast import Assign as _Assign
    from .py_ast import ClassDef as _ClassDef
    from .py_ast import For as _For
    from .py_ast import FuncDef as _FuncDef
    from .py_ast import If as _If
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import Name as _Name
    from .py_ast import Try as _Try
    from .py_ast import TupleExpr as _TupleExpr
    from .py_ast import While as _While
    from .py_ast import With as _With

    names = []
    pending = [stmt]
    while pending:
        s = pending.pop()
        if _closed_world_is_node(s, (_FuncDef, _ClassDef)):
            continue
        if _closed_world_is_node(s, _Assign):
            pending_targets = list(_py_ast_field_value(s, "targets", ()))
            while pending_targets:
                target = pending_targets.pop()
                if _closed_world_is_node(target, (_TupleExpr, _ListExpr)):
                    pending_targets.extend(
                        reversed(_py_ast_field_value(target, "elems", ()))
                    )
                    continue
                if _closed_world_is_node(target, _Name):
                    target_name = _py_ast_field_value(target, "ident", "")
                    if target_name:
                        names.append(target_name)
            continue
        if _closed_world_is_node(s, (_If, _While, _For)):
            for child in _py_ast_field_value(s, "else_body", ()):
                pending.append(child)
            for child in _py_ast_field_value(s, "body", ()):
                pending.append(child)
            continue
        if _closed_world_is_node(s, _Try):
            for child in _py_ast_field_value(s, "finally_body", ()):
                pending.append(child)
            for child in _py_ast_field_value(s, "else_body", ()):
                pending.append(child)
            for handler in _py_ast_field_value(s, "handlers", ()):
                for child in _py_ast_field_value(handler, "body", ()):
                    pending.append(child)
            for child in _py_ast_field_value(s, "body", ()):
                pending.append(child)
            continue
        if _closed_world_is_node(s, _With):
            for child in _py_ast_field_value(s, "body", ()):
                pending.append(child)
    return tuple(names)


def _closed_world_dyn_module_global_export(mod_name: str, target_name: str) -> dict:
    from .py_ast import DynType as _DynType

    return {
        "kind": "module_global",
        "owning_module": mod_name,
        "export_name": target_name,
        "value_ty": encode_type(_DynType("dyn")),
    }


def _merge_closed_world_reexports(
    parsed_modules, module_names, src_paths, native_exports
):
    """Propagate native function/class/constant exports across package re-exports.

    Real packages commonly expose their public API through ``__init__.py`` using
    ``from .mod import name`` and ``from .mod import *``.  Without modelling
    that in the closed-world table, downstream modules see a missing native
    export and fall back to ``py_cpy_import`` even though both sides are compiled
    in the same native invocation.
    """
    from .py_ast import ImportFrom as _ImportFrom

    module_set = set(module_names)
    module_to_ast = {
        mod_name: ast_mod for mod_name, ast_mod in zip(module_names, parsed_modules)
    }
    module_to_src = {
        mod_name: src_path for mod_name, src_path in zip(module_names, src_paths)
    }

    changed = True
    while changed:
        changed = False
        for mod_name in module_names:
            ast_mod = module_to_ast.get(mod_name)
            src_path = module_to_src.get(mod_name, "")
            if ast_mod is None:
                continue
            exports = native_exports.setdefault(mod_name, {})
            for stmt in _py_ast_field_value(ast_mod, "body", ()):
                if not isinstance(stmt, _ImportFrom):
                    continue
                src_mod = _resolve_ast_import_from_module(src_path, mod_name, stmt)
                if not src_mod:
                    continue

                # ``from . import submodule`` re-exports a module object, not a
                # function/class binding.  Import lowering already handles that
                # by registering native module aliases, so do not invent an
                # export-table entry for it here.
                for attr_name, as_name in _py_ast_field_value(stmt, "names", ()):
                    if attr_name == "*":
                        src_exports = native_exports.get(src_mod)
                        if not src_exports:
                            continue
                        for export_name, info in _closed_world_star_export_items(
                            src_exports
                        ):
                            if export_name in exports:
                                continue
                            exports[export_name] = info
                            changed = True
                        continue
                    local_name = as_name or attr_name
                    src_exports = native_exports.get(src_mod)
                    if src_exports and attr_name in src_exports:
                        if exports.get(local_name) is not src_exports[attr_name]:
                            exports[local_name] = src_exports[attr_name]
                            changed = True
                        continue
                    full_submodule = f"{src_mod}.{attr_name}"
                    if full_submodule in module_set:
                        continue


def _closed_world_reexport_edges(
    parsed_modules, module_names, src_paths, all_module_names
):
    from .py_ast import ImportFrom as _ImportFrom

    module_set = set(all_module_names)
    edges = []
    for mod_name, ast_mod, src_path in zip(module_names, parsed_modules, src_paths):
        if ast_mod is None:
            continue
        for stmt in _py_ast_field_value(ast_mod, "body", ()):
            if not isinstance(stmt, _ImportFrom):
                continue
            src_mod = _resolve_ast_import_from_module(src_path, mod_name, stmt)
            if not src_mod:
                continue
            for attr_name, as_name in _py_ast_field_value(stmt, "names", ()):
                if attr_name == "*":
                    edges.append((mod_name, src_mod, "*", "", True))
                    continue
                local_name = as_name or attr_name
                full_submodule = f"{src_mod}.{attr_name}"
                if full_submodule in module_set:
                    continue
                edges.append((mod_name, src_mod, attr_name, local_name, False))
    return edges


def _merge_closed_world_reexport_edges(module_names, native_exports, edges):
    changed = True
    while changed:
        changed = False
        for mod_name in module_names:
            exports = native_exports.setdefault(mod_name, {})
            for edge in edges:
                if len(edge) < 5 or edge[0] != mod_name:
                    continue
                _dst_mod, src_mod, attr_name, local_name, is_star = edge
                src_exports = native_exports.get(src_mod)
                if not src_exports:
                    continue
                if is_star:
                    for export_name, info in _closed_world_star_export_items(
                        src_exports
                    ):
                        if export_name in exports:
                            continue
                        exports[export_name] = info
                        changed = True
                    continue
                if (
                    attr_name in src_exports
                    and exports.get(local_name) is not src_exports[attr_name]
                ):
                    exports[local_name] = src_exports[attr_name]
                    changed = True


def _repair_closed_world_default_global_owners(native_exports) -> None:
    """Point exported function defaults through their original module.

    A module may import ``NAME`` and then use it in a method default. The
    closed-world call signature is built before re-export merging, so its
    initial owner is the importing module even though that module has no
    ``.modvar`` definition for the name. Rebind such references after the
    export graph has converged.
    """
    seen: set[int] = set()

    def visit(value) -> None:
        if isinstance(value, dict):
            marker = id(value)
            if marker in seen:
                return
            seen.add(marker)
            ref = value.get("default_native_global")
            if isinstance(ref, dict):
                owner = str(ref.get("owning_module", ""))
                name = str(ref.get("name", ""))
                source = native_exports.get(owner, {}).get(name)
                if isinstance(source, dict):
                    source_owner = str(source.get("owning_module", owner))
                    source_name = str(source.get("export_name", name))
                    if source_owner != owner or source_name != name:
                        repaired = {
                            "owning_module": source_owner,
                            "name": source_name,
                        }
                        attrs = ref.get("attrs")
                        if attrs:
                            repaired["attrs"] = tuple(attrs)
                        value["default_native_global"] = repaired
            for child in value.values():
                visit(child)
            return
        if isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit(native_exports)


def _mark_closed_world_function_object_exports(
    parsed_modules,
    module_names,
    src_paths,
    native_exports,
    known_module_names=None,
):
    """Mark function exports that must exist as runtime objects.

    Direct native calls can use an exported entry point without allocating a
    ``PyFunc``.  Module attribute reads and explicit ``from`` imports cannot:
    they observe a stable Python function object.  Record only those uses so
    metadata-decorated package functions are published when required without
    eagerly wrapping every decorated function in the closed world.
    """
    from .py_ast import Attr as _Attr
    from .py_ast import Import as _Import
    from .py_ast import ImportFrom as _ImportFrom
    from .py_ast import Name as _Name
    from .py_ast import Type as _Type

    # This table is small (one entry per closed-world module), and a list is
    # the reliable self-host projection here.  pcc1's set construction can
    # lose string members, causing relative module imports such as
    # ``from . import provider`` to be mistaken for value imports; later
    # ``provider.fn`` reads then never mark ``fn`` as needing a PyFunc object.
    known_modules = list(known_module_names or native_exports.keys())
    uses = []
    use_keys = set()

    def mark(module_name: str, attr_name: str) -> None:
        key = module_name + "\x00" + attr_name
        if key not in use_keys:
            use_keys.add(key)
            uses.append((module_name, attr_name))
        info = native_exports.get(module_name, {}).get(attr_name)
        if isinstance(info, dict) and info.get("kind") == "function":
            info["needs_object"] = True

    def collect_nodes(root):
        pending = [root]
        seen = set()
        out = []
        while pending:
            node = pending.pop()
            if node is None or isinstance(node, _Type):
                continue
            if isinstance(node, (tuple, list)):
                pending.extend(node)
                continue
            marker = id(node)
            if marker in seen:
                continue
            seen.add(marker)
            out.append(node)
            for field_name in _py_ast_field_names(node):
                if field_name in ("annotation", "return_ty", "ty", "span"):
                    continue
                pending.append(_py_ast_field_value(node, field_name, None))
        return out

    def attr_parts(node):
        parts = []
        current = node
        while isinstance(current, _Attr):
            parts.append(_py_ast_field_value(current, "name", ""))
            current = _py_ast_field_value(current, "obj", None)
        if not isinstance(current, _Name):
            return None
        parts.append(_py_ast_field_value(current, "ident", ""))
        parts.reverse()
        return parts

    for ast_mod, mod_name, src_path in zip(
        parsed_modules,
        module_names,
        src_paths,
    ):
        module_aliases = {}
        body = _py_ast_field_value(ast_mod, "body", ())
        nodes = collect_nodes(body)
        for node in nodes:
            if isinstance(node, _Import):
                for imported_name, as_name in _py_ast_field_value(
                    node,
                    "names",
                    (),
                ):
                    local_name = as_name or imported_name.split(".")[0]
                    target_module = (
                        imported_name
                        if as_name is not None
                        else imported_name.split(".")[0]
                    )
                    alias_targets = module_aliases.get(local_name)
                    if alias_targets is None:
                        alias_targets = []
                        module_aliases[local_name] = alias_targets
                    if target_module not in alias_targets:
                        alias_targets.append(target_module)
                continue
            if not isinstance(node, _ImportFrom):
                continue
            resolved = _resolve_ast_import_from_module(src_path, mod_name, node)
            for imported_name, as_name in _py_ast_field_value(
                node,
                "names",
                (),
            ):
                if imported_name == "*":
                    mark(resolved, "*")
                    for export_name, info in native_exports.get(resolved, {}).items():
                        if isinstance(info, dict) and info.get("kind") == "function":
                            mark(resolved, export_name)
                    continue
                local_name = as_name or imported_name
                candidate_module = _join_dotted_parts([resolved, imported_name])
                if candidate_module in known_modules:
                    alias_targets = module_aliases.get(local_name)
                    if alias_targets is None:
                        alias_targets = []
                        module_aliases[local_name] = alias_targets
                    if candidate_module not in alias_targets:
                        alias_targets.append(candidate_module)
                    continue
                mark(resolved, imported_name)

        for node in nodes:
            if not isinstance(node, _Attr):
                continue
            parts = attr_parts(node)
            if not parts or len(parts) < 2:
                continue
            for root_module in module_aliases.get(parts[0], ()):
                owner_module = root_module
                if len(parts) > 2:
                    candidate_module = _join_dotted_parts([root_module] + parts[1:-1])
                    if candidate_module in known_modules:
                        owner_module = candidate_module
                mark(owner_module, parts[-1])
    return tuple(uses)


def _apply_closed_world_function_object_uses(native_exports, uses) -> None:
    for module_name, attr_name in uses:
        exports = native_exports.get(module_name, {})
        if attr_name == "*":
            for info in exports.values():
                if isinstance(info, dict) and info.get("kind") == "function":
                    info["needs_object"] = True
            continue
        info = exports.get(attr_name)
        if isinstance(info, dict) and info.get("kind") == "function":
            info["needs_object"] = True


def _closed_world_function_object_exports(native_exports, module_name: str):
    out = {}
    for export_name, info in native_exports.get(module_name, {}).items():
        if not isinstance(info, dict):
            continue
        if info.get("kind") != "function" or not info.get("needs_object"):
            continue
        if info.get("owning_module", module_name) != module_name:
            continue
        out[export_name] = True
    return out


def _write_reexport_edges_wire(path: str, edges) -> None:
    payload = {
        "schema": "pcc.py_frontend.reexport_edges.v1",
        "edges": _native_export_to_wire(edges),
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload))


def _read_reexport_edges_wire(path: str):
    with open(path, "r", encoding="utf-8") as f:
        payload = json.loads(f.read())
    if payload.get("schema") != "pcc.py_frontend.reexport_edges.v1":
        raise PyPipelineError("invalid frontend reexport edges file")
    return _native_export_from_wire(payload.get("edges", ()))


def _closed_world_shallow_func_body(lifter, raw_func, include_assigns: bool):
    from ..parse.py_parse import _Assign as _PPAssign
    from ..parse.py_parse import _Expr as _PPExpr
    from ..parse.py_parse import _Str as _PPStr

    out = []
    for raw_stmt in raw_func.body:
        raw_type = type(raw_stmt)
        if raw_type is _PPExpr and type(raw_stmt.expr) is _PPStr and not out:
            out.append(lifter.lift_stmt(raw_stmt))
            continue
        if include_assigns and raw_type is _PPAssign:
            out.append(lifter.lift_stmt(raw_stmt))
    return tuple(out)


def _closed_world_shallow_func(lifter, raw_func, body):
    from . import py_ast as _pa

    args_list = []
    for param in raw_func.params:
        args_list.append(lifter._lift_arg(param))
    deco_list = []
    for dec in raw_func.decorators or ():
        deco_list.append(lifter.lift_expr(dec))
    from ..parse.py_lift import _lift_type as _lift_type_for_closed_world

    returns = raw_func.returns
    ret_ty = _lift_type_for_closed_world(returns) if returns is not None else None
    return _pa.FuncDef(
        lifter._span(raw_func.line),
        str(raw_func.name),
        tuple(args_list),
        ret_ty,
        tuple(body),
        tuple(deco_list),
        False,
        bool(raw_func.is_async),
    )


def _closed_world_shallow_lift_module(raw_mod, filename: str, module_name: str):
    """Lift only the AST surface needed by closed-world export discovery.

    Full function bodies are unnecessary for the native export table. Keeping
    only signatures, decorators, docstrings, class-body assignments, and
    ``__init__`` field assignments avoids lifting most sibling modules in
    parallel codegen workers while preserving the py_ast shape consumed by the
    existing export/re-export logic.
    """
    from ..parse.py_lift import _Lifter as _ClosedWorldLifter
    from ..parse.py_parse import _Assign as _PPAssign
    from ..parse.py_parse import _ClassDef as _PPClassDef
    from ..parse.py_parse import _Expr as _PPExpr
    from ..parse.py_parse import _FuncDef as _PPFuncDef
    from ..parse.py_parse import _ImportFrom as _PPImportFrom
    from ..parse.py_parse import _Str as _PPStr
    from . import py_ast as _pa

    lifter = _ClosedWorldLifter(filename)
    body = []
    docstring = None
    for raw_stmt in raw_mod.body:
        raw_type = type(raw_stmt)
        if raw_type is _PPImportFrom:
            body.append(lifter.lift_stmt(raw_stmt))
            continue
        if raw_type is _PPAssign:
            body.append(lifter.lift_stmt(raw_stmt))
            continue
        if raw_type is _PPExpr and type(raw_stmt.expr) is _PPStr:
            lifted_expr = lifter.lift_stmt(raw_stmt)
            if docstring is None:
                try:
                    docstring = lifted_expr.expr.value
                except Exception:
                    docstring = None
            body.append(lifted_expr)
            continue
        if raw_type is _PPFuncDef:
            func_body = _closed_world_shallow_func_body(
                lifter,
                raw_stmt,
                include_assigns=False,
            )
            body.append(
                _closed_world_shallow_func(
                    lifter,
                    raw_stmt,
                    func_body,
                )
            )
            continue
        if raw_type is not _PPClassDef:
            continue

        class_body = []
        for raw_body_stmt in raw_stmt.body:
            body_type = type(raw_body_stmt)
            if body_type is _PPAssign:
                class_body.append(lifter.lift_stmt(raw_body_stmt))
                continue
            if body_type is _PPFuncDef:
                include_assigns = str(raw_body_stmt.name) == "__init__"
                method_body = _closed_world_shallow_func_body(
                    lifter,
                    raw_body_stmt,
                    include_assigns=include_assigns,
                )
                class_body.append(
                    _closed_world_shallow_func(
                        lifter,
                        raw_body_stmt,
                        method_body,
                    )
                )
        bases = []
        keywords = []
        for base in raw_stmt.bases:
            if (
                isinstance(base, tuple)
                and len(base) == 4
                and base[0] == "__pcc_kwarg__"
            ):
                keywords.append((base[1], lifter.lift_expr(base[2])))
                continue
            bases.append(lifter.lift_expr(base))
        decorators = []
        for dec in raw_stmt.decorators:
            decorators.append(lifter.lift_expr(dec))
        body.append(
            _pa.ClassDef(
                lifter._span(raw_stmt.line),
                str(raw_stmt.name),
                tuple(bases),
                tuple(keywords),
                tuple(class_body),
                tuple(decorators),
            )
        )
    return _pa.Module(module_name, tuple(body), docstring)


def _closed_world_is_identity_decorator(stmt) -> bool:
    """Whether a function can only return its first argument unchanged.

    Imported bare decorators are normally semantic and must not be discarded.
    A narrow metadata-decorator shape is safe for native callable publication:
    straight-line expression/assignment side effects followed by
    ``return <first positional argument>``.  This covers decorators that set
    documentation or registration metadata while preserving call identity,
    without treating arbitrary sibling decorators as no-ops.
    """
    from .py_ast import Assign as _Assign
    from .py_ast import ExprStmt as _ExprStmt
    from .py_ast import Name as _Name
    from .py_ast import Return as _Return

    args = _py_ast_field_value(stmt, "args", ())
    if not args:
        return False
    first_arg = args[0]
    if _py_ast_field_value(first_arg, "kind", "") not in ("pos", "pos_only"):
        return False
    first_name = _py_ast_field_value(first_arg, "name", "")
    if not first_name:
        return False
    body = _py_ast_field_value(stmt, "body", ())
    if not body:
        return False
    for prefix_stmt in body[:-1]:
        if not _closed_world_is_node(prefix_stmt, (_ExprStmt, _Assign)):
            return False
    final_stmt = body[-1]
    if not _closed_world_is_node(final_stmt, _Return):
        return False
    return_value = _py_ast_field_value(final_stmt, "value", None)
    return _closed_world_is_node(return_value, _Name) and (
        _py_ast_field_value(return_value, "ident", "") == first_name
    )


def build_closed_world_context(
    src_paths,
    module_names,
    profile: Optional[dict] = None,
    lift_indices=None,
    merge_exports: bool = True,
):
    """Build the class/export context for closed-world Python compiles.

    Contextual per-module probes need the same semantic model as the
    multi-file self-host path: a mixin method's ``self`` is the final
    host class (currently ``L1CodeGen``), not the standalone mixin class.
    This helper returns the parsed modules, native export table, and
    inverse base-to-derived map required to run that inference model
    outside ``compile_python_multi``.
    """
    _profile_counter(profile, "build_closed_world_context_entered", len(src_paths))
    import_t = _profile_begin(profile)
    from .py_ast import Assign as _Assign
    from .py_ast import Attr as _Attr
    from .py_ast import BinOp as _BinOp
    from .py_ast import BoolLit as _BoolLit
    from .py_ast import Call as _Call
    from .py_ast import ClassDef as _ClassDef
    from .py_ast import ExprStmt as _ExprStmt
    from .py_ast import FuncDef as _FuncDef
    from .py_ast import Import as _Import
    from .py_ast import ImportFrom as _ImportFrom
    from .py_ast import IntLit as _IntLit
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import Name as _Name
    from .py_ast import NoneLit as _NoneLit
    from .py_ast import StrLit as _StrLit
    from .py_ast import Subscript as _Subscript
    from .py_ast import TupleExpr as _TupleExpr

    _profile_end(profile, "build_closed_world_context_import_py_ast", import_t)
    import_t = _profile_begin(profile)
    from ..parse.py_lift import lift_module as _lift_module
    from ..parse.py_parse import parse as _parse_python_module

    _profile_end(profile, "build_closed_world_context_import_py_lift", import_t)

    lift_index_set = None
    if lift_indices is not None:
        lift_index_set = {}
        for lift_index in lift_indices:
            lift_index_set[int(lift_index)] = True

    parsed_modules = []
    native_exports = {}
    module_index = 0
    for src, mod_name in zip(src_paths, module_names):
        module_t = _profile_begin(profile)
        parse_t = _profile_begin(profile)
        with open(src, "r", encoding="utf-8") as f:
            source = f.read()
        try:
            raw_mod = _parse_python_module(source, filename=src)
        except Exception as ex:
            from ..parse.py_lift import LiftError as _LiftError

            raise _LiftError(
                "parse failed for "
                + mod_name
                + " in "
                + src
                + ": "
                + type(ex).__name__
                + ": "
                + str(ex)
            )
        _profile_end(profile, "build_closed_world_context_parse", parse_t, mod_name)
        lift_t = _profile_begin(profile)
        if lift_index_set is None or module_index in lift_index_set:
            ast_mod = _lift_module(raw_mod, src, mod_name)
        else:
            ast_mod = _closed_world_shallow_lift_module(raw_mod, src, mod_name)
        _profile_end(profile, "build_closed_world_context_lift", lift_t, mod_name)
        parsed_modules.append(ast_mod)
        exports = {}
        class_field_defs = {}
        class_field_names = {}
        top_level_func_names = set()
        ast_body = _py_ast_field_value(ast_mod, "body", ())
        typing_metadata_bindings = {}
        typing_module_aliases = set()
        typing_metadata_exports = (
            "Any",
            "Callable",
            "ClassVar",
            "Dict",
            "Final",
            "Generic",
            "Iterable",
            "Iterator",
            "List",
            "Literal",
            "Mapping",
            "NoReturn",
            "Optional",
            "Protocol",
            "Sequence",
            "Set",
            "SupportsIndex",
            "Tuple",
            "Type",
            "TypeAlias",
            "TypeAliasType",
            "TypedDict",
            "TypeVar",
            "Union",
        )
        for stmt in ast_body:
            if _closed_world_is_node(stmt, _ImportFrom) and (
                _py_ast_field_value(stmt, "module", "") == "typing"
                and not _py_ast_field_value(stmt, "level", 0)
            ):
                for attr_name, as_name in _py_ast_field_value(stmt, "names", ()):
                    if attr_name in typing_metadata_exports:
                        typing_metadata_bindings[as_name or attr_name] = attr_name
                continue
            if not _closed_world_is_node(stmt, _Import):
                continue
            for imported_module, as_name in _py_ast_field_value(stmt, "names", ()):
                if imported_module == "typing":
                    typing_module_aliases.add(as_name or "typing")

        def is_typing_metadata_expr(expr):
            if _closed_world_is_node(expr, _Name):
                return (
                    _py_ast_field_value(expr, "ident", "")
                    in typing_metadata_bindings
                )
            if _closed_world_is_node(expr, _Attr):
                obj = _py_ast_field_value(expr, "obj", None)
                return (
                    _closed_world_is_node(obj, _Name)
                    and _py_ast_field_value(obj, "ident", "")
                    in typing_module_aliases
                    and _py_ast_field_value(expr, "name", "")
                    in typing_metadata_exports
                )
            if _closed_world_is_node(expr, _Subscript):
                return is_typing_metadata_expr(
                    _py_ast_field_value(expr, "obj", None)
                )
            if _closed_world_is_node(expr, _Call):
                return is_typing_metadata_expr(
                    _py_ast_field_value(expr, "func", None)
                )
            if _closed_world_is_node(expr, _BinOp) and _py_ast_field_value(
                expr,
                "op",
                "",
            ) == "|":
                return is_typing_metadata_expr(
                    _py_ast_field_value(expr, "lhs", None)
                ) or is_typing_metadata_expr(
                    _py_ast_field_value(expr, "rhs", None)
                )
            if _closed_world_is_node(expr, _TupleExpr):
                for elem in _py_ast_field_value(expr, "elems", ()):
                    if is_typing_metadata_expr(elem):
                        return True
            return False

        for stmt in ast_body:
            if _closed_world_is_node(stmt, _FuncDef):
                top_level_func_names.add(_py_ast_field_value(stmt, "name", ""))

        def decorator_root_name(expr):
            if _closed_world_is_node(expr, _Call):
                return decorator_root_name(_py_ast_field_value(expr, "func", None))
            current = expr
            while _closed_world_is_node(current, _Attr):
                current = _py_ast_field_value(current, "obj", None)
            if _closed_world_is_node(current, _Name):
                return _py_ast_field_value(current, "ident", "")
            return ""

        partial_decorator_factories = set()
        for stmt in ast_body:
            if not _closed_world_is_node(stmt, _Assign):
                continue
            targets = _py_ast_field_value(stmt, "targets", ())
            if len(targets) != 1 or not _closed_world_is_node(targets[0], _Name):
                continue
            value = _py_ast_field_value(stmt, "value", None)
            if not _closed_world_is_node(value, _Call):
                continue
            partial_func = _py_ast_field_value(value, "func", None)
            is_partial = (
                _closed_world_is_node(partial_func, _Name)
                and _py_ast_field_value(partial_func, "ident", "") == "partial"
            ) or (
                _closed_world_is_node(partial_func, _Attr)
                and _py_ast_field_value(partial_func, "name", "") == "partial"
            )
            if is_partial:
                partial_decorator_factories.add(
                    _py_ast_field_value(targets[0], "ident", "")
                )

        def has_semantic_native_decorator(stmt):
            for decorator in _py_ast_field_value(stmt, "decorators", ()):
                if _closed_world_is_node(decorator, _Call):
                    if decorator_root_name(decorator) in partial_decorator_factories:
                        return True
                    continue
                if _closed_world_is_node(decorator, _Name):
                    if (
                        _py_ast_field_value(decorator, "ident", "")
                        in top_level_func_names
                    ):
                        return True
            return False

        module_box_int_abi = not (
            mod_name == "pcc"
            or mod_name.startswith("pcc.")
            or mod_name == "bootstrap"
            or mod_name.startswith("bootstrap.")
        )
        for stmt in ast_body:
            if _closed_world_is_node(stmt, _FuncDef):
                function_box_int_abi = module_box_int_abi
                if module_box_int_abi and _export_func_uses_unboxed_typed_int_abi(stmt):
                    function_box_int_abi = False
                docstring = None
                stmt_body = _py_ast_field_value(stmt, "body", ())
                if (
                    stmt_body
                    and _closed_world_is_node(stmt_body[0], _ExprStmt)
                    and _closed_world_is_node(
                        _py_ast_field_value(stmt_body[0], "expr", None),
                        _StrLit,
                    )
                ):
                    docstring = _export_literal_value_or_none(
                        _py_ast_field_value(stmt_body[0], "expr", None)
                    )
                stmt_name = _py_ast_field_value(stmt, "name", "")
                stmt_args = _py_ast_field_value(stmt, "args", ())
                exports[stmt_name] = {
                    "kind": "function",
                    "owning_module": mod_name,
                    "export_name": stmt_name,
                    "return_ty": _export_return_type(_export_return_ty_or_none(stmt)),
                    "param_types": _export_param_types(stmt_args),
                    "call_sig": _export_call_sig(
                        stmt_args,
                        mod_name,
                        top_level_func_names,
                    ),
                    "is_async": bool(_py_ast_field_value(stmt, "is_async", False)),
                    "box_int_abi": function_box_int_abi,
                    "docstring": docstring,
                }
                if has_semantic_native_decorator(stmt):
                    # The public module binding is the decorator result, not
                    # the undecorated ``user_<module>_<name>`` entry point.
                    # Cross-module callers must load that stable object.
                    exports[stmt_name]["semantic_decorator"] = True
                    exports[stmt_name]["needs_object"] = True
                if _closed_world_is_identity_decorator(stmt):
                    exports[stmt_name]["identity_decorator"] = True
                continue

            if _closed_world_is_node(stmt, _Assign):
                stmt_targets = _py_ast_field_value(stmt, "targets", ())
                if len(stmt_targets) != 1 or not _closed_world_is_node(
                    stmt_targets[0], _Name
                ):
                    for target_name in _closed_world_module_block_assign_targets(stmt):
                        exports[target_name] = _closed_world_dyn_module_global_export(
                            mod_name,
                            target_name,
                        )
                    continue
                target_name = _py_ast_field_value(stmt_targets[0], "ident", "")
                value = _py_ast_field_value(stmt, "value", None)
                annotation = _py_ast_field_value(stmt, "annotation", None)
                annotation_name = _py_ast_field_value(annotation, "name", "")
                if (
                    typing_metadata_bindings.get(annotation_name) == "TypeAlias"
                    or is_typing_metadata_expr(value)
                ):
                    exports[target_name] = {
                        "kind": "typing_metadata",
                        "owning_module": mod_name,
                        "export_name": target_name,
                    }
                    typing_metadata_bindings[target_name] = "alias"
                    continue
                if _closed_world_is_node(value, _StrLit):
                    literal_value = _export_literal_value_or_none(value)
                    if literal_value is None:
                        continue
                    exports[target_name] = {
                        "kind": "constant",
                        "owning_module": mod_name,
                        "export_name": target_name,
                        "value_kind": "str",
                        "value": literal_value,
                    }
                elif _closed_world_is_node(value, _IntLit):
                    literal_value = _export_literal_value_or_none(value)
                    if literal_value is None:
                        continue
                    exports[target_name] = {
                        "kind": "constant",
                        "owning_module": mod_name,
                        "export_name": target_name,
                        "value_kind": "int",
                        "value": int(literal_value),
                    }
                elif _closed_world_is_node(value, _BoolLit):
                    literal_value = _export_literal_value_or_none(value)
                    if literal_value is None:
                        continue
                    exports[target_name] = {
                        "kind": "constant",
                        "owning_module": mod_name,
                        "export_name": target_name,
                        "value_kind": "bool",
                        "value": bool(literal_value),
                    }
                elif _closed_world_is_node(value, _NoneLit):
                    exports[target_name] = {
                        "kind": "constant",
                        "owning_module": mod_name,
                        "export_name": target_name,
                        "value_kind": "none",
                        "value": None,
                    }
                else:
                    value_ty = _export_static_literal_type(value)
                    if value_ty is None and value is not None:
                        # Computed module-top binding (e.g. ``V = 5 + 3``,
                        # ``V = f() + 8``).  pcc cannot statically type the RHS,
                        # but the binding is a real module global: the module's
                        # init code computes it and stores into the
                        # ``.modvar.<mod>.<name>`` slot (confirmed in IR).
                        # Register as DynType so cross-package ``mod.V`` resolves
                        # via the extern module-global load instead of falling
                        # back to ``py_obj_getattr`` on the module-name string,
                        # which raised AttributeError.  Mirrors the Name/Attr
                        # DynType treatment in ``_export_static_literal_type``.
                        # See docs/investigations/
                        # python-package-init-computed-module-attr-no-libpython.md
                        from .py_ast import DynType as _DynType

                        value_ty = _DynType("dyn")
                    if value_ty is not None:
                        exports[target_name] = {
                            "kind": "module_global",
                            "owning_module": mod_name,
                            "export_name": target_name,
                            "value_ty": encode_type(value_ty),
                        }
                if target_name == "__all__" and target_name in exports:
                    all_names = _export_static_all_names(value)
                    if all_names is not None:
                        exports[target_name]["export_names"] = all_names
                continue

            for target_name in _closed_world_module_block_assign_targets(stmt):
                if target_name in exports:
                    continue
                exports[target_name] = _closed_world_dyn_module_global_export(
                    mod_name,
                    target_name,
                )

            if not _closed_world_is_node(stmt, _ClassDef):
                continue

            stmt_name = str(_py_ast_field_value(stmt, "name", ""))
            stmt_bases = _py_ast_field_value(stmt, "bases", ())
            stmt_body = _py_ast_field_value(stmt, "body", ())
            class_is_dataclass = _class_is_dataclass(stmt)
            field_names = []
            field_defs = []
            for base_expr in stmt_bases:
                if not _closed_world_is_node(base_expr, _Name):
                    continue
                base_ident = _py_ast_field_value(base_expr, "ident", "")
                for inherited_name in class_field_names.get(base_ident, ()):
                    if inherited_name not in field_names:
                        field_names.append(inherited_name)
                for inherited in class_field_defs.get(base_ident, ()):
                    field_defs.append(inherited)

            methods = []
            for body_stmt in stmt_body:
                if _closed_world_is_node(body_stmt, _Assign):
                    body_value = _py_ast_field_value(body_stmt, "value", None)
                    for target in _py_ast_field_value(body_stmt, "targets", ()):
                        if (
                            _closed_world_is_node(target, _Name)
                            and _py_ast_field_value(target, "ident", "") == "__slots__"
                        ):
                            slot_names = []
                            if _closed_world_is_node(body_value, _StrLit):
                                slot_value = _export_literal_value_or_none(body_value)
                                if slot_value is not None:
                                    slot_names.append(slot_value)
                            elif _closed_world_is_node(
                                body_value,
                                (_TupleExpr, _ListExpr),
                            ):
                                for slot_elem in _py_ast_field_value(
                                    body_value,
                                    "elems",
                                    (),
                                ):
                                    if _closed_world_is_node(slot_elem, _StrLit):
                                        slot_value = _export_literal_value_or_none(
                                            slot_elem
                                        )
                                        if slot_value is not None:
                                            slot_names.append(slot_value)
                            for slot_name in slot_names:
                                if (
                                    slot_name not in ("__dict__", "__weakref__")
                                    and slot_name not in field_names
                                ):
                                    field_names.append(slot_name)
                        if class_is_dataclass and _closed_world_is_node(target, _Name):
                            target_ident = _py_ast_field_value(target, "ident", "")
                            if target_ident not in field_names:
                                field_names.append(target_ident)
                            body_ann = _export_annotation_or_none(body_stmt)
                            field_defs.append(
                                {
                                    "name": target_ident,
                                    "annotation": body_ann,
                                    "default": body_value,
                                    "has_default": body_value is not None,
                                }
                            )
                    continue

                if not _closed_world_is_node(body_stmt, _FuncDef):
                    continue

                body_stmt_name = str(_py_ast_field_value(body_stmt, "name", ""))
                body_stmt_args = _py_ast_field_value(body_stmt, "args", ())
                body_stmt_body = _py_ast_field_value(body_stmt, "body", ())
                body_stmt_decorators = _py_ast_field_value(body_stmt, "decorators", ())

                if body_stmt_name == "__init__":
                    init_param_anns = {}
                    for arg in body_stmt_args:
                        arg_name = _py_ast_field_value(arg, "name", "")
                        if arg_name in ("", "self", "cls"):
                            continue
                        arg_ann = _export_annotation_or_none(arg)
                        if arg_ann is not None:
                            init_param_anns[arg_name] = arg_ann
                    for init_stmt in body_stmt_body:
                        if not _closed_world_is_node(init_stmt, _Assign):
                            continue
                        init_value = _py_ast_field_value(init_stmt, "value", None)
                        inferred_ann = _export_annotation_or_none(init_stmt)
                        if (
                            inferred_ann is None
                            and _closed_world_is_node(init_value, _Name)
                            and _py_ast_field_value(init_value, "ident", "")
                            in init_param_anns
                        ):
                            inferred_ann = init_param_anns[
                                _py_ast_field_value(init_value, "ident", "")
                            ]
                        for target in _py_ast_field_value(init_stmt, "targets", ()):
                            target_obj = _py_ast_field_value(target, "obj", None)
                            if not (
                                _closed_world_is_node(target, _Attr)
                                and _closed_world_is_node(target_obj, _Name)
                                and _py_ast_field_value(target_obj, "ident", "")
                                == "self"
                            ):
                                continue
                            target_name = _py_ast_field_value(target, "name", "")
                            if target_name not in field_names:
                                field_names.append(target_name)
                            if inferred_ann is None:
                                continue
                            field_already_defined = False
                            for field_def in field_defs:
                                if field_def["name"] == target_name:
                                    field_already_defined = True
                                    break
                            if field_already_defined:
                                continue
                            field_defs.append(
                                {
                                    "name": target_name,
                                    "annotation": inferred_ann,
                                    "default": None,
                                    "has_default": False,
                                }
                            )

                kind = "instance"
                for dec in body_stmt_decorators:
                    if _closed_world_is_node(dec, _Name):
                        dec_ident = _py_ast_field_value(dec, "ident", "")
                        if dec_ident == "staticmethod":
                            kind = "static"
                        elif dec_ident == "classmethod":
                            kind = "classmethod"
                        elif dec_ident == "property":
                            kind = "property_getter"
                methods.append(
                    {
                        "name": body_stmt_name,
                        "symbol": _export_method_symbol(
                            mod_name,
                            stmt_name,
                            body_stmt_name,
                            top_level_func_names,
                        ),
                        "kind": kind,
                        "return_ty": _export_return_type(
                            _export_return_ty_or_none(body_stmt)
                        ),
                        "param_types": _export_param_types(body_stmt_args),
                        "call_sig": _export_call_sig(
                            body_stmt_args,
                            mod_name,
                            top_level_func_names,
                        ),
                        "is_async": bool(
                            _py_ast_field_value(body_stmt, "is_async", False)
                        ),
                        "box_int_abi": module_box_int_abi,
                    }
                )

            class_field_defs[stmt_name] = tuple(field_defs)
            class_field_names[stmt_name] = tuple(field_names)
            init_method_exists = False
            for method in methods:
                if method["name"] == "__init__":
                    init_method_exists = True
                    break
            if class_is_dataclass and field_defs and not init_method_exists:
                init_sig = [
                    {
                        "name": "self",
                        "kind": "pos",
                        "annotation": None,
                        "default": None,
                        "has_default": False,
                    }
                ]
                init_param_types = [("dyn",)]
                for field in field_defs:
                    field_ann = field.get("annotation")
                    init_sig.append(
                        {
                            "name": field["name"],
                            "kind": "pos",
                            "annotation": (
                                encode_type(field_ann)
                                if field_ann is not None
                                else None
                            ),
                            "default": field["default"],
                            "has_default": field["has_default"],
                        }
                    )
                    init_param_types.append(
                        encode_type(field_ann) if field_ann is not None else ("dyn",)
                    )
                methods.append(
                    {
                        "name": "__init__",
                        "symbol": _export_method_symbol(
                            mod_name,
                            stmt_name,
                            "__init__",
                            top_level_func_names,
                        ),
                        "kind": "instance",
                        "return_ty": ("none",),
                        "param_types": tuple(init_param_types),
                        "call_sig": tuple(init_sig),
                        "box_int_abi": module_box_int_abi,
                    }
                )

            if (
                mod_name == "pcc.py_frontend.codegen.layer1"
                and stmt_name == "L1CodeGen"
            ):
                for host_attr_name in L1_CODEGEN_HOST_ATTRS:
                    if host_attr_name not in field_names:
                        field_names.append(host_attr_name)

            if mod_name == "pcc.py_frontend.py_ast":
                override_names = _PY_AST_FIELD_NAME_OVERRIDES.get(str(stmt_name))
                if override_names is not None and tuple(field_names) != tuple(
                    override_names
                ):
                    field_names = list(override_names)
                    field_defs = []
                    for override_name in override_names:
                        field_defs.append(
                            {
                                "name": override_name,
                                "annotation": None,
                                "default": None,
                                "has_default": False,
                            }
                        )

            field_types_table = []
            for field_def in field_defs:
                ann = field_def.get("annotation")
                if ann is not None:
                    field_types_table.append(
                        (
                            field_def["name"],
                            encode_type(ann),
                        )
                    )
            if mod_name == "pcc.py_frontend.py_ast":
                field_types_table = []
                for field_name in field_names:
                    field_type_text = _py_ast_field_type_override(
                        str(stmt_name),
                        field_name,
                    )
                    if field_type_text is None:
                        continue
                    field_ty = _normalise_export_annotation_text(field_type_text)
                    if field_ty is not None:
                        field_types_table.append(
                            (
                                field_name,
                                encode_type(field_ty),
                            )
                        )
            base_names = []
            for base in stmt_bases:
                base_ident = _py_ast_field_value(base, "ident", "")
                if _closed_world_is_node(base, _Name) and base_ident != "object":
                    base_names.append(base_ident)
            if mod_name == "pcc.py_frontend.py_ast":
                override_bases = _PY_AST_BASE_NAME_OVERRIDES.get(str(stmt_name))
                if override_bases is not None and tuple(base_names) != tuple(
                    override_bases
                ):
                    base_names = list(override_bases)
            exports[stmt_name] = {
                "kind": "class",
                "owning_module": mod_name,
                "export_name": stmt_name,
                "class_name": stmt_name,
                "qualified_name": f"{mod_name}.{stmt_name}",
                "base_names": tuple(base_names),
                "field_names": tuple(field_names),
                "field_types": tuple(field_types_table),
                "methods": tuple(methods),
                "box_int_abi": module_box_int_abi,
            }
        native_exports[mod_name] = exports
        _profile_end(profile, "build_closed_world_context_module", module_t, mod_name)
        module_index += 1

    if merge_exports:
        _merge_closed_world_reexports(
            parsed_modules,
            module_names,
            src_paths,
            native_exports,
        )
        _repair_closed_world_default_global_owners(native_exports)
        _merge_l1_mixin_stack_methods(native_exports)
        _merge_l1_codegen_methods(native_exports)

    _mark_closed_world_function_object_exports(
        parsed_modules,
        module_names,
        src_paths,
        native_exports,
    )

    derived_class_map = _closed_world_derived_class_map(native_exports)
    return parsed_modules, native_exports, derived_class_map


def _closed_world_derived_class_map(native_exports):
    base_to_derived = {}
    for derived_mod, exports in native_exports.items():
        for class_name, info in exports.items():
            if not isinstance(info, dict) or info.get("kind") != "class":
                continue
            for base_name in info.get("base_names", ()):
                base_to_derived.setdefault(base_name, []).append(
                    (derived_mod, class_name)
                )
    derived_class_map = {}
    for base_name, derived_list in base_to_derived.items():
        if len(derived_list) == 1:
            derived_class_map[base_name] = derived_list[0]
    return derived_class_map


def _merge_l1_mixin_stack_methods(native_exports):
    stack_exports = native_exports.get("pcc.py_frontend.codegen.layer1_mixins")
    if not stack_exports:
        return
    stack_info = stack_exports.get("L1CodeGenMixinStack")
    if not isinstance(stack_info, dict) or stack_info.get("kind") != "class":
        return
    methods = []
    seen = {}
    for method in stack_info.get("methods", ()):
        name = method.get("name") if isinstance(method, dict) else None
        if name is not None:
            seen[name] = True
        methods.append(method)
    for base_name in stack_info.get("base_names", ()):
        for _module_name, exports in native_exports.items():
            base_info = exports.get(base_name)
            if not isinstance(base_info, dict) or base_info.get("kind") != "class":
                continue
            for method in base_info.get("methods", ()):
                name = method.get("name") if isinstance(method, dict) else None
                if name is not None and name in seen:
                    continue
                if name is not None:
                    seen[name] = True
                methods.append(method)
            break
    stack_info["methods"] = tuple(methods)


def _merge_l1_codegen_methods(native_exports):
    layer1_exports = native_exports.get("pcc.py_frontend.codegen.layer1")
    if not layer1_exports:
        return
    l1_info = layer1_exports.get("L1CodeGen")
    if not isinstance(l1_info, dict) or l1_info.get("kind") != "class":
        return
    methods = []
    seen = {}
    for method in l1_info.get("methods", ()):
        name = method.get("name") if isinstance(method, dict) else None
        if name is not None:
            seen[name] = True
        methods.append(method)
    for base_name in l1_info.get("base_names", ()):
        for _module_name, exports in native_exports.items():
            base_info = exports.get(base_name)
            if not isinstance(base_info, dict) or base_info.get("kind") != "class":
                continue
            for method in base_info.get("methods", ()):
                name = method.get("name") if isinstance(method, dict) else None
                if name is not None and name in seen:
                    continue
                if name is not None:
                    seen[name] = True
                methods.append(method)
            break
    l1_info["methods"] = tuple(methods)


def _contextual_host_params_for_module(ast_mod, module_name: str):
    """Return helper-function host params that should type as L1CodeGen.

    This is deliberately narrow. It only applies inside codegen modules and
    only to top-level helpers whose first parameter is named ``host``. That
    gives future layer1 helper extractions an explicit host-context path
    without changing ordinary user/program inference.
    """
    module_name = str(module_name or "")
    if not module_name.startswith("pcc.py_frontend.codegen."):
        return None
    out = {}
    from .py_ast import FuncDef as _FuncDef

    for stmt in _py_ast_field_value(ast_mod, "body", ()) or ():
        if not _closed_world_is_node(stmt, _FuncDef):
            continue
        args = _py_ast_field_value(stmt, "args", ()) or ()
        if not args:
            continue
        first = args[0]
        if _py_ast_field_value(first, "name", "") == "host":
            out[_py_ast_field_value(stmt, "name", "")] = ("host",)
    if not out:
        return None
    return out


def count_py_cpy_fallback_calls(ir_text: str) -> int:
    count = 0
    for line in ir_text.splitlines():
        if line.find("@py_cpy_") >= 0 and line.find("call ") >= 0:
            count += 1
    return count


def _copy_native_module_exports(exports):
    out = {}
    if exports is None:
        return out
    for key in exports:
        out[key] = exports[key]
    return out


def _module_uses_default_native_exports(module_name: str) -> bool:
    return _default_native_module_exports(module_name) is not None


PROBE_POLICY_STANDALONE = "standalone"


def compile_contextual_per_module_fallback_counts(
    src_paths,
    module_names,
    contextual_modules,
    *,
    ir_scaffold_mode: str,
    strict_no_libpython: bool = False,
    emit_ir_dir: Optional[str] = None,
    entry_module: Optional[str] = None,
):
    """Return ``py_cpy_*`` call counts for modules under closed-world context.

    This is the diagnostic counterpart to ``compile_python_multi``. It
    compiles selected modules one at a time, but with the same export table
    and mixin self-type context as the full closed-world compile. Use this
    for mixin modules; raw single-file probing gives their ``self`` the
    wrong type.
    """
    from .type_infer import infer_module as _infer_module
    from .codegen.layer1 import L1CodeGen as _L1CodeGen

    wanted = []
    for mod_name in contextual_modules:
        wanted.append(mod_name)
    parsed_modules, native_exports, derived_class_map = build_closed_world_context(
        src_paths, module_names, profile=None
    )
    out = {}
    for ast_mod, mod_name in zip(parsed_modules, module_names):
        should_compile = False
        for wanted_name in wanted:
            if mod_name == wanted_name:
                should_compile = True
                break
        if not should_compile:
            continue
        try:
            external_exports = {}
            for k, v in native_exports.items():
                if k != mod_name:
                    external_exports[k] = v
            typed_mod = _infer_module(
                ast_mod,
                external_exports=external_exports,
                derived_class_map=derived_class_map,
                contextual_host_params=_contextual_host_params_for_module(
                    ast_mod,
                    mod_name,
                ),
            )
            codegen = _L1CodeGen(
                typed_mod,
                emit_cpy_main_exitcode=False,
                ir_scaffold_mode=ir_scaffold_mode,
            )
            codegen._strict_no_libpython = strict_no_libpython
            codegen._prefer_native_callable_values = strict_no_libpython
            if entry_module is not None:
                codegen._skip_program_main = mod_name != entry_module
            if _module_uses_default_native_exports(mod_name):
                codegen_exports = _copy_native_module_exports(
                    codegen._native_module_exports
                )
            else:
                codegen_exports = {}
            for k, v in native_exports.items():
                if k != mod_name:
                    codegen_exports[k] = v
            codegen._native_module_exports = codegen_exports
            codegen._native_function_object_exports = (
                _closed_world_function_object_exports(native_exports, mod_name)
            )
            ir_text = str(codegen.generate(typed_mod))
            out[mod_name] = count_py_cpy_fallback_calls(ir_text)
            if emit_ir_dir is not None:
                # ponytail: caller must pre-create emit_ir_dir. os.makedirs has
                # no no-libpython lowering, and this debug-only IR-dump path
                # would otherwise reintroduce a py_cpy_* fallback into
                # pipeline.py's own per-module ratchet (this function is a
                # test/diagnostic helper — no production caller passes
                # emit_ir_dir). Native os.makedirs is tracked separately.
                ir_name = mod_name.replace(".", "_") + ".ll"
                with open(
                    os.path.join(emit_ir_dir, ir_name),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(ir_text)
        except Exception:
            out[mod_name] = -1
    return out


def _runtime_archive_stale(archive: str) -> bool:
    if not os.path.isfile(archive):
        return True
    if not _runtime_archive_target_matches(archive):
        return True
    archive_mtime = os.path.getmtime(archive)
    archive_base = str(os.path.basename(archive))
    if _runtime_archive_compiler_sources_newer_than(archive_base, archive_mtime):
        return True
    archive_uses_libpython = archive_base in (
        "libpy_runtime_libpython.a",
        "libpy_runtime_pcc_py_libpython.a",
    )
    archive_uses_pcc_python = archive_base in (
        "libpy_runtime_pcc_py.a",
        "libpy_runtime_pcc_py_libpython.a",
    )
    replaced_c_modules = (
        _runtime_pcc_python_replaced_c_modules()
        if archive_uses_pcc_python
        else set()
    )
    header = os.path.join(_PY_RUNTIME_DIR, "include", "py_runtime.h")
    if os.path.isfile(header) and os.path.getmtime(header) > archive_mtime:
        return True
    src_dir = os.path.join(_PY_RUNTIME_DIR, "src")
    if os.path.isdir(src_dir):
        for name in os.listdir(src_dir):
            if not name.endswith(".c"):
                continue
            if name == "py_libpython.c" and not archive_uses_libpython:
                continue
            if name[:-2] in replaced_c_modules:
                continue
            path = os.path.join(src_dir, name)
            if os.path.isfile(path) and os.path.getmtime(path) > archive_mtime:
                return True
    if archive_base in (
        "libpy_runtime_pcc_py.a",
        "libpy_runtime_pcc_py_libpython.a",
    ):
        py_dir = os.path.join(_PY_RUNTIME_DIR, "py")
        if os.path.isdir(py_dir):
            for name in os.listdir(py_dir):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(py_dir, name)
                if os.path.isfile(path) and os.path.getmtime(path) > archive_mtime:
                    return True
        if archive_base == "libpy_runtime_pcc_py_libpython.a":
            base_archive = _PY_RUNTIME_ARCHIVE_PCC_PY
            if (
                os.path.isfile(base_archive)
                and os.path.getmtime(base_archive) > archive_mtime
            ):
                return True
    makefile = os.path.join(_PY_RUNTIME_DIR, "Makefile")
    return os.path.isfile(makefile) and os.path.getmtime(makefile) > archive_mtime


def _runtime_makefile_variable_words(name: str) -> list[str]:
    """Read one simple make variable without duplicating its module list."""
    makefile = os.path.join(_PY_RUNTIME_DIR, "Makefile")
    if not os.path.isfile(makefile):
        return []
    try:
        with open(makefile, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not (line.startswith(name + " =") or line.startswith(name + " +=")):
            i += 1
            continue
        line = line.split("=", 1)[1].strip()
        while True:
            continued = line.endswith("\\")
            if continued:
                line = line[:-1].strip()
            if line:
                out.extend(line.split())
            if not continued:
                break
            i += 1
            if i >= len(lines):
                break
            line = lines[i].strip()
        i += 1
    return out


def _runtime_pcc_python_replaced_c_modules() -> set[str]:
    py_modules = set(_runtime_makefile_variable_words("PY_MODULES"))
    replaced = set()
    for word in _runtime_makefile_variable_words("PY_REPLACED_C_MODULES"):
        if word == "$(PY_MODULES)":
            replaced.update(py_modules)
        else:
            replaced.add(word)
    return replaced


def _runtime_archive_compiler_sources_newer_than(
    archive_base: str,
    archive_mtime: float,
) -> bool:
    if archive_base not in (
        "libpy_runtime_pcc.a",
        "libpy_runtime_pcc_py.a",
        "libpy_runtime_pcc_py_libpython.a",
    ):
        return False
    roots = (
        os.path.join(_PCC_DIR, "backend"),
        os.path.join(_PCC_DIR, "codegen"),
        os.path.join(_PCC_DIR, "evaluater"),
        os.path.join(_PCC_DIR, "llvm_capi"),
        os.path.join(_PCC_DIR, "parse"),
        os.path.join(_PCC_DIR, "py_frontend"),
        os.path.join(_PCC_DIR, "tools"),
        os.path.join(_PCC_DIR, "__main__.py"),
        os.path.join(_PCC_DIR, "api.py"),
        os.path.join(_PCC_DIR, "cli_core.py"),
        os.path.join(_PCC_DIR, "pcc.py"),
        os.path.join(_PCC_DIR, "project.py"),
    )
    for root in roots:
        if os.path.isfile(root):
            if os.path.getmtime(root) > archive_mtime:
                return True
            continue
        if not os.path.isdir(root):
            continue
        pending_dirs = [root]
        while pending_dirs:
            dirpath = pending_dirs.pop()
            try:
                names = os.listdir(dirpath)
            except OSError:
                continue
            for filename in names:
                if (
                    filename == "__pycache__"
                    or filename == ".pytest_cache"
                    or filename.startswith(".")
                ):
                    continue
                path = os.path.join(dirpath, filename)
                if os.path.isdir(path):
                    pending_dirs.append(path)
                    continue
                if not (
                    filename.endswith(".py")
                    or filename.endswith(".c")
                    or filename.endswith(".h")
                ):
                    continue
                if os.path.isfile(path) and os.path.getmtime(path) > archive_mtime:
                    return True
    return False


def _is_py_runtime_library_source(src_path: str) -> bool:
    runtime_py_dir = os.path.abspath(os.path.join(_PY_RUNTIME_DIR, "py"))
    source_path = os.path.abspath(src_path)
    if source_path.startswith(runtime_py_dir + os.sep):
        return True
    parts = source_path.split(os.sep)
    for i, part in enumerate(parts):
        if part != "py" or i == 0:
            continue
        parent = parts[i - 1]
        if parent == "py_runtime" or parent.startswith("py_runtime_"):
            return True
    return False


def _runtime_archive_target_stamp(archive: str) -> str:
    return str(archive) + ".target"


def _runtime_archive_target_matches(archive: str) -> bool:
    stamp = _runtime_archive_target_stamp(archive)
    if not os.path.isfile(stamp):
        return False
    try:
        with open(stamp, "r", encoding="utf-8") as f:
            return f.read().strip() == _runtime_archive_target_id()
    except OSError:
        return False


def _runtime_archive_target_id() -> str:
    import platform

    machine = platform.machine().lower()
    if machine in ("amd64", "x64"):
        machine = "x86_64"
    return f"{sys.platform}:{machine}:{_host_target_triple_for_self_backend()}"


def _write_runtime_archive_target_stamp(archive: str) -> None:
    try:
        with open(_runtime_archive_target_stamp(archive), "w", encoding="utf-8") as f:
            f.write(_runtime_archive_target_id() + "\n")
    except OSError:
        pass


def _run_runtime_make(make_cmd, *, verbose: bool) -> None:
    lock_dir = os.path.join(_PY_RUNTIME_DIR, ".pcc-runtime-build.lock")
    lock_script = (
        'lock="$1"; shift; i=0; '
        'while ! mkdir "$lock" 2>/dev/null; do '
        "i=$((i + 1)); "
        'if [ "$i" -gt 3000 ]; then '
        'echo "timed out waiting for pcc runtime build lock: $lock" >&2; '
        "exit 124; "
        "fi; "
        "sleep 0.1; "
        "done; "
        "trap 'rmdir \"$lock\"' EXIT INT TERM; "
        '"$@"'
    )
    subprocess.run(
        ["sh", "-c", lock_script, "sh", lock_dir, *make_cmd],
        check=True,
        capture_output=not verbose,
    )


def _runtime_cc_mode() -> str:
    """Return the selected runtime-compiler mode ('cc' or 'pcc').

    Controlled by $PCC_RUNTIME_CC (Phase 2 of the runtime self-host
    plan). Default is now 'pcc' for no-libpython Python builds: the
    bootstrap-safe path should use the pcc-emitted runtime archive.
    Set PCC_RUNTIME_CC=cc explicitly for the host-cc oracle archive.
    """
    value = str(os.environ.get(_PY_RUNTIME_CC_ENV, "") or "").strip().lower()
    if value in ("cc", "c", "host"):
        return "cc"
    if value in ("pcc", "self"):
        return "pcc"
    return "pcc"


def _runtime_high_mode() -> str:
    """Return the selected runtime-high source ('c' or 'py').

    Controlled by $PCC_RUNTIME_HIGH (Phase 4 of the runtime self-host
    plan). Default is now 'py' for no-libpython Python builds.
    'c' = runtime-high modules compiled from C, kept for the pcc-C
    oracle path. 'py' = runtime-high modules compiled from pcc-Python
    ports.
    """
    value = str(os.environ.get(_PY_RUNTIME_HIGH_ENV, "") or "").strip().lower()
    if value in ("c", "cc"):
        return "c"
    if value in ("py", "python"):
        return "py"
    return "py"


def _runtime_host_python_for_make() -> str:
    exe = _host_python_command()
    if exe and not os.path.isabs(exe):
        return os.path.abspath(exe)
    return exe


def _ensure_runtime(
    verbose: bool,
    *,
    needs_libpython: bool = False,
) -> Optional[str]:
    """Locate (and optionally build) the required runtime archive.

    Returns the archive path chosen for linking. When the existence
    probe fails we still return that path after warning so the final
    clang invocation can surface a concrete missing-file/link error
    instead of silently omitting the runtime archive.
    """
    explicit_archive = str(
        os.environ.get(_PY_RUNTIME_ARCHIVE_ENV, "") or ""
    ).strip()
    if explicit_archive:
        explicit_archive = os.path.abspath(explicit_archive)
        if not os.path.isfile(explicit_archive):
            raise PyPipelineError(
                "explicit runtime archive not found: " + explicit_archive
            )
        _log(verbose, "runtime archive (explicit): " + explicit_archive)
        return explicit_archive

    runtime_dir = str(os.environ.get(_PY_RUNTIME_DIR_ENV, "") or "").strip()
    runtime_dir = (
        os.path.abspath(runtime_dir) if runtime_dir else _PY_RUNTIME_DIR
    )
    if not os.path.isdir(runtime_dir):
        raise PyPipelineError("explicit runtime directory not found: " + runtime_dir)

    cc_mode = _runtime_cc_mode()
    high_mode = _runtime_high_mode()
    if cc_mode == "pcc":
        # Bootstrap-safe default: pcc-emitted runtime archive with
        # pcc-Python runtime-high modules. PCC_RUNTIME_HIGH=c remains
        # available for the pcc-C oracle path. When CPython fallback is
        # still needed, keep the pcc-Python archive as the base and add
        # only the py_libpython compatibility bridge.
        if high_mode == "py":
            archive = (
                _PY_RUNTIME_ARCHIVE_PCC_PY_LIBPYTHON
                if needs_libpython
                else _PY_RUNTIME_ARCHIVE_PCC_PY
            )
        elif needs_libpython:
            archive = _PY_RUNTIME_ARCHIVE_LIBPYTHON
        else:
            archive = _PY_RUNTIME_ARCHIVE_PCC
    else:
        archive = (
            _PY_RUNTIME_ARCHIVE_LIBPYTHON if needs_libpython else _PY_RUNTIME_ARCHIVE
        )
    if runtime_dir != _PY_RUNTIME_DIR:
        archive = os.path.join(runtime_dir, os.path.basename(archive))
    debug = bool(str(os.environ.get("PCC_DEBUG_RUNTIME", "")).strip())
    if debug:
        _log(True, "[runtime] runtime_dir=" + runtime_dir)
        _log(True, "[runtime] archive=" + str(archive))
        _log(True, "[runtime] makefile=" + os.path.join(runtime_dir, "Makefile"))
        _log(True, "[runtime] needs_libpython=" + str(needs_libpython))
        _log(True, "[runtime] cc_mode=" + str(cc_mode))
        _log(True, "[runtime] high_mode=" + str(high_mode))
        _log(True, "[runtime] archive_exists=" + str(os.path.isfile(archive)))
        if os.path.isfile(archive):
            _log(
                True, "[runtime] archive_stale=" + str(_runtime_archive_stale(archive))
            )
        _log(
            True,
            "[runtime] makefile_exists="
            + str(os.path.isfile(os.path.join(runtime_dir, "Makefile"))),
        )

    archive_stale = True
    if os.path.isfile(archive):
        archive_stale = _runtime_archive_stale(archive)
    if os.path.isfile(archive) and not archive_stale:
        _log(verbose, "runtime archive: " + archive)
        return archive

    makefile = os.path.join(runtime_dir, "Makefile")
    if debug:
        try:
            with open("/tmp/pcc_runtime_debug_probe.txt", "a", encoding="utf-8") as f:
                f.write(
                    "[probe] makefile="
                    + makefile
                    + " exists="
                    + str(os.path.isfile(makefile))
                    + " archive="
                    + archive
                    + " exists="
                    + str(os.path.isfile(archive))
                    + "\n"
                )
        except Exception:
            pass
    if os.path.isfile(makefile):
        runtime_config_forces_rebuild = bool(
            str(os.environ.get("PCC_WITH_THREADS", "")).strip()
            or str(os.environ.get("PCC_REFCOUNT_KIND", "")).strip()
        )
        # A changed runtime source/header/Makefile should use make's normal
        # dependency graph and rebuild only the affected objects. Historically
        # every stale archive selected ``make -B`` here, recompiling every
        # pcc-Python runtime module even when one C-kernel helper changed.
        # Reserve a full rebuild for configuration, target, or pcc compiler
        # changes that can invalidate every emitted object.
        archive_mtime = os.path.getmtime(archive) if os.path.isfile(archive) else 0.0
        full_rebuild = (
            runtime_config_forces_rebuild
            or not _runtime_archive_target_matches(archive)
            or _runtime_archive_compiler_sources_newer_than(
                str(os.path.basename(archive)), archive_mtime
            )
        )
        make_cmd = ["make", "-C", runtime_dir]
        if full_rebuild:
            make_cmd.insert(1, "-B")
        if cc_mode == "pcc":
            if high_mode == "py":
                make_cmd.append(
                    "libpy_runtime_pcc_py_libpython.a"
                    if needs_libpython
                    else "libpy_runtime_pcc_py.a"
                )
            else:
                if needs_libpython:
                    make_cmd.extend(
                        [
                            "PCC_WITH_LIBPYTHON=1",
                            "LIB=libpy_runtime_libpython.a",
                            "OBJDIR=build_libpython",
                        ]
                    )
                else:
                    make_cmd.append("libpy_runtime_pcc.a")
            pcc_bin = _resolve_pcc_binary()
            if pcc_bin and high_mode == "py":
                make_cmd.append(f"PCC={pcc_bin}")
                make_cmd.append(f"PYTHON={_runtime_host_python_for_make()}")
        elif needs_libpython:
            make_cmd.extend(
                [
                    "PCC_WITH_LIBPYTHON=1",
                    "LIB=libpy_runtime_libpython.a",
                    "OBJDIR=build_libpython",
                ]
            )
        _log(verbose, "building runtime: " + _join_strings(make_cmd, " "))
        try:
            _run_runtime_make(make_cmd, verbose=verbose)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(
                f"warning: failed to build py_runtime ({e}); "
                "final link may fail on undefined py_* symbols",
                file=sys.stderr,
            )
            return None
        if os.path.isfile(archive):
            _write_runtime_archive_target_stamp(archive)
            _log(verbose, "runtime archive: " + archive)
            return archive

    print(
        "warning: " + archive + " not found; "
        "final link may fail on undefined py_* symbols",
        file=sys.stderr,
    )
    return archive


def _resolve_pcc_binary() -> Optional[str]:
    """Locate the pcc CLI binary for PCC_RUNTIME_CC=pcc builds."""
    env_override = str(os.environ.get("PCC_BINARY", "") or "").strip()
    if env_override:
        return env_override
    # When a compiled pcc binary is building a nested runtime archive,
    # sys.executable still describes the host Python used during stage0,
    # while sys.argv[0] points at the stage binary that must self-compile
    # the runtime modules. Prefer it when it is an executable path rather
    # than a Python source entry point.
    argv0 = str(sys.argv[0] if len(sys.argv) > 0 else "" or "").strip()
    argv0_base = os.path.basename(argv0)
    if argv0 and argv0_base.startswith("pcc") and not argv0.endswith(".py"):
        argv0_path = os.path.abspath(argv0)
        if os.path.isfile(argv0_path) and os.access(argv0_path, os.X_OK):
            return argv0_path
    # Prefer the pcc installed alongside the running Python (uv/venv).
    candidate = os.path.join(
        os.path.dirname(sys.executable),
        "pcc",
    )
    if os.path.isfile(candidate):
        return candidate
    found = shutil.which("pcc")
    return found


def _emit_ll(ir_text, out_ll_path, verbose: bool) -> None:
    ir_text = str(ir_text)
    out_ll_path = str(out_ll_path)
    if verbose:
        _log(
            verbose,
            "writing LLVM IR to " + out_ll_path + " (" + str(len(ir_text)) + " bytes)",
        )
    with open(out_ll_path, "w") as f:
        f.write(ir_text)


def _resolve_python_ir_pass_names(
    raw: Optional[str] = None,
    *,
    default_raw: Optional[str] = None,
) -> list[str]:
    if raw is None:
        raw = os.environ.get(_PYTHON_IR_PASSES_ENV)
        if raw is None or not str(raw).strip():
            if default_raw is not None:
                raw = default_raw
            else:
                return list(_PYTHON_IR_PASS_PRESETS["default"])
    elif not str(raw).strip() and default_raw is not None:
        raw = default_raw
    if raw is None:
        return list(_PYTHON_IR_PASS_PRESETS["default"])
    normalized = str(raw or "").strip().lower()
    if normalized in ("off", "false", "no", "0"):
        return []
    if not normalized:
        return list(_PYTHON_IR_PASS_PRESETS["default"])
    if normalized in ("on", "true", "yes", "1"):
        return list(_PYTHON_IR_PASS_PRESETS["default"])

    pass_names: list[str] = []
    for token in normalized.split(","):
        name = token.strip()
        if not name:
            continue
        preset = _PYTHON_IR_PASS_PRESETS.get(name)
        if preset is not None:
            for preset_name in preset:
                if preset_name not in pass_names:
                    pass_names.append(preset_name)
            continue
        if name not in pass_names:
            pass_names.append(name)
    return pass_names


def _python_ir_pass_jobs(item_count: int) -> int:
    raw = str(os.environ.get(_PYTHON_IR_PASS_JOBS_ENV, "") or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 1
    else:
        value = min(12, os.cpu_count() or 1)
    return max(1, min(max(1, item_count), value))


def _parse_seconds_text(raw: str, default: float) -> float:
    text = str(raw or "").strip()
    if not text:
        return default
    sign = 1.0
    if text.startswith("-"):
        sign = -1.0
        text = text[1:]
    whole = 0.0
    frac = 0.0
    scale = 1.0
    seen_digit = False
    seen_dot = False
    for ch in text:
        if ch == "." and not seen_dot:
            seen_dot = True
            continue
        if ch < "0" or ch > "9":
            return default
        digit = ord(ch) - ord("0")
        seen_digit = True
        if seen_dot:
            scale = scale * 10.0
            frac = frac + (digit / scale)
        else:
            whole = whole * 10.0 + digit
    if not seen_digit:
        return default
    return sign * (whole + frac)


def _seconds_debug_text(value) -> str:
    if value is None:
        return "disabled"
    scaled = int(value * 1000.0)
    if scaled < 0:
        return "-" + _seconds_debug_text((-scaled) / 1000.0)
    whole = scaled // 1000
    frac = scaled % 1000
    frac_text = _small_int_decimal(frac)
    if frac < 10:
        frac_text = "00" + frac_text
    elif frac < 100:
        frac_text = "0" + frac_text
    return _small_int_decimal(whole) + "." + frac_text + "s"


def _python_ir_pass_timeout_seconds() -> Optional[float]:
    raw = str(os.environ.get(_PYTHON_IR_PASS_TIMEOUT_ENV, "") or "").strip()
    if raw:
        value = _parse_seconds_text(raw, 120.0)
    else:
        value = 120.0
    if value <= 0:
        return None
    return value


def _python_ir_pass_strict_arg(*, strict_no_libpython: bool) -> str:
    return "1" if strict_no_libpython else "0"


def _python_ir_pass_batch_size_summary(
    module_ir_texts: list[tuple[str, str]],
    *,
    limit: int = 3,
) -> str:
    entries: list[tuple[int, str]] = []
    total_bytes = 0
    for module_name, ir_text in module_ir_texts:
        size = len(str(ir_text))
        total_bytes += size
        entries.append((size, str(module_name)))
    parts: list[str] = []
    index = 0
    max_items = limit if limit > 0 else 0
    while index < max_items:
        best_index = -1
        best_size = -1
        best_name = ""
        scan_index = 0
        while scan_index < len(entries):
            size, module_name = entries[scan_index]
            if size > best_size or (size == best_size and module_name > best_name):
                best_size = size
                best_name = module_name
                best_index = scan_index
            scan_index += 1
        if best_index < 0:
            break
        size, module_name = entries[best_index]
        parts.append(module_name + ":" + _small_int_decimal(size))
        entries[best_index] = (-1, "")
        index += 1
    return (
        "total_bytes="
        + _small_int_decimal(total_bytes)
        + " largest="
        + _join_strings(parts, ",")
    )


def _small_int_decimal(value: int) -> str:
    if value < 0:
        return "-" + _small_int_decimal(-value)
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value == 2:
        return "2"
    if value == 3:
        return "3"
    if value == 4:
        return "4"
    if value == 5:
        return "5"
    if value == 6:
        return "6"
    if value == 7:
        return "7"
    if value == 8:
        return "8"
    if value == 9:
        return "9"
    if value == 10:
        return "10"
    if value == 11:
        return "11"
    if value == 12:
        return "12"
    if value == 13:
        return "13"
    if value == 14:
        return "14"
    if value == 15:
        return "15"
    if value == 16:
        return "16"
    if value == 17:
        return "17"
    if value == 18:
        return "18"
    if value == 19:
        return "19"
    if value == 20:
        return "20"
    digits = ""
    current = value
    while current > 0:
        digit = current % 10
        if digit == 0:
            ch = "0"
        elif digit == 1:
            ch = "1"
        elif digit == 2:
            ch = "2"
        elif digit == 3:
            ch = "3"
        elif digit == 4:
            ch = "4"
        elif digit == 5:
            ch = "5"
        elif digit == 6:
            ch = "6"
        elif digit == 7:
            ch = "7"
        elif digit == 8:
            ch = "8"
        else:
            ch = "9"
        digits = ch + digits
        current = current // 10
    return digits


def _python_ir_pass_transport_is_memory() -> bool:
    raw = str(os.environ.get(_PYTHON_IR_PASS_TRANSPORT_ENV, "") or "").strip().lower()
    return raw == "memory"


def _default_python_ir_pass_transport(
    pass_names: list[str],
    default_raw: Optional[str],
) -> Optional[str]:
    raw = str(os.environ.get(_PYTHON_IR_PASS_TRANSPORT_ENV, "") or "").strip().lower()
    if raw:
        return None
    # Auto-select memory transport only for the bounded safe fast preset. The
    # in-memory LLVM path currently miscompiles a bootstrap CLI argv parser
    # shape after mem2reg+sroa+early-cse/instsimplify, while mem2reg+sroa has
    # a focused pass-on parser regression and keeps the pass-on bootstrap
    # bounded.
    normalized_names: list[str] = []
    for pass_name in pass_names:
        normalized_names.append(str(pass_name).strip().lower())
    if tuple(normalized_names) == _PYTHON_IR_PASS_FAST_PRESET:
        return "memory"
    return None


def _effective_python_ir_pass_transport_is_memory(
    default_transport: Optional[str],
) -> bool:
    raw = str(os.environ.get(_PYTHON_IR_PASS_TRANSPORT_ENV, "") or "").strip().lower()
    if raw:
        return raw == "memory"
    return str(default_transport or "").strip().lower() == "memory"


def _python_ir_pass_split_large_modules_enabled() -> bool:
    raw = str(os.environ.get(_PYTHON_IR_PASS_SPLIT_LARGE_MODULES_ENV, "") or "")
    normalized = raw.strip().lower()
    if normalized in ("0", "false", "no", "off", "legacy"):
        return False
    return True


def _python_ir_pass_split_threshold_bytes() -> int:
    return _self_backend_split_int_env(
        _PYTHON_IR_PASS_SPLIT_THRESHOLD_BYTES_ENV,
        4_000_000,
    )


def _python_ir_pass_split_shard_bytes() -> int:
    return _self_backend_split_int_env(
        _PYTHON_IR_PASS_SPLIT_SHARD_BYTES_ENV,
        1_400_000,
    )


def _python_ir_pass_names_allow_module_sharding(pass_names: list[str]) -> bool:
    normalized_names: list[str] = []
    for pass_name in pass_names:
        lowered = str(pass_name).strip().lower()
        if lowered in ("all", "full"):
            return True
        if lowered.startswith("default<o") and lowered.endswith(">"):
            return True
        normalized_names.append(lowered)
    return tuple(normalized_names) == _PYTHON_IR_PASS_FAST_PRESET


def _python_ir_pass_skip_prefixes() -> tuple[str, ...]:
    raw = str(os.environ.get(_PYTHON_IR_PASS_SKIP_MODULE_PREFIXES_ENV, "") or "")
    out: list[str] = []
    for part in raw.split(","):
        prefix = part.strip()
        if prefix:
            out.append(prefix)
    return tuple(out)


def _python_ir_pass_should_skip_module(module_name: str) -> bool:
    name = str(module_name)
    if name in _PYTHON_IR_PASS_UNSAFE_MODULES:
        return True
    for prefix in _PYTHON_IR_PASS_UNSAFE_MODULE_PREFIXES:
        if name == prefix or name.startswith(prefix):
            return True
    for prefix in _python_ir_pass_skip_prefixes():
        if name == prefix or name.startswith(prefix):
            return True
    return False


def _python_ir_pass_skip_modules_for_batch(
    module_ir_texts: list[tuple[str, str]],
) -> tuple[str, ...]:
    out: list[str] = []
    for module_name, _text in module_ir_texts:
        if _python_ir_pass_should_skip_module(module_name):
            out.append(str(module_name))
    return tuple(out)


def _split_large_modules_for_python_ir_passes(
    module_ir_texts: list[tuple[str, str]],
    pass_names: list[str],
) -> list[tuple[str, str]]:
    if not _python_ir_pass_transport_is_memory():
        return module_ir_texts
    if not _python_ir_pass_split_large_modules_enabled():
        return module_ir_texts
    if not _python_ir_pass_names_allow_module_sharding(pass_names):
        return module_ir_texts
    threshold = _python_ir_pass_split_threshold_bytes()
    shard_bytes = _python_ir_pass_split_shard_bytes()
    out: list[tuple[str, str]] = []
    for module_index, (module_name, ir_text) in enumerate(module_ir_texts):
        text = str(ir_text)
        if len(text) < threshold:
            out.append((module_name, text))
            continue
        shards = _split_python_ir_module_for_pass_shards(
            text,
            export_prefix="__pcp" + _small_int_decimal(module_index) + "_",
            shard_bytes=shard_bytes,
        )
        if len(shards) <= 1:
            out.append((module_name, text))
            continue
        for index, shard_text in enumerate(shards):
            out.append(
                (
                    module_name + ".__pass_shard_" + _small_int_decimal(index),
                    shard_text,
                )
            )
    return out


def _split_python_ir_module_for_pass_shards(
    ir_text: str,
    *,
    export_prefix: str,
    shard_bytes: int,
) -> list[str]:
    lines = str(ir_text).splitlines()
    shared_lines: list[str] = []
    global_defs_raw: list[str] = []
    functions_raw: list[tuple[str, str, str, bool]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("define "):
            body_lines = [line]
            i += 1
            while i < len(lines):
                body_lines.append(lines[i])
                if lines[i].startswith("}"):
                    i += 1
                    break
                i += 1
            body = "\n".join(body_lines)
            name = _defined_function_name_from_line(line)
            functions_raw.append(
                (
                    name,
                    body,
                    _function_declaration_from_define_line(line),
                    _llvm_split_line_has_internal_linkage(line),
                )
            )
            continue
        if _self_backend_ir_global_definition_line(line):
            global_defs_raw.append(line)
        else:
            shared_lines.append(line)
        i += 1

    if len(functions_raw) <= 1:
        return [ir_text]

    rename_map = _llvm_split_private_symbol_rename_map(
        global_defs_raw,
        functions_raw,
        export_prefix,
    )
    shared = _rename_llvm_global_refs("\n".join(shared_lines), rename_map).strip()
    global_defs = [
        _self_backend_export_split_global_line(
            _rename_llvm_global_refs(line, rename_map)
        )
        for line in global_defs_raw
    ]
    global_decls = []
    for line in global_defs_raw:
        decl = _global_declaration_from_definition_line(
            _rename_llvm_global_refs(line, rename_map)
        )
        if decl:
            global_decls.append(decl)
    functions = []
    for _name, body, decl, _is_internal in functions_raw:
        functions.append(
            (
                _llvm_split_rename_symbol_name(_name, rename_map),
                _python_ir_pass_export_split_function_text(
                    _rename_llvm_global_refs(body, rename_map)
                ),
                _python_ir_pass_export_split_function_declaration(
                    _rename_llvm_global_refs(decl, rename_map)
                ),
            )
        )
    all_function_decls: list[tuple[str, str]] = []
    for _name, _body, decl in functions:
        if decl:
            all_function_decls.append((_name, decl))

    def make_shard(
        body_parts: list[tuple[str, str, str]],
        *,
        include_global_defs: bool,
    ) -> str:
        body_names = set()
        for name, _, _ in body_parts:
            body_names.add(name)
        pieces: list[str] = []
        if shared:
            pieces.append(shared)
        if include_global_defs:
            pieces.extend(global_defs)
        else:
            pieces.extend(global_decls)
        for name, decl in all_function_decls:
            if name not in body_names:
                pieces.append(decl)
        for _name, body, _decl in body_parts:
            pieces.append(body)
        non_empty_pieces: list[str] = []
        for piece in pieces:
            if piece:
                non_empty_pieces.append(piece)
        return "\n\n".join(non_empty_pieces).strip() + "\n"

    shards: list[str] = []
    if global_defs:
        shards.append(make_shard([], include_global_defs=True))

    current: list[tuple[str, str, str]] = []
    current_bytes = 0
    for function in functions:
        function_bytes = len(function[1])
        if current and current_bytes + function_bytes > shard_bytes:
            shards.append(make_shard(current, include_global_defs=False))
            current = []
            current_bytes = 0
        current.append(function)
        current_bytes += function_bytes
    if current:
        shards.append(make_shard(current, include_global_defs=False))

    if len(shards) <= 1:
        return [ir_text]
    return shards


def _defined_function_name_from_line(line: str) -> str:
    marker = " @"
    pos = _find_substring(line, marker, 0)
    if pos < 0:
        return ""
    start = pos + len(marker)
    end = _find_substring(line, "(", start)
    if end < 0:
        return ""
    return line[start:end]


def _function_declaration_from_define_line(line: str) -> str:
    brace = _find_last_char(line, "{")
    if brace < 0:
        return ""
    head = line[:brace].strip()
    if not head.startswith("define "):
        return ""
    declaration = "declare " + head[len("define ") :]
    declaration = declaration.replace("declare internal ", "declare ", 1)
    declaration = declaration.replace("declare private ", "declare ", 1)
    return declaration


def _python_ir_pass_export_split_function_text(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    lines[0] = lines[0].replace("define internal ", "define ", 1)
    lines[0] = lines[0].replace("define private ", "define ", 1)
    return "\n".join(lines)


def _python_ir_pass_export_split_function_declaration(text: str) -> str:
    text = text.replace("declare internal ", "declare ", 1)
    text = text.replace("declare private ", "declare ", 1)
    return text


def _llvm_split_export_prefix(module_name: str) -> str:
    out = "__pccsplit_"
    text = str(module_name)
    for ch in text:
        if ch.isalnum() or ch == "_" or ch == ".":
            out += ch
        else:
            out += "_"
    return out + "_"


def _llvm_split_line_has_internal_linkage(line: str) -> bool:
    padded = " " + line + " "
    return " internal " in padded or " private " in padded


def _llvm_split_private_symbol_rename_map(
    global_lines: list[str],
    functions: list[tuple[str, str, str, bool]],
    export_prefix: str,
) -> dict[str, str]:
    rename_map: dict[str, str] = {}
    for line in global_lines:
        if _llvm_split_line_has_internal_linkage(line):
            name = _global_name_from_definition_line(line)
            if name:
                rename_map[name] = export_prefix + name
    for name, _body, _decl, is_internal in functions:
        if is_internal and name:
            rename_map[name] = export_prefix + name
    return rename_map


def _llvm_split_rename_symbol_name(name: str, rename_map: dict[str, str]) -> str:
    replacement = rename_map.get(name)
    if replacement is None:
        return name
    return replacement


def _global_name_from_definition_line(line: str) -> str:
    if not line.startswith("@"):
        return ""
    sep = _find_substring(line, " = ", 0)
    if sep < 0:
        return ""
    return line[1:sep]


def _rename_llvm_global_refs(text: str, rename_map: dict[str, str]) -> str:
    if not rename_map:
        return text
    pieces: list[str] = []
    index = 0
    literal_start = 0
    in_quote = False
    escape = False
    while index < len(text):
        ch = text[index]
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            index += 1
            continue
        if ch == '"':
            in_quote = True
            index += 1
            continue
        if ch != "@":
            index += 1
            continue
        start = index + 1
        end = start
        while end < len(text) and _llvm_global_name_char(text[end]):
            end += 1
        name = text[start:end]
        replacement = rename_map.get(name)
        if replacement is not None:
            if literal_start < index:
                pieces.append(text[literal_start:index])
            pieces.append("@" + replacement)
            literal_start = end
        index = end
    if not pieces:
        return text
    if literal_start < len(text):
        pieces.append(text[literal_start:])
    return "".join(pieces)


def _llvm_global_name_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_" or ch == "." or ch == "$" or ch == "-"


def _global_declaration_from_definition_line(line: str) -> str:
    sep = _find_substring(line, " = ", 0)
    if sep < 0:
        return ""
    name = line[:sep]
    rest = line[sep + 3 :]
    kind_pos = _find_global_kind_pos(rest)
    if kind_pos < 0:
        return ""
    kind_end = (
        kind_pos + 8 if _substring_at(rest, "constant", kind_pos) else kind_pos + 6
    )
    kind = rest[kind_pos:kind_end].strip()
    type_text = _global_initializer_type_text(rest[kind_end:].strip())
    if not type_text:
        return ""
    return name + " = external " + kind + " " + type_text


def _find_global_kind_pos(rest: str) -> int:
    best = -1
    if rest.startswith("global "):
        best = 0
    if rest.startswith("constant "):
        best = 0
    index = 0
    while index < len(rest):
        if _substring_at(rest, " global ", index):
            candidate = index + 1
            if best < 0 or candidate < best:
                best = candidate
        if _substring_at(rest, " constant ", index):
            candidate = index + 1
            if best < 0 or candidate < best:
                best = candidate
        index += 1
    return best


def _global_initializer_type_text(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    first = text[0]
    if first not in "{[<":
        end = 0
        while end < len(text) and not text[end].isspace():
            end += 1
        return text[:end]
    matching = {"{": "}", "[": "]", "<": ">"}[first]
    depth = 0
    for index, ch in enumerate(text):
        if ch == first:
            depth += 1
        elif ch == matching:
            depth -= 1
            if depth == 0:
                return text[: index + 1]
    return ""


def _find_substring(text: str, needle: str, start: int) -> int:
    if not needle:
        return start
    index = max(0, start)
    limit = len(text) - len(needle)
    while index <= limit:
        if _substring_at(text, needle, index):
            return index
        index += 1
    return -1


def _substring_at(text: str, needle: str, index: int) -> bool:
    if index < 0:
        return False
    if index + len(needle) > len(text):
        return False
    j = 0
    while j < len(needle):
        if text[index + j] != needle[j]:
            return False
        j += 1
    return True


def _find_last_char(text: str, target: str) -> int:
    index = len(text) - 1
    while index >= 0:
        if text[index] == target:
            return index
        index -= 1
    return -1


def _apply_python_ir_pass_pipeline(
    ir_text: str,
    *,
    module_name: str,
    verbose: bool = False,
    default_raw: Optional[str] = None,
    strict_no_libpython: bool = False,
) -> str:
    pass_names = _resolve_python_ir_pass_names(default_raw=default_raw)
    if not pass_names:
        return str(ir_text)
    if _python_ir_pass_should_skip_module(module_name):
        return str(ir_text)
    default_transport = _default_python_ir_pass_transport(pass_names, default_raw)

    if verbose:
        _log(
            verbose,
            "python IR passes[" + module_name + "]: " + _join_strings(pass_names, ", "),
        )
    host_code = (
        "import os\n"
        "import sys\n"
        "pcc_source_root = sys.argv[1]\n"
        "if pcc_source_root and pcc_source_root not in sys.path:\n"
        "    sys.path.insert(0, pcc_source_root)\n"
        "if pcc_source_root:\n"
        "    os.environ.setdefault('PCC_SOURCE_ROOT', pcc_source_root)\n"
        "    os.environ.setdefault('PCC_REPO_ROOT', pcc_source_root)\n"
        "from pcc.py_frontend.ir_pass_pipeline import run_python_ir_pass_pipeline\n"
        "module_name, pass_csv, ir_path, out_path, strict_text, default_transport = sys.argv[2:8]\n"
        "if strict_text == '1':\n"
        "    os.environ['PCC_PYTHON_IR_PASS_STRICT_NO_LIBPYTHON'] = '1'\n"
        "if default_transport and not str(os.environ.get('PCC_PYTHON_IR_PASS_TRANSPORT', '') or '').strip():\n"
        "    os.environ['PCC_PYTHON_IR_PASS_TRANSPORT'] = default_transport\n"
        "pass_names = tuple(name.strip() for name in pass_csv.split(',') "
        "if name.strip())\n"
        "with open(ir_path, 'r', encoding='utf-8') as f:\n"
        "    ir_text = f.read()\n"
        "out = run_python_ir_pass_pipeline(\n"
        "    ir_text, pass_names=pass_names, module_name=module_name,\n"
        ")\n"
        "with open(out_path, 'w', encoding='utf-8') as f:\n"
        "    f.write(out)\n"
    )
    with tempfile.TemporaryDirectory(prefix="pcc_py_ir_passes_") as tmp:
        ir_path = str(os.path.join(tmp, "input.ll"))
        out_path = str(os.path.join(tmp, "output.ll"))
        with open(ir_path, "w", encoding="utf-8") as f:
            f.write(str(ir_text))
        cmd = [
            _host_python_command(),
            "-c",
            host_code,
            _pcc_source_root_for_host_subprocess(),
            module_name,
            _join_strings(pass_names, ","),
            ir_path,
            out_path,
            _python_ir_pass_strict_arg(strict_no_libpython=strict_no_libpython),
            str(default_transport or ""),
        ]
        timeout_seconds = _python_ir_pass_timeout_seconds()
        try:
            if timeout_seconds is None:
                subprocess.run(cmd, check=True)
            else:
                # Keep Optional[float] out of native subprocess lowering:
                # compiled-stage Dyn-to-i64 coercion treats a boxed float as
                # zero.  The explicit float conversion preserves fractional
                # seconds and the branch preserves timeout=None semantics.
                subprocess.run(
                    cmd,
                    check=True,
                    timeout=float(timeout_seconds),
                )
        except subprocess.TimeoutExpired as e:
            timeout_text = _seconds_debug_text(e.timeout)
            raise PyPipelineError(
                "Python IR pass pipeline timed out for module "
                f"{module_name!r} after {timeout_text}; passes="
                + _join_strings(pass_names, ",")
                + " ir_bytes="
                + _small_int_decimal(len(str(ir_text)))
            ) from e
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            detail = f" (exit {e.returncode})" if hasattr(e, "returncode") else ""
            raise PyPipelineError(
                "Python IR pass pipeline failed for module " f"{module_name!r}{detail}"
            ) from e
        with open(out_path, "r", encoding="utf-8") as f:
            return f.read()


def _apply_python_ir_pass_pipeline_many(
    module_ir_texts: list[tuple[str, str]],
    *,
    verbose: bool = False,
    default_raw: Optional[str] = None,
    strict_no_libpython: bool = False,
) -> list[tuple[str, str]]:
    pass_names = _resolve_python_ir_pass_names(default_raw=default_raw)
    if not pass_names:
        out_pairs: list[tuple[str, str]] = []
        for name, text in module_ir_texts:
            out_pairs.append((name, str(text)))
        return out_pairs
    if not module_ir_texts:
        return []
    default_transport = _default_python_ir_pass_transport(pass_names, default_raw)
    normalized_module_ir_texts: list[tuple[str, str]] = []
    for name, text in module_ir_texts:
        normalized_module_ir_texts.append((name, str(text)))
    module_ir_texts = normalized_module_ir_texts
    split_large_modules = (
        _python_ir_pass_split_large_modules_enabled()
        and _effective_python_ir_pass_transport_is_memory(default_transport)
        and _python_ir_pass_names_allow_module_sharding(pass_names)
    )
    has_large_module = False
    if split_large_modules:
        threshold = _python_ir_pass_split_threshold_bytes()
        for _name, text in module_ir_texts:
            if len(text) >= threshold:
                has_large_module = True
                break
    if len(module_ir_texts) == 1 and not has_large_module:
        name, text = module_ir_texts[0]
        return [
            (
                name,
                _apply_python_ir_pass_pipeline(
                    text,
                    module_name=name,
                    verbose=verbose,
                    default_raw=default_raw,
                    strict_no_libpython=strict_no_libpython,
                ),
            )
        ]

    if verbose:
        _log(
            verbose,
            "python IR passes batch["
            + str(len(module_ir_texts))
            + " modules]: "
            + _join_strings(pass_names, ", "),
        )
    host_code = (
        "import multiprocessing as mp\n"
        "import os\n"
        "import sys\n"
        "pcc_source_root = sys.argv[1]\n"
        "if pcc_source_root and pcc_source_root not in sys.path:\n"
        "    sys.path.insert(0, pcc_source_root)\n"
        "if pcc_source_root:\n"
        "    os.environ.setdefault('PCC_SOURCE_ROOT', pcc_source_root)\n"
        "    os.environ.setdefault('PCC_REPO_ROOT', pcc_source_root)\n"
        "from pcc.py_frontend.ir_pass_pipeline import run_python_ir_pass_pipeline\n"
        "_pipeline = __import__('pcc.py_frontend.pipeline', "
        "fromlist=['_split_large_modules_for_python_ir_passes', "
        "'_python_ir_pass_should_skip_module'])\n"
        "_split_large_modules_for_python_ir_passes = getattr(_pipeline, "
        "'_split_large_modules_for_python_ir_passes')\n"
        "_python_ir_pass_should_skip_module = getattr(_pipeline, "
        "'_python_ir_pass_should_skip_module')\n"
        "def _run_one(item):\n"
        "    module_name, ir_path, out_path, skip_passes = item\n"
        "    with open(ir_path, 'r', encoding='utf-8') as f:\n"
        "        ir_text = f.read()\n"
        "    if skip_passes:\n"
        "        out = ir_text\n"
        "    else:\n"
        "        out = run_python_ir_pass_pipeline(\n"
        "            ir_text, pass_names=pass_names, module_name=module_name,\n"
        "        )\n"
        "    with open(out_path, 'w', encoding='utf-8') as f:\n"
        "        f.write(out)\n"
        "    return 0\n"
        "jobs = int(sys.argv[2])\n"
        "pass_csv = sys.argv[3]\n"
        "split_large_modules = sys.argv[4] == '1'\n"
        "result_path = sys.argv[5]\n"
        "strict_text = sys.argv[6]\n"
        "default_transport = sys.argv[7]\n"
        "if strict_text == '1':\n"
        "    os.environ['PCC_PYTHON_IR_PASS_STRICT_NO_LIBPYTHON'] = '1'\n"
        "if default_transport and not str(os.environ.get('PCC_PYTHON_IR_PASS_TRANSPORT', '') or '').strip():\n"
        "    os.environ['PCC_PYTHON_IR_PASS_TRANSPORT'] = default_transport\n"
        "skip_modules = set(name for name in sys.argv[8].split(',') if name)\n"
        "def _module_should_skip_passes(module_name):\n"
        "    if module_name in skip_modules:\n"
        "        return True\n"
        "    marker = '.__pass_shard_'\n"
        "    marker_pos = module_name.find(marker)\n"
        "    base_module = module_name[:marker_pos] if marker_pos >= 0 else module_name\n"
        "    if base_module in skip_modules:\n"
        "        return True\n"
        "    if _python_ir_pass_should_skip_module(module_name):\n"
        "        return True\n"
        "    if base_module != module_name and _python_ir_pass_should_skip_module(base_module):\n"
        "        return True\n"
        "    return False\n"
        "items = sys.argv[9:]\n"
        "pass_names = tuple(name.strip() for name in pass_csv.split(',') "
        "if name.strip())\n"
        "if len(items) % 2 != 0:\n"
        "    raise SystemExit(2)\n"
        "input_modules = []\n"
        "i = 0\n"
        "while i < len(items):\n"
        "    module_name = items[i]\n"
        "    ir_path = items[i + 1]\n"
        "    with open(ir_path, 'r', encoding='utf-8') as f:\n"
        "        input_modules.append((module_name, f.read()))\n"
        "    i += 2\n"
        "if split_large_modules:\n"
        "    input_modules = _split_large_modules_for_python_ir_passes(\n"
        "        input_modules, list(pass_names),\n"
        "    )\n"
        "result_dir = os.path.dirname(result_path)\n"
        "tasks = []\n"
        "for index, pair in enumerate(input_modules):\n"
        "    module_name, ir_text = pair\n"
        "    ir_path = os.path.join(\n"
        "        result_dir, 'input_expanded_' + str(index) + '.ll',\n"
        "    )\n"
        "    out_path = os.path.join(\n"
        "        result_dir, 'output_expanded_' + str(index) + '.ll',\n"
        "    )\n"
        "    with open(ir_path, 'w', encoding='utf-8') as f:\n"
        "        f.write(ir_text)\n"
        "    tasks.append((module_name, ir_path, out_path, _module_should_skip_passes(module_name)))\n"
        "if jobs <= 0:\n"
        "    jobs = os.cpu_count() or 1\n"
        "jobs = max(1, min(len(tasks), jobs))\n"
        "if jobs > 1 and len(tasks) > 1:\n"
        "    try:\n"
        "        mp.set_start_method('fork')\n"
        "    except (RuntimeError, ValueError):\n"
        "        pass\n"
        "    if mp.get_start_method(allow_none=True) == 'fork':\n"
        "        with mp.Pool(processes=jobs) as pool:\n"
        "            pool.map(_run_one, tasks)\n"
        "    else:\n"
        "        for task in tasks:\n"
        "            _run_one(task)\n"
        "else:\n"
        "    for task in tasks:\n"
        "        _run_one(task)\n"
        "with open(result_path, 'w', encoding='utf-8') as out:\n"
        "    for idx, task in enumerate(tasks):\n"
        "        module_name, _ir_path, out_path, _skip_passes = task\n"
        "        out.write(str(idx) + '\\t' + module_name + '\\t' + out_path + '\\n')\n"
    )
    with tempfile.TemporaryDirectory(prefix="pcc_py_ir_passes_many_") as tmp:
        job_count_hint = len(module_ir_texts)
        if has_large_module:
            job_count_hint = max(job_count_hint, os.cpu_count() or 1)
        result_path = str(os.path.join(tmp, "results.tsv"))
        args = [
            _host_python_command(),
            "-c",
            host_code,
            _pcc_source_root_for_host_subprocess(),
            _small_int_decimal(_python_ir_pass_jobs(job_count_hint)),
            _join_strings(pass_names, ","),
            "1" if split_large_modules else "0",
            result_path,
            _python_ir_pass_strict_arg(strict_no_libpython=strict_no_libpython),
            str(default_transport or ""),
            _join_strings(
                _python_ir_pass_skip_modules_for_batch(module_ir_texts),
                ",",
            ),
        ]
        for index, (module_name, ir_text) in enumerate(module_ir_texts):
            ir_path = str(
                os.path.join(
                    tmp,
                    "input_" + _small_int_decimal(index) + ".ll",
                )
            )
            with open(ir_path, "w", encoding="utf-8") as f:
                f.write(str(ir_text))
            args.extend([module_name, ir_path])
        timeout_seconds = _python_ir_pass_timeout_seconds()
        try:
            if timeout_seconds is None:
                subprocess.run(args, check=True)
            else:
                subprocess.run(
                    args,
                    check=True,
                    timeout=float(timeout_seconds),
                )
        except subprocess.TimeoutExpired as e:
            timeout_text = _seconds_debug_text(e.timeout)
            raise PyPipelineError(
                "Python IR pass batch pipeline timed out after "
                f"{timeout_text}; modules={len(module_ir_texts)} passes="
                + _join_strings(pass_names, ",")
                + " "
                + _python_ir_pass_batch_size_summary(module_ir_texts)
            ) from e
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            detail = f" (exit {e.returncode})" if hasattr(e, "returncode") else ""
            raise PyPipelineError(
                "Python IR pass batch pipeline failed" + detail
            ) from e
        out = []
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                result_lines = f.read().splitlines()
        except OSError as e:
            raise PyPipelineError(f"Python IR pass batch pipeline failed: {e}") from e
        for line in result_lines:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            _idx_text, module_name, out_path = parts
            with open(out_path, "r", encoding="utf-8") as f:
                out.append((module_name, f.read()))
        if not out:
            raise PyPipelineError(
                "Python IR pass batch pipeline failed: missing module result"
            )
        return out


def _default_python_ir_pass_raw_for_backend(
    native_backend: Optional[str],
) -> Optional[str]:
    if native_backend == "self":
        return "off"
    return None


def _default_python_ir_pass_raw_for_request(
    native_backend: Optional[str],
    *,
    emit_llvm_only: bool,
    backend: Optional[str],
) -> Optional[str]:
    if native_backend is None and emit_llvm_only:
        requested_backend = _normalize_native_backend_name(backend)
        if requested_backend == "self":
            return _default_python_ir_pass_raw_for_backend("self")
    return _default_python_ir_pass_raw_for_backend(native_backend)


def _native_extension_export_link_flags(
    needs_native_extension_exports: bool = False,
) -> list[str]:
    """Export pcc C-API shim symbols for dlopen()'d pcc-native extensions."""
    if not needs_native_extension_exports:
        return []
    if sys.platform == "darwin":
        return ["-Wl,-export_dynamic"]
    if sys.platform.startswith("linux"):
        return ["-rdynamic"]
    return []


def _runtime_archive_link_args_for_native_extensions(
    runtime_archive: str,
    needs_native_extension_exports: bool = False,
) -> list[str]:
    """Link the runtime archive and keep the generic C-API shim visible.

    A pcc-native extension references symbols such as ``PyArg_ParseTuple`` only
    after ``dlopen``. Static linkers therefore would not pull
    ``py_capi_shim.o`` from ``libpy_runtime.a`` unless the executable itself has
    an undefined edge. Keep this narrow instead of force-loading the whole
    runtime archive.
    """
    if not needs_native_extension_exports:
        return [runtime_archive]
    if sys.platform == "darwin":
        return ["-Wl,-u,_PyArg_ParseTuple", runtime_archive]
    if sys.platform.startswith("linux"):
        return ["-Wl,-u,PyArg_ParseTuple", runtime_archive]
    return [runtime_archive]


def _link_with_clang(
    ll_paths,
    out_path: str,
    runtime_archive: Optional[str],
    verbose: bool,
    *,
    needs_libpython: bool = False,
    needs_native_extension_exports: bool = False,
    extra_link_inputs: tuple[str, ...] = (),
    extra_link_args: tuple[str, ...] = (),
) -> None:
    """Link one or more ``.ll`` files into a native executable."""
    clang = str(os.environ.get("CC", "") or "").strip() or "clang"
    ll_paths_strs: list[str] = []
    for p in ll_paths:
        ll_paths_strs.append(str(p))
    ll_paths = ll_paths_strs
    out_path = str(out_path)
    if runtime_archive is not None:
        runtime_archive = str(runtime_archive)
    explicit_python_ldflags = ""
    if needs_libpython:
        explicit_python_ldflags = str(os.environ.get("PCC_PYTHON_LDFLAGS", "")).strip()
    if needs_libpython and explicit_python_ldflags:
        target_triple = _link_input_target_triple(ll_paths)
        if target_triple == "unknown-unknown-unknown":
            target_triple = None
        if target_triple is not None:
            target_triple = _normalize_clang_target_triple(target_triple)
    else:
        target_triple = _clang_target_triple_for_link(ll_paths)
    # Do not rewrite the input IR files here. The self-hosted pipeline
    # reaches this path, and mutating large IR text just to replace the
    # placeholder target triple is unnecessary when clang already gets
    # the explicit ``-target`` below.
    # Runtime exceptions are return-code-based now (see py_exc.c);
    # libc++/libc++abi are no longer linked. libm stays for fp math.
    cmd = [clang, *ll_paths, *extra_link_inputs]
    if target_triple is not None:
        cmd.extend(["-target", target_triple])
    cmd.extend(["-o", out_path, "-lm"])
    cmd.extend(extra_link_args)
    cmd.extend(_native_extension_export_link_flags(needs_native_extension_exports))
    # Darwin dyld on current macOS rejects executables without LC_UUID.
    # Bootstrap determinism is handled by compare-time UUID normalization.
    if runtime_archive is not None:
        # Put the archive after the .ll inputs so the linker pulls
        # its symbols in once the user objects have declared them.
        insert_at = 1 + len(ll_paths)
        cmd[insert_at:insert_at] = _runtime_archive_link_args_for_native_extensions(
            runtime_archive,
            needs_native_extension_exports,
        )
    if needs_libpython:
        if explicit_python_ldflags:
            cmd.extend(shlex.split(explicit_python_ldflags))
        else:
            config_cmd = _resolve_python_config_command()
            try:
                out = str(
                    subprocess.check_output(
                        [config_cmd, "--ldflags", "--embed"],
                        text=True,
                    ).strip()
                )
                cmd.extend(out.split())
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                raise PyPipelineError(
                    f"{config_cmd} required for import-using programs: {e}"
                ) from e
    if verbose:
        _log(verbose, "link: " + _join_strings(cmd, " "))
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as e:
        raise PyPipelineError(
            f"{clang} not found on PATH; cannot link Python frontend output"
        ) from e
    except subprocess.CalledProcessError as e:
        raise PyPipelineError(f"clang link failed (exit {e.returncode})") from e


def _ensure_llvm_module_target(path: str, triple: str) -> str:
    path = str(path)
    triple = str(triple)
    marker = 'target triple = "'
    replacement = marker + triple + '"'
    with open(path, "r", encoding="utf-8") as f:
        ir_text = f.read()
    idx = ir_text.find(marker)
    if idx == -1:
        ir_text = replacement + "\n" + ir_text
    else:
        # Manual scan for closing quote — pcc-Python's closed-world
        # codegen has no native lowering for str.find(needle, start),
        # only the 1-arg form. Hand-rolled scan keeps this function
        # closed-world editable.
        close = -1
        scan_i = idx + len(marker)
        ir_len = len(ir_text)
        while scan_i < ir_len:
            if ir_text[scan_i] == '"':
                close = scan_i
                break
            scan_i = scan_i + 1
        if close == -1:
            return path
        current = ir_text[idx + len(marker) : close]
        if current == triple:
            return path
        ir_text = ir_text[:idx] + replacement + ir_text[close + 1 :]
    with open(path, "w", encoding="utf-8") as f:
        f.write(ir_text)
    return path


def _write_utf8_text_file(path: str, text: str) -> None:
    path = str(path)
    text = str(text)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _normalize_clang_target_triple(triple: str) -> str:
    triple = str(triple).strip()
    if not triple:
        return triple
    lower = triple.lower()
    apple_darwin_marker = "-apple-darwin"
    marker_index = lower.find(apple_darwin_marker)
    if marker_index == -1:
        return triple
    prefix = triple[:marker_index]
    suffix = triple[marker_index + len(apple_darwin_marker) :]
    if not suffix:
        return triple
    parts = suffix.split(".", 1)
    version_text = parts[0]
    try:
        darwin_major = int(version_text.split("-", 1)[0])
    except ValueError:
        return triple
    if darwin_major <= 0:
        return triple
    # macOS major version is typically darwin major - 9 for
    # modern XNU toolchains (e.g. darwin23 -> macosx14).
    macos_major = darwin_major - 9
    if macos_major <= 0:
        return triple
    return prefix + "-apple-macosx" + str(macos_major) + ".0.0"


def _link_input_target_triple(ll_paths: list[str]) -> Optional[str]:
    marker = 'target triple = "'
    suffix = '"'
    for ll_path in ll_paths:
        try:
            with open(ll_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith(marker):
                        continue
                    value = line[len(marker) :]
                    if value.endswith(suffix):
                        return value[:-1]
                    return value.split('"', 1)[0]
        except (OSError, TypeError):
            continue
    return None


def _clang_target_triple_for_link(ll_paths: list[str]) -> Optional[str]:
    triple = _link_input_target_triple(ll_paths)
    if triple == "unknown-unknown-unknown":
        triple = None
    if triple is None:
        triple = _host_target_triple_for_self_backend()
    if triple == "unknown-unknown-unknown":
        return None
    return _normalize_clang_target_triple(triple)


def _host_target_triple_for_self_backend() -> str:
    cc = str(os.environ.get("CC", "") or "").strip() or "cc"
    try:
        return str(
            subprocess.check_output(
                [cc, "-dumpmachine"],
                text=True,
            ).strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        if sys.platform == "darwin":
            import platform

            machine = platform.machine().lower()
            if machine == "aarch64":
                machine = "arm64"
            return f"{machine}-apple-darwin{platform.release()}"
        if sys.platform.startswith("linux"):
            import platform

            machine = platform.machine().lower()
            if machine in ("amd64", "x64"):
                machine = "x86_64"
            return f"{machine}-unknown-linux-gnu"
        return "unknown-unknown-unknown"


def _self_backend_ir_text(ir_text: str) -> str:
    ir_text = str(ir_text)
    placeholder = 'target triple = "unknown-unknown-unknown"'
    header = ir_text[:4096]
    idx = header.find(placeholder)
    if idx >= 0:
        replacement = 'target triple = "' + _host_target_triple_for_self_backend() + '"'
        return ir_text[:idx] + replacement + ir_text[idx + len(placeholder) :]
    if 'target triple = "' not in header:
        return (
            'target triple = "'
            + _host_target_triple_for_self_backend()
            + '"\n'
            + ir_text
        )
    return ir_text


def _host_python_command() -> str:
    configured = str(os.environ.get("PCC_HOST_PYTHON", "") or "").strip()
    if configured:
        return configured
    cwd_python3 = str(os.path.join(os.getcwd(), ".venv", "bin", "python3"))
    if os.path.isfile(cwd_python3):
        return cwd_python3
    cwd_python = str(os.path.join(os.getcwd(), ".venv", "bin", "python"))
    if os.path.isfile(cwd_python):
        return cwd_python
    return "python3"


def _pcc_source_root_for_host_subprocess() -> str:
    if os.path.basename(_PCC_DIR) == "pcc":
        return os.path.dirname(_PCC_DIR)
    return _PCC_DIR


def _debug_dump_self_backend_ir_texts(ir_texts: list[str]) -> None:
    dump_dir = str(os.environ.get("PCC_DEBUG_SELF_IR_DUMP_DIR", "") or "").strip()
    if not dump_dir:
        return
    for index, ir_text in enumerate(ir_texts):
        path = str(os.path.join(dump_dir, f"self_backend_input_{index}.ll"))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(ir_text))
        except OSError:
            return


def _emit_self_asm_via_host_python(
    ir_text: str,
    tmp_dir: str,
    index: int,
) -> tuple[str, str]:
    native_result = _emit_self_asm_in_process(ir_text)
    if native_result is not None:
        return native_result
    ir_path = str(os.path.join(tmp_dir, f"self_backend_input_{index}.ll"))
    with open(ir_path, "w", encoding="utf-8") as f:
        f.write(_self_backend_ir_text(ir_text))
    host_py = _host_python_command()
    try:
        out = str(
            subprocess.check_output(
                [
                    host_py,
                    "-c",
                    _SELF_BACKEND_HOST_CODE,
                    _pcc_source_root_for_host_subprocess(),
                    ir_path,
                ],
                text=True,
            )
        )
    except Exception as e:
        raise PyPipelineError(f"self backend native emission failed: {e}") from e
    lines = out.splitlines()
    if not lines:
        raise PyPipelineError(
            "self backend native emission failed: host emitter produced " "no output"
        )
    target_id = lines[0]
    asm_text = "\n".join(lines[1:])
    return target_id, asm_text


def _emit_self_asm_in_process(ir_text: str) -> Optional[tuple[str, str]]:
    """Emit AArch64 Darwin assembly inside pcc1 without a host Python edge."""
    triple = _parse_self_backend_target_triple_native(ir_text)
    if triple == "unknown-unknown-unknown":
        triple = _host_target_triple_for_self_backend()
    if not _is_aarch64_darwin_triple_native(triple):
        return None
    return "self-aarch64-darwin-v0", _emit_aarch64_darwin_asm_native(ir_text, False)


def _self_backend_object_cache_enabled() -> bool:
    value = str(os.environ.get(_SELF_BACKEND_OBJECT_CACHE_ENV, "") or "")
    if value.strip().lower() in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    identity = str(
        os.environ.get(_SELF_BACKEND_OBJECT_CACHE_IDENTITY_ENV, "") or ""
    ).strip()
    return bool(identity)


def _self_backend_object_cache_dir() -> str:
    configured = str(
        os.environ.get(_SELF_BACKEND_OBJECT_CACHE_DIR_ENV, "") or ""
    ).strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(
        os.path.expanduser("~"), ".cache", "pcc", "self-backend-object-cache"
    )


def _self_backend_object_cache_path_allowed(cache_path: str) -> bool:
    if not cache_path or not _self_backend_object_cache_enabled():
        return False
    cache_root = os.path.abspath(_self_backend_object_cache_dir())
    candidate = os.path.abspath(cache_path)
    return candidate.startswith(cache_root + os.sep)


def _plan_self_backend_object_cache(
    worker_items: list[tuple[str, str, str]],
    target_id: str,
    cc: str,
    tmp_dir: str,
) -> list[tuple[str, str]]:
    """Return ``(cache_path, status)`` rows aligned with worker_items."""
    disabled: list[tuple[str, str]] = []
    for _item in worker_items:
        disabled.append(("", "off"))
    if not worker_items or not _self_backend_object_cache_enabled():
        return disabled
    identity = str(
        os.environ.get(_SELF_BACKEND_OBJECT_CACHE_IDENTITY_ENV, "") or ""
    ).strip()
    cache_dir = _self_backend_object_cache_dir()
    manifest_path = str(os.path.join(tmp_dir, "self_backend_cache_inputs.tsv"))
    plan_path = str(os.path.join(tmp_dir, "self_backend_cache_plan.tsv"))
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            for index, item in enumerate(worker_items):
                result_path, obj_path, ir_path = item
                f.write(
                    _small_int_decimal(index)
                    + "\t"
                    + ir_path
                    + "\t"
                    + result_path
                    + "\t"
                    + obj_path
                    + "\n"
                )
        subprocess.run(
            [
                _host_python_command(),
                "-c",
                _SELF_BACKEND_OBJECT_CACHE_PLAN_CODE,
                _SELF_BACKEND_OBJECT_CACHE_VERSION,
                identity,
                target_id,
                cc,
                cache_dir,
                manifest_path,
                plan_path,
            ],
            check=True,
        )
        with open(plan_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return disabled
    if len(lines) != len(worker_items):
        return disabled
    planned: list[tuple[str, str]] = []
    for expected_index, line in enumerate(lines):
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] != _small_int_decimal(expected_index):
            return disabled
        cache_path = parts[1]
        status = parts[2]
        if status not in ("hit", "miss"):
            return disabled
        if not _self_backend_object_cache_path_allowed(cache_path):
            return disabled
        planned.append((cache_path, status))
    return planned


def _publish_self_backend_object_cache(
    worker_items: list[tuple[str, str, str]],
    cache_plan: list[tuple[str, str]],
    tmp_dir: str,
) -> bool:
    publish_rows: list[tuple[str, str]] = []
    for index, item in enumerate(worker_items):
        _result_path, obj_path, _ir_path = item
        cache_path, cache_status = cache_plan[index]
        if cache_status == "miss" and cache_path and os.path.isfile(obj_path):
            publish_rows.append((cache_path, obj_path))
    if not publish_rows:
        return True
    manifest_path = str(os.path.join(tmp_dir, "self_backend_cache_publish.tsv"))
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            for cache_path, obj_path in publish_rows:
                f.write(cache_path + "\t" + obj_path + "\n")
        subprocess.run(
            [
                _host_python_command(),
                "-c",
                _SELF_BACKEND_OBJECT_CACHE_PUBLISH_CODE,
                manifest_path,
            ],
            check=True,
        )
        return True
    except Exception:
        return False


def run_self_backend_emit_worker(
    ir_path: str,
    result_path: str,
    obj_path: str = "",
    cc: str = "",
) -> int:
    """Emit one self-backend module in a short-lived native stage process."""
    try:
        with open(ir_path, "r", encoding="utf-8") as f:
            # Match the source/host worker path: some closed-world modules do
            # not carry a target line of their own.  Native pcc1 workers used
            # to pass those modules straight to the parser, while the host
            # worker normalized them through ``_self_backend_ir_text``.  That
            # split made stage2 fail once it reached native object emission.
            ir_text = _self_backend_ir_text(f.read())
        native_result = _emit_self_asm_in_process(ir_text)
        if native_result is None:
            raise PyPipelineError("native emitter does not support the module target")
        target_id, asm_text = native_result
        result_payload = asm_text
        if obj_path:
            if not cc:
                raise PyPipelineError("self backend emit worker requires a compiler")
            asm_path = result_path + ".s"
            with open(asm_path, "w", encoding="utf-8") as f:
                f.write(asm_text)
            subprocess.run([cc, "-c", asm_path, "-o", obj_path], check=True)
            result_payload = obj_path
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(target_id + "\n")
            f.write(result_payload)
        return 0
    except Exception as exc:
        sys.stderr.write("self backend emit worker failed: " + str(exc) + "\n")
        return 1


def run_self_backend_split_worker(
    ir_path: str,
    result_path: str,
    output_prefix: str,
    export_prefix: str,
    shard_bytes_text: str,
) -> int:
    """Split one large IR module in an isolated compiled-stage process."""
    try:
        with open(ir_path, "r", encoding="utf-8") as f:
            ir_text = f.read()
        shard_bytes = int(shard_bytes_text)
        if shard_bytes <= 0:
            raise PyPipelineError("self backend split worker requires shard bytes")
        shards = _split_self_backend_ir_module_for_object_shards(
            ir_text,
            export_prefix=export_prefix,
            shard_bytes=shard_bytes,
        )
        shard_paths: list[str] = []
        for index, shard_text in enumerate(shards):
            shard_path = output_prefix + _small_int_decimal(index) + ".ll"
            with open(shard_path, "w", encoding="utf-8") as f:
                f.write(shard_text)
            shard_paths.append(shard_path)
        with open(result_path, "w", encoding="utf-8") as f:
            f.write("pcc.self_backend.split.v1\n")
            for shard_path in shard_paths:
                f.write(shard_path + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write("self backend split worker failed: " + str(exc) + "\n")
        return 1


def _emit_self_objects_many_in_process(
    ir_texts: list[str],
    tmp_dir: str,
    cc: str,
    *,
    split_large_modules: bool,
    profile: Optional[dict],
) -> Optional[list[tuple[str, str]]]:
    """Emit and assemble AArch64 modules without Python/pcc subprocesses."""
    if not ir_texts:
        return []
    first_triple = _parse_self_backend_target_triple_native(ir_texts[0])
    if first_triple == "unknown-unknown-unknown":
        first_triple = _host_target_triple_for_self_backend()
    if not _is_aarch64_darwin_triple_native(first_triple):
        return None
    pairs: list[tuple[str, str]] = []
    native_worker = _python_frontend_worker_executable()
    inputs = ir_texts
    if not native_worker and split_large_modules:
        inputs = _split_self_backend_large_ir_modules(ir_texts)
    if native_worker:
        worker_command_prefix = [native_worker]
    elif _source_self_backend_emit_workers_worthwhile(inputs):
        worker_command_prefix = _python_frontend_worker_command_prefix()
    else:
        worker_command_prefix = []
    t = _profile_begin(profile)
    parent_emitted_objects = not worker_command_prefix
    if worker_command_prefix:
        split_threshold = _self_backend_split_threshold_bytes()
        split_shard_bytes = _self_backend_split_shard_bytes()
        planned_inputs: list[tuple[str, str, int]] = []
        split_worker_commands: list[str] = []
        split_module_count = 0
        for index, ir_text in enumerate(inputs):
            ir_path = str(os.path.join(tmp_dir, f"self_backend_module_{index}.ll"))
            with open(ir_path, "w", encoding="utf-8") as f:
                f.write(ir_text)
            input_bytes = len(ir_text)
            if native_worker and split_large_modules and input_bytes >= split_threshold:
                result_path = str(
                    os.path.join(tmp_dir, f"self_backend_split_{index}.result")
                )
                output_prefix = str(
                    os.path.join(tmp_dir, f"self_backend_split_{index}_shard_")
                )
                export_prefix = "__pco" + _small_int_decimal(index) + "_"
                command_parts = []
                for prefix_part in worker_command_prefix:
                    command_parts.append(_shell_quote_arg(prefix_part))
                command_parts.extend(
                    [
                        _shell_quote_arg(_SELF_BACKEND_SPLIT_WORKER_ARG),
                        _shell_quote_arg(ir_path),
                        _shell_quote_arg(result_path),
                        _shell_quote_arg(output_prefix),
                        _shell_quote_arg(export_prefix),
                        _shell_quote_arg(_small_int_decimal(split_shard_bytes)),
                    ]
                )
                split_worker_commands.append(_join_strings(command_parts, " "))
                planned_inputs.append((result_path, output_prefix, -1))
                split_module_count += 1
            else:
                planned_inputs.append((ir_path, "", input_bytes))

        if split_worker_commands:
            split_t = _profile_begin(profile)
            _run_python_frontend_worker_commands(
                split_worker_commands,
                max_parallel=min(
                    2,
                    _self_backend_jobs(len(split_worker_commands)),
                    len(split_worker_commands),
                ),
            )
            _profile_end(profile, "link_self_native_split_workers", split_t)

        worker_inputs: list[tuple[str, int]] = []
        split_shard_count = 0
        for input_path, output_prefix, input_bytes in planned_inputs:
            if input_bytes >= 0:
                worker_inputs.append((input_path, input_bytes))
                continue
            with open(input_path, "r", encoding="utf-8") as f:
                manifest_lines = f.read().splitlines()
            if not manifest_lines or manifest_lines[0] != "pcc.self_backend.split.v1":
                raise PyPipelineError(
                    "self backend split worker produced an invalid manifest"
                )
            shard_paths = manifest_lines[1:]
            if not shard_paths:
                raise PyPipelineError("self backend split worker produced no shards")
            for shard_path in shard_paths:
                if not shard_path.startswith(output_prefix) or not os.path.isfile(
                    shard_path
                ):
                    raise PyPipelineError(
                        "self backend split worker produced an invalid shard path"
                    )
                worker_inputs.append((shard_path, os.path.getsize(shard_path)))
                split_shard_count += 1
        _profile_counter(profile, "link_self_native_split_modules", split_module_count)
        _profile_counter(profile, "link_self_native_split_shards", split_shard_count)

        worker_items: list[tuple[str, str, str]] = []
        worker_input_bytes: list[int] = []
        for index, worker_input in enumerate(worker_inputs):
            ir_path, input_bytes = worker_input
            obj_path = str(os.path.join(tmp_dir, f"self_backend_native_{index}.o"))
            result_path = str(
                os.path.join(tmp_dir, f"self_backend_native_{index}.result")
            )
            worker_items.append((result_path, obj_path, ir_path))
            worker_input_bytes.append(input_bytes)

        cache_plan_t = _profile_begin(profile)
        cache_plan = _plan_self_backend_object_cache(
            worker_items,
            "self-aarch64-darwin-v0",
            cc,
            tmp_dir,
        )
        _profile_end(profile, "link_self_native_object_cache_plan", cache_plan_t)

        large_worker_commands: list[tuple[int, str]] = []
        small_worker_commands: list[str] = []
        for index, worker_item in enumerate(worker_items):
            result_path, obj_path, ir_path = worker_item
            input_bytes = worker_input_bytes[index]
            cache_path, cache_status = cache_plan[index]
            if cache_status == "hit":
                continue
            command_parts = []
            for prefix_part in worker_command_prefix:
                command_parts.append(_shell_quote_arg(prefix_part))
            command_parts.extend(
                [
                    _shell_quote_arg(_SELF_BACKEND_EMIT_WORKER_ARG),
                    _shell_quote_arg(ir_path),
                    _shell_quote_arg(result_path),
                    _shell_quote_arg(obj_path),
                    _shell_quote_arg(cc),
                ]
            )
            command = _join_strings(command_parts, " ")
            if input_bytes >= 1_000_000:
                insert_at = 0
                while (
                    insert_at < len(large_worker_commands)
                    and large_worker_commands[insert_at][0] >= input_bytes
                ):
                    insert_at += 1
                large_worker_commands.insert(insert_at, (input_bytes, command))
            else:
                small_worker_commands.append(command)
        configured_jobs = _self_backend_jobs(len(worker_items))
        if large_worker_commands:
            huge_commands: list[str] = []
            medium_commands: list[str] = []
            for input_bytes, command in large_worker_commands:
                if input_bytes >= 4_000_000:
                    huge_commands.append(command)
                else:
                    medium_commands.append(command)
            if huge_commands:
                _run_python_frontend_worker_commands(
                    huge_commands,
                    max_parallel=min(2, configured_jobs, len(huge_commands)),
                )
            if medium_commands:
                _run_python_frontend_worker_commands(
                    medium_commands,
                    max_parallel=min(8, configured_jobs, len(medium_commands)),
                )
        if small_worker_commands:
            _run_python_frontend_worker_commands(
                small_worker_commands,
                max_parallel=min(12, configured_jobs, len(small_worker_commands)),
            )
        native_object_cache_hits = 0
        native_object_cache_misses = 0
        native_object_cache_disabled = 0
        for worker_index, worker_item in enumerate(worker_items):
            result_path, obj_path, _ir_path = worker_item
            with open(result_path, "r", encoding="utf-8") as f:
                worker_result = f.read()
            worker_result_lines = worker_result.splitlines()
            target_id = worker_result_lines[0] if worker_result_lines else ""
            emitted_obj_path = (
                worker_result_lines[1] if len(worker_result_lines) >= 2 else ""
            )
            if (
                not target_id
                or emitted_obj_path.strip() != obj_path
                or not os.path.isfile(obj_path)
            ):
                raise PyPipelineError(
                    "self backend emit worker produced an invalid result"
                )
            cache_status = (
                worker_result_lines[2]
                if len(worker_result_lines) >= 3
                else cache_plan[worker_index][1]
            )
            if cache_status == "hit":
                native_object_cache_hits += 1
            elif cache_status == "miss":
                native_object_cache_misses += 1
            else:
                native_object_cache_disabled += 1
            pairs.append((target_id, obj_path))
        cache_publish_t = _profile_begin(profile)
        cache_publish_ok = _publish_self_backend_object_cache(
            worker_items,
            cache_plan,
            tmp_dir,
        )
        _profile_end(profile, "link_self_native_object_cache_publish", cache_publish_t)
        _profile_counter(
            profile,
            "link_self_native_object_cache_hits",
            native_object_cache_hits,
        )
        _profile_counter(
            profile,
            "link_self_native_object_cache_misses",
            native_object_cache_misses,
        )
        _profile_counter(
            profile,
            "link_self_native_object_cache_disabled",
            native_object_cache_disabled,
        )
        _profile_counter(
            profile,
            "link_self_native_object_cache_publish_ok",
            1 if cache_publish_ok else 0,
        )
    else:
        for object_index, ir_text in enumerate(inputs):
            asm_path = str(
                os.path.join(tmp_dir, f"self_backend_native_{object_index}.s")
            )
            obj_path = str(
                os.path.join(tmp_dir, f"self_backend_native_{object_index}.o")
            )
            native_result = _emit_self_asm_in_process(ir_text)
            if native_result is None:
                return None
            target_id = native_result[0]
            asm_text = native_result[1]
            with open(asm_path, "w", encoding="utf-8") as f:
                f.write(asm_text)
            subprocess.run([cc, "-c", asm_path, "-o", obj_path], check=True)
            pairs.append((target_id, obj_path))
            # Source-mode stage1 emits hundreds of shards in this process.
            if object_index % 4 == 3:
                gc.collect()
    collect_t = _profile_begin(profile)
    if parent_emitted_objects:
        gc.collect()
    _profile_end(profile, "link_self_emit_objects_collect", collect_t)
    _profile_counter(
        profile,
        "link_self_emit_objects_collect_skipped",
        0 if parent_emitted_objects else 1,
    )
    _profile_end(profile, "link_self_emit_objects_native", t)
    _profile_counter(profile, "link_self_native_object_count", len(pairs))
    return pairs


def _source_self_backend_emit_workers_worthwhile(ir_texts: list[str]) -> bool:
    if len(ir_texts) >= 4:
        return True
    total_bytes = 0
    for ir_text in ir_texts:
        total_bytes += len(ir_text)
    return total_bytes >= 1_000_000


def _emit_self_objects_many_via_host_python(
    ir_texts: list[str],
    tmp_dir: str,
    cc: str,
    *,
    split_large_modules: bool = False,
    profile: Optional[dict] = None,
) -> list[tuple[str, str]]:
    native_results = _emit_self_objects_many_in_process(
        ir_texts,
        tmp_dir,
        cc,
        split_large_modules=split_large_modules,
        profile=profile,
    )
    if native_results is not None:
        return native_results
    ir_paths = []
    t = _profile_begin(profile)
    for index, ir_text in enumerate(ir_texts):
        ir_path = str(os.path.join(tmp_dir, f"self_backend_input_{index}.ll"))
        with open(ir_path, "w", encoding="utf-8") as f:
            f.write(ir_text)
        ir_paths.append(ir_path)
    _profile_end(profile, "link_self_write_object_inputs", t)

    t = _profile_begin(profile)
    job_count_hint = len(ir_paths)
    if split_large_modules:
        threshold = _self_backend_split_threshold_bytes()
        for ir_text in ir_texts:
            if len(ir_text) >= threshold:
                job_count_hint = max(job_count_hint, os.cpu_count() or 1)
                break
    jobs = _self_backend_jobs(job_count_hint)
    host_py = _host_python_command()
    result_path = str(os.path.join(tmp_dir, "self_backend_results.tsv"))
    _profile_end(profile, "link_self_prepare_object_emit", t)
    try:
        t = _profile_begin(profile)
        subprocess.run(
            [
                host_py,
                "-c",
                _SELF_BACKEND_HOST_MANY_CODE,
                _pcc_source_root_for_host_subprocess(),
                _small_int_decimal(jobs),
                cc,
                "1" if split_large_modules else "0",
                result_path,
            ]
            + ir_paths,
            check=True,
        )
        _profile_end(profile, "link_self_object_emit_subprocess", t)
    except Exception as e:
        raise PyPipelineError(f"self backend native emission failed: {e}") from e
    try:
        t = _profile_begin(profile)
        with open(result_path, "r", encoding="utf-8") as f:
            out = f.read()
        _profile_end(profile, "link_self_read_object_results", t)
    except OSError as e:
        raise PyPipelineError(f"self backend native emission failed: {e}") from e

    parsed_results: list[tuple[int, str, str]] = []
    host_emit_sum_ms = 0
    host_emit_max_ms = 0
    host_cc_sum_ms = 0
    host_cc_max_ms = 0
    host_input_bytes = 0
    host_input_max_bytes = 0
    host_object_cache_hits = 0
    host_object_cache_misses = 0
    host_object_cache_disabled = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        idx_text = parts[0]
        target_id = parts[1]
        obj_path = parts[2]
        try:
            idx = int(idx_text)
        except ValueError:
            continue
        if idx < 0:
            continue
        if len(parts) >= 6:
            try:
                emit_ms = int(parts[3])
                cc_ms = int(parts[4])
                byte_len = int(parts[5])
            except ValueError:
                emit_ms = 0
                cc_ms = 0
                byte_len = 0
            host_emit_sum_ms += emit_ms
            host_cc_sum_ms += cc_ms
            host_input_bytes += byte_len
            if byte_len > host_input_max_bytes:
                host_input_max_bytes = byte_len
            if emit_ms > host_emit_max_ms:
                host_emit_max_ms = emit_ms
            if cc_ms > host_cc_max_ms:
                host_cc_max_ms = cc_ms
        if len(parts) >= 7:
            cache_status = parts[6]
            if cache_status == "hit":
                host_object_cache_hits += 1
            elif cache_status == "miss":
                host_object_cache_misses += 1
            elif cache_status == "off":
                host_object_cache_disabled += 1
        parsed_results.append((idx, target_id, obj_path))

    if not parsed_results:
        raise PyPipelineError(
            "self backend native emission failed: missing module result"
        )
    pairs: list[tuple[str, str]] = []
    for _idx, target_id, obj_path in parsed_results:
        pairs.append((target_id, obj_path))
    _profile_counter(
        profile,
        "link_self_host_emit_asm_sum_ms",
        host_emit_sum_ms,
    )
    _profile_counter(
        profile,
        "link_self_host_emit_asm_max_ms",
        host_emit_max_ms,
    )
    _profile_counter(profile, "link_self_host_cc_sum_ms", host_cc_sum_ms)
    _profile_counter(profile, "link_self_host_cc_max_ms", host_cc_max_ms)
    _profile_counter(profile, "link_self_host_input_bytes", host_input_bytes)
    _profile_counter(
        profile,
        "link_self_host_input_max_bytes",
        host_input_max_bytes,
    )
    _profile_counter(profile, "link_self_host_object_count", len(pairs))
    _profile_counter(profile, "link_self_host_jobs", jobs)
    _profile_counter(
        profile,
        "link_self_host_object_cache_hits",
        host_object_cache_hits,
    )
    _profile_counter(
        profile,
        "link_self_host_object_cache_misses",
        host_object_cache_misses,
    )
    _profile_counter(
        profile,
        "link_self_host_object_cache_disabled",
        host_object_cache_disabled,
    )
    return pairs


def _self_backend_jobs(n_modules: int) -> int:
    n_modules = int(n_modules)
    if n_modules <= 1:
        return 1
    raw = str(os.environ.get(_SELF_BACKEND_JOBS_ENV, "") or "").strip()
    if raw:
        try:
            jobs = int(raw)
        except ValueError:
            jobs = 1
        return max(1, min(n_modules, jobs))
    cpu_count = os.cpu_count() or 1
    return max(1, min(n_modules, cpu_count))


def _self_backend_skip_ll_temp() -> bool:
    raw = str(os.environ.get(_SELF_BACKEND_SKIP_LL_TEMP_ENV, "") or "")
    normalized = raw.strip().lower()
    if normalized in ("0", "false", "no", "off", "legacy"):
        return False
    if normalized in ("1", "true", "yes", "on"):
        return True
    return True


def _self_backend_split_large_modules_enabled() -> bool:
    raw = str(os.environ.get(_SELF_BACKEND_SPLIT_LARGE_MODULES_ENV, "") or "")
    normalized = raw.strip().lower()
    if normalized in ("0", "false", "no", "off", "legacy"):
        return False
    return True


def _self_backend_split_int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def _self_backend_split_threshold_bytes() -> int:
    return _self_backend_split_int_env(
        _SELF_BACKEND_SPLIT_THRESHOLD_BYTES_ENV,
        4_000_000,
    )


def _self_backend_split_shard_bytes() -> int:
    return _self_backend_split_int_env(
        _SELF_BACKEND_SPLIT_SHARD_BYTES_ENV,
        2_000_000,
    )


def _split_self_backend_large_ir_modules(ir_texts: list[str]) -> list[str]:
    if not _self_backend_split_large_modules_enabled():
        return ir_texts
    threshold = _self_backend_split_threshold_bytes()
    shard_bytes = _self_backend_split_shard_bytes()
    out: list[str] = []
    for index, text in enumerate(ir_texts):
        text = str(text)
        if len(text) < threshold:
            out.append(text)
            continue
        shards = _split_self_backend_ir_module_for_object_shards(
            text,
            export_prefix="__pco" + _small_int_decimal(index) + "_",
            shard_bytes=shard_bytes,
        )
        out.extend(shards)
    return out


def _split_self_backend_ir_module_for_object_shards(
    ir_text: str,
    *,
    export_prefix: str,
    shard_bytes: int,
) -> list[str]:
    lines = str(ir_text).splitlines()
    shared_lines: list[str] = []
    global_lines_raw: list[str] = []
    functions_raw: list[tuple[str, str, str, bool]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("define "):
            body_lines = [line]
            i += 1
            while i < len(lines):
                body_lines.append(lines[i])
                if lines[i].startswith("}"):
                    i += 1
                    break
                i += 1
            body = "\n".join(body_lines)
            functions_raw.append(
                (
                    _defined_function_name_from_line(line),
                    body,
                    "",
                    _llvm_split_line_has_internal_linkage(line),
                )
            )
            continue
        if _self_backend_ir_global_definition_line(line):
            global_lines_raw.append(line)
        else:
            shared_lines.append(line)
        i += 1

    if len(functions_raw) <= 1:
        return [ir_text]

    rename_map = _llvm_split_private_symbol_rename_map(
        global_lines_raw,
        functions_raw,
        export_prefix,
    )
    shared = _rename_llvm_global_refs("\n".join(shared_lines), rename_map).strip()
    global_lines = []
    for line in global_lines_raw:
        global_lines.append(
            _self_backend_export_split_global_line(
                _rename_llvm_global_refs(line, rename_map)
            )
        )
    functions = []
    for _name, body, _decl, _is_internal in functions_raw:
        functions.append(
            _python_ir_pass_export_split_function_text(
                _rename_llvm_global_refs(body, rename_map)
            )
        )

    def make_shard(body_parts: list[str]) -> str:
        pieces = []
        if shared:
            pieces.append(shared)
        for part in body_parts:
            if part:
                pieces.append(part)
        return "\n\n".join(pieces).strip() + "\n"

    shards: list[str] = []
    if global_lines:
        shards.append(make_shard(global_lines))

    current: list[str] = []
    current_bytes = 0
    for function_text in functions:
        function_bytes = len(function_text)
        if current and current_bytes + function_bytes > shard_bytes:
            shards.append(make_shard(current))
            current = []
            current_bytes = 0
        current.append(function_text)
        current_bytes += function_bytes
    if current:
        shards.append(make_shard(current))

    if len(shards) <= 1:
        return [ir_text]
    return shards


def _self_backend_ir_global_definition_line(line: str) -> bool:
    if not line.startswith("@"):
        return False
    if " = " not in line:
        return False
    if " external " in (" " + line + " "):
        return False
    return " global " in (" " + line + " ") or " constant " in (" " + line + " ")


def _self_backend_export_split_global_line(line: str) -> str:
    line = line.replace(" = internal ", " = ", 1)
    line = line.replace(" = private ", " = ", 1)
    return line


def _platform_link_flags() -> list[str]:
    if sys.platform.startswith("linux"):
        return ["-no-pie", "-Wl,--build-id=none", "-s"]
    return []


def _append_libpython_link_flags(cmd: list[str]) -> None:
    ldflags_env = str(os.environ.get("PCC_PYTHON_LDFLAGS", "")).strip()
    if ldflags_env:
        cmd.extend(shlex.split(ldflags_env))
        return
    config_cmd = _resolve_python_config_command()
    try:
        out = str(
            subprocess.check_output(
                [config_cmd, "--ldflags", "--embed"],
                text=True,
            ).strip()
        )
        cmd.extend(out.split())
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        raise PyPipelineError(
            f"{config_cmd} required for import-using programs: {e}"
        ) from e


def _link_with_self_backend(
    ll_paths,
    out_path: str,
    runtime_archive: Optional[str],
    verbose: bool,
    *,
    needs_libpython: bool = False,
    needs_native_extension_exports: bool = False,
    extra_link_inputs: tuple[str, ...] = (),
    extra_link_args: tuple[str, ...] = (),
    profile: Optional[dict] = None,
) -> None:
    """Lower ``.ll`` files through the self backend and link native asm."""
    ll_paths_strs: list[str] = []
    for p in ll_paths:
        ll_paths_strs.append(str(p))
    ll_paths = ll_paths_strs
    out_path = str(out_path)
    if runtime_archive is not None:
        runtime_archive = str(runtime_archive)

    with tempfile.TemporaryDirectory(prefix="pcc_py_self_") as tmp:
        ir_texts = []
        t = _profile_begin(profile)
        for ll_path in ll_paths:
            with open(ll_path, "r", encoding="utf-8") as f:
                ir_text = _self_backend_ir_text(f.read())
            ir_texts.append(ir_text)
        _profile_end(profile, "link_self_read_ll", t)
        _link_with_self_backend_ir_texts(
            ir_texts,
            out_path,
            runtime_archive,
            verbose,
            needs_libpython=needs_libpython,
            needs_native_extension_exports=needs_native_extension_exports,
            extra_link_inputs=extra_link_inputs,
            extra_link_args=extra_link_args,
            tmp_dir=tmp,
            profile=profile,
        )


def _finish_self_backend_executable(
    tmp_out_path: str,
    out_path: str,
    profile,
) -> None:
    if sys.platform == "darwin":
        t = _profile_begin(profile)
        subprocess.run(
            ["/usr/bin/codesign", "--force", "-s", "-", tmp_out_path],
            check=True,
        )
        subprocess.run(
            ["/usr/bin/codesign", "--verify", tmp_out_path],
            check=True,
        )
        _profile_end(profile, "link_self_codesign", t)

    t = _profile_begin(profile)
    subprocess.run(["/bin/mv", "-f", tmp_out_path, out_path], check=True)
    _profile_end(profile, "link_self_publish_move", t)
    if sys.platform != "darwin":
        return

    t = _profile_begin(profile)
    subprocess.run(
        ["/usr/bin/codesign", "--verify", out_path],
        check=True,
    )
    _profile_end(profile, "link_self_codesign", t)
    t = _profile_begin(profile)
    if _self_backend_publish_sync_enabled():
        subprocess.run(["/bin/sync"], check=True)
    else:
        subprocess.run(
            [
                "/bin/sh",
                "-c",
                'cat "$1" >/dev/null',
                "pcc-self-publish-barrier",
                out_path,
            ],
            check=True,
        )
    _profile_end(profile, "link_self_publish_barrier", t)


def _link_self_backend_ir_texts_run(
    ir_texts: list[str],
    out_path: str,
    runtime_archive: Optional[str],
    verbose: bool,
    *,
    needs_libpython: bool,
    needs_native_extension_exports: bool = False,
    extra_link_inputs: tuple[str, ...] = (),
    extra_link_args: tuple[str, ...] = (),
    tmp: str,
    profile,
) -> None:
    asm_modules = []
    needs_subsections_via_symbols = False
    input_ir_texts: list[str] = []
    t = _profile_begin(profile)
    for text in ir_texts:
        input_ir_texts.append(str(text))
    _profile_end(profile, "link_self_normalize_ir", t)
    _debug_dump_self_backend_ir_texts(input_ir_texts)
    split_large_modules = _self_backend_split_large_modules_enabled()
    has_large_module = False
    t = _profile_begin(profile)
    if split_large_modules:
        threshold = _self_backend_split_threshold_bytes()
        for ir_text in input_ir_texts:
            if len(ir_text) >= threshold:
                has_large_module = True
                break
    _profile_end(profile, "link_self_split_scan", t)
    cc = str(os.environ.get("CC", "") or "").strip() or "cc"

    if len(input_ir_texts) == 1 and not has_large_module:
        t = _profile_begin(profile)
        host_results = [
            _emit_self_asm_via_host_python(input_ir_texts[0], tmp, 0),
        ]
        _profile_end(profile, "link_self_emit_asm_host", t)
    else:
        t = _profile_begin(profile)
        object_results = _emit_self_objects_many_via_host_python(
            input_ir_texts,
            tmp,
            cc,
            split_large_modules=split_large_modules and has_large_module,
            profile=profile,
        )
        _profile_end(profile, "link_self_emit_objects_host", t)
        obj_paths = []
        for target_id, obj_path in object_results:
            if target_id == "self-aarch64-darwin-v0":
                needs_subsections_via_symbols = True
            obj_paths.append(obj_path)
        tmp_out_path = out_path + ".tmp"
        cmd = [cc] + obj_paths + list(extra_link_inputs)
        if runtime_archive is not None:
            cmd.extend(
                _runtime_archive_link_args_for_native_extensions(
                    runtime_archive,
                    needs_native_extension_exports,
                )
            )
        cmd.extend(["-o", tmp_out_path, "-lm"])
        cmd.extend(extra_link_args)
        cmd.extend(_native_extension_export_link_flags(needs_native_extension_exports))
        if sys.platform == "darwin" and needs_subsections_via_symbols:
            cmd.append("-Wl,-dead_strip")
        cmd.extend(_platform_link_flags())
        if needs_libpython:
            _append_libpython_link_flags(cmd)
        _log(verbose, "self link: " + _join_strings(cmd, " "))
        try:
            total_t = _profile_begin(profile)
            t = _profile_begin(profile)
            subprocess.run(cmd, check=True)
            _profile_end(profile, "link_self_cc_driver", t)
            _finish_self_backend_executable(tmp_out_path, out_path, profile)
            _profile_end(profile, "link_self_cc", total_t)
        except FileNotFoundError as e:
            raise PyPipelineError(
                f"{cc} not found on PATH; cannot link Python frontend output"
            ) from e
        except subprocess.CalledProcessError as e:
            raise PyPipelineError(
                f"self backend link failed (exit {e.returncode})"
            ) from e
        return

    t = _profile_begin(profile)
    for target_id, asm_text in host_results:
        asm_lines = asm_text.splitlines()
        if asm_lines and asm_lines[-1] == ".subsections_via_symbols":
            asm_lines = asm_lines[:-1]
        if target_id == "self-aarch64-darwin-v0":
            needs_subsections_via_symbols = True
        asm_modules.append("\n".join(asm_lines).strip())

    non_empty_asm_modules = []
    for fragment in asm_modules:
        if fragment:
            non_empty_asm_modules.append(fragment)
    asm_text = "\n\n".join(non_empty_asm_modules)
    if needs_subsections_via_symbols:
        asm_text += "\n.subsections_via_symbols\n"

    asm_path = str(os.path.join(tmp, "self_backend.s"))
    with open(asm_path, "w", encoding="utf-8") as f:
        f.write(asm_text)
    _profile_end(profile, "link_self_asm_join_write", t)
    tmp_out_path = out_path + ".tmp"
    cmd = [cc, asm_path, *extra_link_inputs, "-o", tmp_out_path, "-lm"]
    cmd.extend(extra_link_args)
    cmd.extend(_native_extension_export_link_flags(needs_native_extension_exports))
    if sys.platform == "darwin" and needs_subsections_via_symbols:
        cmd.append("-Wl,-dead_strip")
    cmd.extend(_platform_link_flags())
    if runtime_archive is not None:
        cmd[2:2] = _runtime_archive_link_args_for_native_extensions(
            runtime_archive,
            needs_native_extension_exports,
        )
    if needs_libpython:
        _append_libpython_link_flags(cmd)
    _log(verbose, "self link: " + _join_strings(cmd, " "))
    try:
        total_t = _profile_begin(profile)
        t = _profile_begin(profile)
        subprocess.run(cmd, check=True)
        _profile_end(profile, "link_self_cc_driver", t)
        _finish_self_backend_executable(tmp_out_path, out_path, profile)
        _profile_end(profile, "link_self_cc", total_t)
    except FileNotFoundError as e:
        raise PyPipelineError(
            f"{cc} not found on PATH; cannot link Python frontend output"
        ) from e
    except subprocess.CalledProcessError as e:
        raise PyPipelineError(f"self backend link failed (exit {e.returncode})") from e


def _link_with_self_backend_ir_texts(
    ir_texts: list[str],
    out_path: str,
    runtime_archive: Optional[str],
    verbose: bool,
    *,
    needs_libpython: bool = False,
    needs_native_extension_exports: bool = False,
    extra_link_inputs: tuple[str, ...] = (),
    extra_link_args: tuple[str, ...] = (),
    tmp_dir: Optional[str] = None,
    profile: Optional[dict] = None,
) -> None:
    out_path = str(out_path)
    if runtime_archive is not None:
        runtime_archive = str(runtime_archive)

    def _finish_self_backend_executable(tmp_out_path: str, profile) -> None:
        if sys.platform == "darwin":
            t = _profile_begin(profile)
            subprocess.run(
                ["/usr/bin/codesign", "--force", "-s", "-", tmp_out_path],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/codesign", "--verify", tmp_out_path],
                check=True,
            )
            _profile_end(profile, "link_self_codesign", t)

        t = _profile_begin(profile)
        subprocess.run(["/bin/mv", "-f", tmp_out_path, out_path], check=True)
        _profile_end(profile, "link_self_publish_move", t)
        if sys.platform != "darwin":
            return

        t = _profile_begin(profile)
        subprocess.run(
            ["/usr/bin/codesign", "--verify", out_path],
            check=True,
        )
        _profile_end(profile, "link_self_codesign", t)
        t = _profile_begin(profile)
        if _self_backend_publish_sync_enabled():
            subprocess.run(["/bin/sync"], check=True)
        else:
            subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    'cat "$1" >/dev/null',
                    "pcc-self-publish-barrier",
                    out_path,
                ],
                check=True,
            )
        _profile_end(profile, "link_self_publish_barrier", t)

    def _run(tmp: str) -> None:
        asm_modules = []
        needs_subsections_via_symbols = False
        input_ir_texts: list[str] = []
        t = _profile_begin(profile)
        for text in ir_texts:
            input_ir_texts.append(str(text))
        _profile_end(profile, "link_self_normalize_ir", t)
        _debug_dump_self_backend_ir_texts(input_ir_texts)
        split_large_modules = _self_backend_split_large_modules_enabled()
        has_large_module = False
        t = _profile_begin(profile)
        if split_large_modules:
            threshold = _self_backend_split_threshold_bytes()
            for ir_text in input_ir_texts:
                if len(ir_text) >= threshold:
                    has_large_module = True
                    break
        _profile_end(profile, "link_self_split_scan", t)
        cc = str(os.environ.get("CC", "") or "").strip() or "cc"

        if len(input_ir_texts) == 1 and not has_large_module:
            t = _profile_begin(profile)
            host_results = [
                _emit_self_asm_via_host_python(input_ir_texts[0], tmp, 0),
            ]
            _profile_end(profile, "link_self_emit_asm_host", t)
        else:
            t = _profile_begin(profile)
            object_results = _emit_self_objects_many_via_host_python(
                input_ir_texts,
                tmp,
                cc,
                split_large_modules=split_large_modules and has_large_module,
                profile=profile,
            )
            _profile_end(profile, "link_self_emit_objects_host", t)
            obj_paths = []
            for target_id, obj_path in object_results:
                if target_id == "self-aarch64-darwin-v0":
                    needs_subsections_via_symbols = True
                obj_paths.append(obj_path)
            tmp_out_path = out_path + ".tmp"
            cmd = [cc] + obj_paths + list(extra_link_inputs)
            if runtime_archive is not None:
                cmd.extend(
                    _runtime_archive_link_args_for_native_extensions(
                        runtime_archive,
                        needs_native_extension_exports,
                    )
                )
            cmd.extend(["-o", tmp_out_path, "-lm"])
            cmd.extend(extra_link_args)
            cmd.extend(
                _native_extension_export_link_flags(needs_native_extension_exports)
            )
            if sys.platform == "darwin" and needs_subsections_via_symbols:
                cmd.append("-Wl,-dead_strip")
            cmd.extend(_platform_link_flags())
            if needs_libpython:
                _append_libpython_link_flags(cmd)
            _log(verbose, "self link: " + _join_strings(cmd, " "))
            try:
                total_t = _profile_begin(profile)
                t = _profile_begin(profile)
                subprocess.run(cmd, check=True)
                _profile_end(profile, "link_self_cc_driver", t)
                _finish_self_backend_executable(tmp_out_path, profile)
                _profile_end(profile, "link_self_cc", total_t)
            except FileNotFoundError as e:
                raise PyPipelineError(
                    f"{cc} not found on PATH; cannot link Python frontend output"
                ) from e
            except subprocess.CalledProcessError as e:
                raise PyPipelineError(
                    f"self backend link failed (exit {e.returncode})"
                ) from e
            return

        t = _profile_begin(profile)
        for target_id, asm_text in host_results:
            asm_lines = asm_text.splitlines()
            if asm_lines and asm_lines[-1] == ".subsections_via_symbols":
                asm_lines = asm_lines[:-1]
            if target_id == "self-aarch64-darwin-v0":
                needs_subsections_via_symbols = True
            asm_modules.append("\n".join(asm_lines).strip())

        non_empty_asm_modules = []
        for fragment in asm_modules:
            if fragment:
                non_empty_asm_modules.append(fragment)
        asm_text = "\n\n".join(non_empty_asm_modules)
        if needs_subsections_via_symbols:
            asm_text += "\n.subsections_via_symbols\n"

        asm_path = str(os.path.join(tmp, "self_backend.s"))
        with open(asm_path, "w", encoding="utf-8") as f:
            f.write(asm_text)
        _profile_end(profile, "link_self_asm_join_write", t)
        tmp_out_path = out_path + ".tmp"
        cmd = [cc, asm_path, *extra_link_inputs, "-o", tmp_out_path, "-lm"]
        cmd.extend(extra_link_args)
        cmd.extend(_native_extension_export_link_flags(needs_native_extension_exports))
        if sys.platform == "darwin" and needs_subsections_via_symbols:
            cmd.append("-Wl,-dead_strip")
        cmd.extend(_platform_link_flags())
        if runtime_archive is not None:
            cmd[2:2] = _runtime_archive_link_args_for_native_extensions(
                runtime_archive,
                needs_native_extension_exports,
            )
        if needs_libpython:
            _append_libpython_link_flags(cmd)
        _log(verbose, "self link: " + _join_strings(cmd, " "))
        try:
            total_t = _profile_begin(profile)
            t = _profile_begin(profile)
            subprocess.run(cmd, check=True)
            _profile_end(profile, "link_self_cc_driver", t)
            _finish_self_backend_executable(tmp_out_path, profile)
            _profile_end(profile, "link_self_cc", total_t)
        except FileNotFoundError as e:
            raise PyPipelineError(
                f"{cc} not found on PATH; cannot link Python frontend output"
            ) from e
        except subprocess.CalledProcessError as e:
            raise PyPipelineError(
                f"self backend link failed (exit {e.returncode})"
            ) from e

    if tmp_dir is None:
        with tempfile.TemporaryDirectory(prefix="pcc_py_self_") as tmp:
            _link_self_backend_ir_texts_run(
                ir_texts,
                out_path,
                runtime_archive,
                verbose,
                needs_libpython=needs_libpython,
                needs_native_extension_exports=needs_native_extension_exports,
                extra_link_inputs=extra_link_inputs,
                extra_link_args=extra_link_args,
                tmp=tmp,
                profile=profile,
            )
    else:
        _link_self_backend_ir_texts_run(
            ir_texts,
            out_path,
            runtime_archive,
            verbose,
            needs_libpython=needs_libpython,
            needs_native_extension_exports=needs_native_extension_exports,
            extra_link_inputs=extra_link_inputs,
            extra_link_args=extra_link_args,
            tmp=str(tmp_dir),
            profile=profile,
        )


def _link_native(
    ll_paths,
    out_path: str,
    runtime_archive: Optional[str],
    verbose: bool,
    *,
    backend,
    needs_libpython: bool = False,
    needs_native_extension_exports: bool = False,
    extra_link_inputs: tuple[str, ...] = (),
    extra_link_args: tuple[str, ...] = (),
    profile: Optional[dict] = None,
) -> None:
    kind = _native_backend_kind(backend)
    if kind == "llvm":
        _link_with_clang(
            ll_paths,
            out_path,
            runtime_archive,
            verbose,
            needs_libpython=needs_libpython,
            needs_native_extension_exports=needs_native_extension_exports,
            extra_link_inputs=extra_link_inputs,
            extra_link_args=extra_link_args,
        )
        return
    if kind == "self":
        _link_with_self_backend(
            ll_paths,
            out_path,
            runtime_archive,
            verbose,
            needs_libpython=needs_libpython,
            needs_native_extension_exports=needs_native_extension_exports,
            extra_link_inputs=extra_link_inputs,
            extra_link_args=extra_link_args,
            profile=profile,
        )
        return
    raise PyPipelineError(f"unsupported Python native backend: {kind}")


def _clang_link_compatible_python_ir(ir_text: str) -> str:
    """Lower newer LLVM memory-effect attrs before handing .ll to clang."""

    text = str(ir_text)
    # Keep the source patterns split so this module's own string-object
    # globals do not contain the exact rewrite needles when pipeline.py is
    # self-compiled. A raw text replace across the emitted IR cannot
    # distinguish attributes from string initializers.
    text = text.replace("memory(" + "argmem: read)", "argmemonly readonly")
    text = text.replace("memory(" + "argmem: write)", "argmemonly writeonly")
    text = text.replace("memory(" + "none)", "readnone")
    text = text.replace("memory(" + "read)", "readonly")
    return text


def _py_ast_field_names(obj):
    # Fixed-layout pcc1 AST instances do not expose ``__dataclass_fields__``.
    # Reuse codegen's explicit self-host schema instead of silently treating
    # every statement/expression as a leaf; the latter drops nested module
    # attribute reads from closed-world function-object publication analysis.
    return tuple(_self_host_ast_field_names(obj))


def _py_ast_field_value(obj, field_name, default=None):
    return getattr(obj, field_name, default)


def _py_ast_name_used_at_runtime(stmts, ident: str) -> bool:
    from .py_ast import Import as _Import
    from .py_ast import ImportFrom as _ImportFrom
    from .py_ast import Name as _Name
    from .py_ast import Type as _Type

    annotation_slots = {"annotation", "return_ty"}
    pending = [stmts]
    while pending:
        x = pending.pop()
        if x is None:
            continue
        if isinstance(x, _Type):
            continue
        if isinstance(x, (_Import, _ImportFrom)):
            continue
        if isinstance(x, tuple):
            for it in x:
                pending.append(it)
            continue
        if isinstance(x, _Name):
            if _py_ast_field_value(x, "ident", "") == ident:
                return True
            continue
        for slot in _py_ast_field_names(x):
            if slot in annotation_slots:
                continue
            pending.append(_py_ast_field_value(x, slot, None))
    return False


def _module_import_is_scaffold(
    module_name: str | None,
    *,
    ir_scaffold_mode: str,
    current_module: str,
) -> bool:
    if module_name == "pcc.llvm_capi.compat":
        return (
            ir_scaffold_mode == "on"
            or current_module == "pcc.py_frontend.codegen.runtime_abi"
        )
    return module_name in _SCAFFOLD_IMPORT_MODULES


def _resolve_relative_import(module, level, cur_parts):
    if not level:
        return module or ""
    if level > len(cur_parts):
        return module or ""
    base = cur_parts[: len(cur_parts) - level]
    if module:
        return ".".join(base + [module])
    return ".".join(base)


def _module_needs_libpython(
    ast_module,
    native_modules=None,
    ir_scaffold_mode: str = "off",
    strict_no_libpython: bool = False,
) -> bool:
    """Scan the parsed AST for any ``import`` statement; if present the
    link step must pull in libpython because codegen will emit
    ``py_cpy_*`` calls. Walks both module scope and function bodies.

    ``native_modules`` is an optional iterable of dotted names that
    are being compiled natively in the same multi-file invocation —
    imports of these are routed to extern symbols (no CPython path).
    """
    from .py_ast import Import as _Import
    from .py_ast import ImportFrom as _ImportFrom
    from .py_ast import Name as _Name
    from .py_ast import Type as _Type

    native_set = set(native_modules or ())
    cur_mod = _py_ast_field_value(ast_module, "name", "") or ""
    cur_parts = cur_mod.split(".") if cur_mod else []

    pending_stmts = [_py_ast_field_value(ast_module, "body", ())]
    while pending_stmts:
        stmts = pending_stmts.pop()
        for stmt in stmts:
            if isinstance(stmt, _ImportFrom):
                stmt_module = _py_ast_field_value(stmt, "module", None)
                stmt_level = _py_ast_field_value(stmt, "level", 0) or 0
                stmt_names = _py_ast_field_value(stmt, "names", ())
                if _module_import_is_scaffold(
                    stmt_module,
                    ir_scaffold_mode=ir_scaffold_mode,
                    current_module=cur_mod,
                ):
                    continue
                if stmt_module in _TEST_FACADE_IMPORT_MODULES:
                    continue
                if (
                    stmt_module is not None
                    and stmt_module.split(".")[0] in _COMPILE_TIME_ONLY_IMPORT_MODULES
                ):
                    continue
                resolved = _resolve_relative_import(
                    stmt_module,
                    stmt_level,
                    cur_parts,
                )
                compile_only_froms = _COMPILE_TIME_ONLY_IMPORT_FROMS.get(resolved)
                if compile_only_froms is not None:
                    remaining_names = []
                    for alias_name, _ in stmt_names:
                        if alias_name not in compile_only_froms:
                            remaining_names.append(alias_name)
                    if not remaining_names:
                        continue
                allowed_froms = _NATIVE_IMPORT_FROMS.get(resolved)
                if allowed_froms is not None:
                    all_allowed = True
                    for alias_name, _ in stmt_names:
                        if alias_name not in allowed_froms:
                            all_allowed = False
                            break
                    if all_allowed:
                        continue
                if stmt_level and (stmt_module is None or stmt_module == ""):
                    # ``from . import foo`` can denote a sibling module.
                    # When all imported names name modules that are part of
                    # the same native multi-file closure, treat this as local
                    # and do not route through CPython.
                    if resolved:
                        all_modules = True
                        for alias_name, _ in stmt_names:
                            if (
                                _join_dotted_parts([resolved, alias_name])
                                not in native_set
                            ):
                                all_modules = False
                                break
                        if all_modules:
                            continue
                if resolved in native_set:
                    continue
                if not strict_no_libpython:
                    return True
            if isinstance(stmt, _Import):
                remaining = []
                for m, as_name in _py_ast_field_value(stmt, "names", ()):
                    local_name = as_name or m.split(".")[0]
                    if (
                        m in _TEST_FACADE_IMPORT_MODULES
                        or m.split(".")[0] in _COMPILE_TIME_ONLY_IMPORT_MODULES
                        or m in _NATIVE_BUILTIN_IMPORTS
                        or m in native_set
                        or _resolve_pcc_native_extension_path(m) is not None
                    ):
                        continue
                    if (
                        m in _ANNOTATION_ONLY_IMPORT_MODULES
                        and not _py_ast_name_used_at_runtime(
                            _py_ast_field_value(ast_module, "body", ()),
                            local_name,
                        )
                    ):
                        continue
                    remaining.append(m)
                if not remaining:
                    continue
                if not strict_no_libpython:
                    return True
            # Only descend into the body / handler / else branches of
            # statements we know carry a list of sub-stmts.
            body = _py_ast_field_value(stmt, "body", None)
            if body:
                pending_stmts.append(body)
            else_body = _py_ast_field_value(stmt, "else_body", None)
            if else_body:
                pending_stmts.append(else_body)
            finally_body = _py_ast_field_value(stmt, "finally_body", None)
            if finally_body:
                pending_stmts.append(finally_body)
            handlers = _py_ast_field_value(stmt, "handlers", None)
            if handlers:
                for h in handlers:
                    h_body = _py_ast_field_value(h, "body", ())
                    pending_stmts.append(h_body)
    return False


def _module_imports_pcc_native_extension(
    ast_module,
    native_modules=None,
    ir_scaffold_mode: str = "off",
) -> bool:
    from .py_ast import Import as _Import
    from .py_ast import ImportFrom as _ImportFrom

    native_set = set(native_modules or ())
    cur_mod = _py_ast_field_value(ast_module, "name", "") or ""
    cur_parts = cur_mod.split(".") if cur_mod else []

    pending_stmts = [_py_ast_field_value(ast_module, "body", ())]
    while pending_stmts:
        stmts = pending_stmts.pop()
        for stmt in stmts:
            if isinstance(stmt, _ImportFrom):
                stmt_module = _py_ast_field_value(stmt, "module", None)
                stmt_level = _py_ast_field_value(stmt, "level", 0) or 0
                if _module_import_is_scaffold(
                    stmt_module,
                    ir_scaffold_mode=ir_scaffold_mode,
                    current_module=cur_mod,
                ):
                    continue
                resolved = _resolve_relative_import(
                    stmt_module,
                    stmt_level,
                    cur_parts,
                )
                if (
                    resolved not in native_set
                    and _resolve_pcc_native_extension_path(resolved) is not None
                ):
                    return True
                for alias_name, _ in _py_ast_field_value(stmt, "names", ()):
                    candidate = _join_dotted_parts([resolved, alias_name])
                    if (
                        candidate not in native_set
                        and _resolve_pcc_native_extension_path(candidate) is not None
                    ):
                        return True
            elif isinstance(stmt, _Import):
                for m, _as_name in _py_ast_field_value(stmt, "names", ()):
                    if (
                        m not in native_set
                        and _resolve_pcc_native_extension_path(m) is not None
                    ):
                        return True
            body = _py_ast_field_value(stmt, "body", None)
            if body:
                pending_stmts.append(body)
            else_body = _py_ast_field_value(stmt, "else_body", None)
            if else_body:
                pending_stmts.append(else_body)
            finally_body = _py_ast_field_value(stmt, "finally_body", None)
            if finally_body:
                pending_stmts.append(finally_body)
            handlers = _py_ast_field_value(stmt, "handlers", None)
            if handlers:
                for h in handlers:
                    h_body = _py_ast_field_value(h, "body", ())
                    pending_stmts.append(h_body)
    return False


def _resolve_python_config_command() -> str:
    config_env = str(os.environ.get("PCC_PYTHON_CONFIG", "")).strip()
    if config_env:
        return config_env

    try:
        import sysconfig as _sysconfig

        bindir = str(_sysconfig.get_config_var("BINDIR") or "").strip()
        ldversion = str(
            _sysconfig.get_config_var("LDVERSION")
            or _sysconfig.get_config_var("VERSION")
            or ""
        ).strip()
        candidates = []
        if bindir:
            if ldversion:
                candidates.append(
                    str(os.path.join(bindir, f"python{ldversion}-config"))
                )
            candidates.extend(
                [
                    str(os.path.join(bindir, "python3-config")),
                    str(os.path.join(bindir, "python-config")),
                ]
            )
        for candidate in candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    except Exception:
        pass

    return "python3-config"


def _ir_needs_libpython(ir_text: str) -> bool:
    """Return True when IR calls a CPython helper that requires the
    libpython-backed runtime archive.

    Generated ``main`` only calls ``py_cpy_main_exitcode`` when the
    compile is already known to need the CPython fallback, so any
    remaining ``py_cpy_*`` call site means the libpython archive is
    required.
    """
    if "@py_cpy_" not in ir_text:
        return False
    prev = ""
    prev2 = ""
    for line in ir_text.splitlines():
        if "@py_cpy_" not in line:
            prev2 = prev
            prev = line
            continue
        stripped = line.lstrip()
        if stripped.startswith("call ") or stripped.startswith("tail call "):
            return True
        if " = call " in line or " = tail call " in line:
            return True
        prev2 = prev
        prev = line
    return False


def compile_python(
    src_path: str,
    out_path: str,
    *,
    verbose: bool = False,
    emit_llvm_only: bool = False,
    libpython_mode: Optional[str] = None,
    ir_scaffold_mode: Optional[str] = None,
    backend: Optional[str] = None,
    gpu_backend: Optional[str] = None,
    recursive_stdlib: bool = False,
    python_library: bool = False,
    runtime_archive: Optional[str] = None,
    profile: Optional[dict] = None,
) -> None:
    """Compile a single ``.py`` file to a native executable.

    Parameters
    ----------
    src_path:
        Path to the input ``.py`` source file.
    out_path:
        Output path. When ``emit_llvm_only`` is False this is the
        native executable; when True it is the ``.ll`` file.
    verbose:
        If True, print each pipeline step and timing info to stderr.
    emit_llvm_only:
        If True, stop after writing LLVM IR (used by ``--emit-llvm``).
    backend:
        Native emission backend for executable output. ``llvm`` keeps
        the historical clang ``.ll`` path; ``self`` lowers ``.ll``
        through the in-repo asm backend before linking.
    python_library:
        Emit a library module object shape: no program ``@main`` is
        synthesized, but module init/top-init functions remain available
        for an embedding entrypoint to call. This is intended for
        pcc-Python runtime archives and must be paired with
        ``emit_llvm_only``.
    runtime_archive:
        Optional explicit native runtime archive for isolated builds/tests.
        When omitted, the configured repository runtime is located or built.
    """
    if python_library and not emit_llvm_only:
        raise PyPipelineError("python_library mode requires emit_llvm_only=True")
    # Imports are deferred so that modules still under construction by
    # sibling agents don't break ``pcc --help`` or ``.c`` compilation.
    total_start = _profile_begin(profile)
    libpython_mode = _resolve_libpython_mode(libpython_mode)
    ir_scaffold_mode = _resolve_ir_scaffold_mode(ir_scaffold_mode)
    gpu_backend_kind = _resolve_gpu_backend_kind(gpu_backend)

    try:
        from .type_infer import infer_module as _infer_module
        from .codegen.layer1 import L1CodeGen as _L1CodeGen
    except ImportError as e:
        raise PyPipelineError(
            f"Python frontend module not available: {e}. "
            "The Python pipeline is currently Phase 1 MVP and some "
            "components may still be under construction."
        ) from e

    if not os.path.isfile(src_path):
        raise PyPipelineError(f"input file not found: {src_path}")

    module_name = _module_name_from_src(src_path)
    gpu_source = None
    gpu_source_has_kernels = False
    gpu_artifact_dir: Optional[str] = None
    gpu_metallib_path: Optional[str] = None
    if gpu_backend_kind == "metal":
        with open(src_path, "r", encoding="utf-8") as f:
            gpu_source = f.read()
        source_contains_gpu_kernel = getattr(
            _load_pcc_gpu_kernel_module(),
            "source_contains_gpu_kernel",
        )

        gpu_source_has_kernels = source_contains_gpu_kernel(gpu_source, src_path)
    should_auto_close = (not emit_llvm_only) or module_name.endswith(".__main__")
    t = _profile_begin(profile)
    auto_srcs, auto_mods = (
        _collect_relative_module_closure(
            src_path,
            include_same_package_absolute=(module_name.endswith(".__main__")),
            recurse_same_package_absolute=(libpython_mode == "off"),
        )
        if should_auto_close
        else ([str(os.path.abspath(src_path))], [module_name])
    )
    _profile_end(profile, "collect_relative_module_closure", t)
    t = _profile_begin(profile)
    auto_srcs, auto_mods = _filter_ir_scaffold_closure(
        auto_srcs,
        auto_mods,
        ir_scaffold_mode=ir_scaffold_mode,
    )
    _profile_end(profile, "filter_ir_scaffold_closure", t)
    t = _profile_begin(profile)
    auto_seen = {mod_name: src_path for src_path, mod_name in zip(auto_srcs, auto_mods)}
    _expand_native_extension_module_object_ports(
        auto_srcs,
        auto_mods,
        auto_seen,
    )
    _profile_end(profile, "expand_native_extension_module_object_ports", t)
    t = _profile_begin(profile)
    _validate_package_site_no_libpython_abi(
        auto_srcs,
        libpython_mode=libpython_mode,
    )
    _profile_end(profile, "validate_package_site_abi", t)
    _profile_counter(profile, "auto_files", len(auto_srcs))
    effective_recursive_stdlib = recursive_stdlib
    if (
        not effective_recursive_stdlib
        and libpython_mode == "off"
        and not python_library
        and _sources_use_native_stdlib(auto_srcs)
    ):
        effective_recursive_stdlib = True
    if gpu_source_has_kernels and effective_recursive_stdlib:
        raise PyPipelineError(
            "--gpu-backend=metal currently supports @gpu.kernel only in "
            "single-file Python compiles"
        )
    # Issue 11.B.1.2: when recursive_stdlib is on, force the multi-file
    # path so _expand_recursive_stdlib has a chance to pull pure-Python
    # stdlib into the native compile. The multi-file path also
    # populates _native_module_exports which lets _emit_import skip
    # py_cpy_import for natively-compiled modules.
    if effective_recursive_stdlib and len(auto_srcs) == 1:
        entry = _first_string(auto_mods)
        compile_python_multi(
            auto_srcs,
            out_path,
            verbose=verbose,
            emit_llvm_only=emit_llvm_only,
            entry_module=entry,
            module_names=auto_mods,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            backend=backend,
            recursive_stdlib=True,
            runtime_archive=runtime_archive,
            profile=profile,
        )
        _profile_end(profile, "compile_python_total", total_start)
        return
    if len(auto_srcs) > 1:
        if gpu_source_has_kernels:
            raise PyPipelineError(
                "--gpu-backend=metal currently supports @gpu.kernel only in "
                "single-file Python compiles"
            )
        if python_library:
            raise PyPipelineError(
                "python_library mode only supports a single Python source"
            )
        if verbose:
            _log(
                verbose,
                "auto multi-file package compile: " + _join_strings(auto_mods, ", "),
            )
        entry = _first_string(auto_mods)
        compile_python_multi(
            auto_srcs,
            out_path,
            verbose=verbose,
            emit_llvm_only=emit_llvm_only,
            entry_module=entry,
            module_names=auto_mods,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            backend=backend,
            recursive_stdlib=effective_recursive_stdlib,
            runtime_archive=runtime_archive,
            profile=profile,
        )
        _profile_end(profile, "compile_python_total", total_start)
        return

    if verbose:
        _log(verbose, "reading " + src_path)
    t = _profile_begin(profile)
    if gpu_source is None:
        with open(src_path, "r", encoding="utf-8") as f:
            source = f.read()
    else:
        source = gpu_source
    _profile_end(profile, "read_source", t)
    if gpu_source_has_kernels:
        t = _profile_begin(profile)
        prepare_gpu_kernels_for_source = getattr(
            _load_pcc_gpu_kernel_module(),
            "prepare_gpu_kernels_for_source",
        )

        artifact_dir = str(out_path) + ".gpu"
        gpu_artifact_dir = artifact_dir
        metallib_path = str(out_path) + ".metallib"
        gpu_metallib_path = metallib_path
        try:
            source, gpu_artifacts = prepare_gpu_kernels_for_source(
                source,
                src_path,
                backend=gpu_backend_kind,
                artifact_dir=artifact_dir,
                metallib_path=metallib_path,
            )
        except Exception as exc:
            raise PyPipelineError(
                "Metal GPU kernel lowering failed: " + str(exc)
            ) from exc
        _profile_counter(profile, "gpu_kernels", len(gpu_artifacts))
        _profile_end(profile, "gpu_kernel_lowering", t)

    _log(verbose, "parse")
    # pcc.parse.py_parse + pcc.parse.py_lift is the bootstrap-safe
    # parser path. The previous CPython-ast escape hatch kept a
    # libpython import edge alive in the compiled pipeline, so the
    # self-host path no longer emits it.
    from ..parse.py_lift import parse_and_lift as _parse_and_lift

    _log(verbose, "parse")
    t = _profile_begin(profile)
    ast_mod = _parse_and_lift(
        source,
        src_path,
        _module_name_from_src(src_path),
    )
    _profile_end(profile, "parse_and_lift", t)

    t = _profile_begin(profile)
    ast_needs_libpython = _module_needs_libpython(
        ast_mod,
        ir_scaffold_mode=ir_scaffold_mode,
        strict_no_libpython=(libpython_mode == "off"),
    )
    _profile_end(profile, "detect_libpython_need", t)
    t = _profile_begin(profile)
    ast_needs_native_extension_exports = _module_imports_pcc_native_extension(
        ast_mod,
        ir_scaffold_mode=ir_scaffold_mode,
    )
    _profile_end(profile, "detect_native_extension_exports", t)

    _log(verbose, "type_infer")
    t = _profile_begin(profile)
    typed_mod = _infer_module(ast_mod)
    _profile_end(profile, "type_infer", t)

    _log(verbose, "codegen (layer1)")
    t = _profile_begin(profile)
    codegen = _L1CodeGen(
        typed_mod,
        (libpython_mode == "on" or (libpython_mode == "auto" and ast_needs_libpython)),
        ir_scaffold_mode,
    )
    codegen._strict_no_libpython = libpython_mode == "off"
    codegen._prefer_native_callable_values = libpython_mode == "off"
    if python_library:
        codegen._skip_program_main = True
        if _is_py_runtime_library_source(src_path):
            codegen._suppress_implicit_gc_roots = True
            codegen._suppress_borrowed_return_retain = True
    # Layer1 codegen returns IR text here.  Do not defensively call
    # isinstance(..., str) or str(...) on the result: pcc1/pcc2 self-host can
    # hit a builtin-type class boundary after codegen has already returned.
    ir_text = codegen.generate(typed_mod)
    _profile_end(profile, "codegen_layer1", t)
    _profile_counter(profile, "ir_bytes", len(ir_text))
    native_backend = None
    if not emit_llvm_only:
        native_backend = _resolve_native_backend(backend)
    t = _profile_begin(profile)
    ir_text = _apply_python_ir_pass_pipeline(
        ir_text,
        module_name=module_name,
        verbose=verbose,
        default_raw=_default_python_ir_pass_raw_for_request(
            native_backend,
            emit_llvm_only=emit_llvm_only,
            backend=backend,
        ),
        strict_no_libpython=(libpython_mode == "off"),
    )
    _profile_end(profile, "python_ir_pass_pipeline", t)

    if emit_llvm_only:
        # out_path is a .ll path; just write it and return.
        t = _profile_begin(profile)
        _emit_ll(ir_text, out_path, verbose)
        _profile_end(profile, "emit_ll", t)
        _profile_end(profile, "compile_python_total", total_start)
        return

    t = _profile_begin(profile)
    needs_libpython = ast_needs_libpython
    reasons = []
    if needs_libpython:
        reasons.append("imports still lower through CPython fallback")
    # Fallback: scan the generated IR for direct call sites into the
    # libpython shim (``py_cpy_*``). Codegen emits these for DynType
    # method dispatch, ``hasattr`` fallback, ``x.__copy__()`` and
    # similar even when the source has no explicit ``import``. Using
    # ``\bcall`` rather than a plain text search avoids triggering on
    # the ``declare external`` stubs emitted unconditionally for all
    # runtime helpers.
    if _ir_needs_libpython(ir_text):
        needs_libpython = True
        reasons.append("generated IR still calls py_cpy_* helpers")
    needs_libpython = _finalize_libpython_mode(
        detected=needs_libpython,
        mode=libpython_mode,
        context=str(src_path),
        reasons=reasons,
    )
    _profile_end(profile, "finalize_libpython_mode", t)
    if native_backend is None:
        native_backend = _resolve_native_backend(backend)
    if verbose:
        _log(verbose, "native backend: " + str(native_backend))

    t = _profile_begin(profile)
    if runtime_archive is not None:
        runtime = os.path.abspath(str(runtime_archive))
        if not os.path.isfile(runtime):
            raise PyPipelineError("explicit runtime archive not found: " + runtime)
    else:
        runtime = _ensure_runtime(
            verbose,
            needs_libpython=needs_libpython,
        )
    _profile_end(profile, "ensure_runtime", t)
    extra_link_inputs: tuple[str, ...] = ()
    extra_link_args: tuple[str, ...] = ()
    if gpu_source_has_kernels and gpu_backend_kind == "metal":
        if gpu_artifact_dir is None:
            gpu_artifact_dir = str(out_path) + ".gpu"
        t = _profile_begin(profile)
        compile_metal_runtime_bridge = getattr(
            _load_pcc_gpu_metal_module(),
            "compile_metal_runtime_bridge",
        )

        try:
            metal_bridge_obj = compile_metal_runtime_bridge(
                os.path.join(gpu_artifact_dir, "pcc_metal_runtime.o"),
            )
        except Exception as exc:
            raise PyPipelineError(
                "Metal GPU host bridge compile failed: " + str(exc)
            ) from exc
        extra_link_inputs = (str(metal_bridge_obj),)
        if gpu_metallib_path is None:
            gpu_metallib_path = str(out_path) + ".metallib"
        extra_link_args = (
            "-Xlinker",
            "-sectcreate",
            "-Xlinker",
            "__PCCMETAL",
            "-Xlinker",
            "__metallib",
            "-Xlinker",
            str(gpu_metallib_path),
            "-framework",
            "Foundation",
            "-framework",
            "Metal",
        )
        _profile_end(profile, "gpu_metal_bridge_compile", t)
    if native_backend == "self" and _self_backend_skip_ll_temp():
        if verbose:
            _log(
                verbose,
                "self backend: linking LLVM IR text without pipeline .ll temp",
            )
        t = _profile_begin(profile)
        _link_with_self_backend_ir_texts(
            [ir_text],
            out_path,
            runtime,
            verbose,
            needs_libpython=needs_libpython,
            needs_native_extension_exports=ast_needs_native_extension_exports,
            extra_link_inputs=extra_link_inputs,
            extra_link_args=extra_link_args,
            profile=profile,
        )
        _profile_end(profile, "link_self_backend_ir_texts", t)
        if verbose:
            _log(verbose, "wrote executable: " + out_path)
        _profile_end(profile, "compile_python_total", total_start)
        return

    # Write IR to a temp .ll, link with clang + runtime, produce exe.
    with tempfile.TemporaryDirectory(prefix="pcc_py_") as tmp:
        ll_name = str(os.path.basename(out_path)) + ".ll"
        ll_path = str(os.path.join(tmp, ll_name))
        link_ir_text = ir_text
        if native_backend != "self":
            link_ir_text = _clang_link_compatible_python_ir(link_ir_text)
        t = _profile_begin(profile)
        _emit_ll(link_ir_text, ll_path, verbose)
        _profile_end(profile, "emit_ll", t)
        t = _profile_begin(profile)
        _link_native(
            [ll_path],
            out_path,
            runtime,
            verbose,
            backend=native_backend,
            needs_libpython=needs_libpython,
            needs_native_extension_exports=ast_needs_native_extension_exports,
            extra_link_inputs=extra_link_inputs,
            extra_link_args=extra_link_args,
            profile=profile,
        )
        _profile_end(profile, "link_native", t)
    if verbose:
        _log(verbose, "wrote executable: " + out_path)
    _profile_end(profile, "compile_python_total", total_start)


def _python_frontend_jobs(job_count_hint: int) -> int:
    raw = str(os.environ.get(_PY_FRONTEND_JOBS_ENV, "") or "").strip().lower()
    if not raw:
        raw = "auto"
    if raw in ("0", "off", "false", "no"):
        return 1
    if raw in ("auto", "on", "true", "yes"):
        jobs = os.cpu_count() or 1
        # Frontend workers now split closed-world export discovery into
        # parallel shards, then run codegen workers with a shared export table.
        # On the bootstrap closure, eight to ten workers win; twelve starts to
        # lose to process/IO contention.
        if jobs > 10:
            jobs = 10
    else:
        try:
            jobs = int(raw)
        except ValueError:
            jobs = 1
    if jobs < 2:
        return 1
    if job_count_hint < 2:
        return 1
    if jobs > job_count_hint:
        jobs = job_count_hint
    return jobs


def _python_frontend_worker_timing_enabled() -> bool:
    raw = str(os.environ.get(_PY_FRONTEND_WORKER_TIMING_ENV, "") or "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _python_frontend_worker_env_prefix() -> str:
    """Keep verbose worker timing separate from aggregate CLI profiling."""
    prefix = "PCC_PY_FRONTEND_JOBS=1"
    if _python_frontend_worker_timing_enabled():
        prefix += " PCC_PY_FRONTEND_WORKER_TIMING=1"
    return prefix


def _python_frontend_ast_wire_enabled() -> bool:
    raw = str(os.environ.get(_PY_FRONTEND_AST_WIRE_ENV, "") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _is_native_worker_executable(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return True
    if magic == b"\x7fELF":
        return True
    if magic in (
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    ):
        return True
    if magic.startswith(b"#!"):
        return False
    return True


def _python_frontend_worker_executable() -> str:
    candidates = []
    try:
        candidates.append(str(getattr(sys, "executable", "") or ""))
    except Exception:
        pass
    try:
        if len(sys.argv) > 0:
            candidates.append(str(sys.argv[0] or ""))
    except Exception:
        pass
    for exe in candidates:
        if not exe:
            continue
        if exe.endswith(".py"):
            continue
        base = os.path.basename(exe).lower()
        # Source-mode CPython stage1 has sys.executable == python.  The hidden
        # worker entry exists on the compiled bootstrap binary, not on the
        # Python interpreter itself.
        if base.startswith("python"):
            continue
        if os.path.isfile(exe) and _is_native_worker_executable(exe):
            return exe
    return ""


def _python_frontend_worker_command_prefix() -> list[str]:
    exe = _python_frontend_worker_executable()
    if exe:
        return [exe]
    try:
        py_exe = str(getattr(sys, "executable", "") or "")
    except Exception:
        py_exe = ""
    if py_exe and os.path.isfile(py_exe):
        # Source-mode stage1 reaches the same bootstrap entry through
        # pcc/__main__.py, so the hidden worker is available as
        # ``python -m pcc --pcc-python-multi-codegen-worker ...``. This avoids
        # forcing stage1 to stay serial while stage2/stage3 use native workers.
        return [py_exe, "-m", "pcc"]
    return []


def _can_spawn_python_frontend_worker() -> bool:
    return bool(_python_frontend_worker_command_prefix())


def _python_frontend_codegen_chunks(src_paths, jobs: int):
    weighted = []
    i = 0
    while i < len(src_paths):
        try:
            with open(src_paths[i], "r", encoding="utf-8") as f:
                weight = len(f.read())
        except OSError:
            weight = 1
        insert_at = 0
        while insert_at < len(weighted) and weighted[insert_at][0] >= weight:
            insert_at += 1
        weighted.insert(insert_at, (weight, i))
        i += 1

    chunks = []
    totals = []
    i = 0
    while i < jobs:
        chunks.append([])
        totals.append(0)
        i += 1

    for weight, index in weighted:
        target = 0
        j = 1
        while j < len(totals):
            if totals[j] < totals[target]:
                target = j
            j += 1
        chunks[target].append(index)
        totals[target] += weight

    out = []
    for chunk in chunks:
        if not chunk:
            continue
        chunk.sort()
        out.append(chunk)
    return out


def _python_frontend_codegen_chunk_count(
    src_count: int, jobs: int, worker_prefix
) -> int:
    src_count = int(src_count)
    jobs = int(jobs)
    if src_count <= 1:
        return 1
    if worker_prefix:
        worker_exe = str(worker_prefix[0])
        worker_base = os.path.basename(worker_exe).lower()
        if not worker_base.startswith("python") and _is_native_worker_executable(
            worker_exe
        ):
            # A compiled frontend worker must own exactly one module. Reusing
            # one native process across a chunk retained compiler graphs and
            # eventually produced zero-byte IR for later modules.
            return src_count
    if jobs <= 1:
        return 1
    if jobs > src_count:
        return src_count
    return jobs


def _write_python_frontend_worker_manifest(
    path: str,
    result_path: str,
    ir_dir: str,
    exports_path: str,
    ast_dir: str,
    src_paths,
    module_names,
    assigned_indices,
    *,
    entry_module: str,
    sibling_inits,
    libpython_mode: str,
    ir_scaffold_mode: str,
    verbose: bool,
    job_kind: str = "codegen",
) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(_PY_FRONTEND_WORKER_MANIFEST_V4 + "\n")
        f.write(result_path + "\n")
        f.write(ir_dir + "\n")
        f.write(exports_path + "\n")
        f.write(job_kind + "\n")
        f.write(ast_dir + "\n")
        f.write(entry_module + "\n")
        f.write(libpython_mode + "\n")
        f.write(ir_scaffold_mode + "\n")
        f.write("1\n" if verbose else "0\n")
        f.write(str(len(sibling_inits)) + "\n")
        for mod_name in sibling_inits:
            f.write(str(mod_name) + "\n")
        f.write(str(len(src_paths)) + "\n")
        i = 0
        while i < len(src_paths):
            f.write(
                str(i) + "\t" + str(module_names[i]) + "\t" + str(src_paths[i]) + "\n"
            )
            i += 1
        f.write(str(len(assigned_indices)) + "\n")
        for index in assigned_indices:
            f.write(str(index) + "\n")


def _read_python_frontend_worker_manifest(path: str):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    pos = 0
    if not lines or lines[0] not in (
        _PY_FRONTEND_WORKER_MANIFEST_V1,
        _PY_FRONTEND_WORKER_MANIFEST_V2,
        _PY_FRONTEND_WORKER_MANIFEST_V3,
        _PY_FRONTEND_WORKER_MANIFEST_V4,
    ):
        raise PyPipelineError("invalid frontend codegen worker manifest")
    version = lines[0]
    pos += 1
    result_path = lines[pos]
    pos += 1
    ir_dir = lines[pos]
    pos += 1
    exports_path = ""
    if version == _PY_FRONTEND_WORKER_MANIFEST_V2:
        exports_path = lines[pos]
        pos += 1
    job_kind = "codegen"
    ast_dir = ""
    if version == _PY_FRONTEND_WORKER_MANIFEST_V3:
        exports_path = lines[pos]
        pos += 1
        job_kind = lines[pos]
        pos += 1
    if version == _PY_FRONTEND_WORKER_MANIFEST_V4:
        exports_path = lines[pos]
        pos += 1
        job_kind = lines[pos]
        pos += 1
        ast_dir = lines[pos]
        pos += 1
    entry_module = lines[pos]
    pos += 1
    libpython_mode = lines[pos]
    pos += 1
    ir_scaffold_mode = lines[pos]
    pos += 1
    verbose = lines[pos] == "1"
    pos += 1
    sibling_count = int(lines[pos])
    pos += 1
    sibling_inits = []
    i = 0
    while i < sibling_count:
        sibling_inits.append(lines[pos])
        pos += 1
        i += 1
    module_count = int(lines[pos])
    pos += 1
    src_paths = []
    module_names = []
    i = 0
    while i < module_count:
        parts = lines[pos].split("\t", 2)
        if len(parts) != 3:
            raise PyPipelineError("invalid frontend worker module entry")
        src_paths.append(parts[2])
        module_names.append(parts[1])
        pos += 1
        i += 1
    assigned_count = int(lines[pos])
    pos += 1
    assigned_indices = []
    i = 0
    while i < assigned_count:
        assigned_indices.append(int(lines[pos]))
        pos += 1
        i += 1
    return {
        "result_path": result_path,
        "ir_dir": ir_dir,
        "exports_path": exports_path,
        "ast_dir": ast_dir,
        "job_kind": job_kind,
        "entry_module": entry_module,
        "libpython_mode": libpython_mode,
        "ir_scaffold_mode": ir_scaffold_mode,
        "verbose": verbose,
        "sibling_inits": tuple(sibling_inits),
        "src_paths": src_paths,
        "module_names": module_names,
        "assigned_indices": assigned_indices,
    }


def _write_python_frontend_worker_error(result_path: str, message: str) -> None:
    safe = str(message).replace("\t", " ").replace("\n", " ")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write("ERR\t" + safe + "\n")


def _read_python_frontend_worker_ir(ir_path: str, module_name: str) -> str:
    """Read one worker result and reject the silent empty-module failure."""
    with open(ir_path, "r", encoding="utf-8") as f:
        ir_text = f.read()
    if len(ir_text) == 0:
        raise PyPipelineError(
            "frontend codegen worker produced empty LLVM IR for module "
            + module_name
        )
    return ir_text


def _safe_exception_text(exc) -> str:
    try:
        text = str(exc)
    except Exception:
        text = ""
    if text is None:
        return ""
    return text


def _shell_quote_arg(text: str) -> str:
    text = str(text)
    if text == "":
        return "''"
    safe = True
    i = 0
    while i < len(text):
        ch = text[i]
        ok = (
            ("a" <= ch <= "z")
            or ("A" <= ch <= "Z")
            or ("0" <= ch <= "9")
            or ch in "/._-+=:,@%"
        )
        if not ok:
            safe = False
            break
        i += 1
    if safe:
        return text
    out = "'"
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            out += "'\"'\"'"
        else:
            out += ch
        i += 1
    out += "'"
    return out


def _run_python_multi_export_worker(manifest) -> int:
    worker_timing = _python_frontend_worker_timing_enabled()
    total_t = time.monotonic() if worker_timing else 0.0
    result_path = str(manifest["result_path"])
    ir_dir = str(manifest["ir_dir"])
    ast_dir = str(manifest.get("ast_dir", "") or "")
    src_paths = manifest["src_paths"]
    module_names = manifest["module_names"]
    assigned_indices = manifest["assigned_indices"]
    subset_srcs = []
    subset_names = []
    for index in assigned_indices:
        subset_srcs.append(src_paths[index])
        subset_names.append(module_names[index])
    parsed_modules, native_exports, _derived_class_map = build_closed_world_context(
        subset_srcs,
        subset_names,
        profile=None,
        lift_indices=None,
        merge_exports=False,
    )
    if ast_dir:
        for local_i, ast_mod in enumerate(parsed_modules):
            index = assigned_indices[local_i]
            ast_path = os.path.join(ast_dir, "module_" + str(index) + ".json")
            _write_py_ast_wire(ast_path, ast_mod)
    edges = _closed_world_reexport_edges(
        parsed_modules,
        subset_names,
        subset_srcs,
        module_names,
    )
    function_object_uses = _mark_closed_world_function_object_exports(
        parsed_modules,
        subset_names,
        subset_srcs,
        native_exports,
        known_module_names=module_names,
    )
    exports_path = os.path.join(
        ir_dir, "exports_" + os.path.basename(result_path) + ".json"
    )
    edges_path = os.path.join(
        ir_dir, "reexports_" + os.path.basename(result_path) + ".json"
    )
    _write_native_exports_wire(
        exports_path,
        native_exports,
        {},
        function_object_uses=function_object_uses,
    )
    _write_reexport_edges_wire(edges_path, edges)
    with open(result_path, "w", encoding="utf-8") as f:
        line = "EXPORT\t" + exports_path + "\t" + edges_path
        if worker_timing:
            total_ms = int((time.monotonic() - total_t) * 1000)
            line += "\t" + str(total_ms)
        f.write(line + "\n")
    return 0


def run_python_multi_codegen_worker(manifest_path: str) -> int:
    result_path = ""
    try:
        manifest = _read_python_frontend_worker_manifest(manifest_path)
        result_path = str(manifest["result_path"])
        job_kind = str(manifest.get("job_kind", "codegen"))
        if job_kind == "export":
            return _run_python_multi_export_worker(manifest)
        from .type_infer import infer_module as _infer_module
        from .codegen.layer1 import L1CodeGen as _L1CodeGen

        src_paths = manifest["src_paths"]
        module_names = manifest["module_names"]
        entry_module = str(manifest["entry_module"])
        sibling_inits = tuple(manifest["sibling_inits"])
        libpython_mode = str(manifest["libpython_mode"])
        ir_scaffold_mode = str(manifest["ir_scaffold_mode"])
        verbose = bool(manifest["verbose"])
        assigned_indices = manifest["assigned_indices"]
        ir_dir = str(manifest["ir_dir"])
        exports_path = str(manifest.get("exports_path", "") or "")
        ast_dir = str(manifest.get("ast_dir", "") or "")
        worker_timing = _python_frontend_worker_timing_enabled()
        if exports_path:
            native_exports, derived_class_map = _read_native_exports_wire(exports_path)
            parsed_modules = [None for _src in src_paths]
            parse_ms_by_index = {}
            if ast_dir:
                for index in assigned_indices:
                    parse_t = time.monotonic() if worker_timing else 0.0
                    ast_path = os.path.join(
                        ast_dir,
                        "module_" + str(index) + ".json",
                    )
                    parsed_modules[index] = _read_py_ast_wire(ast_path)
                    if worker_timing:
                        parse_ms_by_index[index] = int(
                            (time.monotonic() - parse_t) * 1000
                        )
            else:
                from ..parse.py_lift import parse_and_lift as _parse_and_lift

                for index in assigned_indices:
                    src = src_paths[index]
                    mod_name = module_names[index]
                    parse_t = time.monotonic() if worker_timing else 0.0
                    with open(src, "r", encoding="utf-8") as f:
                        source = f.read()
                    parsed_modules[index] = _parse_and_lift(source, src, mod_name)
                    if worker_timing:
                        parse_ms_by_index[index] = int(
                            (time.monotonic() - parse_t) * 1000
                        )
        else:
            parse_ms_by_index = {}
            parsed_modules, native_exports, derived_class_map = (
                build_closed_world_context(
                    src_paths,
                    module_names,
                    profile=None,
                    lift_indices=assigned_indices,
                )
            )
        result_lines = []
        for index in assigned_indices:
            src = src_paths[index]
            mod_name = module_names[index]
            if worker_timing:
                sys.stderr.write(
                    "pcc frontend worker start index="
                    + str(index)
                    + " module="
                    + mod_name
                    + "\n"
                )
            ast_mod = parsed_modules[index]
            needs_native_extension_exports = _module_imports_pcc_native_extension(
                ast_mod,
                native_modules=module_names,
                ir_scaffold_mode=ir_scaffold_mode,
            )
            external_for_this = {}
            for k, v in native_exports.items():
                if k != mod_name:
                    external_for_this[k] = v
            infer_ms = 0
            try:
                infer_t = time.monotonic() if worker_timing else 0.0
                typed_mod = _infer_module(
                    ast_mod,
                    external_exports=external_for_this,
                    derived_class_map=derived_class_map,
                    contextual_host_params=_contextual_host_params_for_module(
                        ast_mod,
                        mod_name,
                    ),
                )
                if worker_timing:
                    infer_ms = int((time.monotonic() - infer_t) * 1000)
                    sys.stderr.write(
                        "pcc frontend worker inferred index="
                        + str(index)
                        + " module="
                        + mod_name
                        + " infer_ms="
                        + str(infer_ms)
                        + "\n"
                    )
            except Exception as exc:
                raise PyPipelineError(
                    "type_infer["
                    + mod_name
                    + "]: "
                    + type(exc).__name__
                    + ": "
                    + _safe_exception_text(exc)
                ) from exc
            try:
                codegen = _L1CodeGen(
                    typed_mod,
                    (libpython_mode == "on"),
                    ir_scaffold_mode,
                )
                codegen._strict_no_libpython = libpython_mode == "off"
                codegen._prefer_native_callable_values = libpython_mode == "off"
                is_entry = mod_name == entry_module
                codegen._skip_program_main = not is_entry
                codegen._sibling_module_inits = sibling_inits
                if _module_uses_default_native_exports(mod_name):
                    codegen_exports = _copy_native_module_exports(
                        codegen._native_module_exports
                    )
                else:
                    codegen_exports = {}
                for k, v in native_exports.items():
                    if k != mod_name:
                        codegen_exports[k] = v
                codegen._native_module_exports = codegen_exports
                codegen._native_function_object_exports = (
                    _closed_world_function_object_exports(native_exports, mod_name)
                )
            except Exception as exc:
                raise PyPipelineError(
                    "codegen_prepare["
                    + mod_name
                    + "]: "
                    + type(exc).__name__
                    + ": "
                    + _safe_exception_text(exc)
                ) from exc
            if verbose:
                _log(verbose, "worker codegen[" + mod_name + "]")
            codegen_ms = 0
            try:
                codegen_t = time.monotonic() if worker_timing else 0.0
                ir_text = str(codegen.generate(typed_mod))
                if worker_timing:
                    codegen_ms = int((time.monotonic() - codegen_t) * 1000)
            except Exception as exc:
                raise PyPipelineError(
                    "codegen["
                    + mod_name
                    + "]: "
                    + type(exc).__name__
                    + ": "
                    + _safe_exception_text(exc)
                ) from exc
            ir_path = os.path.join(ir_dir, "module_" + str(index) + ".ll")
            with open(ir_path, "w", encoding="utf-8") as f:
                f.write(ir_text)
            result_line = (
                "OK\t"
                + str(index)
                + "\t"
                + mod_name
                + "\t"
                + ("1" if _ir_needs_libpython(ir_text) else "0")
                + "\t"
                + ("1" if needs_native_extension_exports else "0")
                + "\t"
                + str(len(ir_text))
                + "\t"
                + ir_path
            )
            if worker_timing:
                result_line += (
                    "\t"
                    + str(parse_ms_by_index.get(index, 0))
                    + "\t"
                    + str(infer_ms)
                    + "\t"
                    + str(codegen_ms)
                )
                sys.stderr.write(
                    "pcc frontend worker done index="
                    + str(index)
                    + " module="
                    + mod_name
                    + " infer_ms="
                    + str(infer_ms)
                    + " codegen_ms="
                    + str(codegen_ms)
                    + "\n"
                )
            result_lines.append(result_line)
        with open(result_path, "w", encoding="utf-8") as f:
            for line in result_lines:
                f.write(line + "\n")
        return 0
    except Exception as exc:
        exc_type = type(exc).__name__
        if exc_type is None:
            exc_type = "Exception"
        message = exc_type + ": " + _safe_exception_text(exc)
        if result_path:
            try:
                _write_python_frontend_worker_error(result_path, message)
            except Exception:
                pass
        try:
            sys.stderr.write("pcc frontend worker failed: " + message + "\n")
        except Exception:
            pass
        return 1


def _run_python_frontend_worker_commands(
    commands, max_parallel: Optional[int] = None
) -> None:
    commands = list(commands)
    if not commands:
        return
    if max_parallel is None:
        max_parallel = len(commands)
    try:
        max_parallel = int(max_parallel)
    except (TypeError, ValueError):
        max_parallel = 1
    if max_parallel < 1:
        max_parallel = 1
    if max_parallel > len(commands):
        max_parallel = len(commands)

    shell_lines = ["set -u", "status=0", "batch_pids=''", "batch_count=0"]
    for command in commands:
        shell_lines.append("(" + command + ") &")
        shell_lines.append('batch_pids="$batch_pids $!"')
        shell_lines.append("batch_count=$((batch_count + 1))")
        shell_lines.append('if [ "$batch_count" -ge ' + str(max_parallel) + " ]; then")
        shell_lines.append("  for pid in $batch_pids; do")
        shell_lines.append('    wait "$pid" || status=1')
        shell_lines.append("  done")
        shell_lines.append("  batch_pids=''")
        shell_lines.append("  batch_count=0")
        shell_lines.append("fi")
    shell_lines.append("for pid in $batch_pids; do")
    shell_lines.append('  wait "$pid" || status=1')
    shell_lines.append("done")
    shell_lines.append("exit $status")
    subprocess.run(["/bin/sh", "-c", "\n".join(shell_lines)], check=True)


def _build_python_frontend_shared_exports_parallel(
    tmp: str,
    src_paths,
    module_names,
    chunks,
    worker_prefix,
    *,
    entry_module: str,
    sibling_inits,
    libpython_mode: str,
    ir_scaffold_mode: str,
    verbose: bool,
    max_parallel: int,
    ast_dir: str = "",
    profile: Optional[dict] = None,
) -> str:
    export_dir = os.path.join(tmp, "exports")
    subprocess.run(["mkdir", "-p", export_dir], check=True)
    result_paths = []
    commands = []
    worker_i = 0
    for chunk in chunks:
        manifest_path = os.path.join(tmp, "export_" + str(worker_i) + ".manifest")
        result_path = os.path.join(tmp, "export_" + str(worker_i) + ".tsv")
        _write_python_frontend_worker_manifest(
            manifest_path,
            result_path,
            export_dir,
            "",
            ast_dir,
            src_paths,
            module_names,
            chunk,
            entry_module=entry_module,
            sibling_inits=sibling_inits,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            verbose=verbose,
            job_kind="export",
        )
        result_paths.append(result_path)
        command_parts = []
        for part in worker_prefix:
            command_parts.append(_shell_quote_arg(part))
        command_parts.append(_shell_quote_arg(_PY_FRONTEND_WORKER_ARG))
        command_parts.append(_shell_quote_arg(manifest_path))
        env_prefix = _python_frontend_worker_env_prefix()
        commands.append(env_prefix + " " + _join_strings(command_parts, " "))
        worker_i += 1

    _run_python_frontend_worker_commands(commands, max_parallel=max_parallel)

    native_exports = {}
    reexport_edges = []
    function_object_uses = []
    export_worker_sum_ms = 0
    export_worker_max_ms = 0
    for result_path in result_paths:
        with open(result_path, "r", encoding="utf-8") as f:
            result_text = f.read()
        for raw_line in result_text.splitlines():
            parts = raw_line.split("\t")
            if not parts:
                continue
            if parts[0] == "ERR":
                message = parts[1] if len(parts) > 1 else "worker error"
                raise PyPipelineError(message)
            if parts[0] != "EXPORT" or len(parts) < 3:
                raise PyPipelineError("invalid frontend export worker result")
            shard_exports, _derived, shard_object_uses = _read_native_exports_wire(
                parts[1],
                include_function_object_uses=True,
            )
            for mod_name, exports in shard_exports.items():
                native_exports[mod_name] = exports
            function_object_uses.extend(shard_object_uses)
            for edge in _read_reexport_edges_wire(parts[2]):
                reexport_edges.append(edge)
            if len(parts) >= 4:
                try:
                    worker_ms = int(parts[3])
                except ValueError:
                    worker_ms = 0
                export_worker_sum_ms += worker_ms
                if worker_ms > export_worker_max_ms:
                    export_worker_max_ms = worker_ms

    _merge_closed_world_reexport_edges(module_names, native_exports, reexport_edges)
    _repair_closed_world_default_global_owners(native_exports)
    _merge_l1_mixin_stack_methods(native_exports)
    _merge_l1_codegen_methods(native_exports)
    _apply_closed_world_function_object_uses(
        native_exports,
        function_object_uses,
    )
    derived_class_map = _closed_world_derived_class_map(native_exports)
    exports_path = os.path.join(tmp, "native_exports.json")
    _write_native_exports_wire(exports_path, native_exports, derived_class_map)
    _profile_counter(
        profile, "multi_frontend_export_worker_sum_ms", export_worker_sum_ms
    )
    _profile_counter(
        profile, "multi_frontend_export_worker_max_ms", export_worker_max_ms
    )
    return exports_path


def _compile_python_multi_codegen_parallel(
    src_paths,
    module_names,
    *,
    jobs: int,
    entry_module: str,
    sibling_inits,
    libpython_mode: str,
    ir_scaffold_mode: str,
    verbose: bool,
    profile: Optional[dict] = None,
) -> Optional[tuple[list[tuple[str, str]], bool, bool, int, list[str]]]:
    if jobs < 1:
        return None
    if not _can_spawn_python_frontend_worker():
        return None
    worker_prefix = _python_frontend_worker_command_prefix()
    if not worker_prefix:
        return None
    chunk_count = _python_frontend_codegen_chunk_count(
        len(src_paths),
        jobs,
        worker_prefix,
    )
    chunks = _python_frontend_codegen_chunks(src_paths, chunk_count)
    if not chunks:
        return None
    _profile_counter(profile, "multi_frontend_chunks", len(chunks))
    _profile_counter(profile, "multi_frontend_worker_concurrency", jobs)
    with tempfile.TemporaryDirectory(prefix="pcc_py_frontend_workers_") as tmp:
        ir_dir = os.path.join(tmp, "ir")
        subprocess.run(["mkdir", "-p", ir_dir], check=True)
        ast_dir = ""
        if _python_frontend_ast_wire_enabled():
            ast_dir = os.path.join(tmp, "ast")
            subprocess.run(["mkdir", "-p", ast_dir], check=True)
            _profile_counter(profile, "multi_frontend_ast_wire_enabled", 1)
        else:
            _profile_counter(profile, "multi_frontend_ast_wire_enabled", 0)
        t = _profile_begin(profile)
        exports_path = _build_python_frontend_shared_exports_parallel(
            tmp,
            src_paths,
            module_names,
            chunks,
            worker_prefix,
            entry_module=entry_module,
            sibling_inits=sibling_inits,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            verbose=verbose,
            max_parallel=jobs,
            ast_dir=ast_dir,
            profile=profile,
        )
        _profile_end(profile, "multi_frontend_export_parallel", t)
        result_paths = []
        commands = []
        worker_i = 0
        for chunk in chunks:
            manifest_path = os.path.join(
                tmp,
                "worker_" + str(worker_i) + ".manifest",
            )
            result_path = os.path.join(tmp, "worker_" + str(worker_i) + ".tsv")
            _write_python_frontend_worker_manifest(
                manifest_path,
                result_path,
                ir_dir,
                exports_path,
                ast_dir,
                src_paths,
                module_names,
                chunk,
                entry_module=entry_module,
                sibling_inits=sibling_inits,
                libpython_mode=libpython_mode,
                ir_scaffold_mode=ir_scaffold_mode,
                verbose=verbose,
            )
            result_paths.append(result_path)
            command_parts = []
            for part in worker_prefix:
                command_parts.append(_shell_quote_arg(part))
            command_parts.append(_shell_quote_arg(_PY_FRONTEND_WORKER_ARG))
            command_parts.append(_shell_quote_arg(manifest_path))
            env_prefix = _python_frontend_worker_env_prefix()
            commands.append(env_prefix + " " + _join_strings(command_parts, " "))
            worker_i += 1

        try:
            t = _profile_begin(profile)
            _run_python_frontend_worker_commands(commands, max_parallel=jobs)
            _profile_end(profile, "multi_frontend_codegen_worker_commands", t)
        except subprocess.CalledProcessError as exc:
            raise PyPipelineError("parallel frontend codegen worker failed") from exc

        t = _profile_begin(profile)
        module_ir_by_index = [None for _ in src_paths]
        any_needs_libpython = False
        any_needs_native_extension_exports = False
        libpython_modules = []
        total_ir_bytes_before_passes = 0
        worker_parse_sum_ms = 0
        worker_parse_max_ms = 0
        worker_parse_max_index = -1
        worker_infer_sum_ms = 0
        worker_infer_max_ms = 0
        worker_infer_max_index = -1
        worker_codegen_sum_ms = 0
        worker_codegen_max_ms = 0
        worker_codegen_max_index = -1
        for result_path in result_paths:
            with open(result_path, "r", encoding="utf-8") as f:
                result_text = f.read()
            for raw_line in result_text.splitlines():
                parts = raw_line.split("\t")
                if not parts:
                    continue
                if parts[0] == "ERR":
                    message = parts[1] if len(parts) > 1 else "worker error"
                    raise PyPipelineError(message)
                if parts[0] != "OK" or len(parts) < 7:
                    raise PyPipelineError("invalid frontend worker result")
                index = int(parts[1])
                mod_name = parts[2]
                needs_libpython = parts[3] == "1"
                needs_native_exports = parts[4] == "1"
                try:
                    total_ir_bytes_before_passes += int(parts[5])
                except ValueError:
                    pass
                ir_path = parts[6]
                ir_text = _read_python_frontend_worker_ir(ir_path, mod_name)
                module_ir_by_index[index] = (mod_name, ir_text)
                if len(parts) >= 10:
                    try:
                        parse_ms = int(parts[7])
                        infer_ms = int(parts[8])
                        codegen_ms = int(parts[9])
                    except ValueError:
                        parse_ms = 0
                        infer_ms = 0
                        codegen_ms = 0
                    worker_parse_sum_ms += parse_ms
                    worker_infer_sum_ms += infer_ms
                    worker_codegen_sum_ms += codegen_ms
                    if parse_ms > worker_parse_max_ms:
                        worker_parse_max_ms = parse_ms
                        worker_parse_max_index = index
                    if infer_ms > worker_infer_max_ms:
                        worker_infer_max_ms = infer_ms
                        worker_infer_max_index = index
                    if codegen_ms > worker_codegen_max_ms:
                        worker_codegen_max_ms = codegen_ms
                        worker_codegen_max_index = index
                if needs_libpython:
                    any_needs_libpython = True
                    if mod_name not in libpython_modules:
                        libpython_modules.append(mod_name)
                if needs_native_exports:
                    any_needs_native_extension_exports = True
        module_ir_texts = []
        i = 0
        while i < len(module_ir_by_index):
            item = module_ir_by_index[i]
            if item is None:
                raise PyPipelineError(
                    "parallel frontend worker missed module " + str(i)
                )
            module_ir_texts.append(item)
            i += 1
        _profile_counter(
            profile, "multi_frontend_worker_parse_sum_ms", worker_parse_sum_ms
        )
        _profile_counter(
            profile, "multi_frontend_worker_parse_max_ms", worker_parse_max_ms
        )
        _profile_counter(
            profile, "multi_frontend_worker_parse_max_index", worker_parse_max_index
        )
        _profile_counter(
            profile, "multi_frontend_worker_infer_sum_ms", worker_infer_sum_ms
        )
        _profile_counter(
            profile, "multi_frontend_worker_infer_max_ms", worker_infer_max_ms
        )
        _profile_counter(
            profile, "multi_frontend_worker_infer_max_index", worker_infer_max_index
        )
        _profile_counter(
            profile, "multi_frontend_worker_codegen_sum_ms", worker_codegen_sum_ms
        )
        _profile_counter(
            profile, "multi_frontend_worker_codegen_max_ms", worker_codegen_max_ms
        )
        _profile_counter(
            profile, "multi_frontend_worker_codegen_max_index", worker_codegen_max_index
        )
        _profile_end(profile, "multi_frontend_codegen_result_read", t)
        return (
            module_ir_texts,
            any_needs_libpython,
            any_needs_native_extension_exports,
            total_ir_bytes_before_passes,
            libpython_modules,
        )


def compile_python_multi(
    src_paths,
    out_path: str,
    *,
    verbose: bool = False,
    emit_llvm_only: bool = False,
    entry_module: Optional[str] = None,
    module_names=None,
    libpython_mode: Optional[str] = None,
    ir_scaffold_mode: Optional[str] = None,
    backend: Optional[str] = None,
    recursive_stdlib: bool = False,
    runtime_archive: Optional[str] = None,
    profile: Optional[dict] = None,
) -> None:
    if verbose:
        print(
            f"ENTRY: compile_python_multi(src_paths={src_paths}, out_path={out_path}, module_names={module_names})"
        )
    """Compile multiple ``.py`` files into a single native executable.

    This is the infrastructure step for #138.5 three-stage
    bootstrap. Each source file is parsed, type-inferred, and
    lowered to LLVM IR independently; the resulting ``.ll`` files
    are handed to clang together so that cross-module symbol
    references — declared as ``external`` by each module's codegen
    — are resolved at link time.

    Parameters
    ----------
    src_paths:
        Ordered list of ``.py`` files. The first entry provides
        the native executable's ``main`` entry point (the one
        that pcc synthesises to call top-level module code).
    module_names:
        Optional parallel list of dotted module names. Defaults
        to the filename stem. The names influence the
        ``user_<module>_<fn>`` symbol mangling so two files can
        define unrelated ``main`` functions without colliding.
    entry_module:
        Dotted module name whose top-level ``main()`` is the
        executable entry. Defaults to the first file's module.
    runtime_archive:
        Optional explicit native runtime archive. This is propagated from
        single-file compilation when package closure selects this path.

    The multi-compile API **does not** yet rewrite cross-module
    imports to extern references — step 2 of the spike plan
    (``docs/plans/multi-file-compile-spike.md``). Until that
    lands, imports between passed source files still route through
    ``py_cpy_import`` and the link pulls libpython. Single-file
    callers should keep using :func:`compile_python`.
    """
    if not src_paths:
        raise PyPipelineError("compile_python_multi requires at least one source file")
    total_start = _profile_begin(profile)
    _profile_counter(profile, "multi_input_files", len(src_paths))
    libpython_mode = _resolve_libpython_mode(libpython_mode)
    ir_scaffold_mode = _resolve_ir_scaffold_mode(ir_scaffold_mode)
    src_paths = list(src_paths)
    if module_names is None:
        module_names = []
        for p in src_paths:
            module_names.append(_module_name_from_src(p))
    if len(module_names) != len(src_paths):
        raise PyPipelineError("module_names length must match src_paths length")

    t = _profile_begin(profile)
    src_paths, module_names = _collect_multi_source_relative_closure(
        src_paths,
        list(module_names),
        recursive_stdlib=recursive_stdlib,
    )
    _profile_end(profile, "collect_multi_source_relative_closure", t)
    t = _profile_begin(profile)
    src_paths, module_names = _filter_ir_scaffold_closure(
        src_paths,
        list(module_names),
        ir_scaffold_mode=ir_scaffold_mode,
    )
    _profile_end(profile, "filter_ir_scaffold_closure", t)
    t = _profile_begin(profile)
    _validate_package_site_no_libpython_abi(
        src_paths,
        libpython_mode=libpython_mode,
    )
    _profile_end(profile, "validate_package_site_abi", t)
    _profile_counter(profile, "multi_files", len(src_paths))

    t = _profile_begin(profile)
    try:
        from .type_infer import infer_module as _infer_module
        from .codegen.layer1 import L1CodeGen as _L1CodeGen
    except ImportError as e:
        raise PyPipelineError(f"Python frontend module not available: {e}") from e
    _profile_end(profile, "frontend_imports", t)

    any_needs_libpython = False
    any_needs_native_extension_exports = False
    libpython_modules = []
    module_ir_texts = []
    native_backend = None
    if not emit_llvm_only:
        native_backend = _resolve_native_backend(backend)
    emit_only_self_backend = (
        emit_llvm_only and _normalize_native_backend_name(backend) == "self"
    )
    reuse_export_ast = native_backend == "self" or emit_only_self_backend

    # Decide which module is the entry (emits ``@main``). Default:
    # first source file in the list.
    if entry_module is None:
        entry_module = module_names[0]
    if entry_module not in module_names:
        raise PyPipelineError(
            f"entry_module {entry_module!r} not among module_names " f"{module_names!r}"
        )
    # Sibling modules whose top-level code the entry must run before
    # its own body. Use dependency order instead of caller order so a
    # child module never initializes before its imported base module.
    t = _profile_begin(profile)
    sibling_inits = _order_module_inits(src_paths, module_names, entry_module)
    _profile_end(profile, "order_module_inits", t)
    _profile_counter(profile, "before_build_closed_world_context", len(src_paths))

    total_ir_bytes_before_passes = 0
    parallel_codegen_result = None
    frontend_jobs = _python_frontend_jobs(len(src_paths))
    _profile_counter(profile, "multi_frontend_jobs", frontend_jobs)
    if native_backend == "self" or emit_only_self_backend:
        t = _profile_begin(profile)
        parallel_codegen_result = _compile_python_multi_codegen_parallel(
            src_paths,
            module_names,
            jobs=frontend_jobs,
            entry_module=entry_module,
            sibling_inits=sibling_inits,
            libpython_mode=libpython_mode,
            ir_scaffold_mode=ir_scaffold_mode,
            verbose=verbose,
            profile=profile,
        )
        _profile_end(profile, "multi_frontend_codegen_parallel", t)

    if parallel_codegen_result is not None:
        (
            module_ir_texts,
            any_needs_libpython,
            any_needs_native_extension_exports,
            total_ir_bytes_before_passes,
            libpython_modules,
        ) = parallel_codegen_result
    else:
        # Pre-pass: build the closed-world context shared by real multi-file
        # compiles and contextual per-module probes.
        t = _profile_begin(profile)
        parsed_modules, native_exports, derived_class_map = build_closed_world_context(
            src_paths, module_names, profile
        )
        _profile_end(profile, "build_closed_world_context", t)
        if not reuse_export_ast:
            parsed_modules = [None for _ in parsed_modules]
            from ..parse.py_lift import parse_and_lift as _parse_and_lift

        # Pre-pass 2 + codegen: reuse the AST produced by the export pass on
        # the self-backend bootstrap path. Other native paths keep the older
        # reparse boundary because compiled pcc_multi still relies on it to avoid
        # lifetime issues when frontend objects cross pcc-native containers.
        for src, mod_name, ast_mod in zip(src_paths, module_names, parsed_modules):
            if ast_mod is None:
                t = _profile_begin(profile)
                with open(src, "r", encoding="utf-8") as f:
                    source = f.read()
                ast_mod = _parse_and_lift(source, src, mod_name)
                _profile_end(profile, "multi_parse_and_lift", t, mod_name)
            if _module_imports_pcc_native_extension(
                ast_mod,
                native_modules=module_names,
                ir_scaffold_mode=ir_scaffold_mode,
            ):
                any_needs_native_extension_exports = True
            external_for_this = {}
            for k, v in native_exports.items():
                if k != mod_name:
                    external_for_this[k] = v
            if verbose:
                _log(verbose, "type_infer[" + mod_name + "]")
            t = _profile_begin(profile)
            try:
                typed_mod = _infer_module(
                    ast_mod,
                    external_exports=external_for_this,
                    derived_class_map=derived_class_map,
                    contextual_host_params=_contextual_host_params_for_module(
                        ast_mod,
                        mod_name,
                    ),
                )
            except Exception as exc:
                raise PyPipelineError(
                    "type_infer[" + mod_name + "]: " + str(exc)
                ) from exc
            _profile_end(profile, "multi_type_infer", t, mod_name)
            if verbose:
                _log(verbose, "codegen " + mod_name)
            try:
                codegen = _L1CodeGen(
                    typed_mod,
                    (libpython_mode == "on"),
                    ir_scaffold_mode,
                )
                codegen._strict_no_libpython = libpython_mode == "off"
                codegen._prefer_native_callable_values = libpython_mode == "off"
            except Exception as exc:
                raise PyPipelineError(
                    "codegen_init["
                    + mod_name
                    + "]: "
                    + type(exc).__name__
                    + ": "
                    + str(exc)
                ) from exc
            prep_step = "entry"
            try:
                is_entry = mod_name == entry_module
                prep_step = "skip_program_main"
                codegen._skip_program_main = not is_entry
                prep_step = "sibling_module_inits"
                codegen._sibling_module_inits = sibling_inits
                # Preserve the baseline native export registry and add cross-module
                # exports from other files, excluding this module to avoid
                # sibling self-reference during multi-file inference/linking.
                if _module_uses_default_native_exports(mod_name):
                    prep_step = "read_default_exports"
                    default_exports = codegen._native_module_exports
                    prep_step = "copy_default_exports"
                    codegen_exports = _copy_native_module_exports(default_exports)
                else:
                    codegen_exports = {}
                prep_step = "merge_closed_world_exports"
                for k, v in native_exports.items():
                    if k != mod_name:
                        codegen_exports[k] = v
                prep_step = "store_exports"
                codegen._native_module_exports = codegen_exports
                codegen._native_function_object_exports = (
                    _closed_world_function_object_exports(native_exports, mod_name)
                )
            except Exception as exc:
                raise PyPipelineError(
                    "codegen_prepare["
                    + mod_name
                    + "]: "
                    + prep_step
                    + ": "
                    + type(exc).__name__
                    + ": "
                    + str(exc)
                ) from exc
            if verbose:
                _log(verbose, "codegen[" + mod_name + "]")
            t = _profile_begin(profile)
            try:
                ir_text = codegen.generate(typed_mod)
                ir_text = str(ir_text)
            except Exception as exc:
                raise PyPipelineError(
                    "codegen[" + mod_name + "]: " + type(exc).__name__ + ": " + str(exc)
                ) from exc
            _profile_end(profile, "multi_codegen_layer1", t, mod_name)
            if libpython_mode == "off" and _ir_needs_libpython(ir_text):
                any_needs_libpython = _finalize_libpython_mode(
                    detected=True,
                    mode=libpython_mode,
                    context="multi-file compile",
                    reasons=[
                        "module "
                        + mod_name
                        + " generated IR still calls py_cpy_* helpers"
                    ],
                )
                if mod_name not in libpython_modules:
                    libpython_modules.append(mod_name)
            total_ir_bytes_before_passes += len(ir_text)
            module_ir_texts.append((mod_name, ir_text))
    _profile_counter(
        profile,
        "multi_ir_bytes_before_passes",
        total_ir_bytes_before_passes,
    )

    t = _profile_begin(profile)
    module_ir_texts = _apply_python_ir_pass_pipeline_many(
        module_ir_texts,
        verbose=verbose,
        default_raw=_default_python_ir_pass_raw_for_request(
            native_backend,
            emit_llvm_only=emit_llvm_only,
            backend=backend,
        ),
        strict_no_libpython=(libpython_mode == "off"),
    )
    _profile_end(profile, "python_ir_pass_pipeline_many", t)
    total_ir_bytes = 0
    for _mod_name, ir_text in module_ir_texts:
        total_ir_bytes += len(str(ir_text))
    _profile_counter(profile, "multi_ir_modules", len(module_ir_texts))
    _profile_counter(profile, "multi_ir_bytes", total_ir_bytes)
    t = _profile_begin(profile)
    for mod_name, ir_text in module_ir_texts:
        if _ir_needs_libpython(ir_text):
            any_needs_libpython = True
            if mod_name not in libpython_modules:
                libpython_modules.append(mod_name)
    _profile_end(profile, "libpython_scan", t)

    if emit_llvm_only:
        # Concatenate all IR texts with a separator comment so the
        # output is still valid LLVM IR (each module's header lines
        # are duplicated but ``llvm-as`` tolerates redundant
        # target-triple / datalayout directives).
        combined = str(
            "\n\n".join(
                f"; ---- module: {name} ----\n{text}" for name, text in module_ir_texts
            )
        )
        out_path = str(out_path)
        if verbose:
            _log(
                verbose,
                "writing LLVM IR to "
                + out_path
                + " ("
                + str(len(combined))
                + " bytes)",
            )
        t = _profile_begin(profile)
        _write_utf8_text_file(out_path, combined)
        _profile_end(profile, "emit_ll_many_combined", t)
        _profile_end(profile, "compile_python_multi_total", total_start)
        return

    t = _profile_begin(profile)
    any_needs_libpython = _finalize_libpython_mode(
        detected=any_needs_libpython,
        mode=libpython_mode,
        context="multi-file compile",
        reasons=(
            ["modules: " + ", ".join(libpython_modules)] if libpython_modules else []
        ),
    )
    _profile_end(profile, "finalize_libpython_mode", t)
    if verbose:
        _log(verbose, "native backend: " + str(native_backend))

    t = _profile_begin(profile)
    if runtime_archive is not None:
        runtime = os.path.abspath(str(runtime_archive))
        if not os.path.isfile(runtime):
            raise PyPipelineError("explicit runtime archive not found: " + runtime)
    else:
        runtime = _ensure_runtime(
            verbose,
            needs_libpython=any_needs_libpython,
        )
    _profile_end(profile, "ensure_runtime", t)
    if native_backend == "self" and _self_backend_skip_ll_temp():
        total_bytes = 0
        for _mod_name, text in module_ir_texts:
            total_bytes = total_bytes + len(str(text))
        if verbose:
            for mod_name, text in module_ir_texts:
                _log(
                    verbose,
                    "passing LLVM IR text to self backend for "
                    + mod_name
                    + " ("
                    + str(len(str(text)))
                    + " bytes)",
                )
        _log(
            verbose,
            "self backend: linking "
            + str(len(module_ir_texts))
            + " LLVM IR text modules without pipeline .ll temp ("
            + str(total_bytes)
            + " bytes)",
        )
        self_backend_texts = []
        for _mod_name, text in module_ir_texts:
            self_backend_texts.append(text)
        t = _profile_begin(profile)
        _link_with_self_backend_ir_texts(
            self_backend_texts,
            out_path,
            runtime,
            verbose,
            needs_libpython=any_needs_libpython,
            needs_native_extension_exports=any_needs_native_extension_exports,
            profile=profile,
        )
        _profile_end(profile, "link_self_backend_ir_texts", t)
        if verbose:
            _log(verbose, "wrote executable: " + out_path)
        _profile_end(profile, "compile_python_multi_total", total_start)
        return

    with tempfile.TemporaryDirectory(prefix="pcc_py_multi_") as tmp:
        ll_paths = []
        t = _profile_begin(profile)
        for mod_name, text in module_ir_texts:
            safe = mod_name.replace(".", "_").replace("-", "_")
            p = str(os.path.join(tmp, safe + ".ll"))
            text = str(text)
            if native_backend != "self":
                text = _clang_link_compatible_python_ir(text)
            if verbose:
                _log(
                    verbose,
                    "writing LLVM IR to " + p + " (" + str(len(text)) + " bytes)",
                )
            _write_utf8_text_file(p, text)
            ll_paths.append(p)
        _profile_end(profile, "emit_ll_many", t)
        t = _profile_begin(profile)
        _link_native(
            ll_paths,
            out_path,
            runtime,
            verbose,
            backend=native_backend,
            needs_libpython=any_needs_libpython,
            needs_native_extension_exports=any_needs_native_extension_exports,
            profile=profile,
        )
        _profile_end(profile, "link_native", t)
    if verbose:
        _log(verbose, "wrote executable: " + out_path)
    _profile_end(profile, "compile_python_multi_total", total_start)
