def add_one(x: int) -> int:
    return x + 1


def main() -> None:
    edge: int = (2 ** 62) - 1
    y: int = add_one(edge)
    print(y)
    print(y > edge)
    print(y == 2 ** 62)
    z: int = y - 1
    print(z == edge)


main()
