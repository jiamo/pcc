class Builder:
    def __init__(self) -> None:
        self.total = 0

    def add(self, x: int) -> "Builder":
        self.total = self.total + x
        return self


def main() -> None:
    b = Builder()
    result = b.add(1).add(2).add(3).add(4)
    print(result.total)


main()
