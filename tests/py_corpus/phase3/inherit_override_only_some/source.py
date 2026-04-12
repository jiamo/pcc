class Shape:
    def area(self) -> None:
        print("shape area")

    def describe(self) -> None:
        print("shape describe")


class Square(Shape):
    def area(self) -> None:
        print("square area")


def main() -> None:
    s = Square()
    s.area()
    s.describe()


main()
