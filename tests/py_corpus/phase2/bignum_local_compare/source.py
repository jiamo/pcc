def main() -> None:
    x: int = 2 ** 100
    y: int = x + 1
    print(x)
    print(y)
    print(x == 2 ** 100)
    print(y > x)


main()
