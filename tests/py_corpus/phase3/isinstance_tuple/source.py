class Apple:
    pass


class Banana:
    pass


class Carrot:
    pass


def describe(x: object) -> None:
    if isinstance(x, (Apple, Banana)):
        print("fruit")
    else:
        print("not fruit")


def main() -> None:
    describe(Apple())
    describe(Banana())
    describe(Carrot())


main()
