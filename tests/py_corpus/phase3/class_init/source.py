class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


def main() -> None:
    p = Point(3, 7)
    print(p.x)
    print(p.y)


main()
