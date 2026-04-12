from contextlib import contextmanager

from .backend import BackendUnavailable, backend_env_name, resolve_backend
from .evaluater.c_evaluator import CEvaluator
from .passes import (
    expand_registered_pass_names,
    find_opt_binary,
    llvm_default_pass_names,
    unique_default_pass_names,
    unique_managed_pass_names,
)
from .project import (
    TranslationUnit,
    collect_cpp_args,
    collect_project,
    collect_translation_units,
    ensure_make_goals,
    run_prepare_commands,
    translation_unit_include_dirs,
)
import os
import sys
import click
from click.core import ParameterSource

_PASS_DISABLE_ENV = "PCC_DISABLE_PASSES"
_LLVM_TEXT_PIPELINE_ENV = "PCC_LLVM_PIPELINE"
_LLVM_OPT_BIN_ENV = "PCC_LLVM_OPT_BIN"


def _normalize_pass_names(values):
    names = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in names:
            names.append(value)
    return tuple(names)


class _temporary_env:
    """Scoped env-var overrides. Explicit class (not ``@contextmanager``)
    to keep the self-host audit clean."""
    _SENTINEL = object()

    def __init__(self, overrides) -> None:
        self._overrides = overrides
        self._previous = {}

    def __enter__(self):
        for key, value in self._overrides.items():
            self._previous[key] = os.environ.get(key, self._SENTINEL)
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for key, value in self._previous.items():
            if value is self._SENTINEL:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _pass_env_overrides(opt_level, enabled_passes=(), disabled_passes=()):
    enabled_requested = _normalize_pass_names(enabled_passes)
    disabled_requested = _normalize_pass_names(disabled_passes)
    if not enabled_requested and not disabled_requested:
        return {}

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
    if touches_llvm and find_opt_binary(os.environ.get(_LLVM_OPT_BIN_ENV)) is None:
        raise ValueError(
            "selecting LLVM passes requires a matching LLVM opt binary; "
            "install llvm@20 or set PCC_LLVM_OPT_BIN"
        )

    overrides = {
        _PASS_DISABLE_ENV: ",".join(disabled) if disabled else None,
    }
    if touches_llvm:
        overrides[_LLVM_TEXT_PIPELINE_ENV] = (
            os.environ.get(_LLVM_TEXT_PIPELINE_ENV) or "default"
        )
    return overrides


