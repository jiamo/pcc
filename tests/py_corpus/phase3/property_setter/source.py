class Box:
    def __init__(self) -> None:
        self._value = 0

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, v: int) -> None:
        self._value = v


def main() -> None:
    b = Box()
    print(b.value)
    b.value = 42
    print(b.value)
    b.value = 100
    print(b.value)


main()
