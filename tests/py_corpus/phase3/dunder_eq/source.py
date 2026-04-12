class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Point):
            return False
        return self.x == other.x and self.y == other.y


def main() -> None:
    a = Point(1, 2)
    b = Point(1, 2)
    c = Point(3, 4)
    print(a == b)
    print(a == c)
    print(b == c)


main()
