def square(x: int) -> int:
    return x * x


def add_one(x: int) -> int:
    return x + 1


def double(x: int) -> int:
    return 2 * x


def main() -> None:
    print(double(add_one(square(3))))
    print(square(add_one(double(2))))
    print(add_one(square(double(3))))
    print(double(double(double(1))))
    print(square(square(2)))


main()
