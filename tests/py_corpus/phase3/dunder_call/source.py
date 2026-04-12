class Multiplier:
    def __init__(self, factor: int) -> None:
        self.factor = factor

    def __call__(self, x: int) -> int:
        return x * self.factor


def main() -> None:
    double = Multiplier(2)
    triple = Multiplier(3)
    print(double(5))
    print(double(10))
    print(triple(5))
    print(triple(10))


main()
