class MathUtil:
    @staticmethod
    def add(a: int, b: int) -> int:
        return a + b

    @staticmethod
    def mul(a: int, b: int) -> int:
        return a * b


def main() -> None:
    print(MathUtil.add(2, 3))
    print(MathUtil.mul(4, 5))
    m = MathUtil()
    print(m.add(10, 20))


main()
