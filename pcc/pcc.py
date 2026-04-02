
from .evaluater.c_evaluator import CEvaluator
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


@click.command(context_settings={"ignore_unknown_options": True})
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
    help="Emit LLVM IR to PATH instead of running.",
)
@click.argument('path')
@click.argument('prog_args', nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def main(
    ctx,
    path,
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
    """
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
            )
            include_dirs = translation_unit_include_dirs(units)
        else:
            source, base_dir = collect_project(
                path, sources_from_make=sources_from_make
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

    pcc = CEvaluator(target_triple=target_triple)
    try:
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
    except (ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
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
