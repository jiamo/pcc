def sum_list(xs: list[int]) -> int:
    total: int = 0
    for v in xs:
        total = total + v
    return total


def main() -> None:
    squares = [i * i for i in range(5)]
    print(sum_list(squares))
    for v in squares:
        print(v)
    explicit: list[int] = [10, 20, 30]
    for v in explicit:
        print(v)


main()
