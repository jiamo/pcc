from .backend import backend_env_name
from .cli_contract import (
    BACKEND_CHOICES,
    DEFAULT_EMIT_LL,
    PYTHON_LIBPYTHON_CHOICES,
)
from .cli_core import cli_main, execute_cli
from .gpu_backend import gpu_backend_env_name


_MAIN_DOC = """Pcc - a C compiler built on Python and LLVM.

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


def _plain_main(argv=None):
    return cli_main(argv)


def _click_entry(
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
    gpu_backend,
    python_libpython,
    python_library,
    verbose,
    prog_args,
):
    click_mod = __import__("click")
    parameter_source = click_mod.core.ParameterSource
    jobs_was_explicit = (
        ctx.get_parameter_source("jobs") == parameter_source.COMMANDLINE
    )
    raise SystemExit(
        execute_cli(
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
            gpu_backend=gpu_backend,
            python_libpython=python_libpython,
            python_library=python_library,
            verbose=verbose,
            prog_args=prog_args,
        )
    )


_click_entry.__doc__ = _MAIN_DOC


def _build_click_main():
    try:
        click_mod = __import__("click")
    except Exception:
        return _plain_main

    cmd = _click_entry
    cmd = click_mod.pass_context(cmd)
    cmd = click_mod.argument("prog_args", nargs=-1, type=click_mod.UNPROCESSED)(cmd)
    cmd = click_mod.argument("path")(cmd)
    cmd = click_mod.option(
        "--python-library",
        is_flag=True,
        default=False,
        help="For .py inputs, emit a library module without synthesizing @main.",
    )(cmd)
    cmd = click_mod.option(
        "--python-libpython",
        type=click_mod.Choice(PYTHON_LIBPYTHON_CHOICES, case_sensitive=False),
        default=None,
        envvar="PCC_PYTHON_LIBPYTHON",
        metavar="MODE",
        help="Python fallback linkage policy: off (default), auto, or on.",
    )(cmd)
    cmd = click_mod.option(
        "--verbose",
        is_flag=True,
        default=False,
        help="Print each pipeline step and timing info (Python pipeline).",
    )(cmd)
    cmd = click_mod.option(
        "--backend",
        type=click_mod.Choice(BACKEND_CHOICES, case_sensitive=False),
        default=None,
        envvar=backend_env_name(),
        metavar="BACKEND",
        help="Backend implementation to use: llvm (default), llvm_capi, or self.",
    )(cmd)
    cmd = click_mod.option(
        "--gpu-backend",
        type=click_mod.Choice(["none", "metal"], case_sensitive=False),
        default=None,
        envvar=gpu_backend_env_name(),
        metavar="BACKEND",
        help="Device backend for annotated GPU kernels: none (default) or metal.",
    )(cmd)
    cmd = click_mod.option(
        "-o",
        "output_path",
        metavar="PATH",
        help="Output path. For .py inputs, writes a native executable here "
        "(or the LLVM IR when --emit-llvm is given).",
    )(cmd)
    cmd = click_mod.option(
        "--emit-llvm",
        "emit_llvm",
        metavar="PATH",
        help="Emit LLVM IR to PATH instead of running. For .py inputs may "
        "be given without a value to emit to <stem>.ll.",
        is_flag=False,
        flag_value=DEFAULT_EMIT_LL,
    )(cmd)
    cmd = click_mod.option(
        "--emit-asm",
        "emit_asm",
        metavar="PATH",
        help="Emit assembly to PATH instead of running.",
    )(cmd)
    cmd = click_mod.option(
        "--emit-obj",
        "emit_obj",
        metavar="PATH",
        help="Emit object file to PATH instead of running. Useful for cross-compilation.",
    )(cmd)
    cmd = click_mod.option(
        "--target",
        "target_triple",
        metavar="TRIPLE",
        help="LLVM target triple for cross-compilation (e.g. x86_64-unknown-linux-gnu, aarch64-unknown-linux-gnu).",
    )(cmd)
    cmd = click_mod.option(
        "-O",
        "opt_level",
        type=click_mod.IntRange(0, 3),
        default=2,
        show_default=True,
        help="Optimization level: 0 (none), 1 (basic), 2 (default), 3 (aggressive).",
    )(cmd)
    cmd = click_mod.option(
        "--ensure-make-goal",
        "ensure_make_goal_specs",
        multiple=True,
        metavar="PATH=GOAL",
        help="Repeat to run `make -C PATH GOAL` before collecting or linking sources.",
    )(cmd)
    cmd = click_mod.option(
        "--prepare-cmd",
        "prepare_cmds",
        multiple=True,
        metavar="CMD",
        help="Repeat to run shell commands before collecting or compiling sources.",
    )(cmd)
    cmd = click_mod.option(
        "--link-arg",
        "link_args",
        multiple=True,
        metavar="ARG",
        help="Repeat to pass raw linker args such as -lm or /path/to/libfoo.a.",
    )(cmd)
    cmd = click_mod.option(
        "--cpp-arg",
        "cpp_args",
        multiple=True,
        metavar="ARG",
        help="Repeat to pass raw preprocessor args such as -DNAME=1, -UFOO, or -I/path.",
    )(cmd)
    cmd = click_mod.option(
        "--depends-on",
        "dependencies",
        multiple=True,
        metavar="PATH[=GOAL]",
        help="Add a dependency file or directory. For directories, use PATH=GOAL to collect sources from a dry-run of `make GOAL`.",
    )(cmd)
    cmd = click_mod.option(
        "--sources-from-make",
        metavar="GOAL",
        help="Collect participating .c files from a dry-run of `make GOAL` instead of scanning the directory",
    )(cmd)
    cmd = click_mod.option(
        "--cache-dir",
        metavar="PATH",
        help="Override the on-disk translation-unit compile cache directory.",
    )(cmd)
    cmd = click_mod.option(
        "--no-cache",
        is_flag=True,
        default=False,
        help="Disable the translation-unit compile cache.",
    )(cmd)
    cmd = click_mod.option(
        "--system-link",
        is_flag=True,
        default=False,
        help="Link and run via the host C compiler instead of MCJIT",
    )(cmd)
    cmd = click_mod.option(
        "--jobs",
        type=click_mod.IntRange(min=1),
        default=8,
        show_default=True,
        help="Number of translation units to compile in parallel when using --separate-tus, --depends-on, or --system-link",
    )(cmd)
    cmd = click_mod.option(
        "--separate-tus",
        is_flag=True,
        default=False,
        help="Compile directory inputs as separate translation units before linking",
    )(cmd)
    cmd = click_mod.option(
        "-g",
        "--debug",
        "emit_debug",
        is_flag=True,
        default=False,
        help="Generate DWARF debug information",
    )(cmd)
    cmd = click_mod.option(
        "--llvmdump",
        is_flag=True,
        default=False,
        help="Dump LLVM IR to temp files",
    )(cmd)
    cmd = click_mod.option(
        "--disable-pass",
        "disabled_passes",
        multiple=True,
        metavar="NAME",
        help="Repeat to disable named pass(es). Works for both pcc passes and concrete LLVM passes.",
    )(cmd)
    cmd = click_mod.option(
        "--pass",
        "enabled_passes",
        multiple=True,
        metavar="NAME",
        help="Repeat to enable only the named pass(es). Works for both pcc passes and concrete LLVM passes.",
    )(cmd)
    cmd = click_mod.command(context_settings={"ignore_unknown_options": True})(cmd)
    return cmd


main = _build_click_main()


if __name__ == "__main__":
    raise SystemExit(_plain_main())
