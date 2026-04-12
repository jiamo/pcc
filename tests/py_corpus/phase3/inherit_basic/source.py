class Animal:
    def sound(self) -> None:
        print("generic sound")


class Dog(Animal):
    pass


def main() -> None:
    d = Dog()
    d.sound()


main()
