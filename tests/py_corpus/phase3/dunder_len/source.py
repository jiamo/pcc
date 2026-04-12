class Bag:
    def __init__(self, n: int) -> None:
        self.n = n

    def __len__(self) -> int:
        return self.n


def main() -> None:
    a = Bag(0)
    b = Bag(5)
    c = Bag(42)
    print(len(a))
    print(len(b))
    print(len(c))


main()
