class Base:
    def __init__(self) -> None:
        self._protected = 1
        self.__mangled = 2

    def read_mangled(self) -> int:
        return self.__mangled


class Child(Base):
    def __init__(self) -> None:
        super().__init__()
        self.__mangled = 99

    def child_mangled(self) -> int:
        return self.__mangled


def main() -> None:
    c = Child()
    print(c._protected)
    print(c.read_mangled())
    print(c.child_mangled())


main()
