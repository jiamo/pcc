class Age:
    def __init__(self) -> None:
        self._value = 0

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, v: int) -> None:
        if v < 0:
            raise ValueError("negative age")
        self._value = v


def main() -> None:
    a = Age()
    a.value = 30
    print(a.value)
    try:
        a.value = -1
    except ValueError as e:
        print("caught")
        print(str(e))
    print(a.value)


main()
