
from .evaluater.c_evaluator import CEvaluator
from .project import collect_project, collect_translation_units
import sys
import click
from click.core import ParameterSource


@click.command(context_settings={"ignore_unknown_options": True})
@click.option("--llvmdump", is_flag=True, default=False, help="Dump LLVM IR to temp files")
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
    help="Number of translation units to compile in parallel when using --separate-tus",
)
@click.option(
    "--sources-from-make",
    metavar="GOAL",
    help="Collect participating .c files from `make -nB GOAL` instead of scanning the directory",
)
@click.argument('path')
@click.argument('prog_args', nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def main(ctx, path, llvmdump, separate_tus, jobs, sources_from_make, prog_args):
    """Pcc - a C compiler built on Python and LLVM.

    PATH can be a .c file or a directory containing .c files.
    Any arguments after PATH (or after --) are passed to the compiled program.

    \b
    Examples:
        pcc hello.c              # compile and run a single file
        pcc myproject/           # compile all .c files in directory
        pcc --sources-from-make app myproject/  # collect sources from `make -nB app`
        pcc --separate-tus --jobs 4 myproject/  # compile project files in parallel
        pcc --llvmdump test.c    # also dump LLVM IR
        pcc myproject/ -- script.lua  # pass args to compiled program
    """
    jobs_was_explicit = (
        ctx.get_parameter_source("jobs") == ParameterSource.COMMANDLINE
    )
    if jobs_was_explicit and not separate_tus:
        click.echo("Error: --jobs requires --separate-tus", err=True)
        sys.exit(1)

    try:
        if separate_tus:
            units, base_dir = collect_translation_units(
                path, sources_from_make=sources_from_make
            )
        else:
            source, base_dir = collect_project(
                path, sources_from_make=sources_from_make
            )
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    pcc = CEvaluator()
    try:
        if separate_tus:
            ret = pcc.evaluate_translation_units(
                units,
                llvmdump=llvmdump,
                base_dir=base_dir,
                prog_args=list(prog_args),
                jobs=jobs,
            )
        else:
            ret = pcc.evaluate(
                source, llvmdump=llvmdump, base_dir=base_dir, prog_args=list(prog_args)
            )
    except (ValueError, RuntimeError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    sys.exit(ret if isinstance(ret, int) else 0)


if __name__ == "__main__":
    main()
