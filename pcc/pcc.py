
from .evaluater.c_evaluator import CEvaluator
import sys
import click


@click.command()
@click.option("--llvmdump", is_flag=True, default=False, help="Dump LLVM IR to temp files")
@click.argument('filename')
def main(filename, llvmdump):
    """Pcc - a C compiler built on Python and LLVM."""
    pcc = CEvaluator()
    with open(filename, "r") as f:
        ret = pcc.evaluate(f.read(), llvmdump=llvmdump)
    sys.exit(ret if isinstance(ret, int) else 0)


if __name__ == "__main__":
    main()
