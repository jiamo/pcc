def sum_range(n: int) -> int:
    total: int = 0
    for i in range(n):
        total = total + i
    return total


def sum_inclusive(n: int) -> int:
    total: int = 0
    for i in range(1, n + 1):
        total = total + i
    return total


def main() -> None:
    print(sum_range(10))
    print(sum_inclusive(10))
    print(sum_range(0))
    print(sum_inclusive(100))


main()
