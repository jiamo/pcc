class Base:
    def greet(self) -> None:
        print("base hello")


class Child(Base):
    def greet(self) -> None:
        super().greet()
        print("child hello")


def main() -> None:
    c = Child()
    c.greet()


main()
