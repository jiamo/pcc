
from .evaluater.c_evaluator import CEvaluator
from .project import collect_project
import os
import sys
import click


@click.command()
@click.option("--llvmdump", is_flag=True, default=False, help="Dump LLVM IR to temp files")
@click.argument('path')
def main(path, llvmdump):
    """Pcc - a C compiler built on Python and LLVM.

    PATH can be a .c file or a directory containing .c files.

    \b
    Examples:
        pcc hello.c              # compile and run a single file
        pcc myproject/            # compile all .c files in directory
        pcc --llvmdump test.c    # also dump LLVM IR
    """
    try:
        source, base_dir = collect_project(path)
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    pcc = CEvaluator()
    ret = pcc.evaluate(source, llvmdump=llvmdump, base_dir=base_dir)
    sys.exit(ret if isinstance(ret, int) else 0)


if __name__ == "__main__":
    main()
