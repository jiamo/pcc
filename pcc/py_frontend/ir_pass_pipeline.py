"""Host-only LLVM IR pass runner for the Python frontend.

The main ``pipeline.py`` module is part of the bootstrap closure, so it
must not import llvmlite or the translated pass classes directly just to
support an optional host optimization step. This module is loaded only by
the host Python subprocess when ``PCC_PYTHON_IR_PASSES`` is enabled.
"""
from __future__ import annotations

import os
import sys
import time
import json
import hashlib


class PythonIRPassError(RuntimeError):
    """Raised when a requested Python IR pass cannot run."""


_LARGE_MODULE_BYTES_ENV = "PCC_PYTHON_IR_PASS_LARGE_MODULE_BYTES"
_MEDIUM_MODULE_BYTES_ENV = "PCC_PYTHON_IR_PASS_MEDIUM_MODULE_BYTES"
_HUGE_MODULE_BYTES_ENV = "PCC_PYTHON_IR_PASS_HUGE_MODULE_BYTES"
_TELEMETRY_ENV = "PCC_PYTHON_IR_PASS_TELEMETRY"
_TELEMETRY_PATH_ENV = "PCC_PYTHON_IR_PASS_TELEMETRY_PATH"
_TRANSPORT_ENV = "PCC_PYTHON_IR_PASS_TRANSPORT"
_STRICT_NO_LIBPYTHON_ENV = "PCC_PYTHON_IR_PASS_STRICT_NO_LIBPYTHON"
_CACHE_ENV = "PCC_PYTHON_IR_PASS_CACHE"
_CACHE_DIR_ENV = "PCC_PYTHON_IR_PASS_CACHE_DIR"
_CACHE_VERSION = "pcc.python-ir-pass-cache.v1"
_TRANSPORT_TEXT = "text"
_TRANSPORT_MEMORY = "memory"
_MEMORY_ALL_PIPELINE = "default<O2>"
_MEMORY_NOOP_PASSES = frozenset({"require", "invalidate"})
_MEMORY_PASS_PIPELINE_OVERRIDES = {
    # Some bootstrap functions currently trip LLVM's optional instcombine
    # fixpoint verifier. The transform is still valid; keep normal module
    # verification after the pass pipeline.
    "instcombine": "instcombine<no-verify-fixpoint>",
    # LLVM's LICM pass manager entry aborts if it is run outside the
    # MemorySSA loop adaptor. Keep the public pass name visible while
    # emitting the pass-manager spelling LLVM expects.
    "licm": "function(loop-mssa(licm))",
}
_DEFAULT_LARGE_MODULE_BYTES = 250_000
_DEFAULT_MEDIUM_MODULE_BYTES = 100_000
_DEFAULT_HUGE_MODULE_BYTES = 5_000_000
_DEFAULT_FAST_PRESET = (
    "mem2reg",
    "sroa",
)
_MEMORY_FAST_PIPELINE = "function(mem2reg,sroa)"
_LARGE_MODULE_ALL_PRESET = (
    "mem2reg",
    "sroa",
    "sccp",
    "dce",
)
_HUGE_MODULE_COSTLY_PASSES = frozenset(_DEFAULT_FAST_PRESET + _LARGE_MODULE_ALL_PRESET)
_LARGE_MODULE_TEXTUAL_PASSES = frozenset(
    {
        # These translated subsets repeatedly split/reparse function text.
        # They are useful on normal modules, but pcc's own layer1 module is
        # multi-megabyte IR; running every textual cleanup there dominates
        # bootstrap time while adding little self-host signal.
        "simplifycfg",
        "instcombine",
        "reassociate",
        "loop-instsimplify",
        "loop-simplifycfg",
        "globalopt",
        "indvars",
        "loop-unroll-full",
        "globaldce",
        "correlated-propagation",
        "constraint-elimination",
        "mldst-motion",
        "gvn",
        "dse",
        "newgvn",
        "loop-simplify",
    }
)
_MEDIUM_MODULE_COSTLY_PASSES = frozenset(
    {
        # Telemetry from py_runtime/build_py/*.ll showed these translated
        # pass subsets dominating all-preset time on 100-250KB modules while
        # often leaving the module text unchanged. Keep them available when
        # explicitly requested, but do not run them by default on medium IR.
        "mldst-motion",
        "loop-simplifycfg",
        "loop-instsimplify",
        "indvars",
        "constraint-elimination",
        "correlated-propagation",
        "loop-sink",
    }
)
_PYTHON_FRONTEND_UNSAFE_PASSES = frozenset()
_PYTHON_FRONTEND_PASS_ENV_DEFAULTS = {
    # LICM's translated subset is correct for focused tests, but on
    # self-host-sized frontend modules it can spend tens of seconds
    # walking many loops for little practical gain. Keep all-pass
    # bootstrap bounded while still running LICM on normal modules.
    "PCC_LICM_LOOP_BUDGET": "8",
}


