def in_range(a: int, b: int, c: int) -> bool:
    return a < b and b < c


def main() -> None:
    print(in_range(1, 2, 3))
    print(in_range(1, 5, 3))
    print(in_range(0, 0, 1))
    print(in_range(-5, -3, 0))
    print(in_range(10, 20, 20))


main()
