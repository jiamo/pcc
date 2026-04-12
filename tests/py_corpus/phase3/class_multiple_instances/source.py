class Counter:
    def __init__(self) -> None:
        self.n = 0

    def inc(self) -> None:
        self.n = self.n + 1


def main() -> None:
    a = Counter()
    b = Counter()
    a.inc()
    a.inc()
    a.inc()
    b.inc()
    print(a.n)
    print(b.n)


main()