def _truthy_env(name: str) -> bool:
    value = str(os.environ.get(name, "") or "").strip().lower()
    return value in ("1", "true", "yes", "on", "stderr", "json")


def resolve_python_ir_pass_transport(raw: str | None = None) -> str:
    value = os.environ.get(_TRANSPORT_ENV, "") if raw is None else raw
    normalized = str(value or "").strip().lower()
    if normalized in ("", _TRANSPORT_TEXT):
        return _TRANSPORT_TEXT
    if normalized == _TRANSPORT_MEMORY:
        return _TRANSPORT_MEMORY
    raise PythonIRPassError(
        f"{_TRANSPORT_ENV} must be 'text' or 'memory', got {value!r}"
    )


def _transport_env_explicitly_set() -> bool:
    return bool(str(os.environ.get(_TRANSPORT_ENV, "") or "").strip())


def _telemetry_path() -> str:
    return str(os.environ.get(_TELEMETRY_PATH_ENV, "") or "").strip()


def _telemetry_enabled() -> bool:
    return _truthy_env(_TELEMETRY_ENV) or bool(_telemetry_path())


def _strict_no_libpython_enabled() -> bool:
    return _truthy_env(_STRICT_NO_LIBPYTHON_ENV)


def _has_py_cpy_call(ir_text: str) -> bool:
    for line in str(ir_text).splitlines():
        if "@py_cpy_" not in line:
            continue
        stripped = line.lstrip()
        if stripped.startswith("call ") or stripped.startswith("tail call "):
            return True
        if " = call " in line or " = tail call " in line:
            return True
    return False


def _emit_telemetry(record: dict) -> None:
    if not _telemetry_enabled():
        return
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path = _telemetry_path()
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
        return
    print(line, file=sys.stderr)


def _memory_pass_cache_enabled() -> bool:
    value = str(os.environ.get(_CACHE_ENV, "") or "").strip().lower()
    return value not in ("off", "false", "no", "0", "disable", "disabled")


def _default_memory_pass_cache_dir() -> str:
    override = str(os.environ.get(_CACHE_DIR_ENV, "") or "").strip()
    if override:
        return os.path.expanduser(override)
    xdg_cache_home = str(os.environ.get("XDG_CACHE_HOME", "") or "").strip()
    if xdg_cache_home:
        base = xdg_cache_home
    else:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "pcc", "python-ir-pass-cache")


def _memory_pass_cache_path(
    ir_text: str,
    pipeline_text: str,
    llvm_capi_binding,
) -> str | None:
    if not pipeline_text or not _memory_pass_cache_enabled():
        return None
    h = hashlib.sha256()
    h.update(_CACHE_VERSION.encode("utf-8"))
    h.update(b"\0")
    h.update(pipeline_text.encode("utf-8"))
    h.update(b"\0")
    h.update(_memory_pass_cache_identity(llvm_capi_binding).encode("utf-8"))
    h.update(b"\0")
    h.update(ir_text.encode("utf-8"))
    digest = h.hexdigest()
    cache_dir = _default_memory_pass_cache_dir()
    return os.path.join(cache_dir, digest[:2], digest + ".ll")


