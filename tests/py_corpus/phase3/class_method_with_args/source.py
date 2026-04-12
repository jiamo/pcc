class Adder:
    def __init__(self, base: int) -> None:
        self.base = base

    def add(self, x: int, y: int) -> int:
        return self.base + x + y


def main() -> None:
    a = Adder(10)
    print(a.add(1, 2))
    print(a.add(100, 200))


main()
