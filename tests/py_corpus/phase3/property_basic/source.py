class Temperature:
    def __init__(self, celsius: int) -> None:
        self._celsius = celsius

    @property
    def celsius(self) -> int:
        return self._celsius


def main() -> None:
    t = Temperature(25)
    print(t.celsius)


main()
