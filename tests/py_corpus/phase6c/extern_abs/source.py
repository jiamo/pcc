from pcc.extern import extern, c_int


abs_fn = extern("abs", (c_int,), c_int)


def main() -> None:
    x: int = abs_fn(-42)
    print(x)


main()
