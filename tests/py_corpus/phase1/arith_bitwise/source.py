def band(a: int, b: int) -> int:
    return a & b


def bor(a: int, b: int) -> int:
    return a | b


def bxor(a: int, b: int) -> int:
    return a ^ b


def shl(a: int, b: int) -> int:
    return a << b


def shr(a: int, b: int) -> int:
    return a >> b


def main() -> None:
    print(band(12, 10))
    print(bor(12, 10))
    print(bxor(12, 10))
    print(shl(3, 4))
    print(shr(64, 2))
    print(band(255, 15))


main()