def _memory_pass_cache_identity(llvm_capi_binding) -> str:
    parts = ["triple=" + str(llvm_capi_binding.get_default_triple())]
    try:
        import llvmlite.binding as llvm

        parts.append("llvm=" + ".".join(str(n) for n in llvm.llvm_version_info))
    except Exception:
        parts.append("llvm=unknown")
    return ";".join(parts)


def _read_memory_pass_cache(cache_path: str | None) -> str | None:
    if not cache_path:
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _write_memory_pass_cache(cache_path: str | None, ir_text: str) -> None:
    if not cache_path:
        return
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp_path = cache_path + "." + str(os.getpid()) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(ir_text)
        os.replace(tmp_path, cache_path)
    except OSError:
        try:
            os.unlink(tmp_path)  # type: ignore[name-defined]
        except Exception:
            pass


def _large_module_limit() -> int:
    return _int_env_limit(_LARGE_MODULE_BYTES_ENV, _DEFAULT_LARGE_MODULE_BYTES)


def _medium_module_limit() -> int:
    return _int_env_limit(_MEDIUM_MODULE_BYTES_ENV, _DEFAULT_MEDIUM_MODULE_BYTES)


def _huge_module_limit() -> int:
    return _int_env_limit(_HUGE_MODULE_BYTES_ENV, _DEFAULT_HUGE_MODULE_BYTES)


def _int_env_limit(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name, "") or default))
    except ValueError:
        value = default
    return max(0, value)


def _skip_for_large_module(pass_name: str, ir_size: int) -> bool:
    limit = _large_module_limit()
    return limit > 0 and ir_size > limit and pass_name in _LARGE_MODULE_TEXTUAL_PASSES


def _skip_for_huge_module(pass_name: str, ir_size: int) -> bool:
    limit = _huge_module_limit()
    return limit > 0 and ir_size > limit and pass_name in _HUGE_MODULE_COSTLY_PASSES


def _skip_for_medium_module(pass_name: str, ir_size: int) -> bool:
    limit = _medium_module_limit()
    large_limit = _large_module_limit()
    return (
        limit > 0
        and ir_size > limit
        and (large_limit <= 0 or ir_size <= large_limit)
        and pass_name in _MEDIUM_MODULE_COSTLY_PASSES
    )


def _skip_for_python_frontend(pass_name: str) -> bool:
    return pass_name in _PYTHON_FRONTEND_UNSAFE_PASSES


def _skip_status_for_pass(
    pass_name: str,
    ir_size: int,
    *,
    transport: str,
) -> tuple[str, dict] | None:
    if _skip_for_python_frontend(pass_name):
        return "skip_unsafe", {}
    if _skip_for_huge_module(pass_name, ir_size):
        return "skip_huge", {
            "huge_module_limit": _huge_module_limit(),
        }
    if transport == _TRANSPORT_MEMORY:
        return None
    if _skip_for_medium_module(pass_name, ir_size):
        return "skip_medium_cost", {
            "medium_module_limit": _medium_module_limit(),
        }
    if _skip_for_large_module(pass_name, ir_size):
        return "skip_large", {
            "large_module_limit": _large_module_limit(),
        }
    return None


def _memory_pipeline_text(pass_names: list[str]) -> str:
    normalized_names: list[str] = []
    for pass_name in pass_names:
        lowered = str(pass_name).strip().lower()
        if not lowered or lowered in _MEMORY_NOOP_PASSES:
            continue
        normalized_names.append(lowered)
    if tuple(normalized_names) == _DEFAULT_FAST_PRESET:
        return _MEMORY_FAST_PIPELINE
    pieces: list[str] = []
    for pass_name in pass_names:
        lowered = str(pass_name).strip().lower()
        if lowered in _MEMORY_NOOP_PASSES:
            continue
        pieces.append(_canonical_memory_pass_name(pass_name))
    return ",".join(pieces)


