class Base:
    def __init__(self, x: int) -> None:
        self.x = x


class Child(Base):
    def __init__(self, x: int, y: int) -> None:
        super().__init__(x)
        self.y = y


def main() -> None:
    c = Child(10, 20)
    print(c.x)
    print(c.y)


main()
