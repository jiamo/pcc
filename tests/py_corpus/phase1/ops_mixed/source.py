def mix_add(a: int, b: float) -> float:
    return a + b


def mix_mul(a: int, b: float) -> float:
    return a * b


def int_pow(a: int, b: int) -> int:
    return a ** b


def float_pow(a: float, b: int) -> float:
    return a ** b


def main() -> None:
    print(mix_add(1, 2.5))
    print(mix_mul(4, 2.5))
    print(10 - 3.5)
    print(10 / 4)
    print(int_pow(2, 10))
    print(float_pow(2.0, 3))
    print(int_pow(3, 4))


main()
