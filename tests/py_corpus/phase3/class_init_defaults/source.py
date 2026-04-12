class Config:
    def __init__(self, x: int = 10, y: int = 20) -> None:
        self.x = x
        self.y = y


def main() -> None:
    a = Config()
    b = Config(5)
    c = Config(5, 6)
    print(a.x)
    print(a.y)
    print(b.x)
    print(b.y)
    print(c.x)
    print(c.y)


main()
