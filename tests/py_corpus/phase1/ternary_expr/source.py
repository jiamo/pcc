def absv(x: int) -> int:
    return x if x >= 0 else -x


def sign(x: int) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def maxv(a: int, b: int) -> int:
    return a if a > b else b


def main() -> None:
    print(absv(5))
    print(absv(-7))
    print(absv(0))
    print(sign(10))
    print(sign(-3))
    print(sign(0))
    print(maxv(3, 4))
    print(maxv(10, 2))


main()
