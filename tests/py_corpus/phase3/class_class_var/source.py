class Widget:
    kind = "generic"

    def __init__(self, name: str) -> None:
        self.name = name


def main() -> None:
    a = Widget("alpha")
    b = Widget("beta")
    print(Widget.kind)
    print(a.kind)
    print(b.kind)
    print(a.name)
    print(b.name)


main()
