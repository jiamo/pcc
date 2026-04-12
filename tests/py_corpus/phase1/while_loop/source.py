def countdown(n: int) -> None:
    while n > 0:
        print(n)
        n = n - 1


def sum_to(n: int) -> int:
    total: int = 0
    i: int = 1
    while i <= n:
        total = total + i
        i = i + 1
    return total


def main() -> None:
    countdown(5)
    print(sum_to(10))
    print(sum_to(100))


main()
