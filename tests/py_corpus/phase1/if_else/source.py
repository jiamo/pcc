def sign(x: int) -> int:
    if x > 0:
        return 1
    else:
        return -1


def abs_val(x: int) -> int:
    if x < 0:
        return -x
    else:
        return x


def main() -> None:
    print(sign(10))
    print(sign(-3))
    print(abs_val(-7))
    print(abs_val(5))
    print(abs_val(0))


main()
