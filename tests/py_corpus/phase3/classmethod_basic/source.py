class Counter:
    count = 0

    def __init__(self) -> None:
        Counter.count = Counter.count + 1

    @classmethod
    def make(cls) -> "Counter":
        return cls()

    @classmethod
    def current(cls) -> int:
        return cls.count


def main() -> None:
    a = Counter.make()
    b = Counter.make()
    c = Counter.make()
    print(Counter.current())
    print(type(a).__name__)


main()
