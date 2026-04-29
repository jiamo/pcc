import os
import sys

_PASS_DISABLE_ENV = "PCC_DISABLE_PASSES"
_LLVM_TEXT_PIPELINE_ENV = "PCC_LLVM_PIPELINE"
_LLVM_OPT_BIN_ENV = "PCC_LLVM_OPT_BIN"
_DEFAULT_EMIT_LL = "__PCC_DEFAULT_LL__"

_HELP_TEXT = """Usage: pcc [OPTIONS] PATH [PROG_ARGS...]

Pcc - a C compiler built on Python and LLVM.

PATH can be a .c file, a .py file, or a directory containing .c files.
Any arguments after PATH (or after --) are passed to the compiled program.

Options:
  -h, --help                Show this help message and exit.
  --python-libpython MODE   auto, on, or off for Python fallback linkage.
  --ir-scaffold MODE        off (default), on, or auto. Enables Path A
                            closed-world IR-builder lowering (Issue 1).
  --pass NAME               Repeat to enable only the named pass(es).
  --disable-pass NAME       Repeat to disable named pass(es).
  --llvmdump                Dump LLVM IR to temp files.
  -g, --debug               Generate DWARF debug information.
  --separate-tus            Compile directory inputs as separate translation units.
  --jobs N                  Parallel jobs for multi-input or system-link modes.
  --system-link             Link and run via the host C compiler.
  --no-cache                Disable the translation-unit compile cache.
  --cache-dir PATH          Override the on-disk compile cache directory.
  --sources-from-make GOAL  Collect project sources from `make -nB GOAL`.
  --depends-on SPEC         Add a dependency file or directory.
  --cpp-arg ARG             Repeat to pass raw preprocessor args.
  --link-arg ARG            Repeat to pass raw linker args.
  --prepare-cmd CMD         Repeat to run shell commands before compile.
  --ensure-make-goal SPEC   Repeat to run `make -C PATH GOAL`.
  -O0..-O3                  Set optimization level (default: -O2).
  --target TRIPLE           LLVM target triple for cross-compilation.
  --emit-obj PATH           Emit object file instead of running.
  --emit-asm PATH           Emit assembly instead of running.
  --emit-llvm[=PATH]        Emit LLVM IR instead of running.
  -o PATH                   Output path for Python inputs.
  --backend BACKEND         llvm, llvm_capi, or self.
  --verbose                 Print Python pipeline timing.
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


def _normalize_pass_names(values):
    names = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in names:
            names.append(value)
    return names


def _copy_seq(values):
    out = []
    if values is None:
        return out
    try:
        n = len(values)
    except Exception:
        for value in values:
            out.append(value)
        return out
    i = 0
    while i < n:
        out.append(values[i])
        i += 1
    return out


def _concat_seq(lhs, rhs):
    out = _copy_seq(lhs)
    rhs_vals = _copy_seq(rhs)
    i = 0
    while i < len(rhs_vals):
        out.append(rhs_vals[i])
        i += 1
    return out


class _temporary_env:
    """Scoped env-var overrides. Explicit class (not ``@contextmanager``)
    to keep the self-host audit clean."""

    _SENTINEL = object()

    def __init__(self, overrides) -> None:
        self._overrides = _copy_seq(overrides)
        self._previous = []

    def __enter__(self):
        for key, value in self._overrides:
            self._previous.append((key, os.getenv(key, self._SENTINEL)))
            if value:
                os.putenv(key, value)
            else:
                os.unsetenv(key)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for key, value in self._previous:
            if value is self._SENTINEL:
                os.unsetenv(key)
            else:
                os.putenv(key, value)


def _pass_env_overrides(opt_level, enabled_passes=(), disabled_passes=()):
    from pcc.passes import (
        expand_registered_pass_names,
        find_opt_binary,
        llvm_default_pass_names,
        unique_default_pass_names,
        unique_managed_pass_names,
    )

    enabled_requested = _normalize_pass_names(enabled_passes)
    disabled_requested = _normalize_pass_names(disabled_passes)
    if not enabled_requested and not disabled_requested:
        return []

    llvm_passes = set(llvm_default_pass_names(opt_level)) if opt_level > 0 else set()
    visible_managed = set(
        unique_managed_pass_names(opt_level, include_llvm=opt_level > 0)
    )
    unknown = sorted((set(enabled_requested) | set(disabled_requested)) - visible_managed)
    if unknown:
        raise ValueError("unknown pass name(s): " + ", ".join(unknown))

    managed_targets = set(unique_default_pass_names())
    managed_targets.update(expand_registered_pass_names(disabled_requested))
    managed_targets.update(expand_registered_pass_names(enabled_requested))
    if opt_level > 0:
        managed_targets.update(llvm_passes)

    enabled = set(expand_registered_pass_names(enabled_requested))
    disabled = list(expand_registered_pass_names(disabled_requested))
    disabled_set = set(disabled)

    if enabled_requested:
        for pass_name in managed_targets:
            if pass_name not in enabled and pass_name not in disabled_set:
                disabled.append(pass_name)
                disabled_set.add(pass_name)

    touches_llvm = opt_level > 0 and bool((enabled | disabled_set) & llvm_passes)
    if touches_llvm and find_opt_binary(os.getenv(_LLVM_OPT_BIN_ENV)) is None:
        raise ValueError(
            "selecting LLVM passes requires a matching LLVM opt binary; "
            "install llvm@20 or set PCC_LLVM_OPT_BIN"
        )

    overrides = []
    if disabled:
        overrides.append((_PASS_DISABLE_ENV, ",".join(disabled)))
    if touches_llvm:
        overrides.append((
            _LLVM_TEXT_PIPELINE_ENV,
            os.getenv(_LLVM_TEXT_PIPELINE_ENV) or "default",
        ))
    return overrides


def _parse_int_option(name, value, *, minimum=None, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} requires an integer")
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return parsed


def _parse_backend(value):
    if value is None:
        lowered = ""
    else:
        lowered = value.strip().lower()
    if lowered not in ("llvm", "llvm_capi", "self"):
        raise ValueError(
            "invalid backend "
            f"{value!r}; expected one of llvm, llvm_capi, self"
        )
    return lowered


def _parse_python_libpython(value):
    lowered = (value or "").strip().lower()
    if lowered not in ("auto", "on", "off"):
        raise ValueError(
            "invalid --python-libpython "
            f"{value!r}; expected auto, on, or off"
        )
    return lowered


def _parse_ir_scaffold(value):
    lowered = (value or "").strip().lower()
    if lowered not in ("auto", "on", "off"):
        raise ValueError(
            "invalid --ir-scaffold "
            f"{value!r}; expected off, on, or auto"
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


def _option_value(arg, prefix):
    parts = arg.split("=", 1)
    if len(parts) == 2:
        return parts[1]
    return ""


def _normalized_sys_argv():
    argv = []
    i = 1
    while i < len(sys.argv):
        argv.append((sys.argv[i] or "") + "")
        i += 1
    return argv


def parse_cli_args(argv=None):
    if argv is None:
        argv = _normalized_sys_argv()
    else:
        argv = _copy_seq(argv)
    normalized_argv = []
    for value in argv:
        normalized_argv.append((value or "") + "")
    argv = normalized_argv
    if len(argv) == 1:
        arg0 = argv[0]
        if arg0 == "-h" or arg0 == "--help":
            return None, 0, None

    path = None
    enabled_passes = []
    disabled_passes = []
    llvmdump = False
    emit_debug = False
    separate_tus = False
    jobs = 8
    jobs_was_explicit = False
    system_link = False
    no_cache = False
    cache_dir = None
    sources_from_make = None
    dependencies = []
    cpp_args = []
    link_args = []
    prepare_cmds = []
    ensure_make_goal_specs = []
    opt_level = 2
    target_triple = None
    emit_obj = None
    emit_asm = None
    emit_llvm = None
    output_path = None
    backend = None
    python_libpython = None
    ir_scaffold = None
    verbose = False
    prog_args = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            prog_args.extend(argv[i + 1 :])
            break
        if arg in ("-h", "--help"):
            return None, 0, None
        if arg in ("--llvmdump",):
            llvmdump = True
            i += 1
            continue
        if arg in ("-g", "--debug"):
            emit_debug = True
            i += 1
            continue
        if arg == "--separate-tus":
            separate_tus = True
            i += 1
            continue
        if arg == "--system-link":
            system_link = True
            i += 1
            continue
        if arg == "--no-cache":
            no_cache = True
            i += 1
            continue
        if arg in ("-v", "--verbose"):
            verbose = True
            i += 1
            continue
        if arg.startswith("--pass="):
            enabled_passes.append(_option_value(arg, "--pass="))
            i += 1
            continue
        if arg == "--pass":
            if i + 1 >= len(argv):
                return None, 2, "--pass requires a value"
            enabled_passes.append(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--disable-pass="):
            disabled_passes.append(_option_value(arg, "--disable-pass="))
            i += 1
            continue
        if arg == "--disable-pass":
            if i + 1 >= len(argv):
                return None, 2, "--disable-pass requires a value"
            disabled_passes.append(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--jobs="):
            try:
                jobs = _parse_int_option(
                    "--jobs", _option_value(arg, "--jobs="), minimum=1
                )
            except ValueError as exc:
                return None, 2, str(exc)
            jobs_was_explicit = True
            i += 1
            continue
        if arg == "--jobs":
            if i + 1 >= len(argv):
                return None, 2, "--jobs requires a value"
            try:
                jobs = _parse_int_option("--jobs", argv[i + 1], minimum=1)
            except ValueError as exc:
                return None, 2, str(exc)
            jobs_was_explicit = True
            i += 2
            continue
        if arg.startswith("--cache-dir="):
            cache_dir = _option_value(arg, "--cache-dir=")
            i += 1
            continue
        if arg == "--cache-dir":
            if i + 1 >= len(argv):
                return None, 2, "--cache-dir requires a value"
            cache_dir = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--sources-from-make="):
            sources_from_make = _option_value(arg, "--sources-from-make=")
            i += 1
            continue
        if arg == "--sources-from-make":
            if i + 1 >= len(argv):
                return None, 2, "--sources-from-make requires a value"
            sources_from_make = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--depends-on="):
            dependencies.append(_option_value(arg, "--depends-on="))
            i += 1
            continue
        if arg == "--depends-on":
            if i + 1 >= len(argv):
                return None, 2, "--depends-on requires a value"
            dependencies.append(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--cpp-arg="):
            cpp_args.append(_option_value(arg, "--cpp-arg="))
            i += 1
            continue
        if arg == "--cpp-arg":
            if i + 1 >= len(argv):
                return None, 2, "--cpp-arg requires a value"
            cpp_args.append(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--link-arg="):
            link_args.append(_option_value(arg, "--link-arg="))
            i += 1
            continue
        if arg == "--link-arg":
            if i + 1 >= len(argv):
                return None, 2, "--link-arg requires a value"
            link_args.append(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--prepare-cmd="):
            prepare_cmds.append(_option_value(arg, "--prepare-cmd="))
            i += 1
            continue
        if arg == "--prepare-cmd":
            if i + 1 >= len(argv):
                return None, 2, "--prepare-cmd requires a value"
            prepare_cmds.append(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--ensure-make-goal="):
            ensure_make_goal_specs.append(_option_value(arg, "--ensure-make-goal="))
            i += 1
            continue
        if arg == "--ensure-make-goal":
            if i + 1 >= len(argv):
                return None, 2, "--ensure-make-goal requires a value"
            ensure_make_goal_specs.append(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--target="):
            target_triple = _option_value(arg, "--target=")
            i += 1
            continue
        if arg == "--target":
            if i + 1 >= len(argv):
                return None, 2, "--target requires a value"
            target_triple = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--emit-obj="):
            emit_obj = _option_value(arg, "--emit-obj=")
            i += 1
            continue
        if arg == "--emit-obj":
            if i + 1 >= len(argv):
                return None, 2, "--emit-obj requires a value"
            emit_obj = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--emit-asm="):
            emit_asm = _option_value(arg, "--emit-asm=")
            i += 1
            continue
        if arg == "--emit-asm":
            if i + 1 >= len(argv):
                return None, 2, "--emit-asm requires a value"
            emit_asm = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--emit-llvm="):
            emit_llvm = _option_value(arg, "--emit-llvm=") or _DEFAULT_EMIT_LL
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
        if arg.startswith("--backend="):
            try:
                backend = _parse_backend(_option_value(arg, "--backend="))
            except ValueError as exc:
                return None, 2, str(exc)
            i += 1
            continue
        if arg == "--backend":
            if i + 1 >= len(argv):
                return None, 2, "--backend requires a value"
            try:
                backend = _parse_backend(argv[i + 1])
            except ValueError as exc:
                return None, 2, str(exc)
            i += 2
            continue
        if arg.startswith("--python-libpython="):
            try:
                python_libpython = _parse_python_libpython(
                    _option_value(arg, "--python-libpython=")
                )
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
                ir_scaffold = _parse_ir_scaffold(
                    _option_value(arg, "--ir-scaffold=")
                )
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
        if arg.startswith("-O") and arg != "-O":
            try:
                opt_level = _parse_int_option(
                    "-O", arg[2:], minimum=0, maximum=3
                )
            except ValueError as exc:
                return None, 2, str(exc)
            i += 1
            continue
        if arg == "-O":
            if i + 1 >= len(argv):
                return None, 2, "-O requires a value"
            try:
                opt_level = _parse_int_option(
                    "-O", argv[i + 1], minimum=0, maximum=3
                )
            except ValueError as exc:
                return None, 2, str(exc)
            i += 2
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
            prog_args.append(arg)
        i += 1

    if path is None:
        return None, 2, "missing required PATH"

    return (
        (
            path,
            enabled_passes,
            disabled_passes,
            llvmdump,
            emit_debug,
            separate_tus,
            jobs,
            jobs_was_explicit,
            system_link,
            no_cache,
            cache_dir,
            sources_from_make,
            dependencies,
            cpp_args,
            link_args,
            prepare_cmds,
            ensure_make_goal_specs,
            opt_level,
            target_triple,
            emit_obj,
            emit_asm,
            emit_llvm,
            output_path,
            backend,
            python_libpython,
            ir_scaffold,
            verbose,
            prog_args,
        ),
        0,
        None,
    )


def _argv_requests_help(argv=None):
    if argv is None:
        if len(sys.argv) != 2:
            return False
        arg0 = (sys.argv[1] or "") + ""
    else:
        if len(argv) != 1:
            return False
        arg0 = (argv[0] or "") + ""
    return arg0 == "-h" or arg0 == "--help"


def _normalize_cli_text(value):
    return (value or "") + ""


def _normalize_cli_optional_text(value):
    if value is None:
        return None
    return (value or "") + ""


def _normalize_cli_flag(value):
    return True if value else False


def _normalize_cli_int(value):
    return int(value)


def _normalize_cli_text_seq(values):
    out = []
    for value in _copy_seq(values):
        out.append((value or "") + "")
    return out


def _execute_python_path(
    *,
    path,
    emit_llvm,
    output_path,
    python_libpython,
    ir_scaffold,
    verbose,
    prog_args,
):
    from pcc.py_frontend.pipeline import PyPipelineError, compile_python

    src_path = path
    emit_ll_only = emit_llvm is not None
    if emit_ll_only:
        if emit_llvm == _DEFAULT_EMIT_LL:
            stem = src_path[:-3]
            ll_out = output_path if output_path else stem + ".ll"
        else:
            ll_out = output_path if output_path else emit_llvm
        try:
            compile_python(
                src_path,
                ll_out,
                verbose=verbose,
                emit_llvm_only=True,
                libpython_mode=python_libpython,
                ir_scaffold_mode=ir_scaffold,
            )
        except PyPipelineError as exc:
            _write_text("Error: " + str(exc), err=True)
            return 1
        return 0

    if output_path:
        try:
            compile_python(
                src_path,
                output_path,
                verbose=verbose,
                libpython_mode=python_libpython,
                ir_scaffold_mode=ir_scaffold,
            )
        except PyPipelineError as exc:
            _write_text("Error: " + str(exc), err=True)
            return 1
        return 0

    import subprocess as _subp
    import tempfile as _tmp

    with _tmp.TemporaryDirectory(prefix="pcc_py_run_") as td:
        exe_path = os.path.join(td, os.path.basename(src_path)[:-3] or "pcc_run")
        try:
            compile_python(
                src_path,
                exe_path,
                verbose=verbose,
                libpython_mode=python_libpython,
                ir_scaffold_mode=ir_scaffold,
            )
        except PyPipelineError as exc:
            _write_text("Error: " + str(exc), err=True)
            return 1
        try:
            run = _subp.run([exe_path] + _copy_seq(prog_args))
        except OSError as exc:
            _write_text(f"Error running exe: {exc}", err=True)
            return 1
        return run.returncode


def execute_cli(
    *,
    path,
    enabled_passes=(),
    disabled_passes=(),
    llvmdump=False,
    emit_debug=False,
    separate_tus=False,
    jobs=8,
    jobs_was_explicit=False,
    system_link=False,
    no_cache=False,
    cache_dir=None,
    sources_from_make=None,
    dependencies=(),
    cpp_args=(),
    link_args=(),
    prepare_cmds=(),
    ensure_make_goal_specs=(),
    opt_level=2,
    target_triple=None,
    emit_obj=None,
    emit_asm=None,
    emit_llvm=None,
    output_path=None,
    backend=None,
    python_libpython=None,
    ir_scaffold=None,
    verbose=False,
    prog_args=(),
):
    del emit_debug
    from pcc.backend import (
        BackendUnavailable,
        backend_request_allows_unimplemented,
        resolve_backend,
    )
    from pcc.evaluater.c_evaluator import CEvaluator
    from pcc.project import (
        TranslationUnit,
        collect_cpp_args,
        collect_project,
        collect_translation_units,
        ensure_make_goals,
        run_prepare_commands,
        translation_unit_include_dirs,
    )

    if isinstance(path, str) and path.endswith(".py"):
        return _execute_python_path(
            path=path,
            emit_llvm=emit_llvm,
            output_path=output_path,
            python_libpython=python_libpython,
            ir_scaffold=ir_scaffold,
            verbose=verbose,
            prog_args=prog_args,
        )

    if emit_llvm == _DEFAULT_EMIT_LL:
        _write_text(
            "Error: --emit-llvm requires a PATH argument for C inputs",
            err=True,
        )
        return 1

    use_multi_input = separate_tus or bool(dependencies)

    if jobs_was_explicit and not (use_multi_input or system_link):
        _write_text(
            "Error: --jobs requires --separate-tus, --depends-on, or --system-link",
            err=True,
        )
        return 1

    try:
        run_prepare_commands(prepare_cmds)
        ensure_make_goals(ensure_make_goal_specs, jobs=jobs)
        pass_env = _pass_env_overrides(
            opt_level,
            enabled_passes=enabled_passes,
            disabled_passes=disabled_passes,
        )
        include_dirs = None
        inferred_cpp_args = collect_cpp_args(
            path,
            sources_from_make=sources_from_make,
            dependencies=dependencies,
        )
        merged_cpp_args = _concat_seq(inferred_cpp_args, cpp_args)
        if use_multi_input:
            units, base_dir = collect_translation_units(
                path,
                sources_from_make=sources_from_make,
                dependencies=dependencies,
                cpp_args=cpp_args,
            )
            include_dirs = translation_unit_include_dirs(units)
        else:
            source, base_dir = collect_project(
                path,
                sources_from_make=sources_from_make,
                cpp_args=cpp_args,
            )
            if system_link:
                unit_path = (
                    os.path.abspath(path)
                    if os.path.isfile(path)
                    else os.path.join(base_dir, "__merged_project__.c")
                )
                units = [
                    TranslationUnit(
                        name=os.path.basename(unit_path),
                        path=unit_path,
                        source=source,
                    )
                ]
                include_dirs = [base_dir]
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        _write_text("Error: " + str(exc), err=True)
        return 1

    emit_mode = emit_obj or emit_asm or emit_llvm
    if target_triple and not emit_mode and not system_link:
        _write_text(
            "Error: --target requires --emit-obj, --emit-asm, --emit-llvm, or --system-link",
            err=True,
        )
        return 1

    try:
        allow_unimplemented_backend = backend_request_allows_unimplemented(backend)
        backend_config = resolve_backend(
            backend,
            allow_unimplemented=allow_unimplemented_backend,
        )
        pcc = CEvaluator(
            target_triple=target_triple,
            backend=backend_config.kind,
            allow_unimplemented_backend=allow_unimplemented_backend,
        )
        with _temporary_env(pass_env):
            if emit_mode:
                if use_multi_input:
                    compiled_units = pcc.compile_translation_units(
                        units,
                        base_dir=base_dir,
                        jobs=jobs,
                        include_dirs=include_dirs,
                        cpp_args=merged_cpp_args,
                        use_compile_cache=not no_cache,
                        cache_dir=cache_dir,
                        frontend_opt_level=opt_level,
                    )
                else:
                    snippet_unit = TranslationUnit(
                        name=os.path.basename(path),
                        path=os.path.abspath(path) if os.path.isfile(path) else None,
                        source=source,
                    )
                    compiled_units = pcc.compile_translation_units(
                        [snippet_unit],
                        base_dir=base_dir,
                        jobs=1,
                        include_dirs=include_dirs,
                        cpp_args=merged_cpp_args,
                        use_compile_cache=not no_cache,
                        cache_dir=cache_dir,
                        frontend_opt_level=opt_level,
                    )
                pcc.emit_compiled_units(
                    compiled_units,
                    emit_obj=emit_obj,
                    emit_asm=emit_asm,
                    emit_llvm=emit_llvm,
                    optimize=opt_level,
                )
                return 0

            if use_multi_input or system_link:
                if system_link:
                    run = pcc.run_translation_units_with_system_cc(
                        units,
                        optimize=opt_level,
                        llvmdump=llvmdump,
                        base_dir=base_dir,
                        prog_args=_copy_seq(prog_args),
                        jobs=jobs,
                        include_dirs=include_dirs,
                        cpp_args=merged_cpp_args,
                        link_args=_copy_seq(link_args),
                        use_compile_cache=not no_cache,
                        cache_dir=cache_dir,
                    )
                    ret = run.returncode
                    if run.stdout:
                        _write_text(run.stdout, nl=not run.stdout.endswith("\n"))
                    if run.stderr:
                        _write_text(
                            run.stderr,
                            err=True,
                            nl=not run.stderr.endswith("\n"),
                        )
                else:
                    ret = pcc.evaluate_translation_units(
                        units,
                        optimize=opt_level,
                        llvmdump=llvmdump,
                        base_dir=base_dir,
                        prog_args=_copy_seq(prog_args),
                        jobs=jobs,
                        include_dirs=include_dirs,
                        cpp_args=merged_cpp_args,
                        link_args=_copy_seq(link_args),
                        use_compile_cache=not no_cache,
                        cache_dir=cache_dir,
                    )
            else:
                ret = pcc.evaluate(
                    source,
                    optimize=opt_level,
                    llvmdump=llvmdump,
                    base_dir=base_dir,
                    prog_args=_copy_seq(prog_args),
                    include_dirs=include_dirs,
                    cpp_args=merged_cpp_args,
                    link_args=_copy_seq(link_args),
                    use_compile_cache=not no_cache,
                    cache_dir=cache_dir,
                )
    except (
        BackendUnavailable,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        _write_text("Error: " + str(exc), err=True)
        return 1
    except Exception as exc:
        err_name = type(exc).__name__
        if err_name in ("ParseError", "SemanticError", "CodegenError"):
            _write_text("Error: " + str(exc), err=True)
            return 1
        raise
    except KeyboardInterrupt:
        _write_text("\nInterrupted.", err=True, nl=False)
        return 130
    return ret if isinstance(ret, int) else 0


def cli_main(argv=None) -> int:
    if _argv_requests_help(argv):
        _write_text(_HELP_TEXT, nl=False)
        return 0
    parsed, status, err = parse_cli_args(argv)
    if parsed is None:
        if err is None:
            _write_text(_HELP_TEXT, nl=False)
        else:
            _write_text("Error: " + str(err), err=True)
            _write_text(_HELP_TEXT, err=True, nl=False)
        return status
    (
        path_raw,
        enabled_passes_raw,
        disabled_passes_raw,
        llvmdump_raw,
        emit_debug_raw,
        separate_tus_raw,
        jobs_raw,
        jobs_was_explicit_raw,
        system_link_raw,
        no_cache_raw,
        cache_dir_raw,
        sources_from_make_raw,
        dependencies_raw,
        cpp_args_raw,
        link_args_raw,
        prepare_cmds_raw,
        ensure_make_goal_specs_raw,
        opt_level_raw,
        target_triple_raw,
        emit_obj_raw,
        emit_asm_raw,
        emit_llvm_raw,
        output_path_raw,
        backend_raw,
        python_libpython_raw,
        ir_scaffold_raw,
        verbose_raw,
        prog_args_raw,
    ) = parsed
    path = (path_raw or "") + ""
    emit_llvm = None if emit_llvm_raw is None else (emit_llvm_raw or "") + ""
    output_path = None if output_path_raw is None else (output_path_raw or "") + ""
    verbose = True if verbose_raw else False
    prog_args = []
    i = 0
    while i < len(prog_args_raw):
        prog_args.append((prog_args_raw[i] or "") + "")
        i += 1
    if path.endswith(".py"):
        if not os.path.isfile(path):
            _write_text("Error: input file not found: " + path, err=True)
            return 1
        from pcc.py_frontend.pipeline import PyPipelineError, compile_python

        emit_ll_only = emit_llvm is not None
        if emit_ll_only:
            if emit_llvm == _DEFAULT_EMIT_LL:
                stem = path[:-3]
                ll_out = output_path if output_path else stem + ".ll"
            else:
                ll_out = output_path if output_path else emit_llvm
            try:
                compile_python(
                    path,
                    ll_out,
                    verbose=verbose,
                    emit_llvm_only=True,
                    libpython_mode=python_libpython_raw,
                    ir_scaffold_mode=ir_scaffold_raw,
                    backend=backend_raw,
                )
            except PyPipelineError as exc:
                _write_text("Error: " + str(exc), err=True)
                return 1
            return 0

        if output_path:
            try:
                compile_python(
                    path,
                    output_path,
                    verbose=verbose,
                    libpython_mode=python_libpython_raw,
                    ir_scaffold_mode=ir_scaffold_raw,
                    backend=backend_raw,
                )
            except PyPipelineError as exc:
                _write_text("Error: " + str(exc), err=True)
                return 1
            return 0

        import subprocess as _subp
        import tempfile as _tmp

        with _tmp.TemporaryDirectory(prefix="pcc_py_run_") as td:
            exe_path = os.path.join(td, os.path.basename(path)[:-3] or "pcc_run")
            try:
                compile_python(
                    path,
                    exe_path,
                    verbose=verbose,
                    libpython_mode=python_libpython_raw,
                    ir_scaffold_mode=ir_scaffold_raw,
                    backend=backend_raw,
                )
            except PyPipelineError as exc:
                _write_text("Error: " + str(exc), err=True)
                return 1
            try:
                run = _subp.run([exe_path] + _copy_seq(prog_args))
            except OSError as exc:
                _write_text(f"Error running exe: {exc}", err=True)
                return 1
            return run.returncode

    enabled_passes = _normalize_cli_text_seq(enabled_passes_raw)
    disabled_passes = _normalize_cli_text_seq(disabled_passes_raw)
    jobs = _normalize_cli_int(jobs_raw)
    jobs_was_explicit = _normalize_cli_flag(jobs_was_explicit_raw)
    system_link = _normalize_cli_flag(system_link_raw)
    no_cache = _normalize_cli_flag(no_cache_raw)
    llvmdump = _normalize_cli_flag(llvmdump_raw)
    emit_debug = _normalize_cli_flag(emit_debug_raw)
    separate_tus = _normalize_cli_flag(separate_tus_raw)
    cache_dir = _normalize_cli_optional_text(cache_dir_raw)
    sources_from_make = _normalize_cli_optional_text(sources_from_make_raw)
    dependencies = _normalize_cli_text_seq(dependencies_raw)
    cpp_args = _normalize_cli_text_seq(cpp_args_raw)
    link_args = _normalize_cli_text_seq(link_args_raw)
    prepare_cmds = _normalize_cli_text_seq(prepare_cmds_raw)
    ensure_make_goal_specs = _normalize_cli_text_seq(ensure_make_goal_specs_raw)
    opt_level = _normalize_cli_int(opt_level_raw)
    target_triple = _normalize_cli_optional_text(target_triple_raw)
    emit_obj = _normalize_cli_optional_text(emit_obj_raw)
    emit_asm = _normalize_cli_optional_text(emit_asm_raw)
    backend = _normalize_cli_optional_text(backend_raw)
    python_libpython = _normalize_cli_optional_text(python_libpython_raw)
    ir_scaffold = _normalize_cli_optional_text(ir_scaffold_raw)
    return execute_cli(
        path=path,
        enabled_passes=enabled_passes,
        disabled_passes=disabled_passes,
        llvmdump=llvmdump,
        emit_debug=emit_debug,
        separate_tus=separate_tus,
        jobs=jobs,
        jobs_was_explicit=jobs_was_explicit,
        system_link=system_link,
        no_cache=no_cache,
        cache_dir=cache_dir,
        sources_from_make=sources_from_make,
        dependencies=dependencies,
        cpp_args=cpp_args,
        link_args=link_args,
        prepare_cmds=prepare_cmds,
        ensure_make_goal_specs=ensure_make_goal_specs,
        opt_level=opt_level,
        target_triple=target_triple,
        emit_obj=emit_obj,
        emit_asm=emit_asm,
        emit_llvm=emit_llvm,
        output_path=output_path,
        backend=backend,
        python_libpython=python_libpython,
        ir_scaffold=ir_scaffold,
        verbose=verbose,
        prog_args=prog_args,
    )


def cli_main_sys_argv() -> int:
    return cli_main()


def cli_main_sys_argv_exit() -> None:
    try:
        code = cli_main()
    except Exception as exc:
        if type(exc).__name__ == "PyPipelineError":
            _write_text("Error: " + str(exc), err=True)
            code = 1
        else:
            raise
    if code != 0:
        from sys import exit as _exit

        _exit(code)


def cli_main_strict_sys_argv_exit() -> None:
    """Entry point for the ``pcc-static`` console script.

    Same as ``pcc`` but with the no-libpython flag combination
    (``--python-libpython=off`` + ``--ir-scaffold=on``) wired in as
    the default. An explicitly-passed ``--python-libpython`` /
    ``--ir-scaffold`` CLI flag, or a pre-set ``PCC_PYTHON_LIBPYTHON``
    / ``PCC_IR_SCAFFOLD`` env var, still takes precedence.
    """
    import os
    os.environ.setdefault("PCC_PYTHON_LIBPYTHON", "off")
    os.environ.setdefault("PCC_IR_SCAFFOLD", "on")
    cli_main_sys_argv_exit()
