def echo(x: int) -> None:
    print(x)


def ident(x: int) -> int:
    return x


def add_one(x: int) -> int:
    return x + 1


def main() -> None:
    n: int = 2 ** 100
    echo(n)
    print(ident(n))
    print(add_one(n))
    print(ident(n) == 2 ** 100)
    print(add_one(n) > n)


main()
