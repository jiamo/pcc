class Rect:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height


def main() -> None:
    r = Rect(width=4, height=5)
    s = Rect(height=10, width=3)
    print(r.width)
    print(r.height)
    print(s.width)
    print(s.height)


main()
