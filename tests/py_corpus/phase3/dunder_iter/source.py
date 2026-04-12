class Range3:
    def __init__(self, n: int) -> None:
        self.n = n

    def __iter__(self) -> "Range3":
        self.i = 0
        return self

    def __next__(self) -> int:
        if self.i >= self.n:
            raise StopIteration
        v = self.i
        self.i = self.i + 1
        return v


def main() -> None:
    r = Range3(4)
    for x in r:
        print(x)


main()
