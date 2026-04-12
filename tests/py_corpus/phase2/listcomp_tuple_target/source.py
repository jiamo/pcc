def main() -> None:
    pairs: list[tuple[int, int]] = [(1, 2), (3, 4), (5, 6)]
    sums = [x + y for (x, y) in pairs]
    for s in sums:
        print(s)
    products = [a * b for (a, b) in pairs if a < b]
    for p in products:
        print(p)


main()