def _canonical_memory_pass_name(pass_name: str) -> str:
    lowered = str(pass_name).strip().lower()
    if lowered.startswith("default<o") and lowered.endswith(">"):
        level = lowered[len("default<o") : -1]
        if level in {"0", "1", "2", "3", "s", "z"}:
            suffix = level if level in {"s", "z"} else level.upper()
            return "default<O" + suffix + ">"
    return _MEMORY_PASS_PIPELINE_OVERRIDES.get(lowered, pass_name)


def _load_python_ir_pass(pass_name: str):
    from pcc.passes.llvm_python_registry import llvm_python_translation

    entry = llvm_python_translation(pass_name)
    if entry is None or not entry.ir_pass_class:
        raise PythonIRPassError(
            "Python IR pass " f"{pass_name!r} has no registered IR-level implementation"
        )
    module_name, sep, class_name = entry.ir_pass_class.partition(":")
    if sep != ":" or not module_name or not class_name:
        raise PythonIRPassError(
            "invalid Python IR pass class path for "
            f"{pass_name!r}: {entry.ir_pass_class!r}"
        )
    try:
        module = __import__(module_name, fromlist=[class_name])
        pass_cls = getattr(module, class_name)
        return pass_cls()
    except Exception as e:
        raise PythonIRPassError(
            "failed to construct Python IR pass "
            f"{pass_name!r} from {entry.ir_pass_class!r}: {e}"
        ) from e


def _expand_pass_names(
    pass_names,
    ir_size: int = 0,
    *,
    transport: str = _TRANSPORT_TEXT,
) -> tuple[str, ...]:
    out: list[str] = []
    for raw in pass_names:
        name = str(raw).strip()
        if not name:
            continue
        if name in ("default", "fast"):
            for registered in _DEFAULT_FAST_PRESET:
                if registered not in out:
                    out.append(registered)
            continue
        if name in ("all", "full"):
            if transport == _TRANSPORT_MEMORY:
                if _MEMORY_ALL_PIPELINE not in out:
                    out.append(_MEMORY_ALL_PIPELINE)
                continue
            if (
                transport == _TRANSPORT_TEXT
                and _large_module_limit() > 0
                and ir_size > _large_module_limit()
            ):
                for registered in _LARGE_MODULE_ALL_PRESET:
                    if registered not in out:
                        out.append(registered)
                continue
            from pcc.passes.llvm_python_registry import llvm_python_translations

            for registered in llvm_python_translations().keys():
                if registered not in out:
                    out.append(registered)
            continue
        if name not in out:
            out.append(name)
    return tuple(out)


