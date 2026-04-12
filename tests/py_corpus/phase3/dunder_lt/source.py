class Num:
    def __init__(self, v: int) -> None:
        self.v = v

    def __lt__(self, other: "Num") -> bool:
        return self.v < other.v


def main() -> None:
    a = Num(3)
    b = Num(1)
    c = Num(2)
    print(a < b)
    print(b < a)
    items = [a, b, c]
    items.sort()
    for x in items:
        print(x.v)


main()
