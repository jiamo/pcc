
from .evaluater.c_evaluator import CEvaluator
import sys
import click


@click.command()
@click.option("--llvmdump", is_flag=True, default=False)
@click.argument('filename')
def main(filename, llvmdump):
    print(filename, llvmdump)
    pcc = CEvaluator()
    print("hello\n")
    with open(filename, "r") as f:

        ret = pcc.evaluate(f.read(),llvmdump=llvmdump)
        print(ret)

if __name__ == "__main__":
    main()