def run_python_ir_pass_pipeline(
    ir_text: str,
    *,
    pass_names,
    module_name: str,
) -> str:
    for key, value in _PYTHON_FRONTEND_PASS_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)

    current = str(ir_text)
    ir_size = len(current)
    transport = resolve_python_ir_pass_transport()
    expanded_passes = tuple(
        _expand_pass_names(pass_names, ir_size, transport=transport)
    )
    if (
        not _transport_env_explicitly_set()
        and expanded_passes == _DEFAULT_FAST_PRESET
    ):
        transport = _TRANSPORT_MEMORY
        expanded_passes = tuple(
            _expand_pass_names(pass_names, ir_size, transport=transport)
        )
    pipeline_start = time.perf_counter()
    _emit_telemetry(
        {
            "event": "start",
            "ir_bytes": ir_size,
            "module": module_name,
            "pass_count": len(expanded_passes),
            "transport": transport,
        }
    )
    if (
        transport == _TRANSPORT_MEMORY
        and _strict_no_libpython_enabled()
        and _has_py_cpy_call(current)
    ):
        for pass_name in expanded_passes:
            _emit_telemetry(
                {
                    "event": "pass",
                    "ir_bytes_after": len(current),
                    "ir_bytes_before": len(current),
                    "module": module_name,
                    "pass": str(pass_name),
                    "status": "skip_cpy_ref",
                    "transport": transport,
                }
            )
        _emit_telemetry(
            {
                "elapsed_ms": round(
                    (time.perf_counter() - pipeline_start) * 1000,
                    3,
                ),
                "event": "end",
                "ir_bytes": len(current),
                "module": module_name,
                "transport": transport,
            }
        )
        return current
    active_passes: list[str] = []
    for pass_name in expanded_passes:
        pass_name = str(pass_name)
        skip = _skip_status_for_pass(
            pass_name,
            ir_size,
            transport=transport,
        )
        if skip is not None:
            status, extra = skip
            _emit_telemetry(
                {
                    "event": "pass",
                    "ir_bytes_before": len(current),
                    "module": module_name,
                    "pass": pass_name,
                    "status": status,
                    **extra,
                }
            )
            continue
        active_passes.append(pass_name)
    if not active_passes:
        _emit_telemetry(
            {
                "elapsed_ms": round(
                    (time.perf_counter() - pipeline_start) * 1000,
                    3,
                ),
                "event": "end",
                "ir_bytes": len(current),
                "module": module_name,
            }
        )
        return current
    if transport == _TRANSPORT_MEMORY:
        return _run_python_ir_memory_pipeline(
            current,
            active_passes,
            module_name=module_name,
            pipeline_start=pipeline_start,
        )
    return _run_python_ir_text_pipeline(
        current,
        active_passes,
        module_name=module_name,
        pipeline_start=pipeline_start,
    )


def _run_python_ir_memory_pipeline(
    current: str,
    active_passes: list[str],
    *,
    module_name: str,
    pipeline_start: float,
) -> str:
    before_bytes = len(current)
    pipeline_text = _memory_pipeline_text(active_passes)
    pass_start = time.perf_counter()
    try:
        from pcc.llvm_capi import binding as llvm_capi_binding

        cache_path = _memory_pass_cache_path(
            current,
            pipeline_text,
            llvm_capi_binding,
        )
        cached = _read_memory_pass_cache(cache_path)
        if cached is not None:
            elapsed_ms = round((time.perf_counter() - pass_start) * 1000, 3)
            for pass_name in active_passes:
                status = (
                    "noop"
                    if str(pass_name).strip().lower() in _MEMORY_NOOP_PASSES
                    else "cache_hit"
                )
                _emit_telemetry(
                    {
                        "elapsed_ms": elapsed_ms if status == "cache_hit" else 0.0,
                        "event": "pass",
                        "ir_bytes_after": len(cached),
                        "ir_bytes_before": before_bytes,
                        "module": module_name,
                        "pass": pass_name,
                        "status": status,
                        "transport": _TRANSPORT_MEMORY,
                    }
                )
            _emit_telemetry(
                {
                    "elapsed_ms": round(
                        (time.perf_counter() - pipeline_start) * 1000,
                        3,
                    ),
                    "event": "end",
                    "ir_bytes": len(cached),
                    "module": module_name,
                    "transport": _TRANSPORT_MEMORY,
                }
            )
            return cached
        if pipeline_text:
            current = llvm_capi_binding.run_passes_on_ir(current, pipeline_text)
        else:
            module = llvm_capi_binding.parse_assembly(current)
            module.verify()
            current = str(module)
        _write_memory_pass_cache(cache_path, current)
    except Exception as e:
        if "parse_assembly:" in str(e):
            _emit_telemetry(
                {
                    "elapsed_ms": round(
                        (time.perf_counter() - pass_start) * 1000,
                        3,
                    ),
                    "event": "pass-batch",
                    "ir_bytes_before": before_bytes,
                    "module": module_name,
                    "pass": pipeline_text,
                    "status": "skip_parse_error",
                    "transport": _TRANSPORT_MEMORY,
                }
            )
            _emit_telemetry(
                {
                    "elapsed_ms": round(
                        (time.perf_counter() - pipeline_start) * 1000,
                        3,
                    ),
                    "event": "end",
                    "ir_bytes": len(current),
                    "module": module_name,
                    "transport": _TRANSPORT_MEMORY,
                }
            )
            return current
        _emit_telemetry(
            {
                "elapsed_ms": round((time.perf_counter() - pass_start) * 1000, 3),
                "event": "pass-batch",
                "ir_bytes_before": before_bytes,
                "module": module_name,
                "pass": pipeline_text,
                "status": "error",
                "transport": _TRANSPORT_MEMORY,
            }
        )
        raise PythonIRPassError(
            "Python IR memory pass pipeline failed for module "
            f"{module_name!r}: {e}"
        ) from e
    elapsed_ms = round((time.perf_counter() - pass_start) * 1000, 3)
    for pass_name in active_passes:
        status = (
            "noop"
            if str(pass_name).strip().lower() in _MEMORY_NOOP_PASSES
            else "run"
        )
        _emit_telemetry(
            {
                "elapsed_ms": elapsed_ms if status == "run" else 0.0,
                "event": "pass",
                "ir_bytes_after": len(current),
                "ir_bytes_before": before_bytes,
                "module": module_name,
                "pass": pass_name,
                "status": status,
                "transport": _TRANSPORT_MEMORY,
            }
        )
    _emit_telemetry(
        {
            "elapsed_ms": round((time.perf_counter() - pipeline_start) * 1000, 3),
            "event": "end",
            "ir_bytes": len(current),
            "module": module_name,
            "transport": _TRANSPORT_MEMORY,
        }
    )
    return current


