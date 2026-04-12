def sum_step(start: int, stop: int, step: int) -> int:
    total: int = 0
    for i in range(start, stop, step):
        total = total + i
    return total


def main() -> None:
    print(sum_step(1, 20, 3))
    print(sum_step(10, 0, -1))
    print(sum_step(0, 10, 2))
    print(sum_step(0, 11, 2))
    print(sum_step(5, 5, 1))


main()
