class Vec:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __add__(self, other: "Vec") -> "Vec":
        return Vec(self.x + other.x, self.y + other.y)


def main() -> None:
    a = Vec(1, 2)
    b = Vec(3, 4)
    c = a + b
    print(c.x)
    print(c.y)


main()