@click.command(context_settings={"ignore_unknown_options": True})
@click.option(
    "--pass",
    "enabled_passes",
    multiple=True,
    metavar="NAME",
    help="Repeat to enable only the named pass(es). Works for both pcc passes and concrete LLVM passes.",
)
@click.option(
    "--disable-pass",
    "disabled_passes",
    multiple=True,
    metavar="NAME",
    help="Repeat to disable named pass(es). Works for both pcc passes and concrete LLVM passes.",
)
@click.option("--llvmdump", is_flag=True, default=False, help="Dump LLVM IR to temp files")
@click.option("-g", "--debug", "emit_debug", is_flag=True, default=False, help="Generate DWARF debug information")
@click.option(
    "--separate-tus",
    is_flag=True,
    default=False,
    help="Compile directory inputs as separate translation units before linking",
)
@click.option(
    "--jobs",
    type=click.IntRange(min=1),
    default=8,
    show_default=True,
    help="Number of translation units to compile in parallel when using --separate-tus, --depends-on, or --system-link",
)
@click.option(
    "--system-link",
    is_flag=True,
    default=False,
    help="Link and run via the host C compiler instead of MCJIT",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Disable the translation-unit compile cache.",
)
@click.option(
    "--cache-dir",
    metavar="PATH",
    help="Override the on-disk translation-unit compile cache directory.",
)
@click.option(
    "--sources-from-make",
    metavar="GOAL",
    help="Collect participating .c files from a dry-run of `make GOAL` instead of scanning the directory",
)
@click.option(
    "--depends-on",
    "dependencies",
    multiple=True,
    metavar="PATH[=GOAL]",
    help="Add a dependency file or directory. For directories, use PATH=GOAL to collect sources from a dry-run of `make GOAL`.",
)
@click.option(
    "--cpp-arg",
    "cpp_args",
    multiple=True,
    metavar="ARG",
    help="Repeat to pass raw preprocessor args such as -DNAME=1, -UFOO, or -I/path.",
)
@click.option(
    "--link-arg",
    "link_args",
    multiple=True,
    metavar="ARG",
    help="Repeat to pass raw linker args such as -lm or /path/to/libfoo.a.",
)
@click.option(
    "--prepare-cmd",
    "prepare_cmds",
    multiple=True,
    metavar="CMD",
    help="Repeat to run shell commands before collecting or compiling sources.",
)
@click.option(
    "--ensure-make-goal",
    "ensure_make_goal_specs",
    multiple=True,
    metavar="PATH=GOAL",
    help="Repeat to run `make -C PATH GOAL` before collecting or linking sources.",
)
@click.option(
    "-O",
    "opt_level",
    type=click.IntRange(0, 3),
    default=2,
    show_default=True,
    help="Optimization level: 0 (none), 1 (basic), 2 (default), 3 (aggressive).",
)
@click.option(
    "--target",
    "target_triple",
    metavar="TRIPLE",
    help="LLVM target triple for cross-compilation (e.g. x86_64-unknown-linux-gnu, aarch64-unknown-linux-gnu).",
)
@click.option(
    "--emit-obj",
    "emit_obj",
    metavar="PATH",
    help="Emit object file to PATH instead of running. Useful for cross-compilation.",
)
@click.option(
    "--emit-asm",
    "emit_asm",
    metavar="PATH",
    help="Emit assembly to PATH instead of running.",
)
@click.option(
    "--emit-llvm",
    "emit_llvm",
    metavar="PATH",
    help="Emit LLVM IR to PATH instead of running. For .py inputs may "
         "be given without a value to emit to <stem>.ll.",
    is_flag=False,
    flag_value="__PCC_DEFAULT_LL__",
)
@click.option(
    "-o",
    "output_path",
    metavar="PATH",
    help="Output path. For .py inputs, writes a native executable here "
         "(or the LLVM IR when --emit-llvm is given).",
)
@click.option(
    "--backend",
    type=click.Choice(["llvm", "llvm_capi", "self"], case_sensitive=False),
    default=None,
    envvar=backend_env_name(),
    metavar="BACKEND",
    help="Backend implementation to use: llvm (default), llvm_capi, or self.",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Print each pipeline step and timing info (Python pipeline).",
)
@click.argument('path')
@click.argument('prog_args', nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def main(
    ctx,
    path,
    enabled_passes,
    disabled_passes,
    llvmdump,
    emit_debug,
    separate_tus,
    jobs,
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
    verbose,
    prog_args,
):
    """Pcc - a C compiler built on Python and LLVM.

    PATH can be a .c file or a directory containing .c files.
    Any arguments after PATH (or after --) are passed to the compiled program.

    \b
    Examples:
        pcc hello.c              # compile and run a single file
        pcc myproject/           # compile all .c files in directory
        pcc --sources-from-make app myproject/  # collect sources from a dry-run of `make app`
        pcc --separate-tus --jobs 4 myproject/  # compile project files in parallel
        pcc --depends-on libs/mylib util/main.c  # compile a main file plus dependency sources
        pcc --system-link --link-arg=-lm mathprog.c  # link and run via the host C compiler
        pcc --prepare-cmd 'cd dep && ./configure' --ensure-make-goal dep=libfoo.a main.c
        pcc --llvmdump test.c    # also dump LLVM IR
        pcc myproject/ -- script.lua  # pass args to compiled program
        pcc hello.py             # compile and link a Python file
        pcc hello.py -o prog     # override output name
        pcc hello.py --emit-llvm # emit hello.ll only, don't link
    """
    # --- Python frontend dispatch ---------------------------------------
    # If the input is a .py file, run the Python pipeline and exit.
    # This must happen before any C-centric parameter validation so that
    # flags like --jobs, --separate-tus, --target etc. (meaningless for
    # Python inputs) don't trigger errors we don't care about.
    if isinstance(path, str) and path.endswith(".py"):
        from .py_frontend.pipeline import compile_python, PyPipelineError
        import subprocess as _subp
        import tempfile as _tmp

        src_path = path

        # --emit-llvm: may be bare (sentinel ``__PCC_DEFAULT_LL__``) or
        # given with a path. Bare form writes ``<stem>.ll`` next to the
        # source (or honors -o if given).
        emit_ll_only = emit_llvm is not None
        if emit_ll_only:
            if emit_llvm == "__PCC_DEFAULT_LL__":
                stem = src_path[:-3]
                ll_out = output_path if output_path else stem + ".ll"
            else:
                ll_out = output_path if output_path else emit_llvm
            try:
                compile_python(
                    src_path, ll_out, verbose=verbose, emit_llvm_only=True
                )
            except PyPipelineError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)
            sys.exit(0)

        # No --emit-llvm: compile + (if -o given) save the exe, else
        # compile to a temp file, run, forward the exit code — matches
        # ``pcc hello.c`` which also compiles-and-runs when the user
        # just wants to see the script's output.
        if output_path:
            try:
                compile_python(src_path, output_path, verbose=verbose)
            except PyPipelineError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)
            sys.exit(0)

        with _tmp.TemporaryDirectory(prefix="pcc_py_run_") as _td:
            exe_path = os.path.join(_td, os.path.basename(src_path)[:-3] or "pcc_run")
            try:
                compile_python(src_path, exe_path, verbose=verbose)
            except PyPipelineError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)
            # Forward program args (anything after ``--``) to the exe.
            extra_args = list(prog_args) if prog_args else []
            try:
                r = _subp.run([exe_path, *extra_args])
            except OSError as e:
                click.echo(f"Error running exe: {e}", err=True)
                sys.exit(1)
            sys.exit(r.returncode)
    # --- end Python frontend dispatch ----------------------------------

    # For .c inputs, --emit-llvm still requires an explicit PATH.
    # Reject the bare-flag sentinel that only makes sense for .py.
    if emit_llvm == "__PCC_DEFAULT_LL__":
        click.echo(
            "Error: --emit-llvm requires a PATH argument for C inputs",
            err=True,
        )
        sys.exit(1)

    jobs_was_explicit = (
        ctx.get_parameter_source("jobs") == ParameterSource.COMMANDLINE
    )
    use_multi_input = separate_tus or bool(dependencies)

    if jobs_was_explicit and not (use_multi_input or system_link):
        click.echo(
            "Error: --jobs requires --separate-tus, --depends-on, or --system-link",
            err=True,
        )
        sys.exit(1)

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
        merged_cpp_args = tuple(inferred_cpp_args) + tuple(cpp_args)
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
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    emit_mode = emit_obj or emit_asm or emit_llvm
    if target_triple and not emit_mode and not system_link:
        click.echo(
            "Error: --target requires --emit-obj, --emit-asm, --emit-llvm, or --system-link",
            err=True,
        )
        sys.exit(1)

    try:
        allow_unimplemented_backend = bool(backend == "self")
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
            # Emit mode: compile to file instead of running
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
                sys.exit(0)

            if use_multi_input or system_link:
                if system_link:
                    run = pcc.run_translation_units_with_system_cc(
                        units,
                        optimize=opt_level,
                        llvmdump=llvmdump,
                        base_dir=base_dir,
                        prog_args=list(prog_args),
                        jobs=jobs,
                        include_dirs=include_dirs,
                        cpp_args=merged_cpp_args,
                        link_args=list(link_args),
                        use_compile_cache=not no_cache,
                        cache_dir=cache_dir,
                    )
                    ret = run.returncode
                    if run.stdout:
                        click.echo(run.stdout, nl=not run.stdout.endswith("\n"))
                    if run.stderr:
                        click.echo(run.stderr, err=True, nl=not run.stderr.endswith("\n"))
                else:
                    ret = pcc.evaluate_translation_units(
                        units,
                        optimize=opt_level,
                        llvmdump=llvmdump,
                        base_dir=base_dir,
                        prog_args=list(prog_args),
                        jobs=jobs,
                        include_dirs=include_dirs,
                        cpp_args=merged_cpp_args,
                        link_args=list(link_args),
                        use_compile_cache=not no_cache,
                        cache_dir=cache_dir,
                    )
            else:
                ret = pcc.evaluate(
                    source,
                    optimize=opt_level,
                    llvmdump=llvmdump,
                    base_dir=base_dir,
                    prog_args=list(prog_args),
                    include_dirs=include_dirs,
                    cpp_args=merged_cpp_args,
                    link_args=list(link_args),
                    use_compile_cache=not no_cache,
                    cache_dir=cache_dir,
                )
    except (BackendUnavailable, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        # Catch codegen/parse errors with clean output
        err_name = type(e).__name__
        if err_name in ("ParseError", "SemanticError", "CodegenError"):
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        raise
    except KeyboardInterrupt:
        click.echo("\nInterrupted.", err=True)
        sys.exit(130)
    sys.exit(ret if isinstance(ret, int) else 0)


if __name__ == "__main__":
    main()
