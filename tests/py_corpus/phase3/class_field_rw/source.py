class Box:
    def __init__(self) -> None:
        self.value = 0


def main() -> None:
    b = Box()
    print(b.value)
    b.value = 42
    print(b.value)
    b.value = b.value + 1
    print(b.value)


main()
