class Greeter:
    def hello(self) -> None:
        print("hi")


def main() -> None:
    g = Greeter()
    g.hello()
    g.hello()


main()