def _run_python_ir_text_pipeline(
    current: str,
    active_passes: list[str],
    *,
    module_name: str,
    pipeline_start: float,
) -> str:
    import llvmlite.binding as llvm
    from pcc.ir_passes.manager import IRPassManager

    module = llvm.parse_assembly(current)
    module.verify()
    for pass_name in active_passes:
        pass_obj = _load_python_ir_pass(pass_name)
        pass_start = time.perf_counter()
        before_bytes = len(current)
        try:
            IRPassManager().add(pass_obj).run(module)
            rewritten = getattr(pass_obj, "rewritten_ir", None)
            if rewritten is not None:
                current = str(rewritten)
                module = llvm.parse_assembly(current)
            module.verify()
        except Exception as e:
            _emit_telemetry(
                {
                    "elapsed_ms": round((time.perf_counter() - pass_start) * 1000, 3),
                    "event": "pass",
                    "ir_bytes_before": before_bytes,
                    "module": module_name,
                    "pass": pass_name,
                    "status": "error",
                }
            )
            raise PythonIRPassError(
                "Python IR pass "
                f"{pass_name!r} failed for module {module_name!r}: {e}"
            ) from e
        _emit_telemetry(
            {
                "elapsed_ms": round((time.perf_counter() - pass_start) * 1000, 3),
                "event": "pass",
                "ir_bytes_after": len(current),
                "ir_bytes_before": before_bytes,
                "module": module_name,
                "pass": pass_name,
                "status": "run",
            }
        )
    _emit_telemetry(
        {
            "elapsed_ms": round((time.perf_counter() - pipeline_start) * 1000, 3),
            "event": "end",
            "ir_bytes": len(current),
            "module": module_name,
        }
    )
    return current


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        print(
            "usage: ir_pass_pipeline <module-name> <pass-csv> <ir-path>",
            file=sys.stderr,
        )
        return 2
    module_name, pass_csv, ir_path = argv
    pass_names = tuple(name.strip() for name in pass_csv.split(",") if name.strip())
    with open(ir_path, "r", encoding="utf-8") as f:
        ir_text = f.read()
    sys.stdout.write(
        run_python_ir_pass_pipeline(
            ir_text,
            pass_names=pass_names,
            module_name=module_name,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
