
from .evaluater.c_evaluator import CEvaluator
from .project import collect_project
import os
import sys
import click


@click.command(context_settings={"ignore_unknown_options": True})
@click.option("--llvmdump", is_flag=True, default=False, help="Dump LLVM IR to temp files")
@click.argument('path')
@click.argument('prog_args', nargs=-1, type=click.UNPROCESSED)
def main(path, llvmdump, prog_args):
    """Pcc - a C compiler built on Python and LLVM.

    PATH can be a .c file or a directory containing .c files.
    Any arguments after PATH (or after --) are passed to the compiled program.

    \b
    Examples:
        pcc hello.c              # compile and run a single file
        pcc myproject/           # compile all .c files in directory
        pcc --llvmdump test.c    # also dump LLVM IR
        pcc myproject/ -- script.lua  # pass args to compiled program
    """
    try:
        source, base_dir = collect_project(path)
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    pcc = CEvaluator()
    ret = pcc.evaluate(source, llvmdump=llvmdump, base_dir=base_dir, prog_args=list(prog_args))
    sys.exit(ret if isinstance(ret, int) else 0)


if __name__ == "__main__":
    main()
